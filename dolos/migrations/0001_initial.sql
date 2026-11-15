CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, filename TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE, suffix TEXT NOT NULL, status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, lease_until REAL, token TEXT,
  error TEXT, accepted INTEGER NOT NULL DEFAULT 0, rejected INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS queue ON documents(status, lease_until);
CREATE TABLE IF NOT EXISTS batch_documents(
  batch_id TEXT NOT NULL, document_id TEXT NOT NULL,
  PRIMARY KEY(batch_id, document_id));
CREATE TABLE IF NOT EXISTS transactions(
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL, source_row INTEGER NOT NULL,
  timestamp TEXT NOT NULL, sender_id TEXT NOT NULL, receiver_id TEXT NOT NULL,
  payload TEXT NOT NULL, UNIQUE(document_id, source_row));
CREATE INDEX IF NOT EXISTS transaction_time ON transactions(timestamp, id);
CREATE INDEX IF NOT EXISTS transaction_sender ON transactions(sender_id);
CREATE INDEX IF NOT EXISTS transaction_receiver ON transactions(receiver_id);
CREATE TABLE IF NOT EXISTS relations(
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL, source_row INTEGER NOT NULL,
  timestamp TEXT, sender_id TEXT NOT NULL, receiver_id TEXT NOT NULL,
  payload TEXT NOT NULL, UNIQUE(document_id, source_row));
CREATE INDEX IF NOT EXISTS relation_time ON relations(timestamp, id);
CREATE INDEX IF NOT EXISTS relation_sender ON relations(sender_id);
CREATE INDEX IF NOT EXISTS relation_receiver ON relations(receiver_id);
CREATE TABLE IF NOT EXISTS row_errors(
  document_id TEXT NOT NULL, source_row INTEGER NOT NULL, error TEXT NOT NULL,
  PRIMARY KEY(document_id, source_row));
