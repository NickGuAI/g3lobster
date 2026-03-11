"""Tests for HandoffBuilder — structured delegation context enrichment."""

from __future__ import annotations

import pytest

from g3lobster.memory.global_memory import GlobalMemoryManager
from g3lobster.memory.handoff import HandoffBuilder
from g3lobster.memory.manager import MemoryManager


@pytest.fixture
def memory(tmp_path):
    return MemoryManager(data_dir=str(tmp_path / "agent"), compact_threshold=100)


@pytest.fixture
def global_memory(tmp_path):
    return GlobalMemoryManager(str(tmp_path / "global"))


class TestHandoffBuilderBuild:
    def test_returns_raw_prompt_when_no_context(self, memory):
        """When parent has empty memory and no procedures, return raw prompt."""
        builder = HandoffBuilder()
        result = builder.build("do something", memory)
        assert result == "do something"

    def test_includes_memory_excerpts(self, memory):
        """Relevant memory sections appear in handoff."""
        memory.write_memory(
            "# MEMORY\n\n"
            "## Dashboard Config\n\nUse dark theme for dashboards.\n\n"
            "## Unrelated\n\nSomething about cats.\n"
        )
        builder = HandoffBuilder()
        result = builder.build("build a dashboard", memory)

        assert "# DELEGATION CONTEXT" in result
        assert "## Parent Memory Excerpts" in result
        assert "Dashboard Config" in result
        assert "dark theme" in result
        assert "# TASK" in result
        assert "build a dashboard" in result

    def test_includes_matching_procedures(self, memory):
        """Matching procedures from parent are included."""
        memory.procedure_store.save_procedures([
            _make_procedure("Deploy Dashboard", "deploy dashboard", [
                "Run build", "Upload artifacts", "Verify health"
            ]),
        ])
        builder = HandoffBuilder()
        result = builder.build("deploy dashboard", memory)

        assert "## Suggested Procedures" in result
        assert "Deploy Dashboard" in result
        assert "Run build" in result

    def test_includes_user_preferences(self, memory, global_memory):
        """User preferences from global memory are included."""
        global_memory.write_user_memory("# USER\n\nAlways use TypeScript.\n")
        builder = HandoffBuilder()
        result = builder.build("write some code", memory, global_memory=global_memory)

        assert "## User Preferences" in result
        assert "Always use TypeScript" in result

    def test_includes_parent_persona_name(self, memory):
        """Parent persona name appears in delegation header."""
        memory.write_memory(
            "# MEMORY\n\n## Task Notes\n\nImportant task context.\n"
        )
        builder = HandoffBuilder()
        result = builder.build("do task work", memory, parent_persona_name="Athena")

        assert "Delegated by: Athena" in result

    def test_respects_budget(self, memory):
        """Handoff context is bounded by max_context_chars."""
        # Write a large memory section
        big_content = "x " * 5000
        memory.write_memory(f"# MEMORY\n\n## Big Section\n\n{big_content}\n")

        builder = HandoffBuilder(max_context_chars=200)
        result = builder.build("find big data", memory)

        # The context portion (excluding the TASK section) should be bounded
        parts = result.split("# TASK\n")
        assert len(parts) == 2
        context_part = parts[0]
        # Context should not contain the full 10000 chars
        assert len(context_part) < 500

    def test_handles_empty_global_memory(self, memory, global_memory):
        """Empty global memory produces no user preferences section."""
        builder = HandoffBuilder()
        result = builder.build("do something", memory, global_memory=global_memory)
        assert result == "do something"

    def test_all_sections_together(self, memory, global_memory):
        """All three sections appear when data is available."""
        memory.write_memory(
            "# MEMORY\n\n## Deploy Notes\n\nAlways deploy to staging first.\n"
        )
        memory.procedure_store.save_procedures([
            _make_procedure("Deploy App", "deploy application", [
                "Build Docker image", "Push to registry", "Update k8s"
            ]),
        ])
        global_memory.write_user_memory("# USER\n\nPrefer verbose logging.\n")

        builder = HandoffBuilder()
        result = builder.build(
            "deploy application",
            memory,
            global_memory=global_memory,
            parent_persona_name="Hermes",
        )

        assert "# DELEGATION CONTEXT" in result
        assert "Delegated by: Hermes" in result
        assert "## Parent Memory Excerpts" in result
        assert "## Suggested Procedures" in result
        assert "## User Preferences" in result
        assert "# TASK" in result
        assert "deploy application" in result


class TestHandoffBuilderEdgeCases:
    def test_zero_matching_memory_sections(self, memory):
        """No memory sections match the task — no excerpt in output."""
        memory.write_memory(
            "# MEMORY\n\n## Cooking Recipes\n\nMake pasta with garlic.\n"
        )
        builder = HandoffBuilder()
        result = builder.build("deploy kubernetes", memory)

        # No overlap → no context → raw prompt
        assert result == "deploy kubernetes"

    def test_procedure_budget_overflow(self, memory):
        """Procedures exceeding budget are truncated."""
        procs = [
            _make_procedure(
                f"Proc {i}", f"task procedure {i}",
                [f"Step {j} for procedure {i}" for j in range(5)],
            )
            for i in range(10)
        ]
        memory.procedure_store.save_procedures(procs)

        builder = HandoffBuilder(max_context_chars=200, procedure_limit=10)
        result = builder.build("task procedure", memory)

        # Should contain some procedures but not all 10
        assert "Suggested Procedures" in result

    def test_custom_budget_and_limit(self, memory):
        """Custom max_context_chars and procedure_limit are respected."""
        builder = HandoffBuilder(max_context_chars=500, procedure_limit=1)
        assert builder.max_context_chars == 500
        assert builder.procedure_limit == 1

    def test_min_budget_floor(self):
        """Budget below 100 is clamped to 100."""
        builder = HandoffBuilder(max_context_chars=10)
        assert builder.max_context_chars == 100


def _make_procedure(title, trigger, steps):
    from g3lobster.memory.procedures import Procedure
    return Procedure(title=title, trigger=trigger, steps=steps, weight=10.0, status="permanent")
