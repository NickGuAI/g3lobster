"""Procedural memory extraction, storage, and matching.

Procedures follow a human-like learning model:
- Candidates are extracted from user→assistant exchanges every few turns.
- Each re-extraction increases the candidate's weight by 1.
- Weights decay with a 30-day half-life (exponential decay on read).
- Weight >= 3 → "usable" (injected into context when matched).
- Weight >= 10 → "permanent" (never decays, persisted in PROCEDURES.md).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "i", "in", "is", "it", "my", "of", "on", "or", "please",
    "the", "this", "to", "we", "with", "you",
}

ACTION_VERBS = {
    "add", "apply", "build", "check", "configure", "create", "deploy",
    "install", "open", "push", "reload", "restart", "review", "run",
    "save", "test", "update", "verify", "write",
}

STEP_PATTERN = re.compile(r"^\s*(?:\d+[\.\):]|[-*])\s+(.+?)\s*$")
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

DECAY_HALF_LIFE_DAYS = 30.0
USABLE_THRESHOLD = 3.0
PERMANENT_THRESHOLD = 10.0


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _tokenize(text: str) -> List[str]:
    return TOKEN_PATTERN.findall(_normalize_text(text))


def _procedure_key(trigger: str) -> str:
    return _normalize_text(trigger)


def _title_from_trigger(trigger: str) -> str:
    words = _normalize_text(trigger).split()
    if not words:
        return "Learned Procedure"
    return " ".join(word.capitalize() for word in words[:6])


def is_empty_procedure_document(content: str) -> bool:
    """Return True when markdown only contains the optional top-level header."""
    significant = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    if not significant:
        return True
    if len(significant) != 1:
        return False
    return significant[0].lower() == "# procedures"


def _days_since(iso_date: str) -> float:
    """Return days elapsed since *iso_date*."""
    try:
        then = date.fromisoformat(iso_date)
    except (ValueError, TypeError):
        return 0.0
    delta = date.today() - then
    return max(0.0, delta.days)


def _apply_decay(weight: float, last_seen: str) -> float:
    """Apply exponential decay: weight * 2^(-days / half_life)."""
    days = _days_since(last_seen)
    if days <= 0:
        return weight
    return weight * math.pow(2, -days / DECAY_HALF_LIFE_DAYS)


@dataclass
class Procedure:
    title: str
    trigger: str
    steps: List[str] = field(default_factory=list)
    weight: float = 1.0
    status: str = "candidate"  # candidate | usable | permanent
    first_seen: str = field(default_factory=lambda: date.today().isoformat())
    last_seen: str = field(default_factory=lambda: date.today().isoformat())

    # Legacy field kept for backward compat with existing PROCEDURES.md files.
    frequency: int = 1

    @property
    def key(self) -> str:
        return _procedure_key(self.trigger)

    @property
    def effective_weight(self) -> float:
        """Weight after time-based decay (permanent procedures don't decay)."""
        if self.status == "permanent":
            return self.weight
        return _apply_decay(self.weight, self.last_seen)


class ProcedureStore:
    """Markdown-backed procedural memory store for permanent procedures."""

    def __init__(self, path: str, min_frequency: int = 3):
        self.path = Path(path)
        self.min_frequency = max(1, int(min_frequency))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("# PROCEDURES\n\n", encoding="utf-8")

    def read_markdown(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def write_markdown(self, content: str) -> None:
        self.path.write_text(content, encoding="utf-8")

    def list_procedures(self) -> List[Procedure]:
        return self.parse_markdown(self.read_markdown())

    def parse_markdown(self, content: str) -> List[Procedure]:
        lines = str(content or "").splitlines()

        sections: List[Tuple[str, List[str]]] = []
        current_title = ""
        current_lines: List[str] = []

        for line in lines:
            if line.startswith("## "):
                if current_title:
                    sections.append((current_title, current_lines))
                current_title = line[3:].strip()
                current_lines = []
                continue
            if current_title:
                current_lines.append(line)

        if current_title:
            sections.append((current_title, current_lines))

        procedures: List[Procedure] = []
        for title, body_lines in sections:
            trigger = ""
            weight = 0.0
            frequency = 1
            last_seen = ""
            first_seen = ""
            status = ""
            steps: List[str] = []
            in_steps = False

            for raw in body_lines:
                line = raw.strip()
                if not line:
                    continue
                if line.lower().startswith("trigger:"):
                    trigger = line.split(":", 1)[1].strip()
                    in_steps = False
                    continue
                if line.lower().startswith("weight:"):
                    try:
                        weight = max(0.0, float(line.split(":", 1)[1].strip()))
                    except ValueError:
                        weight = 1.0
                    in_steps = False
                    continue
                if line.lower().startswith("frequency:"):
                    raw_freq = line.split(":", 1)[1].strip()
                    try:
                        frequency = max(1, int(raw_freq))
                    except ValueError:
                        frequency = 1
                    in_steps = False
                    continue
                if line.lower().startswith("status:"):
                    status = line.split(":", 1)[1].strip().lower()
                    in_steps = False
                    continue
                if line.lower().startswith("last seen:"):
                    last_seen = line.split(":", 1)[1].strip()
                    in_steps = False
                    continue
                if line.lower().startswith("first seen:"):
                    first_seen = line.split(":", 1)[1].strip()
                    in_steps = False
                    continue
                if line.lower().startswith("steps:"):
                    in_steps = True
                    continue
                if in_steps:
                    match = STEP_PATTERN.match(line)
                    if match:
                        steps.append(match.group(1).strip())

            # Legacy PROCEDURES.md files have frequency but not weight.
            # Treat their frequency as weight for migration.
            if weight == 0.0 and frequency > 0:
                weight = float(frequency)
            if not status:
                status = "permanent"

            procedures.append(
                Procedure(
                    title=title or _title_from_trigger(trigger),
                    trigger=trigger or title,
                    steps=steps,
                    weight=weight,
                    frequency=max(1, int(weight)),
                    status=status,
                    first_seen=first_seen or last_seen or date.today().isoformat(),
                    last_seen=last_seen or date.today().isoformat(),
                )
            )
        return procedures

    def save_procedures(self, procedures: Iterable[Procedure]) -> None:
        items = sorted(
            [p for p in procedures if p.trigger and p.steps],
            key=lambda item: (item.title.lower(), item.trigger.lower()),
        )

        lines = ["# PROCEDURES", ""]
        for p in items:
            lines.append(f"## {p.title}")
            lines.append(f"Trigger: {p.trigger}")
            lines.append(f"Weight: {p.weight:.1f}")
            lines.append(f"Status: {p.status}")
            lines.append(f"First seen: {p.first_seen}")
            lines.append(f"Last seen: {p.last_seen}")
            lines.append("")
            lines.append("Steps:")
            for index, step in enumerate(p.steps, start=1):
                lines.append(f"{index}. {step}")
            lines.append("")

        self.write_markdown("\n".join(lines).rstrip() + "\n")

    def upsert_procedures(self, procedures: Iterable[Procedure]) -> List[Procedure]:
        """Merge incoming procedures into existing permanent store."""
        merged: Dict[str, Procedure] = {
            p.key: p for p in self.list_procedures() if p.trigger
        }

        for incoming in procedures:
            if not incoming.trigger or not incoming.steps:
                continue
            key = incoming.key
            existing = merged.get(key)
            if not existing:
                merged[key] = incoming
                continue

            steps = existing.steps
            if len(incoming.steps) > len(existing.steps):
                steps = incoming.steps

            merged[key] = Procedure(
                title=incoming.title or existing.title,
                trigger=incoming.trigger,
                steps=steps,
                weight=max(existing.weight, incoming.weight),
                status=incoming.status if incoming.status == "permanent" else existing.status,
                first_seen=existing.first_seen,
                last_seen=incoming.last_seen or existing.last_seen,
            )

        saved = list(merged.values())
        self.save_procedures(saved)
        return saved

    @staticmethod
    def merge_procedures(global_items: Iterable[Procedure], agent_items: Iterable[Procedure]) -> List[Procedure]:
        merged: Dict[str, Procedure] = {item.key: item for item in global_items if item.trigger}
        for item in agent_items:
            if item.trigger:
                merged[item.key] = item
        return sorted(merged.values(), key=lambda item: item.title.lower())

    @staticmethod
    def match_query(procedures: Iterable[Procedure], query: str, limit: int = 3) -> List[Procedure]:
        normalized_query = _normalize_text(query)
        query_tokens = set(_tokenize(normalized_query))
        scored: List[Tuple[float, Procedure]] = []

        for procedure in procedures:
            trigger = _normalize_text(procedure.trigger)
            if not trigger:
                continue

            trigger_tokens = set(_tokenize(trigger))
            if trigger in normalized_query:
                score = 1.0
            elif normalized_query and normalized_query in trigger:
                score = 0.9
            else:
                overlap = 0.0
                if query_tokens and trigger_tokens:
                    overlap = len(query_tokens & trigger_tokens) / len(query_tokens | trigger_tokens)
                ratio = SequenceMatcher(a=normalized_query, b=trigger).ratio()
                score = max(overlap, ratio * 0.8)

            if score >= 0.45:
                scored.append((score, procedure))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [procedure for _score, procedure in scored[: max(1, int(limit))]]

    @staticmethod
    def extract_candidates(
        messages: Iterable[Dict[str, object]],
    ) -> List[Procedure]:
        """Extract procedure candidates from user→assistant message pairs.

        Unlike the old ``extract_procedures``, this returns every
        trigger+steps pair it finds (frequency=1) so the caller can
        accumulate weight across sessions and time.
        """
        pending_user = ""
        candidates: List[Procedure] = []
        for entry in messages:
            if entry.get("type") != "message":
                continue
            message = entry.get("message", {})
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "")).strip().lower()
            content = str(message.get("content", "")).strip()
            if not role or not content:
                continue

            if role == "user":
                pending_user = content
                continue

            if role != "assistant" or not pending_user:
                continue

            trigger = ProcedureStore._derive_trigger(pending_user)
            steps = ProcedureStore._extract_steps(content)
            pending_user = ""
            if not trigger or len(steps) < 3:
                continue

            candidates.append(
                Procedure(
                    title=_title_from_trigger(trigger),
                    trigger=trigger,
                    steps=steps,
                    weight=1.0,
                    status="candidate",
                    first_seen=date.today().isoformat(),
                    last_seen=date.today().isoformat(),
                )
            )

        return candidates

    # Keep the old name for backward compat in tests/compactor.
    @staticmethod
    def extract_procedures(
        messages: Iterable[Dict[str, object]],
        min_frequency: int = 3,
    ) -> List[Procedure]:
        return ProcedureStore.extract_candidates(messages)

    @staticmethod
    def _derive_trigger(text: str) -> str:
        tokens = _tokenize(text)
        if not tokens:
            return ""
        meaningful = [token for token in tokens if token not in STOP_WORDS]
        if not meaningful:
            meaningful = tokens
        return " ".join(meaningful[:5])

    @staticmethod
    def _extract_steps(text: str) -> List[str]:
        steps: List[str] = []
        for line in text.splitlines():
            match = STEP_PATTERN.match(line)
            if match:
                step = match.group(1).strip()
                if step:
                    steps.append(step)

        if len(steps) >= 3:
            return steps[:8]

        sentence_candidates = re.split(r"[\n.;]", text)
        for raw in sentence_candidates:
            candidate = raw.strip(" -*\t\r")
            if not candidate:
                continue
            tokenized = _tokenize(candidate)
            if len(tokenized) < 2:
                continue
            if tokenized[0] not in ACTION_VERBS:
                continue
            steps.append(candidate)
            if len(steps) >= 8:
                break

        unique_steps: List[str] = []
        seen = set()
        for step in steps:
            normalized = _normalize_text(step)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_steps.append(step)
        return unique_steps[:8]


