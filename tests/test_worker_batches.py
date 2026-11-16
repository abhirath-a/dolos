"""Model Turso's confirmed rollback without using deployed credentials."""
import sqlite3
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from dolos.api import create_app
from dolos.worker import process_one


EXPIRED = 'Hrana: interactive transaction was rolled back because the stream was idle for too long; retry the transaction'


def upload_400(client):
    source = Path('examples/statement.csv').read_text().splitlines()
    data = '\n'.join([source[0], *([source[1]] * 400)])
    response = client.post('/batches', files={'files': ('large.csv', data.encode())})
    assert response.status_code == 202
    return response.json()


def expire_saves(store, monkeypatch, *, failures, fail_at=2):
    original = store.database.connect
    state = {'remaining': failures, 'inserts': 0, 'expired': 0, 'rollback_failures': 0}

    class Connection:
        def __init__(self, inner):
            self.inner, self.count, self.expired = inner, 0, False

        def execute(self, sql, parameters=()):
            if sql.startswith('INSERT OR IGNORE INTO transactions'):
                self.count += 1
                state['inserts'] += 1
                assert len(parameters) <= 700
                if state['remaining'] and self.count == fail_at:
                    state['remaining'] -= 1
                    state['expired'] += 1
                    self.expired = True
                    self.inner.rollback()  # The server rolled back every insert.
                    raise ValueError(EXPIRED)
            return self.inner.execute(sql, parameters)

        def commit(self):
            return self.inner.commit()

        def rollback(self):
            if self.expired:
                state['rollback_failures'] += 1
                raise ValueError('cannot rollback - no transaction is active')
            return self.inner.rollback()

        def close(self):
            return self.inner.close()

    monkeypatch.setattr(store.database, 'connect', lambda: Connection(original()))
    return state


def test_400_rows_use_four_database_insert_requests(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    client = TestClient(app)
    batch = upload_400(client)
    state = expire_saves(app.state.store, monkeypatch, failures=0)
    assert process_one(app.state.store)
    document = client.get('/batches/' + batch['id']).json()['documents'][0]
    assert (document['status'], document['accepted']) == ('completed', 400)
    assert state['inserts'] == 4


def test_confirmed_expiry_retries_whole_save_without_reparsing(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    client = TestClient(app)
    batch = upload_400(client)
    state = expire_saves(app.state.store, monkeypatch, failures=1)
    from dolos import worker
    original_parse = worker.parse_document
    parsed = []

    def parse_once(*args):
        parsed.append(True)
        yield from original_parse(*args)

    monkeypatch.setattr(worker, 'parse_document', parse_once)
    assert process_one(app.state.store)
    document = client.get('/batches/' + batch['id']).json()['documents'][0]
    assert (document['status'], document['accepted']) == ('completed', 400)
    assert state['expired'] == state['rollback_failures'] == 1
    assert state['inserts'] == 6  # Two aborted inserts, then four committed batches.
    assert len(parsed) == 1
    assert len(client.get('/graph', params={'batch_id': batch['id']}).json()['edges']) == 400


def test_repeated_expiry_leaves_no_partial_rows_and_keeps_useful_error(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    client = TestClient(app)
    batch = upload_400(client)
    state = expire_saves(app.state.store, monkeypatch, failures=3)
    assert process_one(app.state.store)
    document = client.get('/batches/' + batch['id']).json()['documents'][0]
    assert document['status'] == 'failed'
    assert 'after three attempts' in document['error']
    assert 'cannot rollback' not in document['error']
    assert state['expired'] == 3
    assert client.get('/graph', params={'batch_id': batch['id']}).json()['edges'] == []


def test_rollback_failure_does_not_replace_original_exception(tmp_path, monkeypatch):
    app = create_app(tmp_path)

    class BadRollback:
        def rollback(self):
            raise ValueError('cannot rollback - no transaction is active')

        def close(self):
            pass

    monkeypatch.setattr(app.state.store.database, 'connect', BadRollback)
    with pytest.raises(sqlite3.OperationalError, match='original failure'):
        with app.state.store.connect():
            raise sqlite3.OperationalError('original failure')


@pytest.mark.parametrize('driver', ['sqlite', 'libsql'])
def test_all_four_real_demo_csvs_save_with_batched_inserts(tmp_path, monkeypatch, driver):
    if driver == 'libsql':
        pytest.importorskip('libsql')
    monkeypatch.setenv('DOLOS_DATABASE_DRIVER', driver)
    app = create_app(tmp_path)
    client = TestClient(app)
    with ZipFile('frontend/public/dolos-video-statements.zip') as archive:
        files = [('files', (name, archive.read(name), 'text/csv'))
                 for name in sorted(archive.namelist()) if name.endswith('.csv')]
    assert len(files) == 4
    response = client.post('/batches', files=files)
    assert response.status_code == 202
    batch_id = response.json()['id']
    while process_one(app.state.store):
        pass
    batch = client.get('/batches/' + batch_id).json()
    assert batch['status'] == 'completed'
    assert sorted(document['accepted'] for document in batch['documents']) == [200, 200, 400, 400]
    assert all(document['rejected'] == 0 for document in batch['documents'])
    graph = client.get('/graph', params={'batch_id': batch_id, 'limit': 2000}).json()
    assert len(graph['edges']) == 1200
