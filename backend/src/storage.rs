use crate::db::now;
use anyhow::{Context, Result, ensure};
use aws_sdk_s3::{
    Client,
    config::{BehaviorVersion, Credentials, Region},
    presigning::PresigningConfig,
    primitives::ByteStream,
};
use sha2::{Digest, Sha256};
use std::{
    env,
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::{fs, io::AsyncWriteExt};
use uuid::Uuid;

pub const FILE_LIMIT: i64 = 25 * 1024 * 1024;
pub const BATCH_LIMIT: i64 = 100 * 1024 * 1024;
pub const UPLOAD_EXPIRY: Duration = Duration::from_secs(900);

pub fn id() -> String {
    Uuid::new_v4().simple().to_string()
}

pub fn suffix(filename: &str) -> Option<String> {
    let suffix = format!(
        ".{}",
        Path::new(filename)
            .extension()?
            .to_str()?
            .to_ascii_lowercase()
    );
    content_type(&suffix)?;
    Some(suffix)
}

pub fn content_type(suffix: &str) -> Option<&'static str> {
    match suffix {
        ".pdf" => Some("application/pdf"),
        ".csv" => Some("text/csv"),
        ".json" => Some("application/json"),
        _ => None,
    }
}

pub fn filename(value: &str) -> String {
    value.rsplit('/').next().unwrap_or(value).to_owned()
}

pub struct Document {
    pub filename: String,
    pub suffix: String,
    pub sha256: String,
    pub path: PathBuf,
    pub size: i64,
}

#[derive(Clone)]
pub struct Location {
    pub backend: String,
    pub bucket: Option<String>,
    pub key: String,
    pub size: i64,
}

#[derive(Clone)]
pub struct R2 {
    pub client: Client,
    pub bucket: String,
    pub prefix: String,
}

#[derive(Clone)]
pub struct Storage {
    dir: PathBuf,
    pub r2: Option<R2>,
    remote_writes: bool,
}

impl Storage {
    pub async fn open() -> Result<Self> {
        let dir = PathBuf::from(env::var("DOLOS_DATA_DIR").unwrap_or_else(|_| "data".into()))
            .join("uploads");
        fs::create_dir_all(&dir).await?;
        let backend = env::var("DOLOS_STORAGE_BACKEND").unwrap_or_else(|_| "local".into());
        ensure!(
            matches!(backend.as_str(), "local" | "r2"),
            "Invalid DOLOS_STORAGE_BACKEND"
        );
        let remote_writes = backend == "r2";
        let r2 = if remote_writes
            || [
                "R2_ACCOUNT_ID",
                "R2_BUCKET_NAME",
                "R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY",
            ]
            .iter()
            .all(|name| env::var_os(name).is_some_and(|value| !value.is_empty()))
        {
            let account = env::var("R2_ACCOUNT_ID")?;
            ensure!(
                account.len() == 32 && account.bytes().all(|ch| ch.is_ascii_hexdigit()),
                "Invalid R2_ACCOUNT_ID"
            );
            let bucket = env::var("R2_BUCKET_NAME")?;
            let credentials = Credentials::new(
                env::var("R2_ACCESS_KEY_ID")?,
                env::var("R2_SECRET_ACCESS_KEY")?,
                None,
                None,
                "Dolos",
            );
            let prefix = env::var("R2_PREFIX")
                .unwrap_or_else(|_| "dolos".into())
                .trim_matches('/')
                .to_owned();
            ensure!(
                !prefix.is_empty()
                    && prefix.len() <= 100
                    && prefix
                        .bytes()
                        .all(|c| c.is_ascii_alphanumeric() || b"/_-".contains(&c)),
                "Invalid R2_PREFIX"
            );
            let config = aws_sdk_s3::Config::builder()
                .behavior_version(BehaviorVersion::latest())
                .region(Region::new("auto"))
                .endpoint_url(format!("https://{account}.r2.cloudflarestorage.com"))
                .credentials_provider(credentials)
                .force_path_style(true)
                .timeout_config(
                    aws_sdk_s3::config::timeout::TimeoutConfig::builder()
                        .connect_timeout(Duration::from_secs(5))
                        .operation_attempt_timeout(Duration::from_secs(12))
                        .operation_timeout(Duration::from_secs(20))
                        .build(),
                )
                .request_checksum_calculation(
                    aws_sdk_s3::config::RequestChecksumCalculation::WhenRequired,
                )
                .response_checksum_validation(
                    aws_sdk_s3::config::ResponseChecksumValidation::WhenRequired,
                )
                .build();
            Some(R2 {
                client: Client::from_conf(config),
                bucket,
                prefix,
            })
        } else {
            None
        };
        Ok(Self {
            dir,
            r2,
            remote_writes,
        })
    }

    pub fn direct(&self) -> Option<&R2> {
        if self.remote_writes {
            self.r2.as_ref()
        } else {
            None
        }
    }

