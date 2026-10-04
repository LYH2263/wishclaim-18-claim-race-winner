import pytest

@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Isolated sqlite file per test; db_path() reads DATA_DIR lazily."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import seed
    seed.init_db()
    return tmp_path
