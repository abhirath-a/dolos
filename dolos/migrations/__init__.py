"""Ordered, checksummed migrations; each migration and its ledger row commit together."""
import hashlib
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).parent
MIGRATIONS = (
    (1, 'initial_schema', '0001_initial.sql'),
    (2, 'famous_cases', '0002_famous_cases.json'),
    (3, 'object_storage', '0003_object_storage.sql'),
)


def _execute_script(db, script):
    # executescript is unavailable in libsql and may implicitly commit in sqlite3.
    # Split using SQLite's parser instead, retaining the caller's transaction.
    buffer = ''
    for character in script:
        buffer += character
        if character == ';' and sqlite3.complete_statement(buffer):
            db.execute(buffer)
            buffer = ''
    if buffer.strip():
        raise ValueError('Migration contains an unterminated SQL statement')


def apply_migrations(db, target=None, include_data=True):
    db.execute('''CREATE TABLE IF NOT EXISTS schema_migrations(
        version INTEGER PRIMARY KEY, name TEXT NOT NULL,
        checksum TEXT NOT NULL, applied_at REAL NOT NULL)''')
    applied = []
    for version, name, filename in MIGRATIONS:
        if target is not None and version > target:
            break
        if not include_data and filename.endswith('.json'):
            continue
        content = (ROOT / filename).read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        db.execute('BEGIN IMMEDIATE')
        try:
            existing = db.execute('SELECT name,checksum FROM schema_migrations WHERE version=?', (version,)).fetchone()
            if existing:
                if existing['checksum'] != checksum or existing['name'] != name:
                    raise RuntimeError(f'Applied migration {version} changed; add a new migration instead.')
            else:
                if filename.endswith('.sql'):
                    _execute_script(db, content.decode('utf-8'))
                else:
                    from ..demo import insert_cases
                    insert_cases(db)
                db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?)', (version, name, checksum, time.time()))
                applied.append(version)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return applied
