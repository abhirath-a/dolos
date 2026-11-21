use anyhow::{Result, ensure};
use libsql::params;
use std::ops::RangeInclusive;

use crate::{
    db::{Commands, now},
    storage::id,
};

const MAX_RESPONSE_BYTES_PER_RUN: usize = 32 * 1024 * 1024;

/// The provider adapter appends response bytes before consuming them.
pub(crate) struct Journal {
    db: Commands,
    pub run_id: String,
    sha256: String,
    sequence: i64,
    bytes: usize,
}

impl Journal {
    pub fn new(db: Commands, sha256: String) -> Self {
        Self {
            db,
            run_id: id(),
            sha256,
            sequence: 0,
            bytes: 0,
        }
    }

    pub(super) fn ensure_capacity(&self, maximum_response: usize) -> Result<()> {
        ensure!(
            self.bytes + maximum_response <= MAX_RESPONSE_BYTES_PER_RUN,
            "PDF extraction observations exceed the size limit. Split the statement."
        );
        Ok(())
    }

    pub async fn record(
        &mut self,
        provider: &str,
        model: &str,
        mode: &str,
        pages: &RangeInclusive<u32>,
        status: u16,
        body: &[u8],
    ) -> Result<()> {
        ensure!(
            self.bytes + body.len() <= MAX_RESPONSE_BYTES_PER_RUN,
            "PDF extraction observations exceed the size limit. Split the statement."
        );
        self.db
            .conn()?
            .execute(
                "INSERT INTO extraction_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                params![
                    self.run_id.clone(),
                    self.sequence + 1,
                    self.sha256.clone(),
                    provider,
                    model,
                    mode,
                    i64::from(*pages.start()),
                    i64::from(*pages.end()),
                    i64::from(status),
                    body.to_vec(),
                    now()
                ],
            )
            .await?;
        self.sequence += 1;
        self.bytes += body.len();
        Ok(())
    }
}
