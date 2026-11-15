"""Separate durable queue worker: python -m dolos.worker."""
import argparse
import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager

from .parsers import DocumentError, parse_document
from .store import Store
from .storage import storage_for, StorageError

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
LEASE_SECONDS = 300
HEARTBEAT_SECONDS = 30


def claim(store):
    now = time.time()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("""UPDATE documents SET status='failed',error='Worker lease expired after maximum attempts'
            WHERE status='processing' AND lease_until<? AND attempts>=?""", (now, MAX_ATTEMPTS))
        row = db.execute("""SELECT * FROM documents WHERE attempts<? AND
            (status='queued' OR (status='processing' AND lease_until<?)) ORDER BY rowid LIMIT 1""",
                         (MAX_ATTEMPTS, now)).fetchone()
        if row is None:
            return None
        token = uuid.uuid4().hex
        db.execute("""UPDATE documents SET status='processing',attempts=attempts+1,
            lease_until=?,token=?,error=NULL WHERE id=?""", (now + LEASE_SECONDS, token, row['id']))
        return dict(row, token=token)


@contextmanager
def heartbeat(store, job):
    """Keep long PDF/LLM jobs leased while retaining commit fencing after replacement."""
    stop = threading.Event()

    def renew():
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                with store.connect() as db:
                    db.execute("""UPDATE documents SET lease_until=?
                        WHERE id=? AND token=? AND status='processing'""",
                               (time.time() + LEASE_SECONDS, job['id'], job['token']))
            except Exception:
                log.warning('Could not renew document lease: %s', job['id'])

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def process_one(store):
    job = claim(store)
    if job is None:
        return False
    # Spool before taking the database write lock. A failed parser never leaves partial edges.
    import tempfile
    try:
        location = store.object_location(job['id'])
        storage = storage_for(store.root, location['backend'] if location else 'local')
        with heartbeat(store, job), storage.materialize(location, job['sha256'], job['suffix']) as path, tempfile.TemporaryFile(mode='w+t', encoding='utf-8') as staged:
            accepted = rejected = 0
            review_required = False
            for index, row, error in parse_document(path, job['suffix']):
                staged.write(json.dumps([index, row, error]) + '\n')
                accepted += row is not None
                rejected += error is not None
                review_required = review_required or bool(row and row.get('review_required'))
            if not accepted and not rejected:
                raise DocumentError('Statement contains no transaction rows.')
            staged.seek(0)
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT token,status FROM documents WHERE id=?', (job['id'],)).fetchone()
                if current['token'] != job['token'] or current['status'] != 'processing':
                    return True  # A replacement worker owns the lease; fence this result.
                for line in staged:
                    index, row, error = json.loads(line)
                    if error:
                        db.execute('INSERT OR REPLACE INTO row_errors VALUES(?,?,?)', (job['id'], index, error))
                    else:
                        tx_id = f"{job['id']}:{index}"
                        table = 'relations' if row.get('record_type') == 'relation' else 'transactions'
                        db.execute('INSERT OR IGNORE INTO ' + table + ' VALUES(?,?,?,?,?,?,?)',
                                   (tx_id, job['id'], index, row['timestamp'], row['sender_id'],
                                    row['receiver_id'], json.dumps(row)))
                db.execute('''UPDATE documents SET status=?,accepted=?,rejected=?,lease_until=NULL
                    WHERE id=? AND token=?''', ('needs_review' if rejected or review_required else 'completed',
                                              accepted, rejected, job['id'], job['token']))
    except Exception as exc:
        log.exception('Document processing failed: %s', job['id'])
        # No source text or account data is returned in public diagnostics.
        error = str(exc) if isinstance(exc, (DocumentError, StorageError)) else 'Parser failed; check encoding/format and worker logs.'
        with store.connect() as db:
            db.execute("""UPDATE documents SET status='failed',error=?,lease_until=NULL
                WHERE id=? AND token=? AND status='processing'""", (error, job['id'], job['token']))
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Process at most one document and exit')
    parser.add_argument('--poll-seconds', type=float, default=1)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    store = Store()
    while True:
        found = process_one(store)
        if args.once:
            return
        if not found:
            time.sleep(max(.1, args.poll_seconds))


if __name__ == '__main__':
    main()
