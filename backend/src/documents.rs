use crate::{
    Admission, ApiError, App, Reply, ValidJson,
    db::{Queries, now},
    storage::{self, BATCH_LIMIT, Document, FILE_LIMIT, InvalidUpload, Location, MissingUpload},
};
use anyhow::{Context, Result};
use axum::{
    Json,
    extract::{Multipart, Path, State, multipart::MultipartRejection},
    http::StatusCode,
};
use libsql::{TransactionBehavior, params};
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{collections::HashSet, time::Duration};
use tempfile::TempDir;
use tokio::{fs, io::AsyncWriteExt, time::Instant};

mod compiler;
mod projection;

use compiler::{Compilation, compile};
use projection::{commit_outcome, record_source};

const PARSE_BUDGET: Duration = Duration::from_secs(85);

fn unsupported() -> ApiError {
    ApiError::new(
        StatusCode::UNSUPPORTED_MEDIA_TYPE,
        "Supported file extensions: .csv, .json, .pdf",
    )
}

fn size_limit() -> ApiError {
    ApiError::new(
        StatusCode::PAYLOAD_TOO_LARGE,
        "Limit: 25 MiB per file, 100 MiB per batch.",
    )
}

fn processing_timeout() -> ApiError {
    ApiError::new(
        StatusCode::GATEWAY_TIMEOUT,
        "The request time limit expired. Retry with a smaller batch.",
    )
}

fn storage_error(error: anyhow::Error) -> ApiError {
    if error.is::<InvalidUpload>() {
        return ApiError::invalid(error.to_string());
    }
    if error.is::<MissingUpload>() {
        return ApiError::new(StatusCode::CONFLICT, error.to_string());
    }
    ApiError::unavailable(error, "Document storage is unavailable.")
}

pub async fn upload(
    State(app): State<App>,
    multipart: Result<Multipart, MultipartRejection>,
) -> Reply {
    let permit = app.admit()?;
    let mut multipart =
        multipart.map_err(|error| ApiError::new(error.status(), "Expected a multipart upload."))?;
    let deadline = Instant::now() + PARSE_BUDGET;
    let scratch = TempDir::new()?;
    let mut staged = Vec::new();
    let mut batch_size = 0;
    while let Some(mut field) = multipart.next_field().await.map_err(ApiError::multipart)? {
        let raw_name = field
            .file_name()
            .filter(|_| field.name() == Some("files"))
            .ok_or_else(|| ApiError::invalid("Expected a file in the files field."))?;
        if staged.len() == 100 {
            return Err(ApiError::new(
                StatusCode::PAYLOAD_TOO_LARGE,
                "Upload 1–100 documents per batch.",
            ));
        }
        let filename = storage::filename(raw_name);
        let suffix = storage::suffix(&filename).ok_or_else(unsupported)?;
        let path = scratch.path().join(staged.len().to_string());
        let mut output = fs::File::create(&path).await?;
        let mut hash = Sha256::new();
        let mut size = 0;
        while let Some(chunk) = field.chunk().await.map_err(ApiError::multipart)? {
            size += chunk.len() as i64;
            batch_size += chunk.len() as i64;
            if size > FILE_LIMIT || batch_size > BATCH_LIMIT {
                return Err(size_limit());
            }
            hash.update(&chunk);
            output.write_all(&chunk).await?;
        }
        if size == 0 {
            return Err(ApiError::invalid("Empty documents are not accepted."));
        }
        output.flush().await?;
        staged.push((
            None,
            Document {
                filename,
                suffix,
                sha256: format!("{:x}", hash.finalize()),
                path,
                size,
            },
        ));
    }
    if staged.is_empty() {
        return Err(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "Upload 1–100 documents per batch.",
        ));
    }
    ingest_batch(&app, &storage::id(), staged, false, deadline, &permit).await
}

#[derive(Deserialize)]
pub struct PresignRequest {
    files: Vec<UploadSpec>,
}
#[derive(Deserialize)]
struct UploadSpec {
    filename: String,
    size: i64,
    sha256: String,
}

