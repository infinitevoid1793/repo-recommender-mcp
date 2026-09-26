"""DuckDB-backed persistence for recommendation history and the interest profile.

state.duckdb is created on first run and lives outside version control.
Delete it to wipe history and reseed the profile from config.yaml.

DuckDB allows only one read-write process per file, and Claude Desktop may
run several copies of this server. Connections are therefore short-lived:
opened per call and closed immediately, with a brief retry on lock conflicts.
"""

import json
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
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS repos_seen (
                full_name TEXT PRIMARY KEY,
                first_seen TIMESTAMP NOT NULL DEFAULT current_timestamp,
                last_seen TIMESTAMP NOT NULL DEFAULT current_timestamp,
                times_seen INTEGER NOT NULL DEFAULT 1,
                description TEXT,
                topics TEXT,
                stars INTEGER,
                primary_language TEXT,
                pushed_at TEXT
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS repo_health_snapshots (
                full_name TEXT NOT NULL,
                fetched_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
                prs_scanned INTEGER,
                outside_prs_sampled INTEGER,
                merged INTEGER,
                closed_unmerged INTEGER,
                open_count INTEGER,
                merge_rate DOUBLE,
                median_days_to_merge DOUBLE,
                median_days_to_close_unmerged DOUBLE,
                oldest_open_days INTEGER,
                last_outside_pr_merged_at TEXT,
                low_confidence BOOLEAN
            )
            """
        )
        con.execute("CREATE SEQUENCE IF NOT EXISTS shortlist_id_seq START 1")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS shortlists (
                id INTEGER PRIMARY KEY,
                topic TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
                updated_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
                notes TEXT
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS shortlist_repos (
                shortlist_id INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                full_name TEXT NOT NULL,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'shortlisted',
                PRIMARY KEY (shortlist_id, full_name)
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


SHORTLIST_STATUSES = {"shortlisted", "interested", "dismissed", "pursued"}

HEALTH_COLUMNS = [
    "prs_scanned",
    "outside_prs_sampled",
    "merged",
    "closed_unmerged",
    "open_count",
    "merge_rate",
    "median_days_to_merge",
    "median_days_to_close_unmerged",
    "oldest_open_days",
    "last_outside_pr_merged_at",
    "low_confidence",
]


def get_seen_and_shortlisted(full_names: list[str]) -> tuple[set[str], set[str]]:
    if not full_names:
        return set(), set()
    marks = ",".join("?" for _ in full_names)
    with session() as con:
        seen = con.execute(
            f"SELECT full_name FROM repos_seen WHERE full_name IN ({marks})", full_names
        ).fetchall()
        listed = con.execute(
            f"SELECT DISTINCT full_name FROM shortlist_repos WHERE full_name IN ({marks})",
            full_names,
        ).fetchall()
    return {r[0] for r in seen}, {r[0] for r in listed}


def record_repos_seen(repos: list[dict]) -> None:
    with session() as con:
        for r in repos:
            con.execute(
                """
                INSERT INTO repos_seen
                    (full_name, description, topics, stars, primary_language, pushed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (full_name) DO UPDATE SET
                    last_seen = now(),
                    times_seen = repos_seen.times_seen + 1,
                    description = excluded.description,
                    topics = excluded.topics,
                    stars = excluded.stars,
                    primary_language = excluded.primary_language,
                    pushed_at = excluded.pushed_at
                """,
                [
                    r["full_name"],
                    r.get("description"),
                    json.dumps(r.get("topics", [])),
                    r.get("stars"),
                    r.get("primary_language"),
                    r.get("pushed_at"),
                ],
            )


def _latest_health(con, full_name: str) -> dict | None:
    row = con.execute(
        f"""
        SELECT fetched_at, {", ".join(HEALTH_COLUMNS)} FROM repo_health_snapshots
        WHERE full_name = ? ORDER BY fetched_at DESC LIMIT 1
        """,
        [full_name],
    ).fetchone()
    if not row:
        return None
    snapshot = dict(zip(["fetched_at"] + HEALTH_COLUMNS, row))
    snapshot["fetched_at"] = snapshot["fetched_at"].isoformat()
    return snapshot


def get_latest_health_snapshot(full_name: str) -> dict | None:
    with session() as con:
        return _latest_health(con, full_name)


def save_health_snapshot(full_name: str, metrics: dict) -> None:
    with session() as con:
        con.execute(
            f"""
            INSERT INTO repo_health_snapshots (full_name, {", ".join(HEALTH_COLUMNS)})
            VALUES (?, {", ".join("?" for _ in HEALTH_COLUMNS)})
            """,
            [full_name] + [metrics.get(c) for c in HEALTH_COLUMNS],
        )


def save_shortlist(
    topic: str, repos: list[dict], shortlist_id: int | None, notes: str | None
) -> int:
    with session() as con:
        con.execute("BEGIN")
        try:
            if shortlist_id is None:
                (shortlist_id,) = con.execute("SELECT nextval('shortlist_id_seq')").fetchone()
                con.execute(
                    "INSERT INTO shortlists (id, topic, notes) VALUES (?, ?, ?)",
                    [shortlist_id, topic, notes],
                )
            else:
                exists = con.execute(
                    "SELECT count(*) FROM shortlists WHERE id = ?", [shortlist_id]
                ).fetchone()[0]
                if not exists:
                    raise ValueError(f"No shortlist with id {shortlist_id}")
                con.execute(
                    "UPDATE shortlists SET topic = ?, notes = coalesce(?, notes), "
                    "updated_at = now() WHERE id = ?",
                    [topic, notes, shortlist_id],
                )
                con.execute("DELETE FROM shortlist_repos WHERE shortlist_id = ?", [shortlist_id])
            for rank, r in enumerate(repos, start=1):
                con.execute(
                    "INSERT INTO shortlist_repos (shortlist_id, rank, full_name, reason, status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [shortlist_id, rank, r["full_name"], r.get("reason"), r["status"]],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return shortlist_id


def get_shortlists(topic: str | None, limit: int) -> list[dict]:
    with session() as con:
        if topic:
            heads = con.execute(
                "SELECT id, topic, created_at, updated_at, notes FROM shortlists "
                "WHERE lower(topic) LIKE ? ORDER BY updated_at DESC LIMIT ?",
                [f"%{topic.lower()}%", limit],
            ).fetchall()
        else:
            heads = con.execute(
                "SELECT id, topic, created_at, updated_at, notes FROM shortlists "
                "ORDER BY updated_at DESC LIMIT ?",
                [limit],
            ).fetchall()
        result = []
        for sid, t, created, updated, notes in heads:
            rows = con.execute(
                "SELECT rank, full_name, reason, status FROM shortlist_repos "
                "WHERE shortlist_id = ? ORDER BY rank",
                [sid],
            ).fetchall()
            result.append(
                {
                    "shortlist_id": sid,
                    "topic": t,
                    "created_at": created.isoformat(),
                    "updated_at": updated.isoformat(),
                    "notes": notes,
                    "repos": [
                        {
                            "rank": rk,
                            "full_name": fn,
                            "reason": rs,
                            "status": st,
                            "latest_health": _latest_health(con, fn),
                        }
                        for rk, fn, rs, st in rows
                    ],
                }
            )
    return result
