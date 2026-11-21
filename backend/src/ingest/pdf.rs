use std::{collections::HashSet, env, path::Path, sync::Arc, time::Duration};

use anyhow::{Context, Result, bail};
use base64::{Engine, engine::general_purpose::STANDARD};
use lopdf::{
    DecompressError, Dictionary, Document as Pdf, Error as PdfError, Object, ObjectId, dictionary,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use tokio::sync::OwnedSemaphorePermit;

use super::{
    MAX_FILE_BYTES, ParsedRow, StatementRow, canonical_header, csv_reader, csv_rows,
    journal::Journal,
};

mod interpret;
use interpret::{Extraction, ExtractionSource, normalize_transfer};

const ATTACHMENT: &str = "dolos-transactions.csv";
const PAGE_LIMIT: usize = 200;
const PAGE_TEXT_LIMIT: usize = 2 * 1024 * 1024;
const DOCUMENT_TEXT_LIMIT: usize = 32 * 1024 * 1024;
const PAGE_IMAGE_LIMIT: usize = 4 * 1024 * 1024;
const MAX_PROVIDER_RESPONSE: usize = 4 * 1024 * 1024;
const CHUNK_PAGES: u32 = 3;
const INSTRUCTIONS: &str = include_str!("pdf_instructions.txt");

pub(super) struct Flexible {
    bytes: Arc<[u8]>,
    pdf: Arc<Pdf>,
    pages: Vec<String>,
    sha256: String,
}

pub(super) fn inspect(
    bytes: Vec<u8>,
    emit: &mut impl FnMut(ParsedRow) -> Result<()>,
) -> Result<Option<Flexible>> {
    let pdf = Pdf::load_mem(&bytes).map_err(|_| anyhow::anyhow!("Could not read PDF document."))?;
    if pdf.trailer.has(b"Encrypt") || pdf.is_encrypted() {
        bail!("Encrypted PDF requires an unencrypted export.");
    }
    let page_count = pdf.page_iter().take(PAGE_LIMIT + 1).count();
    if page_count > PAGE_LIMIT {
        bail!("PDF exceeds 200 pages. Split the statement.");
    }
    if page_count == 0 {
        bail!("PDF contains no pages.");
    }
    if let Some(attachment) = attached_csv(&pdf)? {
        csv_rows(attachment.as_slice(), &mut |mut row| {
            if let Ok(StatementRow::Transaction(payload)) = &mut row.row {
                payload["extraction"] = json!({
                    "provider": "embedded_csv", "attachment": ATTACHMENT, "verification": "canonical_validation_only"
                });
                payload["evidence"] = json!({
                    "attachment": ATTACHMENT, "row": row.index,
                    "note": "Parsed embedded export; visible PDF text was not independently reconciled."
                });
            }
            emit(row)
        })?;
        return Ok(None);
    }
    let mut pages = Vec::with_capacity(page_count);
    let mut text_size = 0;
    for page in 1..=page_count as u32 {
        let text = match pdf.extract_text_with_limit(&[page], PAGE_TEXT_LIMIT) {
            Ok(text) => text,
            Err(PdfError::Decompress(DecompressError::MemoryLimitExceeded { .. })) => {
                bail!("PDF page exceeds the content size limit. Split the statement.");
            }
            Err(_) => String::new(),
        };
        text_size += text.len();
        if text.len() > PAGE_TEXT_LIMIT || text_size > DOCUMENT_TEXT_LIMIT {
            bail!("Extracted PDF text exceeds the size limit. Split the statement.");
        }
        pages.push(text);
    }
    if pages
        .first()
        .and_then(|page| page.lines().next())
        .is_some_and(|first| {
            csv_reader(first.as_bytes())
                .headers()
                .is_ok_and(canonical_header)
        })
    {
        let text = pages.join("\n");
        csv_rows(text.as_bytes(), emit)?;
        return Ok(None);
    }
    let sha256 = hash(&bytes);
    Ok(Some(Flexible {
        bytes: bytes.into(),
        pdf: Arc::new(pdf),
        pages,
        sha256,
    }))
}

fn attached_csv(pdf: &Pdf) -> Result<Option<Vec<u8>>> {
    let catalog = pdf
        .catalog()
        .map_err(|_| anyhow::anyhow!("Invalid PDF catalog."))?;
    let Ok(names) = catalog.get_deref(b"Names", pdf) else {
        return Ok(None);
    };
    let Ok(tree) = names.as_dict()?.get(b"EmbeddedFiles") else {
        return Ok(None);
    };
    let mut visit = vec![tree];
    let mut visited = HashSet::new();
    let mut export = None;
    while let Some(object) = visit.pop() {
        if let Ok(reference) = object.as_reference()
            && !visited.insert(reference)
        {
            bail!("PDF attachment name tree contains repeated references.");
        }
        if visited.len() > 4096 || visit.len() > 4096 {
            bail!("PDF attachment name tree is too large.");
        }
        let node = pdf.dereference(object)?.1.as_dict()?;
        if let Ok(children) = node.get_deref(b"Kids", pdf) {
            visit.extend(children.as_array()?);
        }
        let Ok(names) = node.get_deref(b"Names", pdf) else {
            continue;
        };
        let (pairs, remainder) = names.as_array()?.as_chunks::<2>();
        if !remainder.is_empty() {
            bail!("Invalid PDF attachment name tree.");
        }
        for pair in pairs {
            let name = pdf.dereference(&pair[0])?.1.as_str()?;
            let utf16 = name.strip_prefix(b"\xfe\xff");
            if name != ATTACHMENT.as_bytes()
                && !utf16.is_some_and(|name| {
                    name.iter()
                        .copied()
                        .eq(ATTACHMENT.encode_utf16().flat_map(u16::to_be_bytes))
                })
            {
                continue;
            }
            if export.is_some() {
                bail!("Expected exactly one dolos-transactions.csv attachment.");
            }
            let specification = pdf.dereference(&pair[1])?.1.as_dict()?;
            let files = specification.get_deref(b"EF", pdf)?.as_dict()?;
            let stream = files
                .get_deref(b"UF", pdf)
                .or_else(|_| files.get_deref(b"F", pdf))?
                .as_stream()?;
            let bytes = if stream.dict.has(b"Filter") {
                stream
                    .decompressed_content_with_limit(MAX_FILE_BYTES)
                    .map_err(|_| {
                        anyhow::anyhow!("Could not read the embedded CSV within the 25 MiB limit.")
                    })?
            } else {
                if stream.content.len() > MAX_FILE_BYTES {
                    bail!("Embedded transaction export exceeds 25 MiB.");
                }
                stream.content.clone()
            };
            export = Some(bytes);
        }
    }
    Ok(export)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Provider {
    OpenRouter,
    OpenAi,
}

impl Provider {
    fn name(self) -> &'static str {
        match self {
            Self::OpenRouter => "openrouter",
            Self::OpenAi => "openai",
        }
    }

    fn endpoint(self) -> &'static str {
        match self {
            Self::OpenRouter => "https://openrouter.ai/api/v1/responses",
            Self::OpenAi => "https://api.openai.com/v1/responses",
        }
    }
}