pub async fn presign(
    State(app): State<App>,
    ValidJson(request): ValidJson<PresignRequest>,
) -> Reply {
    if !(1..=100).contains(&request.files.len())
        || request.files.iter().any(|file| {
            !(1..=255).contains(&file.filename.chars().count())
                || !(1..=FILE_LIMIT).contains(&file.size)
                || file.sha256.len() != 64
                || !file
                    .sha256
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        })
    {
        return Err(ApiError::invalid(
            "Invalid upload filename, size, count, or SHA-256.",
        ));
    }
    if request.files.iter().map(|f| f.size).sum::<i64>() > BATCH_LIMIT {
        return Err(size_limit());
    }
    let r2 = app.storage.direct().ok_or_else(|| {
        ApiError::new(
            StatusCode::CONFLICT,
            "Direct uploads require DOLOS_STORAGE_BACKEND=r2.",
        )
    })?;
    let batch = storage::id();
    let mut signed = Vec::new();
    for file in &request.files {
        let suffix = storage::suffix(&file.filename).ok_or_else(unsupported)?;
        let (key, url, expires) = r2
            .presign(&file.sha256, &suffix, file.size)
            .await
            .map_err(storage_error)?;
        signed.push((file, suffix, storage::id(), key, url, expires));
    }
    let conn = app.commands.conn()?;
    let tx = conn
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .await?;
    tx.execute(
        "INSERT INTO batches VALUES(?,?)",
        params![batch.clone(), now()],
    )
    .await?;
    let mut uploads = Vec::new();
    for (file, suffix, id, key, url, expires) in signed {
        tx.execute("INSERT INTO upload_sessions(id,batch_id,filename,suffix,sha256,byte_size,bucket,object_key,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
            params![id.clone(),batch.clone(),storage::filename(&file.filename),suffix.clone(),file.sha256.clone(),file.size,r2.bucket.clone(),key,expires]).await?;
        uploads.push(
            json!({"id":id,"filename":file.filename,"method":"PUT","url":url,
            "headers":{"Content-Type":storage::content_type(&suffix)},"expires_at":expires}),
        );
    }
    tx.commit().await?;
    Ok((
        StatusCode::CREATED,
        Json(
            json!({"batch_id":batch,"status":"awaiting_upload","uploads":uploads,
        "complete_url":format!("/batches/{batch}/complete")}),
        ),
    ))
}

pub async fn complete(State(app): State<App>, Path(batch): Path<String>) -> Reply {
    let permit = app.admit()?;
    let deadline = Instant::now() + PARSE_BUDGET;
    let scratch = TempDir::new()?;
    let conn = app.commands.conn()?;
    let mut rows = conn.query("SELECT id,filename,suffix,sha256,byte_size,bucket,object_key,expires_at,status FROM upload_sessions WHERE batch_id=? ORDER BY id", [batch.as_str()]).await?;
    let mut staged = Vec::new();
    let mut locations = Vec::new();
    let mut keys = Vec::new();
    let mut found = false;
    while let Some(row) = rows.next().await? {
        found = true;
        if row.get::<String>(8)? == "completed" {
            continue;
        }
        if row.get::<f64>(7)? < now() {
            return Err(ApiError::new(
                StatusCode::GONE,
                "Upload session expired; request new upload URLs.",
            ));
        }
        let loc = Location {
            backend: "r2".into(),
            bucket: Some(row.get(5)?),
            key: row.get(6)?,
            size: row.get(4)?,
        };
        staged.push((
            Some(row.get::<String>(0)?),
            Document {
                filename: row.get(1)?,
                suffix: row.get(2)?,
                sha256: row.get(3)?,
                size: loc.size,
                path: scratch.path().join(staged.len().to_string()),
            },
        ));
        keys.push(loc.key.clone());
        locations.push(loc);
    }
    drop(rows);
    if !found {
        return Err(ApiError::not_found("Direct-upload batch not found"));
    }
    if staged.is_empty() {
        return batch_result(&app.queries, &batch, StatusCode::ACCEPTED).await;
    }
    for ((_, document), loc) in staged.iter().zip(locations) {
        if let Err(error) = tokio::time::timeout_at(
            deadline,
            app.storage
                .materialize(&loc, &document.path, &document.sha256),
        )
        .await
        .map_err(|_| processing_timeout())?
        {
            // A concurrent completion may already have deleted these staging objects.
            let status = app.queries.batch(&batch).await?;
            if status
                .as_ref()
                .is_some_and(|value| value["status"] != "awaiting_upload")
            {
                return batch_result(&app.queries, &batch, StatusCode::ACCEPTED).await;
            }
            return Err(storage_error(error));
        }
    }
    let result = ingest_batch(&app, &batch, staged, true, deadline, &permit).await?;
    if let Some(r2) = &app.storage.r2 {
        let cleanup = async {
            let prefix = format!("{}/incoming/", r2.prefix);
            for key in keys.into_iter().filter(|key| key.starts_with(&prefix)) {
                if r2
                    .client
                    .delete_object()
                    .bucket(&r2.bucket)
                    .key(key)
                    .send()
                    .await
                    .is_err()
                {
                    tracing::warn!("Could not clean up completed R2 upload staging");
                }
            }
        };
        let _ = tokio::time::timeout(Duration::from_secs(2), cleanup).await;
    }
    Ok(result)
}

