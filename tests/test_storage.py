import hashlib
import io
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

boto3 = pytest.importorskip('boto3')
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from dolos.api import create_app
from dolos.storage import R2Storage, StorageError
from dolos.storage_setup import setup_bucket, migrate_local
from dolos.store import Store
from dolos.worker import process_one


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.bucket_exists = True
        self.cors = None
        self.rules = [{'ID': 'existing', 'Status': 'Enabled', 'Filter': {'Prefix': 'unrelated/'}, 'Expiration': {'Days': 7}}]
        self.closed_bodies = []

    def head_bucket(self, **kwargs):
        if not self.bucket_exists:
            raise ClientError({'Error': {'Code': '404'}}, 'HeadBucket')

    def create_bucket(self, **kwargs):
        self.bucket_exists = True

    def put_object(self, Bucket, Key, Body, ContentLength, ContentType):
        data = Body.read()
        assert len(data) == ContentLength
        self.objects[Key] = data
        return {'ETag': 'fake'}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        body = io.BytesIO(self.objects[Key])
        self.closed_bodies.append(body)
        return {'Body': body, 'ContentLength': len(self.objects[Key])}

    def generate_presigned_url(self, operation, Params, ExpiresIn, HttpMethod):
        assert operation == 'put_object' and HttpMethod == 'PUT' and ExpiresIn == 900
        assert Params['ContentLength'] > 0
        return 'https://example.r2.cloudflarestorage.com/' + Params['Key'] + '?signed=fake'

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def put_bucket_cors(self, Bucket, CORSConfiguration):
        self.cors = CORSConfiguration

    def get_bucket_lifecycle_configuration(self, Bucket):
        return {'Rules': self.rules}

    def put_bucket_lifecycle_configuration(self, Bucket, LifecycleConfiguration):
        self.rules = LifecycleConfiguration['Rules']


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('DOLOS_STORAGE_BACKEND', 'r2')
    monkeypatch.setenv('R2_ACCOUNT_ID', 'a' * 32)
    monkeypatch.setenv('R2_BUCKET_NAME', 'dolos-statements')
    monkeypatch.setenv('R2_ACCESS_KEY_ID', 'fake-access')
    monkeypatch.setenv('R2_SECRET_ACCESS_KEY', 'fake-secret')
    monkeypatch.delenv('DOLOS_API_KEY', raising=False)
    fake = FakeS3()
    monkeypatch.setattr(boto3, 'client', lambda *args, **kwargs: fake)
    app = create_app(tmp_path / 'api-files')
    with TestClient(app) as client:
        yield client, app.state.store, fake


def sample():
    return Path('examples/statement.csv').read_bytes()


