CREATE TABLE document_sources (
    document_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    backend TEXT NOT NULL,
    bucket TEXT,
    object_key TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    recorded_at REAL,
    PRIMARY KEY (document_id, sequence)
);
INSERT INTO document_sources
SELECT d.id, 1, d.sha256, COALESCE(o.backend, 'local'), o.bucket,
       COALESCE(o.object_key, d.sha256 || d.suffix), COALESCE(o.byte_size, 0), NULL
FROM documents d LEFT JOIN document_objects o ON o.document_id = d.id
WHERE d.suffix != '.case';
CREATE TABLE compilation_attempts (
    document_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    compiler_version INTEGER NOT NULL,
    run_id TEXT UNIQUE,
    recorded_at REAL NOT NULL,
    status TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    rejected INTEGER NOT NULL,
    error TEXT,
    projected INTEGER NOT NULL CHECK (projected IN (0, 1)),
    PRIMARY KEY (document_id, sequence)
);
CREATE TABLE extraction_observations (
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    provider TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    mode TEXT NOT NULL,
    first_page INTEGER NOT NULL,
    last_page INTEGER NOT NULL,
    http_status INTEGER NOT NULL,
    response BLOB NOT NULL,
    recorded_at REAL NOT NULL,
    PRIMARY KEY (run_id, sequence)
);
CREATE INDEX extraction_observations_source ON extraction_observations(sha256, run_id, sequence);