async fn batch_result(db: &Queries, batch: &str, status: StatusCode) -> Reply {
    Ok((
        status,
        Json(
            db.batch(batch)
                .await?
                .ok_or_else(|| ApiError::not_found("Batch not found"))?,
        ),
    ))
}

pub async fn get_batch(State(app): State<App>, Path(batch): Path<String>) -> Reply {
    batch_result(&app.queries, &batch, StatusCode::OK).await
}

struct Prepared {
    location: Location,
    rows: Option<Compilation>,
}
async fn ingest_batch(
    app: &App,
    batch: &str,
    staged: Vec<(Option<String>, Document)>,
    direct: bool,
    deadline: Instant,
    permit: &Admission,
) -> Reply {
    let conn = app.commands.conn()?;
    let mut seen = HashSet::new();
    let mut prepared = Vec::new();
    let mut compile_flags = Vec::new();
    for (_, document) in &staged {
        if !seen.insert(&document.sha256) {
            prepared.push(None);
            compile_flags.push(false);
            continue;
        }
        let first = conn.query("SELECT d.status,o.backend FROM documents d LEFT JOIN document_objects o ON d.id=o.document_id WHERE sha256=?", [document.sha256.as_str()]).await?.next().await?;
        let needs_repair = app.storage.direct().is_some()
            && match &first {
                Some(row) => row.get::<Option<String>>(1)?.as_deref() != Some("r2"),
                None => false,
            };
        if first.is_some() && !needs_repair {
            prepared.push(None);
            compile_flags.push(false);
            continue;
        }
        let location = tokio::time::timeout_at(deadline, app.storage.save(document))
            .await
            .map_err(|_| processing_timeout())?
            .map_err(storage_error)?;
        let should_compile = match first {
            None => true,
            Some(row) => matches!(
                row.get::<String>(0)?.as_str(),
                "failed" | "queued" | "processing"
            ),
        };
        prepared.push(Some(Prepared {
            location,
            rows: None,
        }));
        compile_flags.push(should_compile);
    }
    for ((_, document), (target, should_compile)) in
        staged.iter().zip(prepared.iter_mut().zip(compile_flags))
    {
        if should_compile && let Some(target) = target {
            target.rows = Some(compile(&app.http, &app.commands, document, deadline, permit).await);
        }
    }

    let tx = conn
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .await?;
    if !direct {
        tx.execute("INSERT INTO batches VALUES(?,?)", params![batch, now()])
            .await?;
    }
    for ((session, document), prepared) in staged.into_iter().zip(prepared) {
        if let Some(session) = &session {
            let existing = tx
                .query(
                    "SELECT status FROM upload_sessions WHERE id=?",
                    [session.as_str()],
                )
                .await?
                .next()
                .await?
                .context("Upload session disappeared")?;
            if existing.get::<String>(0)? == "completed" {
                continue;
            }
        }
        let existing = tx
            .query(
                "SELECT id,status FROM documents WHERE sha256=?",
                [document.sha256.as_str()],
            )
            .await?
            .next()
            .await?;
        let id = if let Some(row) = existing {
            let id = row.get::<String>(0)?;
            if let Some(Prepared { location, rows }) = prepared {
                let mut apply = false;
                if location.backend == "r2" {
                    let changed = tx.execute("INSERT INTO document_objects VALUES(?,?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET backend=excluded.backend,bucket=excluded.bucket,object_key=excluded.object_key,byte_size=excluded.byte_size WHERE document_objects.backend='local'",
                        params![id.clone(),location.backend.clone(),location.bucket.clone(),location.key.clone(),location.size]).await?;
                    if changed > 0 {
                        record_source(&tx, &id, &document.sha256, &location).await?;
                        apply = matches!(
                            row.get::<String>(1)?.as_str(),
                            "failed" | "queued" | "processing"
                        );
                    }
                }
                if let Some(result) = rows {
                    commit_outcome(&tx, &id, result, apply).await?;
                }
            }
            id
        } else {
            let id = storage::id();
            let Prepared { location, rows } = prepared.context("Missing staged document")?;
            tx.execute("INSERT INTO documents(id,batch_id,filename,sha256,suffix,status) VALUES(?,?,?,?,?,'failed')",
                params![id.clone(),batch,document.filename,document.sha256.clone(),document.suffix]).await?;
            tx.execute(
                "INSERT INTO document_objects VALUES(?,?,?,?,?)",
                params![
                    id.clone(),
                    location.backend.clone(),
                    location.bucket.clone(),
                    location.key.clone(),
                    location.size
                ],
            )
            .await?;
            record_source(&tx, &id, &document.sha256, &location).await?;
            commit_outcome(&tx, &id, rows.context("Missing compiled statement")?, true).await?;
            id
        };
        tx.execute(
            "INSERT OR IGNORE INTO batch_documents VALUES(?,?)",
            params![batch, id.clone()],
        )
        .await?;
        if let Some(session) = &session {
            tx.execute(
                "UPDATE upload_sessions SET status='completed',document_id=? WHERE id=?",
                params![id, session.clone()],
            )
            .await?;
        }
    }
    tx.commit().await?;
    batch_result(&app.queries, batch, StatusCode::ACCEPTED).await
}

