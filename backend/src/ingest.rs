use std::{collections::HashSet, fs::File, io::Read, path::Path, sync::Arc};

use anyhow::{Context, Result, bail};
use serde::{
    Deserializer,
    de::{self, DeserializeSeed, IgnoredAny, MapAccess, SeqAccess, Visitor},
};
use serde_json::Value;
use tokio::sync::OwnedSemaphorePermit;

mod core;
pub(crate) mod journal;
mod pdf;

use core::CanonicalInput;
pub(crate) use core::normalize_time;

pub const MAX_FILE_BYTES: usize = 25 * 1024 * 1024;
pub use core::FIELDS;
const IBM_HEADER: [&str; 11] = [
    "Timestamp",
    "From Bank",
    "Account",
    "To Bank",
    "Account",
    "Amount Received",
    "Receiving Currency",
    "Amount Paid",
    "Payment Currency",
    "Payment Format",
    "Is Laundering",
];

/// The validated statement IR shared by all ingestion strategies.
#[derive(Debug)]
pub enum StatementRow {
    Transaction(Value),
    Relation(Value),
}

impl StatementRow {
    pub fn into_payload(self) -> Value {
        match self {
            Self::Transaction(value) | Self::Relation(value) => value,
        }
    }
}

#[derive(Debug)]
pub struct ParsedRow {
    pub index: usize,
    pub row: std::result::Result<StatementRow, String>,
}

#[cfg(test)]
async fn parse_document(
    path: &Path,
    suffix: &str,
    client: &reqwest::Client,
) -> Result<Vec<ParsedRow>> {
    parse_document_with_guard(path, suffix, client, None).await
}

pub async fn parse_document_with_guard(
    path: &Path,
    suffix: &str,
    client: &reqwest::Client,
    guard: Option<Arc<OwnedSemaphorePermit>>,
) -> Result<Vec<ParsedRow>> {
    fold_document_with_guard(
        path,
        suffix,
        client,
        guard,
        None,
        Vec::new(),
        |rows, row| {
            rows.push(row);
            Ok(())
        },
    )
    .await
}

pub async fn fold_document_with_guard<T, F>(
    path: &Path,
    suffix: &str,
    client: &reqwest::Client,
    guard: Option<Arc<OwnedSemaphorePermit>>,
    journal: Option<journal::Journal>,
    mut accumulator: T,
    mut append: F,
) -> Result<T>
where
    T: Send + 'static,
    F: FnMut(&mut T, ParsedRow) -> Result<()> + Send + 'static,
{
    let format = Format::for_suffix(suffix)?;
    let path = path.to_owned();
    let cpu_guard = guard.clone();
    let (document, accumulator, append, count) =
        tokio::task::spawn_blocking(move || -> Result<_> {
            let _guard = cpu_guard;
            let file = File::open(path)?;
            if file.metadata()?.len() > MAX_FILE_BYTES as u64 {
                bail!("Document exceeds 25 MiB.");
            }
            let mut input = file.take(MAX_FILE_BYTES as u64 + 1);
            let mut count = 0;
            let mut emit = |row| {
                count += 1;
                append(&mut accumulator, row)
            };
            let document = format.decode(&mut input, &mut emit)?;
            if input.limit() == 0 {
                bail!("Document exceeds 25 MiB.");
            }
            Ok((document, accumulator, append, count))
        })
        .await??;
    if let Some(document) = document {
        return pdf::extract(document, client, guard, journal, accumulator, append).await;
    }
    if count == 0 {
        bail!("Statement contains no transaction rows.");
    }
    Ok(accumulator)
}

enum Format {
    Csv,
    Json,
    Pdf,
}

impl Format {
    fn for_suffix(suffix: &str) -> Result<Self> {
        match suffix {
            ".csv" => Ok(Self::Csv),
            ".json" => Ok(Self::Json),
            ".pdf" => Ok(Self::Pdf),
            _ => bail!("Supported files: CSV, JSON, and PDF."),
        }
    }

    fn decode(
        self,
        mut input: impl Read,
        emit: &mut impl FnMut(ParsedRow) -> Result<()>,
    ) -> Result<Option<pdf::Flexible>> {
        if matches!(self, Self::Csv) {
            csv_rows(input, emit)?;
            return Ok(None);
        }
        let mut bytes = Vec::new();
        input.read_to_end(&mut bytes)?;
        match self {
            Self::Json => json_rows(&bytes, emit)?,
            Self::Pdf => return pdf::inspect(bytes, emit),
            Self::Csv => unreachable!("CSV is streamed above"),
        }
        Ok(None)
    }
}

fn without_bom(bytes: &[u8]) -> &[u8] {
    bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(bytes)
}

fn canonical_header(header: &csv::StringRecord) -> bool {
    header.iter().eq(IBM_HEADER)
        || FIELDS
            .iter()
            .all(|field| header.iter().any(|name| name == *field))
}

fn csv_reader<R: Read>(input: R) -> csv::Reader<R> {
    csv::ReaderBuilder::new().flexible(true).from_reader(input)
}

