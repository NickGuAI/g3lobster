"""Global user memory and shared procedural memory."""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

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
        self._knowledge_lock = threading.Lock()

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

    def list_knowledge_metadata(self) -> List[Dict[str, Any]]:
        """List knowledge files with parsed YAML frontmatter metadata."""
        result: List[Dict[str, Any]] = []
        for rel_path in self.list_knowledge():
            content = self.read_knowledge_file(rel_path)
            meta = _parse_frontmatter(content)
            meta["path"] = rel_path
            result.append(meta)
        return result

    def read_knowledge_file(self, path: str) -> str:
        """Read a single knowledge file by path relative to knowledge_dir."""
        full_path = self.knowledge_dir / path
        if not full_path.is_file():
            return ""
        return full_path.read_text(encoding="utf-8")

    def read_all_knowledge(self) -> Dict[str, str]:
        """Read all knowledge files. Returns {relative_path: content}."""
        return {rel: self.read_knowledge_file(rel) for rel in self.list_knowledge()}

    def write_knowledge(self, title: str, content: str, source_agent: str, topic: str) -> str:
        """Write a knowledge file with YAML frontmatter. Returns the relative file path."""
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")[:60] or "untitled"
        filename = f"{slug}.md"
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = f"---\nsource: {source_agent}\ntopic: {topic}\ncreated: {created}\n---\n\n# {title}\n\n{content}\n"
        with self._knowledge_lock:
            (self.knowledge_dir / filename).write_text(body, encoding="utf-8")
        return filename


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    """Parse YAML frontmatter from a knowledge file into a dict."""
    meta: Dict[str, Any] = {}
    if not text.startswith("---"):
        return meta
    parts = text.split("---", 2)
    if len(parts) < 3:
        return meta
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta
