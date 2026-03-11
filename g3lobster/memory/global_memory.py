"""Global user memory and shared procedural memory."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

from g3lobster.memory.procedures import ProcedureStore, is_empty_procedure_document


class GlobalMemoryManager:
    """Manages cross-agent memory under data/.memory."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.memory_dir = self.data_dir / ".memory"
        self.user_file = self.memory_dir / "USER.md"
        self.procedures_file = self.memory_dir / "PROCEDURES.md"
        self.knowledge_dir = self.memory_dir / "knowledge"
        self._procedures_lock = threading.Lock()

        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)

        if not self.user_file.exists():
            self.user_file.write_text("# USER\n\n", encoding="utf-8")
        if not self.procedures_file.exists():
            self.procedures_file.write_text("# PROCEDURES\n\n", encoding="utf-8")

        self.procedures = ProcedureStore(str(self.procedures_file))

    def read_user_memory(self) -> str:
        return self.user_file.read_text(encoding="utf-8")

    def write_user_memory(self, content: str) -> None:
        self.user_file.write_text(content, encoding="utf-8")

    def read_procedures(self) -> str:
        return self.procedures_file.read_text(encoding="utf-8")

    def write_procedures(self, content: str) -> None:
        procedures = self.procedures.parse_markdown(content)
        if not procedures and not is_empty_procedure_document(content):
            raise ValueError("Invalid procedures format. Provide markdown sections with Trigger and Steps.")
        with self._procedures_lock:
            self.procedures.save_procedures(procedures)

    def upsert_procedures(self, procedures) -> None:
        """Thread-safe wrapper around ProcedureStore.upsert_procedures."""
        with self._procedures_lock:
            self.procedures.upsert_procedures(procedures)

    def _user_memory_dir(self, user_id: str) -> Path:
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", user_id) or "default"
        return self.memory_dir / "users" / safe_id

    def read_user_memory_for(self, user_id: str) -> str:
        """Read per-user USER.md, falling back to shared USER.md."""
        user_dir = self._user_memory_dir(user_id)
        user_file = user_dir / "USER.md"
        if user_file.exists():
            return user_file.read_text(encoding="utf-8")
        return self.read_user_memory()

    def write_user_memory_for(self, user_id: str, content: str) -> None:
        """Write per-user USER.md."""
        user_dir = self._user_memory_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "USER.md").write_text(content, encoding="utf-8")

    def list_knowledge(self) -> List[str]:
        return sorted(str(path.relative_to(self.knowledge_dir)) for path in self.knowledge_dir.rglob("*") if path.is_file())

    def add_knowledge(self, key: str, content: str) -> Path:
        """Write a knowledge entry to knowledge/{sanitized_key}.md."""
        safe_key = re.sub(r"[^a-zA-Z0-9_.-]", "_", key.strip())[:80] or "entry"
        path = self.knowledge_dir / f"{safe_key}.md"
        path.write_text(content.strip() + "\n", encoding="utf-8")
        return path

    def get_knowledge(self, key: str) -> str | None:
        """Read a single knowledge entry by key."""
        safe_key = re.sub(r"[^a-zA-Z0-9_.-]", "_", key.strip())[:80] or "entry"
        path = self.knowledge_dir / f"{safe_key}.md"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return None

    def remove_knowledge(self, keyword: str) -> int:
        """Remove knowledge files matching keyword. Returns count removed."""
        keyword_lower = keyword.lower()
        removed = 0
        for path in list(self.knowledge_dir.glob("*.md")):
            if keyword_lower in path.stem.lower():
                path.unlink()
                removed += 1
        return removed

    def read_all_knowledge(self) -> dict[str, str]:
        """Read all knowledge files. Returns {filename: content}."""
        result: dict[str, str] = {}
        for path in sorted(self.knowledge_dir.glob("*.md")):
            result[path.stem] = path.read_text(encoding="utf-8").strip()
        return result
