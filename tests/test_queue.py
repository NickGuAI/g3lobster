from __future__ import annotations

from g3lobster.agents.persona import (
    AgentPersona,
    delete_persona,
    ensure_unique_agent_id,
    list_personas,
    load_persona,
    save_persona,
)


def test_persona_crud_and_slug_helpers(tmp_path) -> None:
    data_dir = str(tmp_path / "data")

    first = AgentPersona(
        id="ops-bot",
        name="Ops Bot",
        emoji="🛠️",
        soul="Always prefer concrete next steps.",
        model="gemini",
        mcp_servers=["gmail", "calendar"],
    )

    saved = save_persona(data_dir, first)
    assert saved.id == "ops-bot"

    loaded = load_persona(data_dir, "ops-bot")
    assert loaded is not None
    assert loaded.name == "Ops Bot"
    assert loaded.soul == "Always prefer concrete next steps."
    assert loaded.mcp_servers == ["gmail", "calendar"]

    personas = list_personas(data_dir)
    assert [item.id for item in personas] == ["ops-bot"]

    assert ensure_unique_agent_id(data_dir, "ops-bot") == "ops-bot-2"

    assert delete_persona(data_dir, "ops-bot") is True
    assert load_persona(data_dir, "ops-bot") is None
