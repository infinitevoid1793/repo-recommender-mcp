import pytest

from repo_recommender import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the database at a fresh file per test. DuckDB allows one
    read-write process per file, so tests must never share one."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.duckdb")
    db.init("seeded profile")
    return db
