"""Database round-trips against a temp DuckDB file per test."""


class TestInterests:
    def test_seeded_from_config_on_first_run(self, temp_db):
        assert temp_db.get_interests() == "seeded profile"

    def test_update_replaces_the_profile(self, temp_db):
        temp_db.update_interests("new profile")
        assert temp_db.get_interests() == "new profile"

    def test_init_does_not_reseed_over_an_updated_profile(self, temp_db):
        temp_db.update_interests("mine")
        temp_db.init("seeded profile")
        assert temp_db.get_interests() == "mine"


class TestRecommendations:
    def test_recorded_issues_are_returned_for_dedup(self, temp_db):
        temp_db.record_recommendation("a/b", 1, "http://x", "fits")
        assert ("a/b", 1) in temp_db.get_recommended_keys()

    def test_starts_empty(self, temp_db):
        assert temp_db.get_recommended_keys() == set()

    def test_recording_the_same_issue_twice_does_not_duplicate(self, temp_db):
        temp_db.record_recommendation("a/b", 1, "http://x", "first")
        temp_db.record_recommendation("a/b", 1, "http://x", "second")
        assert len(temp_db.get_recommended_keys()) == 1

    def test_same_number_in_different_repos_are_distinct(self, temp_db):
        temp_db.record_recommendation("a/b", 1, "http://x", "r")
        temp_db.record_recommendation("c/d", 1, "http://y", "r")
        assert len(temp_db.get_recommended_keys()) == 2


class TestReposSeen:
    repo = {
        "full_name": "a/b",
        "description": "d",
        "topics": ["t"],
        "stars": 5,
        "primary_language": "Python",
        "pushed_at": "2026-09-01T00:00:00Z",
    }

    def test_first_sighting_is_not_flagged_as_seen(self, temp_db):
        seen, shortlisted = temp_db.get_seen_and_shortlisted(["a/b"])
        assert seen == set() and shortlisted == set()

    def test_second_sighting_is_flagged(self, temp_db):
        temp_db.record_repos_seen([self.repo])
        seen, _ = temp_db.get_seen_and_shortlisted(["a/b"])
        assert seen == {"a/b"}

    def test_empty_lookup_avoids_a_malformed_query(self, temp_db):
        assert temp_db.get_seen_and_shortlisted([]) == (set(), set())


class TestHealthSnapshots:
    metrics = {"merge_rate": 0.5, "outside_prs_sampled": 20, "low_confidence": False}

    def test_no_snapshot_initially(self, temp_db):
        assert temp_db.get_latest_health_snapshot("a/b") is None

    def test_latest_snapshot_is_returned(self, temp_db):
        temp_db.save_health_snapshot("a/b", self.metrics)
        snapshot = temp_db.get_latest_health_snapshot("a/b")
        assert snapshot["merge_rate"] == 0.5
        assert snapshot["fetched_at"]

    def test_snapshots_accumulate_rather_than_overwrite(self, temp_db):
        """History is the point: trends need every reading kept."""
        temp_db.save_health_snapshot("a/b", self.metrics)
        temp_db.save_health_snapshot("a/b", {**self.metrics, "merge_rate": 0.9})
        with temp_db.session() as con:
            count = con.execute(
                "SELECT count(*) FROM repo_health_snapshots WHERE full_name = 'a/b'"
            ).fetchone()[0]
        assert count == 2
        assert temp_db.get_latest_health_snapshot("a/b")["merge_rate"] == 0.9


class TestShortlists:
    entries = [
        {"full_name": "a/b", "reason": "first", "status": "shortlisted"},
        {"full_name": "c/d", "reason": "second", "status": "interested"},
    ]

    def test_save_and_read_back_with_ranks_in_order(self, temp_db):
        sid = temp_db.save_shortlist("soccer", self.entries, None, "notes")
        lists = temp_db.get_shortlists(None, 5)
        assert len(lists) == 1
        assert lists[0]["shortlist_id"] == sid
        assert [(r["rank"], r["full_name"]) for r in lists[0]["repos"]] == [(1, "a/b"), (2, "c/d")]

    def test_topic_filter_is_case_insensitive_substring(self, temp_db):
        temp_db.save_shortlist("soccer analytics", self.entries, None, None)
        assert len(temp_db.get_shortlists("SOCCER", 5)) == 1
        assert temp_db.get_shortlists("kaggle", 5) == []

    def test_resaving_with_an_id_replaces_the_entries(self, temp_db):
        sid = temp_db.save_shortlist("soccer", self.entries, None, None)
        temp_db.save_shortlist(
            "soccer", [{"full_name": "c/d", "reason": "now first", "status": "pursued"}], sid, None
        )
        lists = temp_db.get_shortlists(None, 5)
        assert len(lists) == 1
        assert [(r["full_name"], r["status"]) for r in lists[0]["repos"]] == [("c/d", "pursued")]

    def test_unknown_id_is_rejected_rather_than_silently_creating_a_list(self, temp_db):
        import pytest

        with pytest.raises(ValueError, match="No shortlist with id"):
            temp_db.save_shortlist("x", self.entries, 999, None)

    def test_shortlisted_repos_are_flagged_in_search_results(self, temp_db):
        temp_db.save_shortlist("soccer", self.entries, None, None)
        _, shortlisted = temp_db.get_seen_and_shortlisted(["a/b", "zz/zz"])
        assert shortlisted == {"a/b"}

    def test_each_repo_carries_its_latest_health_snapshot(self, temp_db):
        temp_db.save_shortlist("soccer", self.entries, None, None)
        temp_db.save_health_snapshot("a/b", {"merge_rate": 0.4})
        repos = {r["full_name"]: r for r in temp_db.get_shortlists(None, 5)[0]["repos"]}
        assert repos["a/b"]["latest_health"]["merge_rate"] == 0.4
        assert repos["c/d"]["latest_health"] is None

    def test_limit_returns_most_recently_updated_first(self, temp_db):
        temp_db.save_shortlist("first", self.entries, None, None)
        second = temp_db.save_shortlist("second", self.entries, None, None)
        lists = temp_db.get_shortlists(None, 1)
        assert len(lists) == 1 and lists[0]["shortlist_id"] == second
