"""DuckDB-backed persistence for recommendation history and the interest profile.

state.duckdb is created on first run and lives outside version control.
Delete it to wipe history and reseed the profile from config.yaml.

DuckDB allows only one read-write process per file, and Claude Desktop may
run several copies of this server. Connections are therefore short-lived:
opened per call and closed immediately, with a brief retry on lock conflicts.
"""

import time
from contextlib import contextmanager
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).parent / "state.duckdb"
LOCK_RETRY_SECONDS = 10
LOCK_RETRY_INTERVAL = 0.1


@contextmanager
def session():
    deadline = time.monotonic() + LOCK_RETRY_SECONDS
    while True:
        try:
            con = duckdb.connect(str(DB_PATH))
            break
        except duckdb.IOException:
            if time.monotonic() >= deadline:
                raise
            time.sleep(LOCK_RETRY_INTERVAL)
    try:
        yield con
    finally:
        con.close()


def init(profile_blurb: str) -> None:
    with session() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS recommendations (
                repo TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                url TEXT,
                reason TEXT,
                recommended_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
                PRIMARY KEY (repo, issue_number)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS interests (
                id INTEGER PRIMARY KEY,
                notes TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT current_timestamp
            )
            """
        )
        (count,) = con.execute("SELECT count(*) FROM interests").fetchone()
        if count == 0 and profile_blurb:
            con.execute("INSERT INTO interests (id, notes) VALUES (1, ?)", [profile_blurb])


def get_interests() -> str:
    with session() as con:
        row = con.execute(
            "SELECT notes FROM interests ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return row[0] if row else ""


def update_interests(notes: str) -> None:
    with session() as con:
        con.execute(
            """
            INSERT INTO interests (id, notes, updated_at)
            VALUES (1, ?, now())
            ON CONFLICT (id) DO UPDATE SET notes = excluded.notes, updated_at = excluded.updated_at
            """,
            [notes],
        )


def record_recommendation(repo: str, issue_number: int, url: str, reason: str) -> None:
    with session() as con:
        con.execute(
            """
            INSERT INTO recommendations (repo, issue_number, url, reason)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (repo, issue_number) DO UPDATE SET
                url = excluded.url, reason = excluded.reason, recommended_at = now()
            """,
            [repo, issue_number, url, reason],
        )


def get_recommended_keys() -> set[tuple[str, int]]:
    with session() as con:
        rows = con.execute("SELECT repo, issue_number FROM recommendations").fetchall()
    return {(repo, issue_number) for repo, issue_number in rows}