class CandidateStore:
    """JSON-backed candidate map for procedure learning.

    Tracks procedure candidates with weight, decay, and promotion logic.
    Separate from ProcedureStore (PROCEDURES.md) which only holds permanent procs.
    """

    def __init__(
        self,
        path: str,
        usable_threshold: float = USABLE_THRESHOLD,
        permanent_threshold: float = PERMANENT_THRESHOLD,
    ):
        self.path = Path(path)
        self.usable_threshold = usable_threshold
        self.permanent_threshold = permanent_threshold
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def _read(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: Dict[str, Dict[str, Any]]) -> None:
        self.path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def ingest(self, candidates: Iterable[Procedure]) -> List[Procedure]:
        """Ingest extracted candidates, accumulating weight.

        Returns list of procedures that crossed the permanent threshold
        this cycle (newly promoted).
        """
        data = self._read()
        newly_promoted: List[Procedure] = []

        for candidate in candidates:
            if not candidate.trigger or not candidate.steps:
                continue
            key = candidate.key
            existing = data.get(key)

            if existing is None:
                data[key] = {
                    "title": candidate.title,
                    "trigger": candidate.trigger,
                    "steps": candidate.steps,
                    "weight": 1.0,
                    "status": "candidate",
                    "first_seen": candidate.first_seen or date.today().isoformat(),
                    "last_seen": date.today().isoformat(),
                }
                continue

            # Apply decay to existing weight before adding the new observation.
            old_weight = float(existing.get("weight", 0))
            old_status = str(existing.get("status", "candidate"))
            if old_status == "permanent":
                decayed = old_weight
            else:
                decayed = _apply_decay(old_weight, str(existing.get("last_seen", "")))

            new_weight = decayed + 1.0

            # Keep longer step list.
            steps = existing.get("steps", [])
            if len(candidate.steps) > len(steps):
                steps = candidate.steps

            # Determine status.
            was_permanent = old_status == "permanent"
            if was_permanent:
                new_status = "permanent"
            elif new_weight >= self.permanent_threshold:
                new_status = "permanent"
            elif new_weight >= self.usable_threshold:
                new_status = "usable"
            else:
                new_status = "candidate"

            data[key] = {
                "title": candidate.title or existing.get("title", ""),
                "trigger": candidate.trigger,
                "steps": steps,
                "weight": round(new_weight, 2),
                "status": new_status,
                "first_seen": existing.get("first_seen", date.today().isoformat()),
                "last_seen": date.today().isoformat(),
            }

            if new_status == "permanent" and not was_permanent:
                newly_promoted.append(self._to_procedure(data[key]))

        self._write(data)
        return newly_promoted

    def list_usable(self) -> List[Procedure]:
        """Return all procedures with effective weight >= usable threshold."""
        data = self._read()
        result: List[Procedure] = []
        for entry in data.values():
            proc = self._to_procedure(entry)
            if proc.effective_weight >= self.usable_threshold:
                result.append(proc)
        return result

    def list_all(self) -> List[Procedure]:
        """Return all candidates regardless of status."""
        data = self._read()
        return [self._to_procedure(entry) for entry in data.values()]

    @staticmethod
    def _to_procedure(entry: Dict[str, Any]) -> Procedure:
        return Procedure(
            title=str(entry.get("title", "")),
            trigger=str(entry.get("trigger", "")),
            steps=list(entry.get("steps", [])),
            weight=float(entry.get("weight", 1.0)),
            status=str(entry.get("status", "candidate")),
            first_seen=str(entry.get("first_seen", date.today().isoformat())),
            last_seen=str(entry.get("last_seen", date.today().isoformat())),
        )
