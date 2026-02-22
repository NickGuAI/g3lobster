from __future__ import annotations

from g3lobster.memory.procedures import CandidateStore, Procedure, ProcedureStore


def test_extract_candidates_returns_every_trigger_steps_pair() -> None:
    messages = []
    for _turn in range(3):
        messages.append(
            {
                "type": "message",
                "message": {
                    "role": "user",
                    "content": "Please deploy the app to production",
                },
            }
        )
        messages.append(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": "\n".join(
                        [
                            "1. Check git status",
                            "2. Run tests",
                            "3. Build image",
                            "4. Push image",
                            "5. Verify health",
                        ]
                    ),
                },
            }
        )

    candidates = ProcedureStore.extract_candidates(messages)
    assert len(candidates) == 3
    assert all(c.trigger == "deploy app production" for c in candidates)
    assert all(c.weight == 1.0 for c in candidates)
    assert all(c.status == "candidate" for c in candidates)
    assert candidates[0].steps[0] == "Check git status"


def test_candidate_store_weight_accumulation(tmp_path) -> None:
    store = CandidateStore(str(tmp_path / "candidates.json"))

    candidate = Procedure(
        title="Deploy App",
        trigger="deploy app production",
        steps=["Check status", "Run tests", "Deploy"],
        weight=1.0,
        status="candidate",
    )

    # First ingestion: weight = 1
    promoted = store.ingest([candidate])
    assert promoted == []
    items = store.list_all()
    assert len(items) == 1
    assert items[0].weight == 1.0
    assert items[0].status == "candidate"

    # Second ingestion: weight = 2
    store.ingest([candidate])
    items = store.list_all()
    assert items[0].weight == 2.0
    assert items[0].status == "candidate"

    # Third ingestion: weight = 3, crosses usable threshold
    store.ingest([candidate])
    items = store.list_all()
    assert items[0].weight == 3.0
    assert items[0].status == "usable"

    # Check list_usable
    usable = store.list_usable()
    assert len(usable) == 1
    assert usable[0].trigger == "deploy app production"


def test_candidate_store_promotion_to_permanent(tmp_path) -> None:
    store = CandidateStore(
        str(tmp_path / "candidates.json"),
        usable_threshold=3.0,
        permanent_threshold=5.0,
    )

    candidate = Procedure(
        title="Deploy App",
        trigger="deploy app production",
        steps=["Check status", "Run tests", "Deploy"],
    )

    # Build up weight: 1 → 2 → 3 → 4
    for _ in range(4):
        store.ingest([candidate])

    items = store.list_all()
    assert items[0].weight == 4.0
    assert items[0].status == "usable"

    # 5th ingestion crosses permanent threshold — should return promoted
    promoted = store.ingest([candidate])
    assert len(promoted) == 1
    assert promoted[0].status == "permanent"
    assert promoted[0].trigger == "deploy app production"

    items = store.list_all()
    assert items[0].weight == 5.0
    assert items[0].status == "permanent"


def test_candidate_store_permanent_never_decays(tmp_path, monkeypatch) -> None:
    store = CandidateStore(
        str(tmp_path / "candidates.json"),
        permanent_threshold=3.0,
    )

    candidate = Procedure(
        title="Deploy App",
        trigger="deploy app production",
        steps=["Check status", "Run tests", "Deploy"],
    )

    # Get to permanent status (3 ingestions)
    for _ in range(3):
        store.ingest([candidate])

    items = store.list_all()
    assert items[0].status == "permanent"

    # Even with a stale last_seen, effective weight should not decay
    proc = items[0]
    assert proc.effective_weight == proc.weight


def test_candidate_store_keeps_longer_steps(tmp_path) -> None:
    store = CandidateStore(str(tmp_path / "candidates.json"))

    short = Procedure(
        title="Deploy",
        trigger="deploy app production",
        steps=["Check", "Run", "Deploy"],
    )
    store.ingest([short])

    long = Procedure(
        title="Deploy",
        trigger="deploy app production",
        steps=["Check", "Run", "Build", "Push", "Deploy"],
    )
    store.ingest([long])

    items = store.list_all()
    assert len(items[0].steps) == 5


def test_merge_and_match_prefers_agent_specific_procedures(tmp_path) -> None:
    global_store = ProcedureStore(str(tmp_path / "global.md"))
    agent_store = ProcedureStore(str(tmp_path / "agent.md"))

    global_store.save_procedures(
        [
            Procedure(
                title="Deploy Global",
                trigger="deploy app production",
                steps=["Check status", "Run tests", "Deploy"],
                weight=5.0,
                status="permanent",
            )
        ]
    )
    agent_store.save_procedures(
        [
            Procedure(
                title="Deploy Agent",
                trigger="deploy app production",
                steps=["Check repo", "Run focused tests", "Deploy", "Verify"],
                weight=2.0,
                status="permanent",
            )
        ]
    )

    merged = ProcedureStore.merge_procedures(
        global_store.list_procedures(),
        agent_store.list_procedures(),
    )
    assert len(merged) == 1
    assert merged[0].title == "Deploy Agent"

    matched = ProcedureStore.match_query(merged, "deploy the app to production please")
    assert matched
    assert matched[0].title == "Deploy Agent"


def test_procedure_store_roundtrip_with_weight_and_status(tmp_path) -> None:
    store = ProcedureStore(str(tmp_path / "procedures.md"))
    proc = Procedure(
        title="Deploy App",
        trigger="deploy app production",
        steps=["Check status", "Run tests", "Deploy"],
        weight=7.5,
        status="permanent",
        first_seen="2026-01-01",
        last_seen="2026-02-14",
    )
    store.save_procedures([proc])

    loaded = store.list_procedures()
    assert len(loaded) == 1
    assert loaded[0].weight == 7.5
    assert loaded[0].status == "permanent"
    assert loaded[0].first_seen == "2026-01-01"
    assert loaded[0].last_seen == "2026-02-14"


def test_legacy_frequency_migrates_to_weight(tmp_path) -> None:
    """PROCEDURES.md files using Frequency instead of Weight should still load."""
    store = ProcedureStore(str(tmp_path / "procedures.md"))
    store.write_markdown(
        "\n".join(
            [
                "# PROCEDURES",
                "",
                "## Deploy App",
                "Trigger: deploy app production",
                "Frequency: 5",
                "Last seen: 2026-02-13",
                "",
                "Steps:",
                "1. Check git status",
                "2. Run tests",
                "3. Deploy",
            ]
        )
        + "\n"
    )

    loaded = store.list_procedures()
    assert len(loaded) == 1
    assert loaded[0].weight == 5.0
    assert loaded[0].status == "permanent"
