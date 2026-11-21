use anyhow::{Context, Result, ensure};
use libsql::{Builder, Connection, Database, TransactionBehavior, Value as Sql, params};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    env,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Clone)]
pub struct Store(std::sync::Arc<Database>);

/// SQL write capability, used only by document commands.
#[derive(Clone)]
pub struct Commands(Store);

/// Read-side access to materialized projections.
#[derive(Clone)]
pub struct Queries(Store);

impl Commands {
    pub fn conn(&self) -> Result<Connection> {
        self.0.conn()
    }
}

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

impl Store {
    pub async fn open() -> Result<Self> {
        let url = env::var("DOLOS_DATABASE_URL")
            .or_else(|_| env::var("TURSO_DATABASE_URL"))
            .unwrap_or_else(|_| "file:./var/dolos.sqlite3".to_owned());
        Self::open_url(url, env::var("TURSO_AUTH_TOKEN").unwrap_or_default()).await
    }

    pub(crate) async fn open_url(url: String, auth_token: String) -> Result<Self> {
        let remote = ["libsql://", "https://", "http://"]
            .iter()
            .any(|prefix| url.starts_with(prefix));
        let db = if remote {
            Builder::new_remote(url, auth_token).build().await?
        } else {
            let path = Path::new(url.strip_prefix("file:").unwrap_or(&url));
            ensure!(
                path != Path::new(":memory:"),
                "A persistent SQLite file is required"
            );
            if let Some(parent) = path.parent().filter(|path| !path.as_os_str().is_empty()) {
                std::fs::create_dir_all(parent)?;
            }
            Builder::new_local(path).build().await?
        };
        let store = Self(std::sync::Arc::new(db));
        let conn = store.conn()?;
        if !remote {
            conn.query("PRAGMA journal_mode=WAL", ())
                .await?
                .next()
                .await?;
        }
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,checksum TEXT NOT NULL,applied_at REAL NOT NULL)", ()).await?;
        for (version, name, sql) in [
            (
                1,
                "initial_schema",
                include_str!("../../dolos/migrations/0001_initial.sql"),
            ),
            (
                3,
                "object_storage",
                include_str!("../../dolos/migrations/0003_object_storage.sql"),
            ),
            (
                4,
                "remove_document_queue",
                "DROP INDEX IF EXISTS queue; UPDATE documents SET status='failed', error='Processing was interrupted during migration. Retry this document.', lease_until=NULL, token=NULL WHERE status IN ('queued','processing');",
            ),
            (
                5,
                "document_observations",
                include_str!("../migrations/0005_observations.sql"),
            ),
        ] {
            let checksum = format!("{:x}", Sha256::digest(sql));
            let tx = conn
                .transaction_with_behavior(TransactionBehavior::Immediate)
                .await?;
            let mut existing = tx
                .query(
                    "SELECT name,checksum FROM schema_migrations WHERE version=?",
                    [version],
                )
                .await?;
            if let Some(row) = existing.next().await? {
                ensure!(
                    row.get::<String>(0)? == name && row.get::<String>(1)? == checksum,
                    "Migration {version} has changed"
                );
            } else {
                for statement in sql.split(';').map(str::trim).filter(|sql| !sql.is_empty()) {
                    tx.execute(statement, ()).await?;
                }
                tx.execute(
                    "INSERT INTO schema_migrations VALUES(?,?,?,?)",
                    params![version, name, checksum, now()],
                )
                .await?;
            }
            drop(existing);
            tx.commit().await?;
        }
        Ok(store)
    }

    fn conn(&self) -> Result<Connection> {
        let conn = self.0.connect()?;
        // Remote libSQL handles lock waits at the server.
        let _ = conn.busy_timeout(std::time::Duration::from_secs(5));
        Ok(conn)
    }

    pub fn split(self) -> (Commands, Queries) {
        (Commands(self.clone()), Queries(self))
    }
}

impl Queries {
    pub async fn ready(&self) -> Result<()> {
        self.0.conn()?.query("SELECT 1", ()).await?.next().await?;
        Ok(())
    }

    pub async fn batches_exist(&self, ids: &[String]) -> Result<bool> {
        let ids = serde_json::to_string(ids)?;
        Ok(self
            .0
            .conn()?
            .query(
                "SELECT 1 FROM json_each(?) WHERE value NOT IN (SELECT id FROM batches) LIMIT 1",
                [ids],
            )
            .await?
            .next()
            .await?
            .is_none())
    }

    pub async fn row_errors(
        &self,
        id: &str,
        limit: i64,
        offset: i64,
    ) -> Result<Option<Vec<Value>>> {
        let conn = self.0.conn()?;
        if conn
            .query("SELECT 1 FROM documents WHERE id=?", [id])
            .await?
            .next()
            .await?
            .is_none()
        {
            return Ok(None);
        }
        let mut result = conn.query(
            "SELECT source_row,error FROM row_errors WHERE document_id=? ORDER BY source_row LIMIT ? OFFSET ?",
            params![id,limit,offset],
        ).await?;
        let mut rows = Vec::new();
        while let Some(row) = result.next().await? {
            rows.push(json!({"source_row":row.get::<i64>(0)?,"error":row.get::<String>(1)?}));
        }
        Ok(Some(rows))
    }

