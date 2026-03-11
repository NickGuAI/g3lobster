"""Unit tests for the standup trend analysis module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from g3lobster.standup.store import StandupEntry, StandupStore
from g3lobster.standup.trends import TrendAnalyzer


AGENT_ID = "test-agent"


def _make_entry(
    user_id: str,
    date: str,
    response: str = "daily update",
    blockers: list[str] | None = None,
    display_name: str | None = None,
) -> StandupEntry:
    return StandupEntry(
        user_id=user_id,
        display_name=display_name or user_id,
        date=date,
        response=response,
        blockers=blockers or [],
    )


def _recent_date(days_ago: int) -> str:
    """Return an ISO date string for *days_ago* days before today (UTC)."""
    return (datetime.now(tz=timezone.utc).date() - timedelta(days=days_ago)).isoformat()


# ------------------------------------------------------------------
# Tokenizer & similarity helpers
# ------------------------------------------------------------------


def test_tokenize(tmp_path):
    store = StandupStore(str(tmp_path))
    analyzer = TrendAnalyzer(store)

    tokens = analyzer._tokenize("The CI/CD pipeline, is BLOCKED!")
    # Should be lowercased, punctuation stripped, stop words removed
    assert "the" not in tokens
    assert "is" not in tokens
    assert "cicd" in tokens
    assert "pipeline" in tokens
    assert "blocked" in tokens


def test_jaccard_similarity(tmp_path):
    store = StandupStore(str(tmp_path))
    analyzer = TrendAnalyzer(store)

    # Exact match
    assert analyzer._jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0

    # No overlap
    assert analyzer._jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0

    # Partial overlap: intersection={a}, union={a,b,c} -> 1/3
    result = analyzer._jaccard_similarity({"a", "b"}, {"a", "c"})
    assert abs(result - 1.0 / 3.0) < 1e-9


def test_jaccard_similarity_empty_sets(tmp_path):
    store = StandupStore(str(tmp_path))
    analyzer = TrendAnalyzer(store)

    assert analyzer._jaccard_similarity(set(), set()) == 0.0


# ------------------------------------------------------------------
# Blocker analysis
# ------------------------------------------------------------------


def test_analyze_blockers_no_data(tmp_path):
    store = StandupStore(str(tmp_path))
    analyzer = TrendAnalyzer(store)

    result = analyzer.analyze_blockers(AGENT_ID)

    assert result["total_blockers"] == 0
    assert result["recurring_blockers"] == []
    assert result["blockers_by_user"] == {}


def test_analyze_blockers_recurring(tmp_path):
    store = StandupStore(str(tmp_path))
    analyzer = TrendAnalyzer(store)

    # Same user reports a similar blocker across 4 distinct days within the last 14 days.
    blocker_text = "Waiting on CI/CD pipeline approval"
    for days_ago in (1, 3, 5, 7):
        entry = _make_entry(
            user_id="alice",
            date=_recent_date(days_ago),
            blockers=[blocker_text],
        )
        store.add_entry(AGENT_ID, entry)

    result = analyzer.analyze_blockers(AGENT_ID, days=14)

    assert result["total_blockers"] == 4
    assert len(result["recurring_blockers"]) == 1

    recurring = result["recurring_blockers"][0]
    assert recurring["user"] == "alice"
    assert recurring["days_blocked"] == 4
    assert recurring["blocker_summary"] == blocker_text


def test_analyze_blockers_not_recurring(tmp_path):
    store = StandupStore(str(tmp_path))
    analyzer = TrendAnalyzer(store)

    # Same blocker text but on only 2 distinct days -- should NOT be flagged.
    for days_ago in (1, 2):
        entry = _make_entry(
            user_id="bob",
            date=_recent_date(days_ago),
            blockers=["Waiting on code review"],
        )
        store.add_entry(AGENT_ID, entry)

    result = analyzer.analyze_blockers(AGENT_ID, days=14)

    assert result["total_blockers"] == 2
    assert result["recurring_blockers"] == []


# ------------------------------------------------------------------
# Participation patterns
# ------------------------------------------------------------------


def test_analyze_patterns_participation(tmp_path):
    store = StandupStore(str(tmp_path))
    analyzer = TrendAnalyzer(store)

    # Create entries across 4 days.  alice responds on all 4, bob on 2.
    for days_ago in (0, 1, 2, 3):
        store.add_entry(
            AGENT_ID,
            _make_entry("alice", _recent_date(days_ago)),
        )
    for days_ago in (0, 2):
        store.add_entry(
            AGENT_ID,
            _make_entry("bob", _recent_date(days_ago)),
        )

    result = analyzer.analyze_patterns(AGENT_ID, days=14)

    assert result["participation"]["alice"]["responded"] == 4
    assert result["participation"]["alice"]["total"] == 4
    assert result["participation"]["alice"]["rate"] == 1.0

    assert result["participation"]["bob"]["responded"] == 2
    assert result["participation"]["bob"]["total"] == 4
    assert result["participation"]["bob"]["rate"] == 0.5


def test_analyze_patterns_no_shows(tmp_path):
    store = StandupStore(str(tmp_path))
    analyzer = TrendAnalyzer(store)

    # 5 standup days total.  carol only responds on 2 -> 40% < 50%.
    all_days = [_recent_date(d) for d in range(5)]

    # alice responds every day (ensures 5 standup dates exist).
    for d in all_days:
        store.add_entry(AGENT_ID, _make_entry("alice", d))

    # carol responds on only 2 days.
    for d in all_days[:2]:
        store.add_entry(AGENT_ID, _make_entry("carol", d))

    result = analyzer.analyze_patterns(AGENT_ID, days=14)

    assert "carol" in result["frequent_no_shows"]
    assert "alice" not in result["frequent_no_shows"]


# ------------------------------------------------------------------
# Report formatting
# ------------------------------------------------------------------


def test_format_trend_report(tmp_path):
    store = StandupStore(str(tmp_path))
    analyzer = TrendAnalyzer(store)

    blocker_analysis = {
        "period_days": 14,
        "total_blockers": 5,
        "recurring_blockers": [
            {
                "user": "alice",
                "blocker_summary": "CI pipeline blocked",
                "days_blocked": 4,
                "first_seen": "2026-03-01",
                "last_seen": "2026-03-04",
            }
        ],
        "blockers_by_user": {},
    }

    pattern_analysis = {
        "period_days": 30,
        "participation": {
            "alice": {"responded": 20, "total": 20, "rate": 1.0},
            "bob": {"responded": 8, "total": 20, "rate": 0.4},
        },
        "frequent_no_shows": ["bob"],
    }

    report = analyzer.format_trend_report(blocker_analysis, pattern_analysis)

    # Recurring blockers section
    assert "## Recurring Blockers" in report
    assert "alice" in report
    assert "CI pipeline blocked" in report
    assert "4 days" in report

    # Participation table
    assert "## Participation" in report
    assert "| alice |" in report
    assert "| bob |" in report
    assert "100%" in report
    assert "40%" in report

    # Attention section
    assert "## Attention Needed" in report
    assert "low participation rate" in report
    assert "bob" in report


def test_format_trend_report_empty(tmp_path):
    store = StandupStore(str(tmp_path))
    analyzer = TrendAnalyzer(store)

    blocker_analysis = {
        "period_days": 14,
        "total_blockers": 0,
        "recurring_blockers": [],
        "blockers_by_user": {},
    }

    pattern_analysis = {
        "period_days": 30,
        "participation": {},
        "frequent_no_shows": [],
    }

    report = analyzer.format_trend_report(blocker_analysis, pattern_analysis)

    assert "No recurring blockers detected." in report
    assert "No participation data available." in report
    assert "Nothing requires immediate attention." in report