pub async fn retry(State(app): State<App>, Path(id): Path<String>) -> Reply {
    let permit = app.admit()?;
    let conn = app.commands.conn()?;
    let row = conn.query("SELECT d.status,d.attempts,d.filename,d.suffix,d.sha256,o.backend,o.bucket,o.object_key,o.byte_size FROM documents d LEFT JOIN document_objects o ON d.id=o.document_id WHERE d.id=?", [id.as_str()]).await?
        .next().await?.ok_or_else(|| ApiError::not_found("Document not found"))?;
    let conflict = || {
        ApiError::new(
            StatusCode::CONFLICT,
            "Only failed documents can be retried. Correct rejected rows in a new file.",
        )
    };
    if row.get::<String>(0)? != "failed" {
        return Err(conflict());
    }
    let attempts: i64 = row.get(1)?;
    let scratch = TempDir::new()?;
    let document = Document {
        filename: row.get(2)?,
        suffix: row.get(3)?,
        sha256: row.get(4)?,
        path: scratch.path().join("statement"),
        size: row.get::<Option<i64>>(8)?.unwrap_or_default(),
    };
    let loc = Location {
        backend: row
            .get::<Option<String>>(5)?
            .unwrap_or_else(|| "local".into()),
        bucket: row.get(6)?,
        key: row
            .get::<Option<String>>(7)?
            .unwrap_or_else(|| format!("{}{}", document.sha256, document.suffix)),
        size: document.size,
    };
    let deadline = Instant::now() + PARSE_BUDGET;
    let outcome = if tokio::time::timeout_at(
        deadline,
        app.storage
            .materialize(&loc, &document.path, &document.sha256),
    )
    .await
    .map_err(|_| processing_timeout())?
    .is_ok()
    {
        compile(&app.http, &app.commands, &document, deadline, &permit).await
    } else {
        Compilation::failed(
            "Could not retrieve the original document. Check upload storage before retrying.",
        )
    };
    let tx = conn
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .await?;
    if tx
        .query(
            "SELECT 1 FROM documents WHERE id=? AND status='failed' AND attempts=?",
            params![id.clone(), attempts],
        )
        .await?
        .next()
        .await?
        .is_none()
    {
        commit_outcome(&tx, &id, outcome, false).await?;
        tx.commit().await?;
        return Err(conflict());
    }
    let status = commit_outcome(&tx, &id, outcome, true).await?;
    tx.commit().await?;
    Ok((StatusCode::ACCEPTED, Json(json!({"id":id,"status":status}))))
}
