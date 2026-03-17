"""Health checks for pool agents."""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from g3lobster.pool.types import AgentState


@dataclass
class HealthIssue:
    agent_id: str
    issue: str


@dataclass
class HeartbeatSuggestion:
    category: str
    severity: str
    message: str
    task_id: Optional[str] = None
    source: Optional[str] = None

    def as_dict(self) -> Dict[str, str]:
        payload: Dict[str, str] = {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
        }
        if self.task_id:
            payload["task_id"] = self.task_id
        if self.source:
            payload["source"] = self.source
        return payload


@dataclass
class HeartbeatReview:
    timestamp: str
    summary: str
    suggestions: List[HeartbeatSuggestion]
    stats: Dict[str, int]

    def as_event(self, agent_id: str) -> Dict[str, object]:
        return {
            "type": "heartbeat_review",
            "agent_id": agent_id,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "suggestions": [item.as_dict() for item in self.suggestions],
            "stats": dict(self.stats),
        }


class HealthInspector:
    """Detects dead and stuck agents from runtime metadata."""

    def inspect(self, agents: List[object], stuck_timeout_s: int) -> List[HealthIssue]:
        now = time.time()
        issues: List[HealthIssue] = []
        stuck_enabled = stuck_timeout_s > 0

        for agent in agents:
            state = getattr(agent, "state", None)
            agent_id = str(getattr(agent, "id", "unknown"))

            if state == AgentState.BUSY:
                busy_since = getattr(agent, "busy_since", None)
                if (
                    stuck_enabled
                    and busy_since
                    and (now - busy_since) > stuck_timeout_s
                ):
                    issues.append(HealthIssue(agent_id=agent_id, issue="stuck"))
                continue

            is_alive = getattr(agent, "is_alive", None)
            if callable(is_alive) and not is_alive() and state not in {AgentState.STOPPED, AgentState.STARTING}:
                issues.append(HealthIssue(agent_id=agent_id, issue="dead"))

        return issues

    def inspect_orphaned_tasks(self, tasks: Iterable[object], active_agent_ids: Iterable[str]) -> List[str]:
        """Detect queued/working tasks assigned to agents that are no longer active."""
        active = set(active_agent_ids)
        orphaned_ids: List[str] = []

        for task in tasks:
            raw_status = getattr(task, "status", "")
            status = raw_status.value if hasattr(raw_status, "value") else str(raw_status)
            if status not in {"queued", "working"}:
                continue

            agent_id = getattr(task, "agent_id", None)
            if not agent_id or agent_id in active:
                continue

            task_id = str(getattr(task, "id", "")).strip()
            if task_id:
                orphaned_ids.append(task_id)

        return orphaned_ids

    def build_heartbeat_review(
        self,
        *,
        agent_id: str,
        tasks: Iterable[object],
        goals: Iterable[str],
        control_plane_tasks: Iterable[object] = (),
        now: Optional[float] = None,
        stale_after_s: float = 1800.0,
        overdue_after_s: float = 7200.0,
    ) -> HeartbeatReview:
        now_ts = float(now if now is not None else time.time())
        stale_after_s = max(60.0, float(stale_after_s))
        overdue_after_s = max(stale_after_s, float(overdue_after_s))

        normalized = self._normalize_tasks(tasks, now=now_ts, source="task_store")
        normalized.extend(self._normalize_tasks(control_plane_tasks, now=now_ts, source="control_plane"))

        stats = {
            "pending": 0,
            "in_progress": 0,
            "blocked": 0,
            "completed": 0,
            "failed": 0,
            "stale": 0,
            "overdue": 0,
            "reviewed_tasks": len(normalized),
            "goals": 0,
        }
        suggestions: List[HeartbeatSuggestion] = []

        active_statuses = {"pending", "queued", "submitted", "running", "working", "in_progress", "blocked"}
        blocked_markers = (
            "blocked",
            "waiting for human",
            "awaiting human",
            "awaiting approval",
            "need approval",
            "waiting on",
            "need input",
            "awaiting input",
        )

        for item in normalized:
            status = item["status"]
            if status in {"pending", "queued", "submitted"}:
                stats["pending"] += 1
            elif status in {"running", "working", "in_progress"}:
                stats["in_progress"] += 1
            elif status in {"completed", "done"}:
                stats["completed"] += 1
            elif status in {"failed", "error"}:
                stats["failed"] += 1

            age_s = float(item["age_s"])
            if status in active_statuses:
                if age_s >= stale_after_s:
                    stats["stale"] += 1
                if age_s >= overdue_after_s:
                    stats["overdue"] += 1

                if age_s >= overdue_after_s:
                    suggestions.append(
                        HeartbeatSuggestion(
                            category="stale_or_overdue",
                            severity="high",
                            message=(
                                f"Task '{item['label']}' has been {status} for {int(age_s // 60)} minutes; "
                                "escalate or re-plan."
                            ),
                            task_id=item["id"],
                            source=item["source"],
                        )
                    )
                elif age_s >= stale_after_s:
                    suggestions.append(
                        HeartbeatSuggestion(
                            category="stale_or_overdue",
                            severity="medium",
                            message=(
                                f"Task '{item['label']}' is stale ({int(age_s // 60)} minutes in {status}); "
                                "refresh owner/next action."
                            ),
                            task_id=item["id"],
                            source=item["source"],
                        )
                    )

            lowered_text = str(item["text"]).lower()
            if status == "blocked" or any(marker in lowered_text for marker in blocked_markers):
                stats["blocked"] += 1
                suggestions.append(
                    HeartbeatSuggestion(
                        category="blocked_human_input",
                        severity="high" if "approval" in lowered_text else "medium",
                        message=(
                            f"Task '{item['label']}' appears blocked and may need human input."
                        ),
                        task_id=item["id"],
                        source=item["source"],
                    )
                )

        goal_lines = self._extract_goals(goals)
        stats["goals"] = len(goal_lines)
        active_count = stats["pending"] + stats["in_progress"] + stats["blocked"]
        if goal_lines and active_count == 0:
            suggestions.append(
                HeartbeatSuggestion(
                    category="proactive_task",
                    severity="info",
                    message=f"No active work. Create a task for goal: {goal_lines[0]}",
                )
            )
        elif goal_lines and active_count < len(goal_lines):
            gap = len(goal_lines) - active_count
            suggestions.append(
                HeartbeatSuggestion(
                    category="proactive_task",
                    severity="low",
                    message=(
                        f"{gap} goal(s) are not mapped to active work; consider creating follow-up tasks."
                    ),
                )
            )

        cron_hint = self._cron_candidate(normalized)
        if cron_hint:
            suggestions.append(
                HeartbeatSuggestion(
                    category="cron_candidate",
                    severity="low",
                    message=cron_hint,
                )
            )

        if not suggestions:
            suggestions.append(
                HeartbeatSuggestion(
                    category="proactive_task",
                    severity="info",
                    message="No urgent issues detected. Continue current execution plan.",
                )
            )

        severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
        suggestions.sort(key=lambda item: (severity_order.get(item.severity, 99), item.category, item.message))
        if len(suggestions) > 12:
            suggestions = suggestions[:12]

        summary = (
            f"{len(suggestions)} suggestion(s) for {agent_id}. "
            f"pending={stats['pending']}, in_progress={stats['in_progress']}, "
            f"blocked={stats['blocked']}, overdue={stats['overdue']}."
        )

        return HeartbeatReview(
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            summary=summary,
            suggestions=suggestions,
            stats=stats,
        )

    @staticmethod
    def _value(item: object, key: str, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    @classmethod
    def _normalize_tasks(cls, tasks: Iterable[object], *, now: float, source: str) -> List[Dict[str, object]]:
        normalized: List[Dict[str, object]] = []
        for task in tasks:
            raw_status = cls._value(task, "status", "")
            status = raw_status.value if hasattr(raw_status, "value") else str(raw_status)
            status = status.strip().lower()

            task_id = str(cls._value(task, "id", "") or "").strip()
            prompt = str(cls._value(task, "prompt", "") or "").strip()
            error = str(cls._value(task, "error", "") or "").strip()
            result_md = str(
                cls._value(task, "result_md", cls._value(task, "result", "")) or ""
            ).strip()
            created_at = cls._float_or_none(cls._value(task, "created_at", None))
            started_at = cls._float_or_none(cls._value(task, "started_at", None))
            updated_at = cls._float_or_none(cls._value(task, "updated_at", None))
            reference_ts = started_at or updated_at or created_at or now
            age_s = max(0.0, now - reference_ts)

            label = task_id[:8] if task_id else (prompt[:48] or "task")
            normalized.append(
                {
                    "id": task_id,
                    "label": label,
                    "status": status,
                    "source": source,
                    "prompt": prompt,
                    "text": "\n".join(part for part in [prompt, error, result_md] if part),
                    "age_s": age_s,
                    "created_at": created_at,
                }
            )
        return normalized

    @staticmethod
    def _extract_goals(goals: Iterable[str]) -> List[str]:
        goal_lines: List[str] = []
        seen = set()
        for source in goals:
            for raw_line in str(source or "").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if not (
                    line.startswith(("-", "*"))
                    or re.match(r"^\d+[.)]\s+", line)
                    or ":" in line
                ):
                    continue
                cleaned = re.sub(r"^[-*]\s+", "", line)
                cleaned = re.sub(r"^\d+[.)]\s+", "", cleaned)
                cleaned = cleaned.strip()
                if len(cleaned) < 8:
                    continue
                lowered = cleaned.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                goal_lines.append(cleaned)
                if len(goal_lines) >= 10:
                    return goal_lines
        return goal_lines

    @staticmethod
    def _cron_candidate(tasks: List[Dict[str, object]]) -> Optional[str]:
        prompts: List[str] = []
        for item in tasks:
            status = str(item.get("status") or "")
            if status not in {"completed", "done"}:
                continue
            prompt = str(item.get("prompt") or "").strip()
            if len(prompt) < 6:
                continue
            normalized = re.sub(r"\s+", " ", prompt.lower())
            normalized = re.sub(r"\b\d+\b", "", normalized).strip()
            if len(normalized) >= 6:
                prompts.append(normalized[:120])
        if not prompts:
            return None
        counts = Counter(prompts)
        pattern, frequency = counts.most_common(1)[0]
        if frequency < 3:
            return None
        return (
            f"Recurring pattern detected ({frequency}x): '{pattern[:60]}'. "
            "Consider scheduling it as a cron task."
        )

    @staticmethod
    def _float_or_none(value: object) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
