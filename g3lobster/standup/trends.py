"""Trend analysis module for g3lobster's standup conductor.

Analyses blocker recurrence and participation patterns across standup entries.
"""

from __future__ import annotations

import logging
import string
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from g3lobster.standup.store import StandupStore

_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "was",
        "are",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "about",
        "between",
        "through",
        "and",
        "but",
        "or",
        "not",
        "no",
        "nor",
        "so",
        "yet",
        "it",
        "its",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "he",
        "him",
        "his",
        "she",
        "her",
        "they",
        "them",
        "their",
        "this",
        "that",
        "these",
        "those",
        "am",
        "if",
        "then",
        "than",
        "too",
        "very",
        "just",
        "also",
        "still",
    }
)

_PUNCTUATION_TABLE = str.maketrans("", "", string.punctuation)


class TrendAnalyzer:
    """Analyses standup entries over time to surface recurring blockers and participation trends."""

    def __init__(self, store: StandupStore) -> None:
        self._store = store
        self._logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_blockers(self, agent_id: str, days: int = 14) -> Dict:
        """Analyse blocker trends over the past *days* days.

        Returns a dict with total blocker count, recurring blockers (same user
        mentioning a similar blocker for 3+ days), and a per-user breakdown.
        """
        end = datetime.now(tz=timezone.utc).date()
        start = end - timedelta(days=days - 1)

        entries_by_date = self._store.get_entries_range(
            agent_id,
            start.isoformat(),
            end.isoformat(),
        )

        # Collect all blockers grouped by user.
        # Each item: (date_str, blocker_text)
        user_blockers: Dict[str, List[Dict]] = defaultdict(list)
        total_blockers = 0

        for date_str, entries in entries_by_date.items():
            for entry in entries:
                for blocker in entry.blockers:
                    user_blockers[entry.user_id].append({"text": blocker, "date": date_str})
                    total_blockers += 1

        # Detect recurring blockers per user using keyword similarity.
        recurring: List[Dict] = []
        for user_id, blocker_list in user_blockers.items():
            recurring.extend(self._find_recurring_blockers(user_id, blocker_list))

        return {
            "period_days": days,
            "total_blockers": total_blockers,
            "recurring_blockers": recurring,
            "blockers_by_user": dict(user_blockers),
        }

    def analyze_patterns(self, agent_id: str, days: int = 30) -> Dict:
        """Detect participation patterns over the past *days* days.

        Returns per-user participation rates and a list of frequent no-shows
        (users responding less than 50% of the time).
        """
        end = datetime.now(tz=timezone.utc).date()
        start = end - timedelta(days=days - 1)

        entries_by_date = self._store.get_entries_range(
            agent_id,
            start.isoformat(),
            end.isoformat(),
        )

        total_standups = len(entries_by_date)
        if total_standups == 0:
            return {
                "period_days": days,
                "participation": {},
                "frequent_no_shows": [],
            }

        # Count how many standups each user responded to.
        user_dates: Dict[str, set] = defaultdict(set)
        for date_str, entries in entries_by_date.items():
            for entry in entries:
                user_dates[entry.user_id].add(date_str)

        participation: Dict[str, Dict] = {}
        frequent_no_shows: List[str] = []

        for user_id, dates in user_dates.items():
            responded = len(dates)
            rate = responded / total_standups if total_standups else 0.0
            participation[user_id] = {
                "responded": responded,
                "total": total_standups,
                "rate": round(rate, 2),
            }
            if rate < 0.5:
                frequent_no_shows.append(user_id)

        return {
            "period_days": days,
            "participation": participation,
            "frequent_no_shows": frequent_no_shows,
        }

    def format_trend_report(self, blocker_analysis: Dict, pattern_analysis: Dict) -> str:
        """Format a combined markdown report from blocker and pattern analyses."""
        sections: List[str] = []

        # --- Recurring Blockers ---
        sections.append("## Recurring Blockers\n")
        recurring = blocker_analysis.get("recurring_blockers", [])
        if recurring:
            for item in recurring:
                sections.append(
                    f"- **{item['user']}**: {item['blocker_summary']} "
                    f"({item['days_blocked']} days, "
                    f"{item['first_seen']} \u2013 {item['last_seen']})"
                )
        else:
            sections.append("No recurring blockers detected.\n")

        # --- Participation ---
        sections.append("\n## Participation\n")
        participation = pattern_analysis.get("participation", {})
        if participation:
            sections.append("| User | Responded | Total | Rate |")
            sections.append("|------|-----------|-------|------|")
            for user_id, stats in sorted(participation.items()):
                pct = f"{stats['rate'] * 100:.0f}%"
                sections.append(
                    f"| {user_id} | {stats['responded']} | {stats['total']} | {pct} |"
                )
        else:
            sections.append("No participation data available.\n")

        # --- Attention Needed ---
        sections.append("\n## Attention Needed\n")
        attention_items: List[str] = []

        no_shows = pattern_analysis.get("frequent_no_shows", [])
        for user_id in no_shows:
            rate = participation.get(user_id, {}).get("rate", 0)
            attention_items.append(
                f"- **{user_id}** has a low participation rate ({rate * 100:.0f}%)"
            )

        long_blockers = [b for b in recurring if b["days_blocked"] >= 5]
        for item in long_blockers:
            attention_items.append(
                f"- **{item['user']}** has been blocked for {item['days_blocked']} days: "
                f"{item['blocker_summary']}"
            )

        if attention_items:
            sections.extend(attention_items)
        else:
            sections.append("Nothing requires immediate attention.")

        return "\n".join(sections)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> set:
        """Lowercase, strip punctuation, and return a set of non-stop-word tokens."""
        cleaned = text.lower().translate(_PUNCTUATION_TABLE)
        return {w for w in cleaned.split() if w and w not in _STOP_WORDS}

    def _jaccard_similarity(self, set_a: set, set_b: set) -> float:
        """Return the Jaccard similarity between two sets."""
        if not set_a and not set_b:
            return 0.0
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union)

    def _find_recurring_blockers(
        self, user_id: str, blocker_list: List[Dict]
    ) -> List[Dict]:
        """Identify clusters of similar blockers spanning 3+ distinct days."""
        if len(blocker_list) < 3:
            return []

        # Group by tokenized representation and cluster similar ones.
        # Each cluster tracks dates and representative text.
        clusters: List[Dict] = []

        for item in blocker_list:
            tokens = self._tokenize(item["text"])
            if not tokens:
                continue

            matched = False
            for cluster in clusters:
                if self._jaccard_similarity(tokens, cluster["tokens"]) > 0.3:
                    cluster["dates"].add(item["date"])
                    cluster["texts"].append(item["text"])
                    matched = True
                    break

            if not matched:
                clusters.append(
                    {
                        "tokens": tokens,
                        "dates": {item["date"]},
                        "texts": [item["text"]],
                    }
                )

        results: List[Dict] = []
        for cluster in clusters:
            if len(cluster["dates"]) >= 3:
                sorted_dates = sorted(cluster["dates"])
                # Use the most common text as the summary.
                summary = Counter(cluster["texts"]).most_common(1)[0][0]
                results.append(
                    {
                        "user": user_id,
                        "blocker_summary": summary,
                        "days_blocked": len(cluster["dates"]),
                        "first_seen": sorted_dates[0],
                        "last_seen": sorted_dates[-1],
                    }
                )

        return results
