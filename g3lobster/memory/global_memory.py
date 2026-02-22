"""Global user memory and shared procedural memory."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List

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

    def list_knowledge(self) -> List[str]:
        return sorted(str(path.relative_to(self.knowledge_dir)) for path in self.knowledge_dir.rglob("*") if path.is_file())
