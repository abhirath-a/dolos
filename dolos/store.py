import json
import os
from contextlib import contextmanager
from pathlib import Path

from .database import Database


class Store:
    def __init__(self, root=None, database_url=None, driver=None, initialize=True):
        # Uploaded files are independent of the local/remote database location.
        self.root = Path(root or os.environ.get('DOLOS_DATA_DIR', 'data')).resolve()
        self.database = Database(database_url, driver)
        if initialize:
            from .migrations import apply_migrations
            with self.connect() as db:
                if not self.database.remote:
                    db.execute('PRAGMA journal_mode=WAL')
                # Create schema without seeding historical data on worker startup.
                apply_migrations(db, include_data=False)

    @contextmanager
    def connect(self):
        db = self.database.connect()
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def batch(self, batch_id):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM batches WHERE id=?', (batch_id,)).fetchone():
                return None
            docs = [dict(r) for r in db.execute('''SELECT d.id,d.filename,d.status,d.attempts,
                d.error,d.accepted,d.rejected FROM documents d JOIN batch_documents b
                ON b.document_id=d.id WHERE b.batch_id=? ORDER BY d.id''', (batch_id,))]
        with self.connect() as db:
            pending = db.execute("SELECT COUNT(*) FROM upload_sessions WHERE batch_id=? AND status='pending'", (batch_id,)).fetchone()[0]
        if pending:
            return {'id': batch_id, 'status': 'awaiting_upload', 'documents': docs, 'pending_uploads': pending}
        terminal = {'completed', 'needs_review', 'failed'}
        status = 'processing' if any(d['status'] == 'processing' for d in docs) else 'queued'
        if all(d['status'] in terminal for d in docs):
            status = 'completed' if all(d['status'] == 'completed' for d in docs) else 'needs_review'
        return {'id': batch_id, 'status': status, 'documents': docs}

    def object_location(self, document_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM document_objects WHERE document_id=?', (document_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def record_object(db, document_id, location):
        db.execute('INSERT INTO document_objects VALUES(?,?,?,?,?)',
                   (document_id, location['backend'], location['bucket'], location['object_key'], location['byte_size']))

    def transactions(self, limit=1000, offset=0, account=None, before=None, batch_id=None):
        return self._rows('transactions', limit, offset, account, before, batch_id)

    def relations(self, limit=1000, offset=0, account=None, before=None, batch_id=None):
        return self._rows('relations', limit, offset, account, before, batch_id)

    def graph_rows(self, limit=1000, offset=0, account=None, before=None, batch_id=None):
        return self._rows('(SELECT * FROM transactions UNION ALL SELECT * FROM relations)',
                          limit, offset, account, before, batch_id)

    def _rows(self, source, limit, offset, account, before, batch_id):
        clauses, args = [], []
        if account:
            clauses.append('(sender_id=? OR receiver_id=?)')
            args.extend([account, account])
        if before:
            clauses.append('timestamp<?')
            args.append(before)
        if batch_id:
            clauses.append('document_id IN (SELECT document_id FROM batch_documents WHERE batch_id=?)')
            args.append(batch_id)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.connect() as db:
            rows = db.execute('SELECT * FROM ' + source + where +
                              ' ORDER BY timestamp,id LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
        return [dict(json.loads(r['payload']), id=r['id'], document_id=r['document_id'],
                     source_row=r['source_row']) for r in rows]
