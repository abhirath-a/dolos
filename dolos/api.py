import hashlib
import logging
from contextlib import ExitStack
import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .graph import build_graph
from .inference import ModelUnavailable, score
from .parsers import FIELDS, timestamp
from .store import Store
from .storage import (storage_for, StorageError, UploadNotReady, CONTENT_TYPES,
                      MAX_FILE_BYTES, MAX_BATCH_BYTES)

MAX_HISTORY = 100_000
bearer = HTTPBearer(auto_error=False)
log = logging.getLogger(__name__)


class UploadSpec(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class PresignBatch(BaseModel):
    files: List[UploadSpec] = Field(min_length=1, max_length=100)


def publish_batch(store, batch_id, prepared, direct=False):
    """Only publish documents after every object has been persisted and verified."""
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not direct:
            db.execute('INSERT INTO batches VALUES(?,?)', (batch_id, time.time()))
        for item in prepared:
            if direct:
                session = db.execute('SELECT status FROM upload_sessions WHERE id=?', (item['id'],)).fetchone()
                if session['status'] == 'completed':
                    continue  # Another completion request already published this session.
            existing = db.execute('SELECT id FROM documents WHERE sha256=?', (item['sha256'],)).fetchone()
            document_id = existing['id'] if existing else uuid.uuid4().hex
            if not existing:
                db.execute("""INSERT INTO documents(id,batch_id,filename,sha256,suffix,status)
                    VALUES(?,?,?,?,?,'queued')""", (document_id, batch_id, item['filename'], item['sha256'], item['suffix']))
                store.record_object(db, document_id, item['location'])
            db.execute('INSERT OR IGNORE INTO batch_documents VALUES(?,?)', (batch_id, document_id))
            if direct:
                db.execute("UPDATE upload_sessions SET status='completed',document_id=? WHERE id=?", (document_id, item['id']))
    return store.batch(batch_id)


def authorize(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    expected = os.environ.get('DOLOS_API_KEY')
    if expected and (credentials is None or not secrets.compare_digest(credentials.credentials, expected)):
        raise HTTPException(401, 'Invalid bearer token', headers={'WWW-Authenticate': 'Bearer'})


def create_app(data_dir=None):
    app = FastAPI(title='Dolos transaction graph API', version='0.1.0', dependencies=[Depends(authorize)])
    store = Store(data_dir)
    app.state.store = store

    @app.exception_handler(StorageError)
    async def storage_error(request, exc):
        return JSONResponse(status_code=503, content={'detail': str(exc)})

    @app.get('/health')
    def health():
        with store.connect() as db:
            queued = db.execute("SELECT COUNT(*) FROM documents WHERE status IN ('queued','processing')").fetchone()[0]
        return {'status': 'ok', 'pending_documents': queued,
                'model_configured': bool(os.environ.get('DOLOS_MODEL_DIR'))}

    @app.get('/demo/cases')
    def demo_cases():
        from .demo import load_dataset
        dataset = load_dataset()
        return {'dataset_id': dataset['dataset_id'], 'researched_on': dataset['researched_on'],
                'notes': dataset['notes'], 'sources': dataset['sources'],
                'cases': [{k: v for k, v in case.items() if k not in {'nodes', 'edges'}}
                          for case in dataset['cases']]}

    @app.get('/demo/cases/{case_id}')
    def demo_case(case_id: str):
        from .demo import load_dataset
        dataset = load_dataset()
        case = next((c for c in dataset['cases'] if c['id'] == case_id), None)
        if case is None:
            raise HTTPException(404, 'Demo case not found')
        return dict(case, sources=[s for s in dataset['sources'] if s['id'] in case['source_ids']],
                    dataset_notes=dataset['notes'])

    @app.get('/statement-schema')
    def schema():
        return {'required_columns': FIELDS, 'optional_columns': ['reference'],
                'timestamp_policy': 'ISO 8601 with offset; naive timestamps assumed UTC. IBM YYYY/MM/DD HH:MM accepted.',
                'amount_policy': 'Nonnegative decimal strings; sender pays amount_paid, receiver gets amount_received.',
                'identity_policy': 'Exact bank/account strings; no fuzzy matching or counterparty guessing.',
                'pdf_support': 'Flexible PDF-to-relation extraction, default OpenRouter free models; requires worker OPENROUTER_API_KEY and dolos[pdf-llm]. Names alone can form graph nodes; missing fields remain null.'}

    @app.post('/batches', status_code=202)
    async def upload(files: List[UploadFile] = File(...)):
        if not 1 <= len(files) <= 100:
            raise HTTPException(413, 'Upload 1–100 documents per batch.')
        batch_size = 0
        try:
            with tempfile.TemporaryDirectory(prefix='dolos-upload-') as directory:
                staged = []
                for index, file in enumerate(files):
                    suffix = Path(file.filename or '').suffix.lower()
                    if suffix not in CONTENT_TYPES:
                        raise HTTPException(415, 'Supported file extensions: .csv, .json, .pdf')
                    digest = hashlib.sha256()
                    size = 0
                    path = Path(directory) / (str(index) + suffix)
                    with path.open('wb') as output:
                        while True:
                            chunk = await file.read(1024 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            batch_size += len(chunk)
                            if size > MAX_FILE_BYTES or batch_size > MAX_BATCH_BYTES:
                                raise HTTPException(413, 'Limit: 25 MiB per file, 100 MiB per batch.')
                            digest.update(chunk)
                            output.write(chunk)
                    if not size:
                        raise HTTPException(422, 'Empty documents are not accepted.')
                    staged.append({'filename': Path(file.filename).name, 'suffix': suffix,
                                   'sha256': digest.hexdigest(), 'path': path})
                storage = storage_for(store.root)
                for item in staged:
                    item['location'] = await run_in_threadpool(storage.save, item['path'], item['sha256'], item['suffix'])
                return await run_in_threadpool(publish_batch, store, uuid.uuid4().hex, staged)
        finally:
            for file in files:
                await file.close()

    @app.post('/uploads/presign', status_code=201)
    def presign_uploads(request: PresignBatch):
        if sum(file.size for file in request.files) > MAX_BATCH_BYTES:
            raise HTTPException(413, 'Limit: 100 MiB per batch.')
        storage = storage_for(store.root)
        if storage.backend != 'r2':
            raise HTTPException(409, 'Direct uploads require DOLOS_STORAGE_BACKEND=r2.')
        if any(Path(file.filename).suffix.lower() not in CONTENT_TYPES for file in request.files):
            raise HTTPException(415, 'Supported file extensions: .csv, .json, .pdf')
        batch_id = uuid.uuid4().hex
        expires_at = time.time() + 900
        uploads = []
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO batches VALUES(?,?)', (batch_id, time.time()))
            for file in request.files:
                suffix = Path(file.filename).suffix.lower()
                key, url = storage.presign(file.sha256, suffix, file.size)
                upload_id = uuid.uuid4().hex
                db.execute("""INSERT INTO upload_sessions
                    (id,batch_id,filename,suffix,sha256,byte_size,bucket,object_key,expires_at)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (upload_id, batch_id, Path(file.filename).name, suffix,
                        file.sha256, file.size, storage.bucket, key, expires_at))
                uploads.append({'id': upload_id, 'filename': file.filename, 'method': 'PUT', 'url': url,
                                'headers': {'Content-Type': CONTENT_TYPES[suffix]}, 'expires_at': expires_at})
        return {'batch_id': batch_id, 'status': 'awaiting_upload', 'uploads': uploads,
                'complete_url': f'/batches/{batch_id}/complete'}

    @app.post('/batches/{batch_id}/complete', status_code=202)
    def complete_uploads(batch_id: str):
        with store.connect() as db:
            sessions = [dict(row) for row in db.execute('SELECT * FROM upload_sessions WHERE batch_id=? ORDER BY id', (batch_id,))]
        if not sessions:
            raise HTTPException(404, 'Direct-upload batch not found')
        if all(item['status'] == 'completed' for item in sessions):
            return store.batch(batch_id)
        if any(item['expires_at'] < time.time() for item in sessions if item['status'] == 'pending'):
            raise HTTPException(410, 'Upload session expired; request new upload URLs.')
        storage = storage_for(store.root, 'r2')
        prepared = []
        try:
            with ExitStack() as stack:
                # Verify every staging object before publishing any job. Downloading
                # from R2 does not put file bytes in the browser-to-API request body.
                for item in sessions:
                    if item['status'] == 'completed':
                        continue
                    location = {'bucket': item['bucket'], 'object_key': item['object_key'], 'byte_size': item['byte_size']}
                    item['path'] = stack.enter_context(storage.materialize(location, item['sha256'], item['suffix']))
                    prepared.append(item)
                for item in prepared:
                    # Republish the verified bytes under a server-only key. Replaying
                    # a still-valid browser PUT cannot modify the queued document.
                    item['location'] = storage.save(item['path'], item['sha256'], item['suffix'])
                result = publish_batch(store, batch_id, prepared, direct=True)
        except UploadNotReady as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        for item in prepared:
            try:
                storage.remove_staging(item['object_key'])
            except Exception:
                log.warning('Could not remove completed upload staging object: %s', item['id'])
        return result

    @app.get('/batches/{batch_id}')
    def batch(batch_id: str):
        result = store.batch(batch_id)
        if result is None:
            raise HTTPException(404, 'Batch not found')
        return result

    @app.get('/documents/{document_id}/errors')
    def errors(document_id: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        with store.connect() as db:
            if not db.execute('SELECT 1 FROM documents WHERE id=?', (document_id,)).fetchone():
                raise HTTPException(404, 'Document not found')
            return {'errors': [dict(r) for r in db.execute('''SELECT source_row,error FROM row_errors
                WHERE document_id=? ORDER BY source_row LIMIT ? OFFSET ?''', (document_id, limit, offset))]}

    @app.post('/documents/{document_id}/retry', status_code=202)
    def retry(document_id: str):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM documents WHERE id=?', (document_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Document not found')
            if row['status'] != 'failed':
                raise HTTPException(409, 'Only failed documents can be retried. Correct rejected rows in a new file.')
            db.execute("UPDATE documents SET status='queued',attempts=0,error=NULL,token=NULL WHERE id=?", (document_id,))
        return {'id': document_id, 'status': 'queued'}

    def select_rows(limit, offset, account, before, batch_id, source='transactions'):
        if before:
            try:
                before = timestamp(before)
            except ValueError:
                raise HTTPException(422, 'before must be an ISO 8601 timestamp')
        return getattr(store, source)(limit, offset, account, before, batch_id)

    @app.get('/transactions')
    def transactions(limit: int = Query(100, ge=1, le=10000), offset: int = Query(0, ge=0),
                     account: Optional[str] = None, before: Optional[str] = None, batch_id: Optional[str] = None):
        rows = select_rows(limit + 1, offset, account, before, batch_id)
        return {'transactions': rows[:limit], 'has_more': len(rows) > limit,
                'next_offset': offset + limit if len(rows) > limit else None}

    @app.get('/relations')
    def relations(limit: int = Query(100, ge=1, le=10000), offset: int = Query(0, ge=0),
                  account: Optional[str] = None, before: Optional[str] = None, batch_id: Optional[str] = None):
        rows = select_rows(limit + 1, offset, account, before, batch_id, source='relations')
        return {'relations': rows[:limit], 'has_more': len(rows) > limit,
                'next_offset': offset + limit if len(rows) > limit else None}

    @app.get('/graph')
    def graph(limit: int = Query(1000, ge=1, le=10000), offset: int = Query(0, ge=0),
              account: Optional[str] = None, before: Optional[str] = None, batch_id: Optional[str] = None):
        rows = select_rows(limit + 1, offset, account, before, batch_id, source='graph_rows')
        return dict(build_graph(rows[:limit]), has_more=len(rows) > limit,
                    next_offset=offset + limit if len(rows) > limit else None,
                    scope='Summaries cover only the returned page; before is exclusive.')

    @app.get('/scores')
    def scores(limit: int = Query(100, ge=1, le=1000),
               model: Literal['graph_xgboost', 'transaction_xgboost', 'graphsage', 'graphsage_no_neighbors'] = 'graph_xgboost'):
        rows = store.transactions(MAX_HISTORY + 1)
        if len(rows) > MAX_HISTORY:
            raise HTTPException(413, 'Scoring exceeds the 100,000-row in-memory history limit. Offline scoring is required.')
        try:
            return score(rows, os.environ.get('DOLOS_MODEL_DIR'), model, limit)
        except ModelUnavailable as exc:
            raise HTTPException(503, str(exc))

    return app


app = create_app()
