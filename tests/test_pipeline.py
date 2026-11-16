import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dolos.api import create_app
from dolos.worker import claim, process_one


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv('DOLOS_API_KEY', raising=False)
    monkeypatch.delenv('DOLOS_MODEL_DIR', raising=False)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        yield client, app.state.store


def upload(client, content=None, filename='statement.csv'):
    content = content if content is not None else Path('examples/statement.csv').read_bytes()
    response = client.post('/batches', files=[('files', (filename, content))])
    assert response.status_code == 202, response.text
    return response.json()


def test_end_to_end_and_idempotency(setup):
    client, store = setup
    first = upload(client)
    assert first['status'] == 'queued'
    assert client.get('/graph').json()['edges'] == []
    assert process_one(store)
    result = client.get('/batches/' + first['id']).json()
    assert result['status'] == 'completed'
    assert result['documents'][0]['accepted'] == 3
    graph = client.get('/graph').json()
    assert len(graph['nodes']) == 3
    assert len(graph['edges']) == 3  # Identical repeated transfers are preserved.
    assert graph['nodes'][0]['account'] == '000123'
    assert graph['summaries'][1]['amount_paid'] == '200.00'
    second = upload(client)
    assert second['documents'][0]['id'] == first['documents'][0]['id']
    assert not process_one(store)
    assert len(client.get('/graph', params={'batch_id': second['id']}).json()['edges']) == 3
    page = client.get('/graph', params={'limit': 2}).json()
    assert page['has_more'] and page['next_offset'] == 2
    assert len(client.get('/transactions', params={'offset': 2}).json()['transactions']) == 1
    assert len(client.get('/graph', params={'before': '2026-11-01T10:15:00Z'}).json()['edges']) == 1
    assert client.get('/scores').status_code == 503


def test_invalid_rows_review_and_atomic_parse_failure(setup):
    client, store = setup
    rows = Path('examples/statement.csv').read_text().splitlines()
    rows.append(rows[1].replace('125.50', 'NaN'))
    batch = upload(client, '\n'.join(rows).encode())
    assert process_one(store)
    doc = client.get('/batches/' + batch['id']).json()['documents'][0]
    assert doc['status'] == 'needs_review'
    assert (doc['accepted'], doc['rejected']) == (3, 1)
    assert client.get(f"/documents/{doc['id']}/errors").json()['errors'][0]['source_row'] == 4
    bad = upload(client, b'{"transactions": [', 'bad.json')
    assert process_one(store)
    assert client.get('/batches/' + bad['id']).json()['documents'][0]['status'] == 'failed'
    assert len(client.get('/graph').json()['edges']) == 3
    assert client.post('/documents/' + bad['documents'][0]['id'] + '/retry').status_code == 202


def test_exclusive_claim_and_crash_recovery(setup):
    client, store = setup
    batch = upload(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: claim(store), range(2)))
    assert sum(j is not None for j in jobs) == 1
    with store.connect() as db:
        db.execute('UPDATE documents SET lease_until=?', (time.time() - 1,))
    assert process_one(store)
    doc = client.get('/batches/' + batch['id']).json()['documents'][0]
    assert doc['attempts'] == 2 and doc['accepted'] == 3
    assert not process_one(store)


def test_multi_document_batch_json_and_identity(setup):
    client, store = setup
    row = dict(timestamp='2026-11-01T09:00:00-05:00', from_bank='a:b', from_account='c',
               to_bank='a', to_account='b:c', amount_received='1.01', receiving_currency='USD',
               amount_paid='1.01', payment_currency='USD', payment_format='ACH')
    response = client.post('/batches', files=[
        ('files', ('a.csv', Path('examples/statement.csv').read_bytes())),
        ('files', ('b.json', json.dumps([row]).encode()))])
    assert response.status_code == 202
    assert process_one(store) and process_one(store)
    graph = client.get('/graph').json()
    assert len(graph['nodes']) == 5
    assert len(graph['edges']) == 4
    assert client.get('/batches/' + response.json()['id']).json()['status'] == 'completed'