struct Extractor<'a> {
    client: &'a reqwest::Client,
    provider: Provider,
    key: String,
    model: String,
    direct: bool,
}

impl<'a> Extractor<'a> {
    fn new(client: &'a reqwest::Client) -> Result<Self> {
        let (provider, key, default_model) = match env::var("DOLOS_PDF_PROVIDER").as_deref() {
            Ok("openrouter") | Err(env::VarError::NotPresent) => (
                Provider::OpenRouter,
                "OPENROUTER_API_KEY",
                "openrouter/free",
            ),
            Ok("openai") => (Provider::OpenAi, "OPENAI_API_KEY", "gpt-4o-mini"),
            _ => bail!("DOLOS_PDF_PROVIDER must be openrouter or openai."),
        };
        let direct = match env::var("DOLOS_PDF_MODE").as_deref() {
            Ok("text") | Err(env::VarError::NotPresent) => false,
            Ok("direct") => true,
            _ => bail!("DOLOS_PDF_MODE must be direct or text."),
        };
        Ok(Self {
            client,
            provider,
            key: env::var(key)
                .ok()
                .filter(|key| !key.is_empty())
                .with_context(|| format!("Flexible PDF extraction requires {key} on the API."))?,
            model: env::var("DOLOS_PDF_MODEL")
                .ok()
                .filter(|model| !model.is_empty())
                .unwrap_or_else(|| default_model.into()),
            direct,
        })
    }

