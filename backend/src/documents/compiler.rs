use anyhow::{Context, Result};
use serde::Deserialize;
use std::io::{BufWriter, Write};
use tokio::time::Instant;

use crate::{
    Admission,
    db::Commands,
    graph::str_field,
    ingest::{ParsedRow, StatementRow, fold_document_with_guard, journal::Journal},
    storage::Document,
};

type Outcome = std::result::Result<Spool, String>;

pub(super) struct Compilation {
    pub(super) outcome: Outcome,
    pub(super) run_id: Option<String>,
}

impl Compilation {
    pub(super) fn failed(message: &str) -> Self {
        Self {
            outcome: Err(message.into()),
            run_id: None,
        }
    }
}

pub(super) struct Spool {
    pub(super) rows: tempfile::NamedTempFile,
    pub(super) accepted: i64,
    pub(super) rejected: i64,
    pub(super) review: bool,
}

pub(super) async fn compile(
    client: &reqwest::Client,
    commands: &Commands,
    file: &Document,
    deadline: Instant,
    permit: &Admission,
) -> Compilation {
    let journal =
        (file.suffix == ".pdf").then(|| Journal::new(commands.clone(), file.sha256.clone()));
    let run_id = journal.as_ref().map(|journal| journal.run_id.clone());
    let outcome = stage_document(client, file, deadline, permit, journal).await;
    Compilation { outcome, run_id }
}

async fn stage_document(
    client: &reqwest::Client,
    file: &Document,
    deadline: Instant,
    permit: &Admission,
    journal: Option<Journal>,
) -> Outcome {
    const TIMEOUT: &str =
        "Document processing exceeded the request time limit. Retry this document individually.";
    const CANNOT_STAGE: &str =
        "Could not stage extracted rows. Retry after checking available disk space.";
    if Instant::now() >= deadline {
        return Err(TIMEOUT.into());
    }
    let stage = Stage::new().map_err(|_| CANNOT_STAGE.to_owned())?;
    let parse = fold_document_with_guard(
        &file.path,
        &file.suffix,
        client,
        Some(permit.clone()),
        journal,
        stage,
        move |stage, row| {
            anyhow::ensure!(Instant::now() < deadline, TIMEOUT);
            stage.append(row).context(CANNOT_STAGE)
        },
    );
    let stage = match tokio::time::timeout_at(deadline, parse).await {
        Ok(Ok(stage)) if stage.accepted + stage.rejected > 0 => stage,
        Ok(Ok(_)) => return Err("Statement contains no transaction rows.".into()),
        Ok(Err(error)) => {
            let message = error.to_string();
            if message == TIMEOUT || message == CANNOT_STAGE {
                return Err(message);
            }
            tracing::warn!(kind = %file.suffix, "Document parsing failed");
            if let Some(message) = public_parse_error(&error) {
                return Err(message);
            }
            return Err("Document parsing failed. Check the document format or PDF extraction configuration before retrying.".into());
        }
        Err(_) => return Err(TIMEOUT.into()),
    };
    stage.finish().map_err(|_| CANNOT_STAGE.to_owned())
}

fn public_parse_error(error: &anyhow::Error) -> Option<String> {
    let message = error.to_string();
    let adapter_validation = [
        "Statement contains no transaction rows.",
        "Invalid JSON document.",
        "JSON must be an array or an object with a transactions array.",
        "Expected canonical CSV columns or the original IBM header. See /statement-schema.",
        "CSV header contains duplicate columns.",
    ]
    .contains(&message.as_str());
    // These messages come from controlled parser failures. Provider replies and raw source stay private.
    let pdf_validation = [
        "Flexible PDF extraction requires ",
        "Encrypted PDF requires ",
        "PDF exceeds 200 pages.",
        "Could not read PDF document.",
        "Expected exactly one dolos-transactions.csv attachment.",
        "No transfer relations were identified in the PDF.",
        "PDF extraction ",
        "Could not read PDF extraction response.",
        "Install Poppler pdftoppm ",
        "Extractor returned a transfer outside ",
    ]
    .iter()
    .any(|prefix| message.starts_with(prefix));
    (adapter_validation || pdf_validation).then_some(message)
}

#[derive(serde::Serialize, Deserialize)]
pub(super) enum StagedRow {
    Error {
        index: i64,
        error: String,
    },
    Accepted {
        index: i64,
        timestamp: Option<String>,
        sender: String,
        receiver: String,
        payload: String,
    },
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, serde::Serialize, Deserialize)]
pub(super) enum ProjectionKind {
    Transactions,
    Relations,
    Errors,
}

impl ProjectionKind {
    const ALL: [Self; 3] = [Self::Transactions, Self::Relations, Self::Errors];

    pub(super) fn table(self) -> &'static str {
        match self {
            Self::Transactions => "transactions",
            Self::Relations => "relations",
            Self::Errors => "row_errors",
        }
    }

    pub(super) fn width(self) -> usize {
        if self == Self::Errors { 3 } else { 7 }
    }
}
const ROWS_PER_BATCH: usize = 100;

struct Stage {
    output: BufWriter<tempfile::NamedTempFile>,
    buffers: [Vec<StagedRow>; 3],
    accepted: i64,
    rejected: i64,
    review: bool,
}

impl Stage {
    fn new() -> Result<Self> {
        Ok(Self {
            output: BufWriter::new(tempfile::NamedTempFile::new()?),
            buffers: std::array::from_fn(|_| Vec::with_capacity(ROWS_PER_BATCH)),
            accepted: 0,
            rejected: 0,
            review: false,
        })
    }

    fn flush(&mut self, kind: ProjectionKind) -> Result<()> {
        let rows = &mut self.buffers[kind as usize];
        if !rows.is_empty() {
            serde_json::to_writer(&mut self.output, &(kind, &rows))?;
            self.output.write_all(b"\n")?;
            rows.clear();
        }
        Ok(())
    }

    fn append(&mut self, ParsedRow { index, row }: ParsedRow) -> Result<()> {
        let index = index as i64;
        let (kind, staged) = match row {
            Err(error) => {
                self.rejected += 1;
                (ProjectionKind::Errors, StagedRow::Error { index, error })
            }
            Ok(row) => {
                self.accepted += 1;
                let (kind, row) = match row {
                    StatementRow::Transaction(row) => (ProjectionKind::Transactions, row),
                    StatementRow::Relation(row) => (ProjectionKind::Relations, row),
                };
                self.review |= row["review_required"] == true;
                (
                    kind,
                    StagedRow::Accepted {
                        index,
                        timestamp: row["timestamp"].as_str().map(str::to_owned),
                        sender: str_field(&row, "sender_id").to_owned(),
                        receiver: str_field(&row, "receiver_id").to_owned(),
                        payload: serde_json::to_string(&row)?,
                    },
                )
            }
        };
        let buffer = &mut self.buffers[kind as usize];
        buffer.push(staged);
        if buffer.len() == ROWS_PER_BATCH {
            self.flush(kind)?;
        }
        Ok(())
    }

    fn finish(mut self) -> Result<Spool> {
        for kind in ProjectionKind::ALL {
            self.flush(kind)?;
        }
        self.output.flush()?;
        Ok(Spool {
            rows: self.output.into_inner()?,
            accepted: self.accepted,
            rejected: self.rejected,
            review: self.review,
        })
    }
}
