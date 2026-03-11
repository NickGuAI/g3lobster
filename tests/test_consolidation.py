"""Tests for the nightly consolidation pipeline."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from g3lobster.memory.consolidation import ConsolidationPipeline, ConsolidationReport
from g3lobster.memory.manager import MemoryManager


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    return tmp_path / "agent_data"


@pytest.fixture
def memory_manager(memory_dir: Path) -> MemoryManager:
    return MemoryManager(
        data_dir=str(memory_dir),
        compact_threshold=999,  # disable auto-compaction
        gemini_command="echo",  # stub
        gemini_args=["test-summary"],
    )


@pytest.fixture
def pipeline() -> ConsolidationPipeline:
    return ConsolidationPipeline(
        gemini_command="echo",
        gemini_args=["- Fact one\n- Fact two"],
        gemini_timeout_s=10.0,
        days_window=7,
        stale_days=30,
    )


class TestFactExtraction:
    def test_extract_facts_from_daily_notes(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        today = date.today()
        memory_manager.append_daily_note("Deployed version 2.3 to prod.", day=today)
        memory_manager.append_daily_note("Fixed auth bug in login flow.", day=today)

        count = pipeline.extract_facts(memory_manager)
        assert count > 0

        content = memory_manager.read_memory()
        assert "Consolidation" in content

    def test_extract_facts_no_notes(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        count = pipeline.extract_facts(memory_manager)
        assert count == 0

    def test_extract_facts_gemini_failure_uses_fallback(
        self, memory_manager: MemoryManager
    ) -> None:
        pipeline = ConsolidationPipeline(
            gemini_command="false",  # exits with code 1
            gemini_args=[],
            days_window=7,
            stale_days=30,
        )
        today = date.today()
        memory_manager.append_daily_note("Some note content.", day=today)

        count = pipeline.extract_facts(memory_manager)
        assert count > 0

        content = memory_manager.read_memory()
        assert "Consolidated" in content


class TestMemoryDedup:
    def test_dedup_removes_near_duplicates(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        memory_manager.append_memory_section("Topic A", "deployed version two to production server")
        memory_manager.append_memory_section("Topic B", "deployed version two to production server today")

        count = pipeline.dedup_memory(memory_manager)
        assert count >= 1

        content = memory_manager.read_memory()
        sections = [line for line in content.splitlines() if line.startswith("## ")]
        assert len(sections) == 1

    def test_dedup_keeps_distinct_sections(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        memory_manager.append_memory_section("Topic A", "deployed version two to production server")
        memory_manager.append_memory_section("Topic B", "completely different content about database migration")

        count = pipeline.dedup_memory(memory_manager)
        assert count == 0

    def test_dedup_no_sections(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        count = pipeline.dedup_memory(memory_manager)
        assert count == 0


class TestStaleEviction:
    def test_evict_stale_removes_old_sections(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        old_date = (date.today() - timedelta(days=45)).isoformat()
        recent_date = (date.today() - timedelta(days=5)).isoformat()

        memory_manager.append_memory_section(
            f"Consolidation {old_date}", "old fact that should be evicted"
        )
        memory_manager.append_memory_section(
            f"Consolidation {recent_date}", "recent fact to keep"
        )

        count = pipeline.evict_stale(memory_manager)
        assert count == 1

        content = memory_manager.read_memory()
        assert old_date not in content
        assert recent_date in content

    def test_evict_stale_keeps_undated_sections(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        memory_manager.append_memory_section("User Preferences", "always use dark mode")

        count = pipeline.evict_stale(memory_manager)
        assert count == 0

        content = memory_manager.read_memory()
        assert "User Preferences" in content

    def test_evict_stale_no_sections(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        count = pipeline.evict_stale(memory_manager)
        assert count == 0


class TestEntryCompression:
    def test_compress_old_daily_notes(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        # Create notes from 3 weeks ago (same week)
        base = date.today() - timedelta(days=21)
        # Ensure at least 2 notes in the same week
        day1 = base
        day2 = base + timedelta(days=1)

        memory_manager.append_daily_note("Note from day 1", day=day1)
        memory_manager.append_daily_note("Note from day 2", day=day2)

        count = pipeline.compress_entries(memory_manager)
        assert count >= 2

        # Originals should be archived
        assert not memory_manager.daily_note_path(day1).exists()
        assert not memory_manager.daily_note_path(day2).exists()

        # Archive should exist
        archive_dir = memory_manager.daily_dir / "archive"
        assert archive_dir.exists()
        assert len(list(archive_dir.glob("*.md"))) >= 2

    def test_compress_skips_recent_notes(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        today = date.today()
        memory_manager.append_daily_note("Recent note", day=today)

        count = pipeline.compress_entries(memory_manager)
        assert count == 0
        assert memory_manager.daily_note_path(today).exists()


class TestSkillDistillation:
    def test_distill_promotes_heavy_candidates(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        from g3lobster.memory.procedures import Procedure

        # Manually write a candidate with high weight
        candidate = Procedure(
            title="Deploy App",
            trigger="deploy app",
            steps=["Check git status", "Run tests", "Deploy"],
            weight=11.0,
            status="usable",
        )
        memory_manager.candidate_store._write({
            candidate.key: {
                "title": candidate.title,
                "trigger": candidate.trigger,
                "steps": candidate.steps,
                "weight": candidate.weight,
                "status": candidate.status,
                "first_seen": date.today().isoformat(),
                "last_seen": date.today().isoformat(),
            }
        })

        count = pipeline.distill_skills(memory_manager)
        assert count == 1

        # Should now be in permanent procedures
        procedures = memory_manager.procedure_store.list_procedures()
        assert any(p.trigger == "deploy app" for p in procedures)

    def test_distill_skips_low_weight_candidates(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        from g3lobster.memory.procedures import Procedure

        candidate = Procedure(
            title="Low Weight",
            trigger="low weight task",
            steps=["Step 1", "Step 2", "Step 3"],
            weight=2.0,
            status="candidate",
        )
        memory_manager.candidate_store._write({
            candidate.key: {
                "title": candidate.title,
                "trigger": candidate.trigger,
                "steps": candidate.steps,
                "weight": candidate.weight,
                "status": candidate.status,
                "first_seen": date.today().isoformat(),
                "last_seen": date.today().isoformat(),
            }
        })

        count = pipeline.distill_skills(memory_manager)
        assert count == 0


class TestConsolidationReport:
    def test_report_summary_with_changes(self) -> None:
        report = ConsolidationReport(
            agent_id="test",
            facts_extracted=3,
            sections_deduped=1,
            facts_evicted=2,
        )
        summary = report.summary
        assert "3 facts extracted" in summary
        assert "1 sections deduped" in summary
        assert "2 facts evicted" in summary

    def test_report_summary_no_changes(self) -> None:
        report = ConsolidationReport(agent_id="test")
        assert report.summary == "no changes"

    def test_report_summary_with_errors(self) -> None:
        report = ConsolidationReport(agent_id="test", errors=["stage failed"])
        assert "1 errors" in report.summary


class TestFullPipeline:
    def test_run_executes_all_stages(
        self, pipeline: ConsolidationPipeline, memory_manager: MemoryManager
    ) -> None:
        today = date.today()
        memory_manager.append_daily_note("Test note for pipeline run.", day=today)

        report = pipeline.run("test-agent", memory_manager)
        assert report.agent_id == "test-agent"
        assert report.facts_extracted > 0
        assert not report.errors

    def test_run_handles_stage_errors_gracefully(
        self, memory_manager: MemoryManager
    ) -> None:
        pipeline = ConsolidationPipeline(
            gemini_command="nonexistent_command_xyz",
            gemini_args=[],
            days_window=7,
            stale_days=30,
        )
        today = date.today()
        memory_manager.append_daily_note("Test note.", day=today)

        report = pipeline.run("test-agent", memory_manager)
        assert report.agent_id == "test-agent"
        # Should still complete even with Gemini failures
        # (fallback kicks in for fact extraction)


class TestCronManagerConsolidation:
    def test_cron_manager_accepts_consolidation_params(self) -> None:
        from unittest.mock import MagicMock
        from g3lobster.cron.manager import CronManager
        from g3lobster.cron.store import CronStore

        store = MagicMock(spec=CronStore)
        registry = MagicMock()

        manager = CronManager(
            cron_store=store,
            registry=registry,
            consolidation_enabled=True,
            consolidation_schedule="0 3 * * *",
            consolidation_days_window=14,
            consolidation_stale_days=60,
        )

        assert manager._consolidation_enabled is True
        assert manager._consolidation_schedule == "0 3 * * *"
        assert manager._consolidation_days_window == 14
        assert manager._consolidation_stale_days == 60


class TestConfigFields:
    def test_agents_config_has_consolidation_fields(self) -> None:
        from g3lobster.config import AgentsConfig

        config = AgentsConfig()
        assert config.consolidation_enabled is True
        assert config.consolidation_schedule == "0 2 * * *"
        assert config.consolidation_days_window == 7
        assert config.consolidation_stale_days == 30
