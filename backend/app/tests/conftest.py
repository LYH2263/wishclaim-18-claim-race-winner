import os

import pytest


@pytest.fixture()
def db_dir(tmp_path, monkeypatch):
    """Point DATA_DIR at an isolated temp DB for each test."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import seed
    seed.init_db()
    return tmp_path
