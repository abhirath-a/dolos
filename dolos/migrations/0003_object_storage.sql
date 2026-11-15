CREATE TABLE IF NOT EXISTS document_objects(
    document_id TEXT PRIMARY KEY,
    backend TEXT NOT NULL CHECK(backend IN ('local','r2')),
    bucket TEXT,
    object_key TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size > 0)
);
CREATE TABLE IF NOT EXISTS upload_sessions(
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    suffix TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size > 0),
    bucket TEXT NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    expires_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed')),
    document_id TEXT
);
CREATE INDEX IF NOT EXISTS upload_session_batch ON upload_sessions(batch_id);
