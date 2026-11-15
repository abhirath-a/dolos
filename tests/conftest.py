import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Uploads and database configuration are independent, including in tests."""
    monkeypatch.setenv('DOLOS_DATABASE_URL', 'file:' + str(tmp_path / 'database' / 'dolos.sqlite3'))
    monkeypatch.setenv('DOLOS_DATABASE_DRIVER', 'sqlite')
    monkeypatch.setenv('DOLOS_STORAGE_BACKEND', 'local')
    monkeypatch.delenv('TURSO_DATABASE_URL', raising=False)
    monkeypatch.delenv('TURSO_AUTH_TOKEN', raising=False)


def pytest_configure(config):
    # api.app is built during test collection, before autouse fixtures run.
    # Keep collection away from any deployment database configured in the shell.
    config._dolos_collection_dir = tempfile.TemporaryDirectory(prefix='dolos-test-collection-')
    root = Path(config._dolos_collection_dir.name)
    overrides = {'DOLOS_DATABASE_URL': 'file:' + str(root / 'database.sqlite3'),
                 'DOLOS_DATABASE_DRIVER': 'sqlite', 'DOLOS_DATA_DIR': str(root / 'uploads'),
                 'DOLOS_STORAGE_BACKEND': 'local'}
    config._dolos_original_environment = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)


def pytest_unconfigure(config):
    for key, value in getattr(config, '_dolos_original_environment', {}).items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    temporary = getattr(config, '_dolos_collection_dir', None)
    if temporary:
        temporary.cleanup()
