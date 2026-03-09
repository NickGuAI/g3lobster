"""Agent export/import routes."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from g3lobster.agents.persona import (
    AgentPersona,
    agent_dir,
    is_valid_agent_id,
    load_persona,
    save_persona,
    slugify_agent_id,
)

router = APIRouter(prefix="/agents", tags=["export"])


@router.get("/{agent_id}/export")
async def export_agent(
    agent_id: str,
    request: Request,
    include_sessions: bool = Query(True),
) -> StreamingResponse:
    config = request.app.state.config
    persona = load_persona(config.agents.data_dir, agent_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Agent not found")

    path = agent_dir(config.agents.data_dir, agent_id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # agent.json
        agent_json = path / "agent.json"
        if agent_json.exists():
            zf.write(agent_json, "agent.json")

        # SOUL.md
        soul_file = path / "SOUL.md"
        if soul_file.exists():
            zf.write(soul_file, "SOUL.md")

        # .memory/ directory
        memory_dir = path / ".memory"
        if memory_dir.exists():
            for file in memory_dir.rglob("*"):
                if file.is_file():
                    arcname = ".memory/" + str(file.relative_to(memory_dir))
                    zf.write(file, arcname)

        # sessions/ directory (optional)
        if include_sessions:
            sessions_dir = path / "sessions"
            if sessions_dir.exists():
                for file in sessions_dir.rglob("*"):
                    if file.is_file():
                        arcname = "sessions/" + str(file.relative_to(sessions_dir))
                        zf.write(file, arcname)

    buf.seek(0)
    filename = f"{agent_id}.g3agent"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def import_agent(
    request: Request,
    archive: UploadFile = File(...),
    agent_id: Optional[str] = Query(None),
    overwrite: bool = Query(False),
) -> dict:
    config = request.app.state.config

    # Read and validate the zip archive
    content = await archive.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip archive")

    # Extract agent.json to determine agent identity
    if "agent.json" not in zf.namelist():
        raise HTTPException(status_code=400, detail="Archive missing agent.json")

    try:
        agent_data = json.loads(zf.read("agent.json"))
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(status_code=400, detail="Invalid agent.json in archive")

    # Determine target agent_id
    target_id = agent_id or agent_data.get("id", "")
    if not target_id:
        raise HTTPException(status_code=400, detail="Cannot determine agent id")

    target_id = slugify_agent_id(target_id) if not is_valid_agent_id(target_id) else target_id

    # Check for conflicts
    target_path = agent_dir(config.agents.data_dir, target_id)
    if target_path.exists() and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"Agent '{target_id}' already exists. Use ?overwrite=true to replace.",
        )

    # If overwriting, stop the agent first
    if target_path.exists() and overwrite:
        registry = request.app.state.registry
        await registry.stop_agent(target_id)
        shutil.rmtree(target_path)

    # Extract archive to target directory
    target_path.mkdir(parents=True, exist_ok=True)
    for name in zf.namelist():
        # Security: prevent path traversal
        if ".." in name or name.startswith("/"):
            continue
        dest = target_path / name
        if name.endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(name))

    # Update agent.json with the target_id (in case it was renamed)
    agent_data["id"] = target_id
    (target_path / "agent.json").write_text(
        json.dumps(agent_data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {"agent_id": target_id, "imported": True}
