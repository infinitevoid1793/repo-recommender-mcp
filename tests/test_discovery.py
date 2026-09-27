from datetime import UTC, datetime, timedelta

from repo_recommender.tools.discovery import (
    MAX_TOPICS,
    build_issue_query,
    build_repo_queries,
    rank_repos,
)

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def days_ago(n: int) -> str:
    return (NOW - timedelta(days=n)).isoformat().replace("+00:00", "Z")


class TestBuildIssueQuery:
    def test_always_scopes_to_open_issues(self):
        assert build_issue_query(None, None, None, None) == "is:issue is:open"

    def test_quotes_multi_word_labels(self):
        query = build_issue_query(["good first issue"], None, None, None)
        assert 'label:"good first issue"' in query

    def test_falls_back_to_configured_seeds(self):
        query = build_issue_query(None, None, None, None, default_seed_repos=["a/b"])
        assert "repo:a/b" in query

    def test_explicit_seeds_override_the_default(self):
        query = build_issue_query(None, None, ["x/y"], None, default_seed_repos=["a/b"])
        assert "repo:x/y" in query and "repo:a/b" not in query

    def test_empty_seed_list_searches_all_of_github(self):
        """An empty list is the documented way to opt out of the seed list."""
        query = build_issue_query(None, None, [], None, default_seed_repos=["a/b"])
        assert "repo:" not in query


class TestBuildRepoQueries:
    def test_one_query_per_topic_so_topics_are_ored(self):
        queries = build_repo_queries(["soccer", "football"], None, None, 10, "2026-01-01")
        assert len(queries) == 2
        assert "topic:soccer" in queries[0] and "topic:football" in queries[1]

    def test_caps_topics_to_bound_the_wait(self):
        queries = build_repo_queries([f"t{i}" for i in range(9)], None, None, 10, "2026-01-01")
        assert len(queries) == MAX_TOPICS

    def test_every_query_excludes_archived_and_forks(self):
        for query in build_repo_queries(["a", "b"], None, None, 5, "2026-01-01"):
            assert "archived:false" in query
            assert "fork:false" in query
            assert "stars:>=5" in query
            assert "pushed:>=2026-01-01" in query

    def test_works_with_keywords_and_no_topics(self):
        queries = build_repo_queries(None, "expected goals", ["python"], 10, "2026-01-01")
        assert len(queries) == 1
        assert "topic:" not in queries[0]
        assert "expected goals" in queries[0]
        assert "language:python" in queries[0]


class TestRankRepos:
    def test_recent_beats_stale_at_equal_stars(self):
        repos = [
            {"full_name": "stale/repo", "stars": 100, "pushed_at": days_ago(300)},
            {"full_name": "fresh/repo", "stars": 100, "pushed_at": days_ago(1)},
        ]
        assert rank_repos(repos, 360, now=NOW)[0]["full_name"] == "fresh/repo"

    def test_popular_beats_obscure_at_equal_recency(self):
        repos = [
            {"full_name": "obscure/repo", "stars": 10, "pushed_at": days_ago(5)},
            {"full_name": "popular/repo", "stars": 10000, "pushed_at": days_ago(5)},
        ]
        assert rank_repos(repos, 360, now=NOW)[0]["full_name"] == "popular/repo"

    def test_stars_are_log_scaled_so_giants_do_not_crowd_out_niche_repos(self):
        """A far more active niche repo should outrank a huge stale one."""
        repos = [
            {"full_name": "giant/stale", "stars": 200000, "pushed_at": days_ago(340)},
            {"full_name": "niche/active", "stars": 30, "pushed_at": days_ago(1)},
        ]
        assert rank_repos(repos, 360, now=NOW)[0]["full_name"] == "niche/active"

    def test_empty_input(self):
        assert rank_repos([], 360, now=NOW) == []

    def test_zero_stars_does_not_blow_up(self):
        repos = [{"full_name": "a/b", "stars": 0, "pushed_at": days_ago(10)}]
        assert rank_repos(repos, 360, now=NOW)[0]["full_name"] == "a/b"

    def test_does_not_leak_the_internal_score(self):
        repos = [{"full_name": "a/b", "stars": 5, "pushed_at": days_ago(10)}]
        assert "_score" not in rank_repos(repos, 360, now=NOW)[0]