    pub async fn save(&self, document: &Document) -> Result<Location> {
        if let Some(r2) = self.direct() {
            let key = format!(
                "{}/documents/{}{}",
                r2.prefix, document.sha256, document.suffix
            );
            let uploaded = r2
                .client
                .put_object()
                .bucket(&r2.bucket)
                .key(&key)
                .content_type(content_type(&document.suffix).context("Unsupported document type")?)
                .content_length(document.size)
                .if_none_match("*")
                .body(ByteStream::from_path(&document.path).await?)
                .send()
                .await;
            // An existing object at this digest is already the immutable source.
            if let Err(error) = uploaded
                && !error
                    .raw_response()
                    .is_some_and(|response| response.status().as_u16() == 412)
            {
                return Err(error.into());
            }
            return Ok(Location {
                backend: "r2".into(),
                bucket: Some(r2.bucket.clone()),
                key,
                size: document.size,
            });
        }
        let key = format!("{}{}", document.sha256, document.suffix);
        let destination = self.dir.join(&key);
        let temp = tempfile::NamedTempFile::new_in(&self.dir)?.into_temp_path();
        fs::copy(&document.path, &temp).await?;
        if let Err(error) = temp.persist_noclobber(&destination) {
            if error.error.kind() != std::io::ErrorKind::AlreadyExists {
                return Err(error.into());
            }
            let mut existing = fs::File::open(&destination).await?;
            ensure!(
                existing.metadata().await?.len() == document.size as u64,
                InvalidUpload
            );
            let mut hash = Sha256::new();
            let mut chunk = vec![0; 64 * 1024];
            use tokio::io::AsyncReadExt;
            loop {
                let count = existing.read(&mut chunk).await?;
                if count == 0 {
                    break;
                }
                hash.update(&chunk[..count]);
            }
            ensure!(
                format!("{:x}", hash.finalize()) == document.sha256,
                InvalidUpload
            );
        }
        Ok(Location {
            backend: "local".into(),
            bucket: None,
            key,
            size: document.size,
        })
    }

    pub async fn materialize(&self, loc: &Location, path: &Path, sha256: &str) -> Result<()> {
        ensure!(loc.size <= FILE_LIMIT, InvalidUpload);
        if loc.backend == "r2" {
            let r2 = self.r2.as_ref().context("R2 is not configured")?;
            ensure!(
                loc.bucket.as_deref() == Some(r2.bucket.as_str()),
                "Document requires its original R2 bucket"
            );
            let response = r2
                .client
                .get_object()
                .bucket(&r2.bucket)
                .key(&loc.key)
                .send()
                .await
                .map_err(|err| {
                    if err.as_service_error().is_some_and(|e| e.is_no_such_key()) {
                        anyhow::Error::from(MissingUpload)
                    } else {
                        err.into()
                    }
                })?;
            ensure!(
                response.content_length() == Some(loc.size) && loc.size > 0,
                InvalidUpload
            );
            let mut body = response.body;
            let mut output = fs::File::create(path).await?;
            let mut total = 0;
            let mut hash = Sha256::new();
            while let Some(chunk) = body.next().await {
                let bytes = chunk?;
                total += bytes.len() as i64;
                ensure!(total <= loc.size, InvalidUpload);
                hash.update(&bytes);
                output.write_all(&bytes).await?;
            }
            ensure!(
                total == loc.size && format!("{:x}", hash.finalize()) == sha256,
                InvalidUpload
            );
            return Ok(());
        }
        ensure!(
            loc.backend == "local"
                && !loc.key.is_empty()
                && loc.key != "."
                && loc.key != ".."
                && filename(&loc.key) == loc.key,
            "Invalid local storage key"
        );
        let original = self.dir.join(&loc.key);
        let size = fs::metadata(&original).await?.len() as i64;
        ensure!(
            size > 0 && size <= FILE_LIMIT && (loc.size == 0 || size == loc.size),
            InvalidUpload
        );
        let bytes = fs::read(original).await?;
        ensure!(
            format!("{:x}", Sha256::digest(&bytes)) == sha256,
            InvalidUpload
        );
        fs::write(path, bytes).await?;
        Ok(())
    }
}

impl R2 {
    pub async fn presign(
        &self,
        sha256: &str,
        suffix: &str,
        size: i64,
    ) -> Result<(String, String, f64)> {
        let key = format!("{}/incoming/{}/{}{}", self.prefix, id(), sha256, suffix);
        let request = self
            .client
            .put_object()
            .bucket(&self.bucket)
            .key(&key)
            .content_type(content_type(suffix).context("Unsupported document type")?)
            .content_length(size)
            .presigned(PresigningConfig::expires_in(UPLOAD_EXPIRY)?)
            .await?;
        Ok((
            key,
            request.uri().to_string(),
            now() + UPLOAD_EXPIRY.as_secs_f64(),
        ))
    }
}

#[derive(Debug)]
pub struct InvalidUpload;
impl std::fmt::Display for InvalidUpload {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Uploaded file size or SHA-256 does not match the declared values.")
    }
}
impl std::error::Error for InvalidUpload {}

#[derive(Debug)]
pub struct MissingUpload;
impl std::fmt::Display for MissingUpload {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Upload is not present in R2 yet.")
    }
}
impl std::error::Error for MissingUpload {}