    async fn request(
        &self,
        endpoint: &str,
        content: Vec<Value>,
        mut journal: Option<&mut Journal>,
        targets: std::ops::RangeInclusive<u32>,
        direct: bool,
    ) -> Result<(Extraction, String)> {
        let mut body = json!({
            "model": self.model, "store": false, "max_output_tokens": 16000,
            "input": [
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": content}
            ],
            "text": {"format": {
                "type": "json_schema", "name": "Extraction", "schema": schema(), "strict": true
            }}
        });
        if self.provider == Provider::OpenRouter {
            body["provider"] = json!({"sort": "price", "require_parameters": true});
        }

        for attempt in 0..2 {
            if let Some(journal) = &journal {
                journal.ensure_capacity(MAX_PROVIDER_RESPONSE)?;
            }
            let response = self
                .client
                .post(endpoint)
                .bearer_auth(&self.key)
                .timeout(Duration::from_secs(180))
                .json(&body)
                .send()
                .await
                .map_err(|_| {
                    anyhow::anyhow!("PDF extraction provider did not respond. Retry the document.")
                })?;
            let status = response.status().as_u16();
            let bytes = bounded_response(response).await?;
            if let Some(journal) = journal.as_deref_mut() {
                journal
                    .record(
                        self.provider.name(),
                        &self.model,
                        if direct { "direct" } else { "text" },
                        &targets,
                        status,
                        &bytes,
                    )
                    .await?;
            }
            match status {
                200..=299 => {}
                429 => bail!("PDF extraction rate or quota limit reached. Retry later."),
                402 => bail!("PDF extraction provider requires credits."),
                status @ (401 | 403) => bail!(
                    "PDF extraction access denied (HTTP {status}). Check the API provider key and model access."
                ),
                status @ (400 | 404 | 422) => bail!(
                    "PDF extraction request rejected (HTTP {status}). Check the configured model endpoint supports structured output and the supplied input type."
                ),
                status => {
                    bail!("PDF extraction provider failed (HTTP {status}). Retry the document.")
                }
            }
            let response: Value = serde_json::from_slice(&bytes)
                .map_err(|_| anyhow::anyhow!("PDF extraction provider returned invalid JSON."))?;
            if response["status"] != "completed" {
                bail!("PDF extraction was refused or incomplete. No partial graph was committed.");
            }
            let parts = response["output"]
                .as_array()
                .into_iter()
                .flatten()
                .flat_map(|item| item["content"].as_array().into_iter().flatten());
            let mut output = String::new();
            for part in parts {
                if part["type"] == "refusal" {
                    bail!("PDF extraction was refused. No partial graph was committed.");
                }
                if part["type"] == "output_text" {
                    output.push_str(part["text"].as_str().unwrap_or(""));
                }
            }
            if output.is_empty() {
                bail!("PDF extraction returned no output. No partial graph was committed.");
            }
            if let Ok(result) = serde_json::from_str::<Extraction>(&output) {
                if !result.complete {
                    bail!(
                        "PDF extraction could not read all target transactions. No partial graph was committed."
                    );
                }
                let model = response["model"].as_str().unwrap_or(&self.model).to_owned();
                return Ok((result, model));
            }
            if attempt == 0 {
                body["input"][0]["content"] = json!(format!(
                    "{INSTRUCTIONS}\nThe previous response failed schema validation. Extract all target transfers again and follow the exact schema. Use null for absent optional facts."
                ));
            }
        }
        bail!(
            "PDF extraction returned invalid structured data after two attempts. No partial graph was committed."
        )
    }
}

