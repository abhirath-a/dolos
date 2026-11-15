import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dolos import demo
from dolos.database import Database
from dolos.migrate import migrate
from dolos.migrations import ROOT
from dolos.parsers import parse_document
from dolos.store import Store


def test_database_location_independent_from_uploads(tmp_path):
    upload_dir = tmp_path / 'files'
    database_path = tmp_path / 'elsewhere' / 'dolos.sqlite3'
    store = Store(upload_dir, database_url='file:' + str(database_path))
    assert database_path.is_file()
    assert not (upload_dir / 'dolos.sqlite3').exists()
    assert not (upload_dir / 'dolos.sqlite3').exists()
    with store.connect() as db:
        assert db.execute('SELECT version FROM schema_migrations').fetchone()[0] == 1
    migrate(upload_dir, 'file:' + str(database_path))
    assert len(store.graph_rows()) == 29


def test_existing_schema_and_uploaded_records_are_preserved(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    _, row, _ = next(parse_document(Path('examples/statement.csv'), '.csv'))
    with sqlite3.connect(path) as db:
        db.executescript((ROOT / '0001_initial.sql').read_text())
        db.execute('INSERT INTO batches VALUES(?,?)', ('uploaded-batch', 1))
        db.execute("INSERT INTO documents(id,batch_id,filename,sha256,suffix,status) VALUES(?,?,?,?,?,?)",
                   ('uploaded-doc', 'uploaded-batch', 'old.csv', 'unique-upload', '.csv', 'completed'))
        db.execute('INSERT INTO batch_documents VALUES(?,?)', ('uploaded-batch', 'uploaded-doc'))
        db.execute('INSERT INTO transactions VALUES(?,?,?,?,?,?,?)',
                   ('uploaded-tx', 'uploaded-doc', 1, row['timestamp'], row['sender_id'], row['receiver_id'], json.dumps(row)))
    first = migrate(tmp_path / 'uploads', 'file:' + str(path))
    assert first['applied'] == [1, 2, 3]
    store = Store(tmp_path / 'uploads', 'file:' + str(path))
    assert store.transactions()[0]['id'] == 'uploaded-tx'
    assert store.batch('uploaded-batch')['status'] == 'completed'
    assert len(store.graph_rows()) == 30
    assert migrate(tmp_path / 'uploads', 'file:' + str(path))['applied'] == []


def test_failed_data_migration_rolls_back_and_can_resume(tmp_path, monkeypatch):
    original = demo.insert_cases
    def fail_after_inserts(db):
        original(db)
        raise RuntimeError('Interrupted migration')
    monkeypatch.setattr(demo, 'insert_cases', fail_after_inserts)
    with pytest.raises(RuntimeError, match='Interrupted'):
        migrate(tmp_path)
    store = Store(tmp_path)
    with store.connect() as db:
        assert [r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')] == [1, 3]
    assert store.graph_rows() == []
    monkeypatch.setattr(demo, 'insert_cases', original)
    assert migrate(tmp_path)['applied'] == [2]
    assert len(store.relations()) == 29


def test_migration_concurrency_and_checksum_protection(tmp_path):
    store = Store(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: migrate(tmp_path), range(2)))
    assert sum(2 in result['applied'] for result in results) == 1
    assert len(store.relations()) == 29
    with store.connect() as db:
        db.execute("UPDATE schema_migrations SET checksum='changed' WHERE version=2")
    with pytest.raises(RuntimeError, match='changed'):
        migrate(tmp_path)


def test_remote_config_uses_libsql_without_creating_local_database(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import sys
    calls = []
    def connect(database, **options):
        calls.append((database, options))
        return sqlite3.connect(':memory:', isolation_level=None)
    monkeypatch.setitem(sys.modules, 'libsql', SimpleNamespace(connect=connect))
    monkeypatch.delenv('DOLOS_DATABASE_DRIVER', raising=False)
    monkeypatch.setenv('TURSO_AUTH_TOKEN', 'test-token')
    database = Database('libsql://test-db.turso.io')
    assert database.remote and database.path is None
    db = database.connect()
    try:
        db.execute('CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)')
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO test VALUES(?,?)', (1, 'value'))
        db.commit()
        row = db.execute('SELECT id,name FROM test').fetchone()
        assert row[0] == row['id'] == 1 and dict(row)['name'] == 'value'
    finally:
        db.close()
    assert calls == [('libsql://test-db.turso.io', {'isolation_level': None, 'auth_token': 'test-token'})]


def test_turso_url_alias_and_invalid_driver(monkeypatch):
    monkeypatch.delenv('DOLOS_DATABASE_URL', raising=False)
    monkeypatch.delenv('DOLOS_DATABASE_DRIVER', raising=False)
    monkeypatch.setenv('TURSO_DATABASE_URL', 'libsql://test-db.turso.io')
    assert Database().remote
    with pytest.raises(ValueError, match='require the libsql'):
        Database(driver='sqlite')


def test_native_libsql_migrations_and_queue(tmp_path, monkeypatch):
    pytest.importorskip('libsql')
    from dolos.worker import claim
    url = 'file:' + str(tmp_path / 'native-libsql.sqlite3')
    result = migrate(tmp_path / 'uploads', url, driver='libsql')
    assert result['applied'] == [1, 2, 3]
    store = Store(tmp_path / 'uploads', url, driver='libsql')
    assert len(store.relations()) == 29
    assert migrate(tmp_path / 'uploads', url, driver='libsql')['applied'] == []
    with store.connect() as db:
        db.execute("INSERT INTO documents(id,batch_id,filename,sha256,suffix,status) VALUES('queued','batch','file.csv','new','.csv','queued')")
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: claim(store), range(2)))
    assert sum(job is not None for job in jobs) == 1

    with pytest.raises(RuntimeError, match='abort'):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE documents SET status='failed' WHERE id='queued'")
            raise RuntimeError('abort')
    with store.connect() as db:
        assert db.execute("SELECT status FROM documents WHERE id='queued'").fetchone()['status'] == 'processing'
    from fastapi.testclient import TestClient
    from dolos.api import create_app
    from dolos.worker import process_one
    monkeypatch.setenv('DOLOS_DATABASE_URL', url)
    monkeypatch.setenv('DOLOS_DATABASE_DRIVER', 'libsql')
    monkeypatch.delenv('DOLOS_API_KEY', raising=False)
    with TestClient(create_app(tmp_path / 'uploads')) as client:
        response = client.post('/batches', files={'files': ('statement.csv', Path('examples/statement.csv').read_bytes())})
        assert response.status_code == 202
        assert process_one(store)
        assert client.get('/batches/' + response.json()['id']).json()['status'] == 'completed'
        assert len(client.get('/graph').json()['edges']) == 32
