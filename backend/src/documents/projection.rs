use anyhow::Result;
use libsql::{Transaction, Value as Sql, params};
use std::io::{BufRead, BufReader};

use super::compiler::{Compilation, ProjectionKind, StagedRow};
use crate::{db::now, storage::Location};

pub(super) async fn record_source(
    tx: &Transaction,
    id: &str,
    digest: &str,
    location: &Location,
) -> Result<()> {
    tx.execute(
        "INSERT INTO document_sources SELECT ?, COALESCE(MAX(sequence), 0)+1, ?, ?, ?, ?, ?, ? FROM document_sources WHERE document_id=?",
        params![id,digest,location.backend.clone(),location.bucket.clone(),location.key.clone(),location.size,now(),id],
    ).await?;
    Ok(())
}

const COMPILER_VERSION: i64 = 1;

pub(super) async fn commit_outcome(
    tx: &Transaction,
    id: &str,
    compilation: Compilation,
    apply: bool,
) -> Result<&'static str> {
    let Compilation { outcome, run_id } = compilation;
    let (status, error, accepted, rejected) = match &outcome {
        Err(error) => ("failed", Some(error.as_str()), 0, 0),
        Ok(spool) => (
            if spool.review || spool.rejected > 0 {
                "needs_review"
            } else {
                "completed"
            },
            None,
            spool.accepted,
            spool.rejected,
        ),
    };
    tx.execute(
        "INSERT INTO compilation_attempts SELECT ?, COALESCE(MAX(sequence), 0)+1, ?, ?, ?, ?, ?, ?, ?, ? FROM compilation_attempts WHERE document_id=?",
        params![id,COMPILER_VERSION,run_id,now(),status,accepted,rejected,error,i64::from(apply),id],
    ).await?;
    if !apply {
        return Ok(status);
    }
    if let Ok(spool) = &outcome {
        let mut file = BufReader::new(spool.rows.reopen()?);
        let mut line = String::new();
        let mut statements = std::collections::HashMap::new();
        while file.read_line(&mut line)? != 0 {
            let (kind, rows): (ProjectionKind, Vec<StagedRow>) = serde_json::from_str(&line)?;
            let table = kind.table();
            let width = kind.width();
            let sql = statements.entry((kind, rows.len())).or_insert_with(|| {
                let group = format!("({})", ["?"].repeat(width).join(","));
                format!(
                    "INSERT INTO {table} VALUES {}",
                    vec![group.as_str(); rows.len()].join(",")
                )
            });
            let mut values = Vec::with_capacity(width * rows.len());
            for row in rows {
                match row {
                    StagedRow::Error { index, error } => {
                        values.extend([id.into(), index.into(), error.into()])
                    }
                    StagedRow::Accepted {
                        index,
                        timestamp,
                        sender,
                        receiver,
                        payload,
                    } => values.extend([
                        format!("{id}:{index}").into(),
                        id.into(),
                        index.into(),
                        timestamp.map(Sql::from).unwrap_or(Sql::Null),
                        sender.into(),
                        receiver.into(),
                        payload.into(),
                    ]),
                }
            }
            tx.execute(sql.as_str(), libsql::params_from_iter(values))
                .await?;
            line.clear();
        }
    }
    tx.execute("UPDATE documents SET status=?,error=?,accepted=?,rejected=?,attempts=attempts+1,lease_until=NULL,token=NULL WHERE id=?",
        params![status,error,accepted,rejected,id]).await?;
    Ok(status)
}
