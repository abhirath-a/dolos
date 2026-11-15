"""Small DB-API adapter for local SQLite and direct-to-primary libSQL/Turso."""
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Row(dict):
    """Named rows with positional lookup, independent of driver row_factory support."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.columns = [column[0] for column in (cursor.description or [])]

    def fetchone(self):
        row = self.cursor.fetchone()
        return None if row is None else Row(zip(self.columns, row))

    def fetchall(self):
        return [Row(zip(self.columns, row)) for row in self.cursor.fetchall()]

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class Connection:
    def __init__(self, connection, retry_locked=False):
        self.raw = connection
        self.retry_locked = retry_locked

    def execute(self, sql, parameters=()):
        deadline = time.monotonic() + 30
        while True:
            try:
                return Cursor(self.raw.execute(sql, parameters))
            except (ValueError, sqlite3.OperationalError) as exc:
                locked = 'database is locked' in str(exc).lower() or 'database table is locked' in str(exc).lower()
                if not self.retry_locked or not locked or time.monotonic() >= deadline:
                    raise
                # Native libsql may hold the GIL while its busy handler waits.
                # Retry in Python so the thread owning the write lock can commit.
                time.sleep(.01)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


class Database:
    def __init__(self, url=None, driver=None):
        self.url = url or os.environ.get('DOLOS_DATABASE_URL') or os.environ.get('TURSO_DATABASE_URL') or 'file:./var/dolos.sqlite3'
        parsed = urlsplit(self.url)
        self.remote = parsed.scheme in {'libsql', 'https', 'http'}
        if parsed.scheme and parsed.scheme not in {'file', 'libsql', 'https', 'http'}:
            raise ValueError('Database URL must be a local path, file: URL, or libsql/HTTPS URL.')
        self.driver = driver or os.environ.get('DOLOS_DATABASE_DRIVER') or ('libsql' if self.remote else 'sqlite')
        if self.driver not in {'sqlite', 'libsql'} or (self.remote and self.driver != 'libsql'):
            raise ValueError('Remote databases require the libsql driver; local drivers are sqlite or libsql.')
        self.path = None
        if not self.remote:
            if parsed.netloc or parsed.query or parsed.fragment or parsed.path == ':memory:':
                raise ValueError('Use a persistent local database file without URL query parameters.')
            self.path = Path(unquote(parsed.path) if parsed.scheme == 'file' else self.url).expanduser().resolve()
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self):
        if self.driver == 'sqlite':
            raw = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        else:
            try:
                import libsql
            except ImportError as exc:
                raise RuntimeError('Install dolos[turso] to use the libsql driver.') from exc
            options = {'isolation_level': None}
            if self.remote:
                token = os.environ.get('TURSO_AUTH_TOKEN')
                if token:
                    options['auth_token'] = token
            raw = libsql.connect(self.url if self.remote else str(self.path), **options)
        connection = Connection(raw, retry_locked=self.driver == 'libsql' and not self.remote)
        if not self.remote:
            connection.execute('PRAGMA busy_timeout=' + ('0' if self.driver == 'libsql' else '30000'))
        return connection