def test_auth_and_upload_rejection(setup, monkeypatch):
    client, store = setup
    assert client.post('/batches', files={'files': ('a.exe', b'x')}).status_code == 415
    assert client.post('/batches', files={'files': ('a.csv', b'')}).status_code == 422
    monkeypatch.setenv('DOLOS_API_KEY', 'test-secret')
    assert client.get('/graph').status_code == 401
    assert client.get('/graph', headers={'Authorization': 'Bearer test-secret'}).status_code == 200
    assert client.get('/graph', headers={'Authorization': 'Bearer wrong'}).status_code == 401


def test_ibm_duplicate_account_headers(setup):
    client, store = setup
    from dolos.parsers import IBM_HEADER
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(IBM_HEADER)
    writer.writerow(['2022/09/01 00:00', '001', '0001', '002', '0002', '1', 'US Dollar', '1', 'US Dollar', 'ACH', '0'])
    upload(client, output.getvalue().encode())
    assert process_one(store)
    row = client.get('/transactions').json()['transactions'][0]
    assert row['from_account'] == '0001' and row['to_account'] == '0002'


def test_expired_worker_is_fenced_before_graph_commit(setup, monkeypatch):
    client, store = setup
    batch = upload(client)
    from dolos import worker
    original = worker.parse_document

    def replaced_lease(path, suffix):
        for entry in original(path, suffix):
            with store.connect() as db:
                db.execute('UPDATE documents SET token=?,lease_until=?', ('replacement', time.time() - 1))
            yield entry

    monkeypatch.setattr(worker, 'parse_document', replaced_lease)
    assert process_one(store)
    assert client.get('/graph').json()['edges'] == []
    monkeypatch.setattr(worker, 'parse_document', original)
    assert process_one(store)
    assert len(client.get('/graph').json()['edges']) == 3


def test_maximum_crash_attempts_mark_job_failed(setup):
    client, store = setup
    batch = upload(client)
    with store.connect() as db:
        db.execute("UPDATE documents SET status='processing',attempts=3,lease_until=?", (time.time() - 1,))
    assert not process_one(store)
    doc = client.get('/batches/' + batch['id']).json()['documents'][0]
    assert doc['status'] == 'failed'
    assert client.get('/graph').json()['edges'] == []


def test_decimal_precision_and_hostile_exponents(setup):
    client, store = setup
    rows = Path('examples/statement.csv').read_text().splitlines()
    rows = [rows[0], rows[1].replace('125.50', '999999999999999999.123456789123456789')]
    rows.append(rows[1])
    rows.append(rows[1].replace('999999999999999999.123456789123456789', '1e-999999999'))
    upload(client, '\n'.join(rows).encode())
    assert process_one(store)
    graph = client.get('/graph').json()
    assert len(graph['edges']) == 2
    assert graph['summaries'][0]['amount_paid'] == '1999999999999999998.246913578246913578'


def test_heartbeat_keeps_long_extraction_leased(setup, monkeypatch):
    client, store = setup
    from dolos import worker
    upload(client)
    monkeypatch.setattr(worker, 'LEASE_SECONDS', .2)
    monkeypatch.setattr(worker, 'HEARTBEAT_SECONDS', .01)
    original = worker.parse_document

    def slow_parse(path, suffix):
        time.sleep(.25)  # Longer than the lease, simulating an in-flight provider call.
        assert worker.claim(store) is None
        yield from original(path, suffix)

    monkeypatch.setattr(worker, 'parse_document', slow_parse)
    assert process_one(store)
    assert len(client.get('/graph').json()['edges']) == 3


def test_unexpected_parser_failure_reports_stage_without_source_data(setup, monkeypatch):
    client, store = setup
    from dolos import worker
    batch = upload(client)

    def fail_parse(*args):
        raise RuntimeError('private statement text and account details')

    monkeypatch.setattr(worker, 'parse_document', fail_parse)
    assert process_one(store)
    document = client.get('/batches/' + batch['id']).json()['documents'][0]
    assert document['status'] == 'failed'
    assert 'parsing document (RuntimeError)' in document['error']
    assert 'private statement' not in document['error']
    assert client.get('/graph').json()['edges'] == []
