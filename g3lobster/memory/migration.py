"""Memory layout migration utilities."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def _copy_if_missing(source: Path, dest: Path) -> bool:
    if not source.exists() or dest.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return True


def migrate_agent_memory_layout(agent_runtime_dir: str) -> bool:
    """Migrate legacy memory/ layout into .memory/ (idempotent)."""
    root = Path(agent_runtime_dir).expanduser().resolve()
    old_memory = root / "memory"
    new_memory = root / ".memory"
    new_daily = new_memory / "daily"
    legacy_backup = root / "memory.v1"
    changed = False

    new_memory.mkdir(parents=True, exist_ok=True)
    new_daily.mkdir(parents=True, exist_ok=True)

    changed = _copy_if_missing(old_memory / "MEMORY.md", new_memory / "MEMORY.md") or changed
    changed = _copy_if_missing(old_memory / "PROCEDURES.md", new_memory / "PROCEDURES.md") or changed

    old_daily = old_memory / "memory"
    if old_daily.exists() and old_daily.is_dir():
        for file in sorted(old_daily.glob("*.md")):
            changed = _copy_if_missing(file, new_daily / file.name) or changed

    if old_memory.exists() and old_memory.is_dir() and not legacy_backup.exists():
        try:
            old_memory.rename(legacy_backup)
        except OSError as exc:
            logger.error(
                "Could not archive legacy memory directory %s to %s: %s",
                old_memory,
                legacy_backup,
                exc,
            )
        else:
            changed = True

    memory_file = new_memory / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text("# MEMORY\n\n", encoding="utf-8")
        changed = True

    procedures_file = new_memory / "PROCEDURES.md"
    if not procedures_file.exists():
        procedures_file.write_text("# PROCEDURES\n\n", encoding="utf-8")
        changed = True

    if changed:
        logger.info("Migrated memory layout for %s", root)
    return changed