    pub async fn batch(&self, id: &str) -> Result<Option<Value>> {
        let conn = self.0.conn()?;
        if conn
            .query("SELECT 1 FROM batches WHERE id=?", [id])
            .await?
            .next()
            .await?
            .is_none()
        {
            return Ok(None);
        }
        let mut results = conn.query("SELECT d.id,d.filename,d.status,d.attempts,d.error,d.accepted,d.rejected FROM documents d JOIN batch_documents b ON b.document_id=d.id WHERE b.batch_id=? ORDER BY d.id", [id]).await?;
        let mut docs = Vec::new();
        while let Some(row) = results.next().await? {
            docs.push(json!({"id":row.get::<String>(0)?,"filename":row.get::<String>(1)?,
                "status":row.get::<String>(2)?,"attempts":row.get::<i64>(3)?,"error":row.get::<Option<String>>(4)?,
                "accepted":row.get::<i64>(5)?,"rejected":row.get::<i64>(6)?}));
        }
        let pending = conn
            .query(
                "SELECT COUNT(*) FROM upload_sessions WHERE batch_id=? AND status='pending'",
                [id],
            )
            .await?
            .next()
            .await?
            .context("Missing upload count")?
            .get::<i64>(0)?;
        if pending > 0 {
            return Ok(Some(
                json!({"id":id,"status":"awaiting_upload","documents":docs,"pending_uploads":pending}),
            ));
        }
        let status = if docs.iter().all(|d| d["status"] == "completed") {
            "completed"
        } else if docs.iter().any(|d| d["status"] == "processing") {
            "processing"
        } else if docs.iter().any(|d| d["status"] == "queued") {
            "queued"
        } else {
            "needs_review"
        };
        Ok(Some(json!({"id":id,"status":status,"documents":docs})))
    }

    pub async fn rows(&self, source: &'static str, filters: RowFilters<'_>) -> Result<Vec<Value>> {
        let RowFilters {
            limit,
            offset,
            account,
            before,
            after,
            batch,
            batches,
            edge_ids,
        } = filters;
        let mut query = format!("SELECT id,document_id,source_row,payload FROM {source} WHERE 1=1");
        let mut values: Vec<Sql> = Vec::new();
        if let Some(account) = account.filter(|id| !id.is_empty()) {
            query.push_str(" AND (sender_id=? OR receiver_id=?)");
            values.extend([account.into(), account.into()]);
        }
        for (input, clause) in [
            (before, " AND timestamp<?"),
            (after, " AND timestamp>=?"),
            (
                batch,
                " AND document_id IN (SELECT document_id FROM batch_documents WHERE batch_id=?)",
            ),
        ] {
            if let Some(input) = input.filter(|s| !s.is_empty()) {
                query.push_str(clause);
                values.push(input.into());
            }
        }
        if !edge_ids.is_empty() {
            query.push_str(" AND id IN (SELECT value FROM json_each(?))");
            values.push(serde_json::to_string(edge_ids)?.into());
        }
        if !batches.is_empty() {
            query.push_str(" AND document_id IN (SELECT document_id FROM batch_documents WHERE batch_id IN (SELECT value FROM json_each(?)))");
            values.push(serde_json::to_string(batches)?.into());
        }
        query.push_str(" ORDER BY timestamp,id LIMIT ? OFFSET ?");
        values.extend([limit.into(), offset.into()]);
        let conn = self.0.conn()?;
        let mut result = conn.query(&query, libsql::params_from_iter(values)).await?;
        let mut rows = Vec::new();
        while let Some(row) = result.next().await? {
            let payload: String = row.get(3)?;
            let mut data: Value = serde_json::from_str(&payload)?;
            data["id"] = row.get::<String>(0)?.into();
            data["document_id"] = row.get::<String>(1)?.into();
            data["source_row"] = row.get::<i64>(2)?.into();
            rows.push(data);
        }
        Ok(rows)
    }
}

#[derive(Default)]
pub struct RowFilters<'a> {
    pub limit: i64,
    pub offset: i64,
    pub account: Option<&'a str>,
    pub before: Option<&'a str>,
    pub after: Option<&'a str>,
    pub batch: Option<&'a str>,
    pub batches: &'a [String],
    pub edge_ids: &'a [String],
}

pub const GRAPH_ROWS: &str = "(SELECT * FROM transactions UNION ALL SELECT * FROM relations)";

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn cutover_makes_interrupted_documents_retryable_and_preserves_completed_rows()
    -> Result<()> {
        let dir = tempfile::TempDir::new()?;
        let path = dir.path().join("legacy.sqlite3");
        let legacy = Builder::new_local(&path).build().await?.connect()?;
        legacy
            .execute_batch(include_str!("../../dolos/migrations/0001_initial.sql"))
            .await?;
        legacy
            .execute("INSERT INTO batches VALUES('batch',0)", ())
            .await?;
        legacy
            .execute(
                "INSERT INTO documents(id,batch_id,filename,sha256,suffix,status,lease_until,token)
            VALUES('pending','batch','pending.pdf','abc','.pdf','queued',10,'lease'),
                  ('done','batch','done.csv','def','.csv','completed',NULL,NULL)",
                (),
            )
            .await?;
        legacy
            .execute(
                "INSERT INTO batch_documents VALUES('batch','done'),('batch','pending')",
                (),
            )
            .await?;
        legacy.execute("INSERT INTO transactions VALUES('row','done',1,'2025-01-01T00:00:00.000000+00:00','a','b','{}')", ()).await?;
        drop(legacy);

        for _ in 0..2 {
            let store = Store::open_url(path.to_string_lossy().into_owned(), String::new()).await?;
            let batch = Queries(store.clone())
                .batch("batch")
                .await?
                .context("Missing migrated batch")?;
            assert_eq!(batch["status"], "needs_review");
            assert_eq!(batch["documents"][0]["status"], "completed");
            assert_eq!(batch["documents"][1]["status"], "failed");
            let rows = Queries(store.clone())
                .rows(
                    "transactions",
                    RowFilters {
                        limit: 1,
                        ..Default::default()
                    },
                )
                .await?;
            assert_eq!(rows[0]["id"], "row");
        }
        Ok(())
    }
}