fn csv_rows(input: impl Read, emit: &mut impl FnMut(ParsedRow) -> Result<()>) -> Result<()> {
    let mut reader = csv_reader(input);
    let header = reader.headers().context("CSV header must use UTF-8.")?;
    if !canonical_header(header) {
        bail!("Expected canonical CSV columns or the original IBM header. See /statement-schema.");
    }
    let names: Vec<_> = if header.iter().eq(IBM_HEADER) {
        FIELDS.into_iter().chain(["is_laundering"]).collect()
    } else {
        let names: HashSet<_> = header.iter().collect();
        if names.len() != header.len() {
            bail!("CSV header contains duplicate columns.");
        }
        header.iter().collect()
    };
    let column_count = names.len();
    let columns = FIELDS.map(|field| {
        names
            .iter()
            .position(|name| *name == field)
            .expect("canonical header checked")
    });
    let reference = names.iter().position(|name| *name == "reference");
    let mut index = 0;
    let mut record = csv::StringRecord::new();
    while reader
        .read_record(&mut record)
        .context("CSV data must use UTF-8.")?
    {
        if record.iter().all(|value| value.trim().is_empty()) {
            continue;
        }
        let row = if record.len() == column_count {
            CanonicalInput::csv(
                columns.map(|column| &record[column]),
                reference.map(|column| &record[column]),
            )
            .interpret()
            .map(StatementRow::Transaction)
        } else {
            Err("Column count differs from header".into())
        };
        index += 1;
        emit(ParsedRow { index, row })?;
    }
    Ok(())
}

fn json_rows(bytes: &[u8], emit: &mut impl FnMut(ParsedRow) -> Result<()>) -> Result<()> {
    let mut decoder = serde_json::Deserializer::from_slice(without_bom(bytes));
    let mut emit_error = None;
    let result = decoder.deserialize_any(JsonRows {
        emit: &mut |row| {
            emit(row).map_err(|error| {
                emit_error = Some(error);
                anyhow::anyhow!("Could not stage extracted rows.")
            })
        },
    });
    if let Some(error) = emit_error {
        return Err(error);
    }
    result.map_err(|error| {
        let message = if error.is_data() {
            "JSON must be an array or an object with a transactions array."
        } else {
            "Invalid JSON document."
        };
        anyhow::Error::new(error).context(message)
    })?;
    decoder.end().context("Invalid JSON document.")
}

struct JsonRows<'a, F> {
    emit: &'a mut F,
}

impl<'de, F> DeserializeSeed<'de> for JsonRows<'_, F>
where
    F: FnMut(ParsedRow) -> Result<()>,
{
    type Value = ();

    fn deserialize<D: Deserializer<'de>>(self, decoder: D) -> std::result::Result<(), D::Error> {
        decoder.deserialize_seq(self)
    }
}

impl<'de, F> Visitor<'de> for JsonRows<'_, F>
where
    F: FnMut(ParsedRow) -> Result<()>,
{
    type Value = ();

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("an array or an object with a transactions array")
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut items: A) -> std::result::Result<(), A::Error> {
        let mut index = 0;
        while let Some(item) = items.next_element::<Value>()? {
            index += 1;
            let row = item
                .as_object()
                .ok_or_else(|| "Expected a transaction object".to_owned())
                .and_then(CanonicalInput::json)
                .and_then(CanonicalInput::interpret)
                .map(StatementRow::Transaction);
            (self.emit)(ParsedRow { index, row }).map_err(de::Error::custom)?;
        }
        Ok(())
    }

    fn visit_map<A: MapAccess<'de>>(self, mut object: A) -> std::result::Result<(), A::Error> {
        let mut found = false;
        while let Some(key) = object.next_key::<String>()? {
            if key == "transactions" {
                if found {
                    return Err(de::Error::duplicate_field("transactions"));
                }
                found = true;
                object.next_value_seed(JsonRows { emit: self.emit })?;
            } else {
                object.next_value::<IgnoredAny>()?;
            }
        }
        if !found {
            return Err(de::Error::missing_field("transactions"));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::core::decimal;
    use super::*;
    use serde_json::json;

    #[tokio::test]
    async fn parses_saved_statement_csv() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/statement.csv");
        let rows = parse_document(&path, ".csv", &reqwest::Client::new())
            .await
            .unwrap();
        assert_eq!(rows.len(), 3);
        assert!(rows.iter().all(|row| row.row.is_ok()));
    }

    #[test]
    fn canonical_numeric_and_identity_fields_use_exact_source_values() {
        let input = br#"[{
            "timestamp": "2026-11-18T23:08:34.890123-04:00",
            "from_bank": "B\u00e9", "from_account": "0000123", "to_bank": "ABC", "to_account": "003",
            "amount_received": 999999999999999999.123456789012345678,
            "amount_paid": "1.23400e-2", "receiving_currency": "USD", "payment_currency": "USD",
            "payment_format": "ACH"
        }]"#;
        let mut rows = Vec::new();
        json_rows(input, &mut |row| {
            rows.push(row);
            Ok(())
        })
        .unwrap();
        assert_eq!(rows[0].index, 1);
        let row = rows.into_iter().next().unwrap().row.unwrap().into_payload();
        assert_eq!(row["timestamp"], "2026-11-19T03:08:34.890123+00:00");
        assert_eq!(row["sender_id"], json!(r#"["B\u00e9","0000123"]"#));
        assert_eq!(
            row["amount_received"],
            "999999999999999999.123456789012345678"
        );
        assert_eq!(row["amount_paid"], "0.0123400");
        assert_eq!(
            decimal("1000000000000000000").unwrap(),
            "1000000000000000000"
        );
        assert!(decimal("1000000000000000000.000000000000000001").is_err());
        assert!(decimal("0.0000000000000000001").is_err());
    }
}
