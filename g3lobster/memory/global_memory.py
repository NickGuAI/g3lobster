"""Global user memory and shared procedural memory."""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from g3lobster.memory.procedures import ProcedureStore, is_empty_procedure_document


def _parse_frontmatter(text: str) -> tuple[Dict[str, str], str]:
    """Parse YAML frontmatter from markdown text.

    Returns (metadata_dict, body) where body is content after the frontmatter.
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: Dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, parts[2].strip()


def _build_frontmatter(metadata: Dict[str, str]) -> str:
    """Build YAML frontmatter string from a metadata dict."""
    lines = ["---"]
    for key, value in metadata.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


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

    def list_knowledge_with_metadata(self) -> List[Dict[str, str]]:
        """List knowledge files with parsed YAML frontmatter metadata.

        Returns list of dicts with ``path``, ``source``, ``topic``, ``created`` fields.
        """
        result: List[Dict[str, str]] = []
        for path in sorted(self.knowledge_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.knowledge_dir))
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, _ = _parse_frontmatter(text)
            result.append({
                "path": rel,
                "source": meta.get("source", ""),
                "topic": meta.get("topic", ""),
                "created": meta.get("created", ""),
            })
        return result

    def add_knowledge(self, key: str, content: str) -> Path:
        """Write a knowledge entry to knowledge/{sanitized_key}.md."""
        safe_key = re.sub(r"[^a-zA-Z0-9_.-]", "_", key.strip())[:80] or "entry"
        path = self.knowledge_dir / f"{safe_key}.md"
        with self._knowledge_lock:
            path.write_text(content.strip() + "\n", encoding="utf-8")
        return path

    def write_knowledge(
        self,
        title: str,
        content: str,
        source_agent: str,
        topic: str,
    ) -> str:
        """Write a knowledge file with YAML frontmatter metadata.

        Creates ``knowledge/{sanitized_title}.md`` with ``source``, ``topic``,
        and ``created`` frontmatter fields.  Returns the relative file path.
        """
        safe_title = re.sub(r"[^a-zA-Z0-9_.-]", "_", title.strip())[:80] or "entry"
        path = self.knowledge_dir / f"{safe_title}.md"
        frontmatter = _build_frontmatter({
            "source": source_agent,
            "topic": topic,
            "created": datetime.now(timezone.utc).isoformat(),
        })
        full_content = f"{frontmatter}\n\n{content.strip()}\n"
        with self._knowledge_lock:
            path.write_text(full_content, encoding="utf-8")
        return str(path.relative_to(self.knowledge_dir))

    def read_knowledge_file(self, path: str) -> str | None:
        """Read a single knowledge file by relative path."""
        full_path = self.knowledge_dir / path
        if full_path.exists():
            return full_path.read_text(encoding="utf-8").strip()
        return None

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
        with self._knowledge_lock:
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

    def read_all_knowledge_with_metadata(self) -> List[Dict[str, Any]]:
        """Read all knowledge files with parsed metadata.

        Returns list of dicts with ``key``, ``content`` (body only),
        ``source``, ``topic``, ``created``, and ``raw`` (full text) fields.
        """
        result: List[Dict[str, Any]] = []
        for path in sorted(self.knowledge_dir.glob("*.md")):
            raw = path.read_text(encoding="utf-8").strip()
            meta, body = _parse_frontmatter(raw)
            result.append({
                "key": path.stem,
                "content": body,
                "source": meta.get("source", ""),
                "topic": meta.get("topic", ""),
                "created": meta.get("created", ""),
                "raw": raw,
            })
        return result
