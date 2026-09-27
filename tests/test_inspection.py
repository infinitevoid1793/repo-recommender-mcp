from datetime import UTC, datetime, timedelta

import pytest

from repo_recommender.tools.inspection import (
    MAX_FILE_LINES,
    is_outside_pr,
    looks_ai_authored,
    slice_lines,
    summarize_feasibility,
    summarize_pr_health,
)

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def stamp(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def pr(state="open", created=10, merged=None, closed=None, draft=False):
    return {
        "state": state,
        "created_at": stamp(created),
        "merged_at": stamp(merged) if merged is not None else None,
        "closed_at": stamp(closed) if closed is not None else None,
        "draft": draft,
    }


class TestIsOutsidePr:
    @pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
    def test_maintainers_are_excluded(self, association):
        assert not is_outside_pr({"user": {"login": "x"}, "author_association": association})

    @pytest.mark.parametrize("association", ["CONTRIBUTOR", "NONE", "FIRST_TIME_CONTRIBUTOR"])
    def test_outsiders_are_counted(self, association):
        assert is_outside_pr({"user": {"login": "x"}, "author_association": association})

    def test_bots_are_excluded_by_login(self):
        assert not is_outside_pr(
            {"user": {"login": "dependabot[bot]"}, "author_association": "NONE"}
        )

    def test_bots_are_excluded_by_type(self):
        assert not is_outside_pr(
            {"user": {"login": "somebot", "type": "Bot"}, "author_association": "NONE"}
        )

    def test_missing_user_is_excluded(self):
        assert not is_outside_pr({"user": None, "author_association": "NONE"})


class TestSummarizePrHealth:
    def test_merge_rate_ignores_still_open_prs(self):
        """Open PRs aren't decided yet, so they must not drag the rate down."""
        sample = [
            pr(state="closed", created=20, merged=18),
            pr(state="closed", created=20, merged=18),
            pr(state="closed", created=20, closed=19),
            pr(state="open", created=5),
        ]
        result = summarize_pr_health(sample, scanned=30, now=NOW)
        assert result["merge_rate"] == 0.67
        assert (result["merged"], result["closed_unmerged"], result["open_count"]) == (2, 1, 1)

    def test_no_decided_prs_gives_no_rate_rather_than_zero(self):
        result = summarize_pr_health([pr(state="open")], scanned=5, now=NOW)
        assert result["merge_rate"] is None

    def test_medians_use_the_right_end_timestamp(self):
        sample = [
            pr(state="closed", created=30, merged=20),
            pr(state="closed", created=10, closed=8),
        ]
        result = summarize_pr_health(sample, scanned=2, now=NOW)
        assert result["median_days_to_merge"] == 10.0
        assert result["median_days_to_close_unmerged"] == 2.0

    def test_low_confidence_below_ten_outside_prs(self):
        assert summarize_pr_health([pr()] * 9, scanned=9, now=NOW)["low_confidence"] is True
        assert summarize_pr_health([pr()] * 10, scanned=10, now=NOW)["low_confidence"] is False

    def test_empty_sample_reports_nothing_rather_than_inventing_numbers(self):
        result = summarize_pr_health([], scanned=0, now=NOW)
        assert result["merge_rate"] is None
        assert result["oldest_open_days"] is None
        assert result["last_outside_pr_merged_at"] is None
        assert result["low_confidence"] is True

    def test_oldest_open_days_uses_the_oldest(self):
        sample = [pr(state="open", created=3), pr(state="open", created=400)]
        assert summarize_pr_health(sample, scanned=2, now=NOW)["oldest_open_days"] == 400

    def test_last_merge_is_the_most_recent(self):
        sample = [
            pr(state="closed", created=100, merged=90),
            pr(state="closed", created=20, merged=2),
        ]
        result = summarize_pr_health(sample, scanned=2, now=NOW)
        assert result["last_outside_pr_merged_at"] == stamp(2)


class TestLooksAiAuthored:
    def test_bot_login(self):
        assert looks_ai_authored("devin-ai-integration[bot]", "")

    def test_marker_in_body(self):
        assert looks_ai_authored("someone", "Generated with an agent")

    def test_plain_human_pr(self):
        assert not looks_ai_authored("sharang", "Fixes the off-by-one in the parser.")

    def test_marker_late_in_a_long_body_is_ignored(self):
        """Only the top of the body is checked, so a passing mention deep in a
        long description doesn't flag a human PR."""
        assert not looks_ai_authored("human", "x" * 2000 + " devin")

    def test_empty_body(self):
        assert not looks_ai_authored("human", "")


class TestSliceLines:
    text = "\n".join(f"line{i}" for i in range(1, 1201))

    def test_whole_small_file(self):
        result = slice_lines("a\nb\nc", None, None)
        assert result["total_lines"] == 3
        assert result["content"] == "a\nb\nc"
        assert result["truncated"] is False

    def test_caps_long_files_and_flags_truncation(self):
        result = slice_lines(self.text, None, None)
        assert result["total_lines"] == 1200
        assert result["end_line"] == MAX_FILE_LINES
        assert result["truncated"] is True

    def test_paging_with_a_later_start_line(self):
        result = slice_lines(self.text, 501, None)
        assert (result["start_line"], result["end_line"]) == (501, 1000)
        assert result["content"].startswith("line501")

    def test_last_page_is_not_truncated(self):
        result = slice_lines(self.text, 1001, None)
        assert (result["start_line"], result["end_line"]) == (1001, 1200)
        assert result["truncated"] is False

    def test_inclusive_range(self):
        result = slice_lines(self.text, 10, 12)
        assert result["content"] == "line10\nline11\nline12"

    def test_range_wider_than_the_cap_is_capped(self):
        result = slice_lines(self.text, 1, 900)
        assert result["end_line"] == MAX_FILE_LINES
        assert result["truncated"] is True

    def test_end_line_past_the_file_is_clamped(self):
        result = slice_lines("a\nb", 1, 99)
        assert result["end_line"] == 2

    @pytest.mark.parametrize(
        "start,end",
        [(0, None), (-5, None), (5, 2)],
    )
    def test_invalid_ranges_are_rejected(self, start, end):
        with pytest.raises(ValueError):
            slice_lines(self.text, start, end)

    def test_start_past_end_of_file_is_rejected(self):
        with pytest.raises(ValueError, match="past the end"):
            slice_lines("a\nb", 99, None)


class TestSummarizeFeasibility:
    def feas_pr(self, state, draft=False, created=1):
        return {"state": state, "draft": draft, "created_at": stamp(created)}

    def test_no_prs_is_uncontested(self):
        result = summarize_feasibility([], stamp(5))
        assert result["contested"] is False
        assert result["being_raced"] is False
        assert result["time_to_first_pr_hours"] is None

    def test_one_open_pr_is_contested_but_not_raced(self):
        result = summarize_feasibility([self.feas_pr("open")], stamp(5))
        assert result["contested"] is True
        assert result["being_raced"] is False

    def test_two_open_prs_is_being_raced(self):
        prs = [self.feas_pr("open"), self.feas_pr("open")]
        assert summarize_feasibility(prs, stamp(5))["being_raced"] is True

    def test_multiple_drafts_count_as_being_raced(self):
        """Drafts signal a race even though none is ready for review."""
        prs = [self.feas_pr("open", draft=True), self.feas_pr("open", draft=True)]
        result = summarize_feasibility(prs, stamp(5))
        assert result["being_raced"] is True
        assert result["contested"] is False
        assert result["pr_counts"]["open_draft"] == 2

    def test_merged_pr_is_visible_even_though_not_contested(self):
        """A merged PR on a still-open issue must not hide behind contested."""
        result = summarize_feasibility([self.feas_pr("merged")], stamp(5))
        assert result["contested"] is False
        assert result["pr_counts"]["merged"] == 1

    def test_rejected_pr_is_counted_separately(self):
        result = summarize_feasibility([self.feas_pr("closed")], stamp(5))
        assert result["pr_counts"]["closed_unmerged"] == 1
        assert result["contested"] is False

    def test_time_to_first_pr_in_hours(self):
        issue_created = "2026-09-24T03:15:32Z"
        prs = [{"state": "open", "draft": False, "created_at": "2026-09-24T04:15:32Z"}]
        assert summarize_feasibility(prs, issue_created)["time_to_first_pr_hours"] == 1.0

    def test_minutes_survive_rounding(self):
        """The motivating case was a PR 66 seconds after filing."""
        prs = [{"state": "open", "draft": False, "created_at": "2026-09-24T03:16:38Z"}]
        result = summarize_feasibility(prs, "2026-09-24T03:15:32Z")
        assert result["time_to_first_pr_hours"] == 0.02
