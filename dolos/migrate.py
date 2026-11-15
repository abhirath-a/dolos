"""Apply schema and data migrations to the configured local or remote database."""
import argparse
import json

from .migrations import apply_migrations
from .store import Store


def migrate(data_dir=None, database_url=None, driver=None):
    store = Store(data_dir, database_url, driver, initialize=False)
    with store.connect() as db:
        if not store.database.remote:
            db.execute('PRAGMA journal_mode=WAL')
        applied = apply_migrations(db)
        migrations = [dict(row) for row in db.execute('SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version')]
    return {'applied': applied, 'migrations': migrations, 'driver': store.database.driver,
            'database': 'remote' if store.database.remote else str(store.database.path),
            'uploads_dir': str(store.root / 'uploads')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', help='Override DOLOS_DATABASE_URL / TURSO_DATABASE_URL')
    parser.add_argument('--data-dir', help='Override uploaded-file directory (independent of database)')
    args = parser.parse_args()
    print(json.dumps(migrate(args.data_dir, args.database_url), indent=2))


if __name__ == '__main__':
    main()
