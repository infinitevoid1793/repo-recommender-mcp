import pytest

from repo_recommender.tools.state import normalize_shortlist_entries


class TestNormalizeShortlistEntries:
    def test_status_defaults_to_shortlisted(self):
        entries = normalize_shortlist_entries([{"full_name": "a/b", "reason": "fits"}])
        assert entries[0]["status"] == "shortlisted"

    @pytest.mark.parametrize("status", ["shortlisted", "interested", "dismissed", "pursued"])
    def test_valid_statuses_pass(self, status):
        entries = normalize_shortlist_entries(
            [{"full_name": "a/b", "reason": "r", "status": status}]
        )
        assert entries[0]["status"] == status

    def test_unknown_status_is_rejected_with_the_valid_options(self):
        with pytest.raises(ValueError, match="Invalid status"):
            normalize_shortlist_entries([{"full_name": "a/b", "reason": "r", "status": "maybe"}])

    def test_duplicates_are_rejected(self):
        with pytest.raises(ValueError, match="Duplicate"):
            normalize_shortlist_entries(
                [{"full_name": "a/b", "reason": "r"}, {"full_name": "a/b", "reason": "r2"}]
            )

    def test_order_is_preserved_because_it_becomes_the_rank(self):
        entries = normalize_shortlist_entries(
            [{"full_name": "a/b", "reason": ""}, {"full_name": "c/d", "reason": ""}]
        )
        assert [e["full_name"] for e in entries] == ["a/b", "c/d"]

    def test_missing_reason_is_allowed(self):
        assert normalize_shortlist_entries([{"full_name": "a/b"}])[0]["reason"] is None