async fn bounded_response(mut response: reqwest::Response) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| anyhow::anyhow!("Could not read PDF extraction response."))?
    {
        if chunk.len() > MAX_PROVIDER_RESPONSE - bytes.len() {
            bail!("PDF extraction response exceeds the size limit.");
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

pub(super) async fn extract<T, F>(
    document: Flexible,
    client: &reqwest::Client,
    guard: Option<Arc<OwnedSemaphorePermit>>,
    mut journal: Option<Journal>,
    mut accumulator: T,
    mut append: F,
) -> Result<T>
where
    T: Send + 'static,
    F: FnMut(&mut T, ParsedRow) -> Result<()> + Send + 'static,
{
    let extractor = Extractor::new(client)?;
    let workspace = tempfile::tempdir()?;
    let mut pages = PageMaterial::new(&document, workspace.path());
    let mut count = 0;
    let total = document.pages.len() as u32;
    for start in (1..=total).step_by(CHUNK_PAGES as usize) {
        let end = (start + CHUNK_PAGES - 1).min(total);
        let mut selected = vec![1, start.saturating_sub(1).max(1), (end + 1).min(total)];
        selected.extend(start..=end);
        selected.sort_unstable();
        selected.dedup();
        let targets: Vec<_> = (start..=end).collect();
        let mut content = vec![json!({
            "type": "input_text",
            "text": format!("Original pages in attachment order: {selected:?}. TARGET original pages: {targets:?}.")
        })];
        let direct = extractor.direct
            || selected
                .iter()
                .any(|page| document.pages[*page as usize - 1].trim().chars().count() < 40);
        if direct && extractor.provider == Provider::OpenAi {
            let attachment = pages.pdf_chunk(&selected, guard.clone()).await?;
            content.push(json!({"type": "input_file", "filename": "statement-pages.pdf", "file_data": attachment}));
        } else {
            for page in selected {
                let text = format!("ORIGINAL PAGE {page}");
                if direct {
                    let image = pages.image(page).await?;
                    content.push(json!({"type": "input_text", "text": text}));
                    content.push(json!({"type": "input_image", "image_url": image}));
                } else {
                    let text = format!("{text}\n{}", document.pages[page as usize - 1]);
                    content.push(json!({"type": "input_text", "text": text}));
                }
            }
        }
        let (result, resolved_model) = extractor
            .request(
                extractor.provider.endpoint(),
                content,
                journal.as_mut(),
                start..=end,
                direct,
            )
            .await?;
        let source = ExtractionSource {
            provider: extractor.provider,
            model: extractor.model.clone(),
            resolved_model,
            direct,
        };
        let sha256 = document.sha256.clone();
        let guard = guard.clone();
        (accumulator, append, count) = tokio::task::spawn_blocking(move || -> Result<_> {
            let _guard = guard;
            for transfer in result.transfers {
                if !(start..=end).contains(&transfer.page) {
                    bail!("Extractor returned a transfer outside its target pages. Retry or review the document.");
                }
                count += 1;
                let row = normalize_transfer(transfer, &sha256, &source).map(StatementRow::Relation);
                append(&mut accumulator, ParsedRow { index: count, row })?;
            }
            for warning in result.warnings {
                count += 1;
                let warning = warning.chars().take(1000).collect::<String>();
                append(&mut accumulator, ParsedRow {
                    index: count,
                    row: Err(format!("PDF extraction warning: {warning}")),
                })?;
            }
            Ok((accumulator, append, count))
        }).await??;
    }
    if count == 0 {
        bail!("No transfer relations were identified in the PDF.");
    }
    Ok(accumulator)
}

struct PageMaterial<'a> {
    document: &'a Flexible,
    directory: &'a Path,
    source: Option<std::path::PathBuf>,
    images: HashSet<u32>,
}

impl<'a> PageMaterial<'a> {
    fn new(document: &'a Flexible, directory: &'a Path) -> Self {
        Self {
            document,
            directory,
            source: None,
            images: HashSet::new(),
        }
    }

    async fn image(&mut self, page: u32) -> Result<String> {
        if self.source.is_none() {
            let path = self.directory.join("statement.pdf");
            tokio::fs::write(&path, &self.document.bytes).await?;
            self.source = Some(path);
        }
        let prefix = self.directory.join(format!("page-{page}"));
        if let Some(source) = self
            .source
            .as_ref()
            .filter(|_| !self.images.contains(&page))
        {
            let page_number = page.to_string();
            let command = tokio::process::Command::new("pdftoppm")
                .args([
                    "-f",
                    &page_number,
                    "-l",
                    &page_number,
                    "-scale-to",
                    "1600",
                    "-singlefile",
                    "-png",
                ])
                .arg(source)
                .arg(&prefix)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .kill_on_drop(true)
                .status();
            let status = tokio::time::timeout(Duration::from_secs(30), command)
                .await
                .context("PDF page rendering timed out.")?
                .context("Install Poppler pdftoppm on the API host to extract scanned PDFs.")?;
            if !status.success() {
                bail!("Could not render PDF page {page}.");
            }
            self.images.insert(page);
        }
        let path = prefix.with_extension("png");
        if tokio::fs::metadata(&path).await?.len() > PAGE_IMAGE_LIMIT as u64 {
            bail!("Rendered PDF page exceeds the image size limit.");
        }
        Ok(format!(
            "data:image/png;base64,{}",
            STANDARD.encode(tokio::fs::read(path).await?)
        ))
    }

    async fn pdf_chunk(
        &self,
        selected: &[u32],
        guard: Option<Arc<OwnedSemaphorePermit>>,
    ) -> Result<String> {
        let original = self.document.pdf.clone();
        let selected = selected.to_owned();
        tokio::task::spawn_blocking(move || {
            let _guard = guard;
            let mut pdf = original.as_ref().clone();
            let pages = pdf.new_object_id();
            let page_ids = original.get_pages();
            let mut children = Vec::with_capacity(selected.len());
            for number in selected {
                let original_page = *page_ids.get(&number).context("PDF page is missing.")?;
                let page = copy_page(&original, original_page, pages)?;
                pdf.objects.insert(original_page, Object::Dictionary(page));
                children.push(Object::Reference(original_page));
            }
            let count = children.len() as i64;
            pdf.objects.insert(
                pages,
                Object::Dictionary(dictionary! {
                    "Type" => "Pages", "Kids" => children, "Count" => count,
                }),
            );
            let catalog = pdf.add_object(dictionary! { "Type" => "Catalog", "Pages" => pages });
            pdf.trailer = dictionary! { "Root" => catalog };
            pdf.prune_objects();
            let mut bytes = Vec::new();
            pdf.save_to(&mut bytes)?;
            if bytes.len() > MAX_FILE_BYTES {
                bail!("Prepared PDF page chunk exceeds the upload size limit.");
            }
            Ok(format!(
                "data:application/pdf;base64,{}",
                STANDARD.encode(bytes)
            ))
        })
        .await?
    }
}

// Flatten the inherited page attributes and copy only content needed for rendering.
// The resulting chunk has no shared page tree, annotations or document attachments.
fn copy_page(pdf: &Pdf, source: ObjectId, parent: ObjectId) -> Result<Dictionary> {
    let root = pdf.get_dictionary(source)?;
    let mut copy = dictionary! { "Type" => "Page", "Parent" => parent };
    for key in [b"Contents".as_slice(), b"Group", b"UserUnit"] {
        if let Ok(value) = root.get(key) {
            copy.set(key, value.clone());
        }
    }
    let mut seen = HashSet::new();
    let mut current = root;
    loop {
        for key in [b"MediaBox".as_slice(), b"CropBox", b"Resources", b"Rotate"] {
            if !copy.has(key)
                && let Ok(value) = current.get(key)
            {
                copy.set(key, value.clone());
            }
        }
        let Ok(id) = current.get(b"Parent").and_then(Object::as_reference) else {
            return Ok(copy);
        };
        if !seen.insert(id) || seen.len() > 64 {
            bail!("PDF page tree contains invalid inheritance.");
        }
        current = pdf.get_dictionary(id)?;
    }
}

fn schema() -> Value {
    let nullable_string = json!({"type": ["string", "null"]});
    let party = json!({
        "type": "object", "additionalProperties": false,
        "properties": {
            "name": nullable_string, "bank": nullable_string, "account": nullable_string,
            "kind": {"type": "string", "enum": ["person", "organization", "unknown"]}
        },
        "required": ["name", "bank", "account", "kind"]
    });
    let transfer = json!({
        "type": "object", "additionalProperties": false,
        "properties": {
            "sender": party, "receiver": party,
            "timestamp": nullable_string, "amount": nullable_string, "currency": nullable_string,
            "description": {"type": "string"}, "evidence": {"type": "string"},
            "page": {"type": "integer"}, "reference": nullable_string,
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "review_reason": nullable_string
        },
        "required": ["sender", "receiver", "timestamp", "amount", "currency", "description",
            "evidence", "page", "reference", "confidence", "review_reason"]
    });
    json!({
        "type": "object", "additionalProperties": false,
        "properties": {
            "transfers": {"type": "array", "items": transfer},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "complete": {"type": "boolean"}
        },
        "required": ["transfers", "warnings", "complete"]
    })
}

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{Json, Router, routing::post};
    use lopdf::Stream;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn attached_document(duplicate: bool) -> Vec<u8> {
        let mut pdf = Pdf::with_version("1.5");
        let pages = pdf.new_object_id();
        let page = pdf.add_object(dictionary! {
            "Type" => "Page", "Parent" => pages,
            "MediaBox" => vec![0.into(), 0.into(), 612.into(), 792.into()],
        });
        pdf.objects.insert(
            pages,
            Object::Dictionary(dictionary! {
                "Type" => "Pages", "Count" => 1, "Kids" => vec![Object::Reference(page)],
            }),
        );
        let export = include_bytes!("../../../examples/statement.csv").to_vec();
        let stream = pdf.add_object(Stream::new(
            dictionary! { "Type" => "EmbeddedFile" },
            export,
        ));
        let file = pdf.add_object(
            dictionary! { "Type" => "Filespec", "EF" => dictionary! { "F" => stream } },
        );
        let mut names = vec![Object::string_literal(ATTACHMENT), Object::Reference(file)];
        if duplicate {
            names.extend(names.clone());
        }
        let tree = pdf.add_object(dictionary! { "Names" => names });
        let catalog = pdf.add_object(dictionary! {
            "Type" => "Catalog", "Pages" => pages,
            "Names" => dictionary! { "EmbeddedFiles" => dictionary! { "Kids" => vec![Object::Reference(tree)] } },
        });
        pdf.trailer.set("Root", catalog);
        let mut bytes = Vec::new();
        pdf.save_to(&mut bytes).unwrap();
        bytes
    }

    #[test]
    fn embedded_export_uses_canonical_rows_and_preserves_provenance() {
        let mut rows = Vec::new();
        let flexible = inspect(attached_document(false), &mut |row| {
            rows.push(row);
            Ok(())
        })
        .unwrap();
        assert!(
            flexible.is_none(),
            "embedded export must bypass flexible extraction"
        );
        assert_eq!(rows.len(), 3);
        let row = rows.into_iter().next().unwrap().row.unwrap().into_payload();
        assert_eq!(row["from_account"], "000123");
        assert_eq!(row["evidence"]["attachment"], ATTACHMENT);
        assert_eq!(
            row["extraction"]["verification"],
            "canonical_validation_only"
        );
        let Err(error) = inspect(attached_document(true), &mut |_| Ok(())) else {
            panic!("ambiguous embedded exports must fail");
        };
        assert!(error.to_string().contains("exactly one"));
    }

    #[tokio::test]
    async fn direct_pdf_payload_rebuilds_selected_pages_without_attachments() {
        let bytes = attached_document(false);
        let flexible = Flexible {
            pdf: Arc::new(Pdf::load_mem(&bytes).unwrap()),
            sha256: hash(&bytes),
            bytes: bytes.into(),
            pages: vec![String::new()],
        };
        let dir = tempfile::tempdir().unwrap();
        let material = PageMaterial::new(&flexible, dir.path());
        let data = material.pdf_chunk(&[1], None).await.unwrap();
        let encoded = data.strip_prefix("data:application/pdf;base64,").unwrap();
        let chunk = Pdf::load_mem(&STANDARD.decode(encoded).unwrap()).unwrap();
        assert_eq!(chunk.page_iter().count(), 1);
        assert!(attached_csv(&chunk).unwrap().is_none());
    }

    #[tokio::test]
    async fn provider_retries_schema_validation_with_same_model_and_content() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!("http://{}/responses", listener.local_addr().unwrap());
        let calls = Arc::new(AtomicUsize::new(0));
        let requests = calls.clone();
        let route = post(move |Json(payload): Json<Value>| {
            let requests = requests.clone();
            async move {
                let call = requests.fetch_add(1, Ordering::Relaxed);
                assert_eq!(payload["model"], "openrouter/free");
                assert_eq!(payload["store"], false);
                assert_eq!(
                    payload["provider"],
                    json!({"sort": "price", "require_parameters": true})
                );
                assert_eq!(payload["text"]["format"]["strict"], true);
                assert!(payload.get("plugins").is_none());
                assert_eq!(
                    payload["input"][1]["content"][0]["text"],
                    "Original statement text"
                );
                let prompt = payload["input"][0]["content"].as_str().unwrap();
                assert_eq!(
                    prompt.contains("previous response failed schema validation"),
                    call == 1
                );
                let output = if call == 0 {
                    json!({"transfers": [], "warnings": []})
                } else {
                    json!({"transfers": [], "warnings": [], "complete": true})
                };
                Json(json!({
                    "status": "completed", "model": "account/model:free",
                    "output": [{"content": [{"type": "output_text", "text": output.to_string()}]}]
                }))
            }
        });
        let server = tokio::spawn(async move {
            axum::serve(listener, Router::new().route("/responses", route))
                .await
                .unwrap();
        });
        let client = reqwest::Client::builder().no_proxy().build().unwrap();
        let extractor = Extractor {
            client: &client,
            provider: Provider::OpenRouter,
            key: "mock".into(),
            model: "openrouter/free".into(),
            direct: false,
        };
        let content = vec![json!({"type": "input_text", "text": "Original statement text"})];
        let scratch = tempfile::tempdir().unwrap();
        let (commands, _) = crate::db::Store::open_url(
            scratch
                .path()
                .join("observations.db")
                .to_string_lossy()
                .into_owned(),
            String::new(),
        )
        .await
        .unwrap()
        .split();
        let mut journal = Journal::new(commands.clone(), "source-digest".into());
        let (result, resolved_model) = extractor
            .request(&endpoint, content, Some(&mut journal), 1..=1, false)
            .await
            .unwrap();
        let conn = commands.conn().unwrap();
        let mut rows = conn.query(
            "SELECT sequence, sha256, response FROM extraction_observations WHERE run_id=? ORDER BY sequence",
            [journal.run_id],
        ).await.unwrap();
        for sequence in 1..=2 {
            let row = rows.next().await.unwrap().unwrap();
            assert_eq!(row.get::<i64>(0).unwrap(), sequence);
            assert_eq!(row.get::<String>(1).unwrap(), "source-digest");
            let bytes: Vec<u8> = row.get(2).unwrap();
            let envelope: Value = serde_json::from_slice(&bytes).unwrap();
            let raw = envelope["output"][0]["content"][0]["text"]
                .as_str()
                .unwrap();
            let original: Value = serde_json::from_str(raw).unwrap();
            assert_eq!(original.get("complete").is_some(), sequence == 2);
        }
        assert!(rows.next().await.unwrap().is_none());
        server.abort();
        assert_eq!(calls.load(Ordering::Relaxed), 2);
        assert!(result.complete);
        assert_eq!(resolved_model, "account/model:free");
    }
}