def presign(client, data=None):
    data = data if data is not None else sample()
    response = client.post('/uploads/presign', json={'files': [{
        'filename': 'statement.csv', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]})
    assert response.status_code == 201, response.text
    return response.json()


def sessions(store, batch_id):
    with store.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM upload_sessions WHERE batch_id=?', (batch_id,))]


def test_multipart_r2_and_worker_on_another_filesystem(setup, tmp_path):
    client, store, fake = setup
    response = client.post('/batches', files={'files': ('statement.csv', sample())})
    assert response.status_code == 202
    document = response.json()['documents'][0]
    location = store.object_location(document['id'])
    assert location['backend'] == 'r2'
    assert location['object_key'].startswith('dolos/documents/')
    assert not (store.root / 'uploads').exists()
    worker_store = Store(tmp_path / 'different-machine')
    assert process_one(worker_store)
    assert len(client.get('/graph').json()['edges']) == 3
    assert all(body.closed for body in fake.closed_bodies)
    second = client.post('/batches', files={'files': ('renamed.csv', sample())})
    assert second.json()['documents'][0]['id'] == document['id']
    assert not process_one(worker_store)


def test_direct_upload_finalize_idempotency_and_staging_replay(setup, tmp_path):
    client, store, fake = setup
    signed = presign(client)
    assert signed['uploads'][0]['headers'] == {'Content-Type': 'text/csv'}
    assert client.get('/batches/' + signed['batch_id']).json()['status'] == 'awaiting_upload'
    assert not process_one(store)
    session = sessions(store, signed['batch_id'])[0]
    fake.objects[session['object_key']] = sample()
    response = client.post(signed['complete_url'])
    assert response.status_code == 202 and response.json()['status'] == 'queued'
    document_id = response.json()['documents'][0]['id']
    assert session['object_key'] not in fake.objects
    assert client.post(signed['complete_url']).json()['documents'][0]['id'] == document_id
    # A browser replay can overwrite only staging, never the published source.
    fake.objects[session['object_key']] = b'changed staging bytes'
    assert process_one(Store(tmp_path / 'worker-files'))
    assert len(client.get('/graph').json()['edges']) == 3
    duplicate = presign(client)
    fake.objects[sessions(store, duplicate['batch_id'])[0]['object_key']] = sample()
    second = client.post(duplicate['complete_url'])
    assert second.json()['documents'][0]['id'] == document_id
    assert not process_one(store)


def test_missing_corrupt_and_expired_uploads_never_queue(setup):
    client, store, fake = setup
    signed = presign(client)
    assert client.post(signed['complete_url']).status_code == 409
    session = sessions(store, signed['batch_id'])[0]
    fake.objects[session['object_key']] = b'x' * len(sample())
    assert client.post(signed['complete_url']).status_code == 422
    assert not process_one(store)
    assert store.graph_rows() == []
    fake.objects[session['object_key']] = sample() + b'x'
    assert client.post(signed['complete_url']).status_code == 422
    with store.connect() as db:
        db.execute('UPDATE upload_sessions SET expires_at=?', (time.time() - 1,))
    assert client.post(signed['complete_url']).status_code == 410
    assert not process_one(store)


def test_multi_file_completion_is_atomic(setup):
    client, store, fake = setup
    data = sample()
    response = client.post('/uploads/presign', json={'files': [
        {'filename': 'a.csv', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
        {'filename': 'b.csv', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]})
    signed = response.json()
    rows = sessions(store, signed['batch_id'])
    fake.objects[rows[0]['object_key']] = data
    assert client.post(signed['complete_url']).status_code == 409
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 0
    fake.objects[rows[1]['object_key']] = data
    assert client.post(signed['complete_url']).status_code == 202
    assert process_one(store) and not process_one(store)  # Exact duplicate content shares one document.


def test_local_files_can_be_backfilled_without_deleting_originals(setup, monkeypatch):
    client, store, fake = setup
    monkeypatch.setenv('DOLOS_STORAGE_BACKEND', 'local')
    response = client.post('/batches', files={'files': ('statement.csv', sample())})
    doc = response.json()['documents'][0]
    old = store.object_location(doc['id'])
    path = store.root / 'uploads' / old['object_key']
    assert path.exists()
    result = migrate_local(store, R2Storage())
    assert result['migrated'] == 1 and path.exists()
    assert store.object_location(doc['id'])['backend'] == 'r2'
    assert migrate_local(store, R2Storage())['migrated'] == 0
    assert process_one(store)


def test_private_bucket_setup_preserves_other_lifecycle_rules(setup):
    _, _, fake = setup
    fake.bucket_exists = False
    result = setup_bucket(R2Storage(), create=True, origins=['https://dolos.example', 'http://localhost:5173'])
    assert result['created']
    assert fake.cors['CORSRules'][0]['AllowedMethods'] == ['PUT']
    assert len(fake.rules) == 2 and fake.rules[0]['ID'] == 'existing'
    setup_bucket(R2Storage())
    assert len(fake.rules) == 2  # Lifecycle setup is idempotent.
    with pytest.raises(ValueError, match='origins'):
        setup_bucket(R2Storage(), origins=['https://dolos.example/path'])


def test_presign_validation_and_required_configuration(setup, monkeypatch):
    client, store, fake = setup
    assert client.post('/uploads/presign', json={'files': []}).status_code == 422
    assert client.post('/uploads/presign', json={'files': [{
        'filename': 'a.exe', 'size': 1, 'sha256': 'a' * 64}]}).status_code == 415
    assert client.post('/uploads/presign', json={'files': [{
        'filename': 'a.csv', 'size': 26 * 1024 * 1024, 'sha256': 'a' * 64}]}).status_code == 422
    monkeypatch.delenv('R2_ACCESS_KEY_ID')
    assert client.post('/uploads/presign', json={'files': [{
        'filename': 'a.csv', 'size': 1, 'sha256': 'a' * 64}]}).status_code == 503


def test_real_sdk_signature_endpoint_and_size_binding(monkeypatch):
    monkeypatch.setenv('R2_ACCOUNT_ID', 'a' * 32)
    monkeypatch.setenv('R2_BUCKET_NAME', 'private-bucket')
    monkeypatch.setenv('R2_ACCESS_KEY_ID', 'fake-access')
    monkeypatch.setenv('R2_SECRET_ACCESS_KEY', 'fake-secret')
    storage = R2Storage()
    key, url = storage.presign('b' * 64, '.pdf', 100)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.hostname == 'a' * 32 + '.r2.cloudflarestorage.com'
    assert key.startswith('dolos/incoming/')
    assert query['X-Amz-Expires'] == ['900']
    assert {'content-length', 'content-type', 'host'} <= set(query['X-Amz-SignedHeaders'][0].split(';'))
    with pytest.raises(StorageError):
        storage.remove_staging('dolos/documents/published.pdf')
