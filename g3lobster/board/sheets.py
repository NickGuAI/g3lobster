"""Optional Google Sheets backend for the task board.

When configured with a ``sheet_id``, this module can sync the local
:class:`~g3lobster.board.store.BoardStore` to/from a Google Sheets
spreadsheet.  The sheet is treated as a simple table with headers
matching :class:`~g3lobster.board.store.BoardItem` fields.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from g3lobster.board.store import BoardItem, BoardStore

logger = logging.getLogger(__name__)

HEADER_ROW = ["id", "type", "title", "link", "status", "agent_id", "metadata", "created_at", "updated_at"]


def _get_sheets_service(credentials_path: Optional[str] = None):
    """Build a Google Sheets API service object."""
    try:
        from google.oauth2 import service_account  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except ImportError:
        logger.warning("google-api-python-client not installed — Sheets sync unavailable")
        return None

    if credentials_path:
        creds = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
    else:
        try:
            import google.auth  # type: ignore
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
        except Exception:
            logger.warning("No credentials available for Sheets API")
            return None

    return build("sheets", "v4", credentials=creds)


def _row_to_item(row: List[str]) -> Optional[BoardItem]:
    """Convert a sheet row (list of strings) to a BoardItem."""
    import json as json_mod

    if len(row) < 5:
        return None
    try:
        metadata = {}
        if len(row) > 6 and row[6]:
            try:
                metadata = json_mod.loads(row[6])
            except (json_mod.JSONDecodeError, TypeError):
                pass

        return BoardItem(
            id=row[0],
            type=row[1],
            title=row[2],
            link=row[3] if len(row) > 3 else "",
            status=row[4] if len(row) > 4 else "todo",
            agent_id=row[5] if len(row) > 5 and row[5] else None,
            metadata=metadata,
            created_at=row[7] if len(row) > 7 else "",
            updated_at=row[8] if len(row) > 8 else "",
        )
    except (IndexError, TypeError):
        return None


def _item_to_row(item: BoardItem) -> List[str]:
    """Convert a BoardItem to a sheet row."""
    import json as json_mod
    return [
        item.id,
        item.type,
        item.title,
        item.link,
        item.status,
        item.agent_id or "",
        json_mod.dumps(item.metadata) if item.metadata else "",
        item.created_at,
        item.updated_at,
    ]


class SheetsSync:
    """Bidirectional sync between local BoardStore and a Google Sheet."""

    def __init__(
        self,
        sheet_id: str,
        store: BoardStore,
        credentials_path: Optional[str] = None,
        sheet_range: str = "Sheet1",
    ) -> None:
        self._sheet_id = sheet_id
        self._store = store
        self._credentials_path = credentials_path
        self._sheet_range = sheet_range

    def push(self) -> Dict[str, Any]:
        """Push local board state to Google Sheets (overwrite)."""
        service = _get_sheets_service(self._credentials_path)
        if not service:
            return {"error": "Sheets API not available"}

        items = self._store.list_items()
        rows = [HEADER_ROW] + [_item_to_row(item) for item in items]

        sheets = service.spreadsheets()
        # Clear existing content
        sheets.values().clear(
            spreadsheetId=self._sheet_id,
            range=self._sheet_range,
        ).execute()

        # Write new content
        result = sheets.values().update(
            spreadsheetId=self._sheet_id,
            range=f"{self._sheet_range}!A1",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()

        return {
            "pushed": len(items),
            "updated_cells": result.get("updatedCells", 0),
        }

    def pull(self) -> Dict[str, Any]:
        """Pull from Google Sheets into local store (overwrite local)."""
        service = _get_sheets_service(self._credentials_path)
        if not service:
            return {"error": "Sheets API not available"}

        sheets = service.spreadsheets()
        result = sheets.values().get(
            spreadsheetId=self._sheet_id,
            range=self._sheet_range,
        ).execute()

        rows = result.get("values", [])
        if len(rows) < 2:
            return {"pulled": 0}

        # Skip header row
        items = []
        for row in rows[1:]:
            item = _row_to_item(row)
            if item:
                items.append(item)

        # Overwrite local store
        self._store._write_items(items)

        return {"pulled": len(items)}

    def sync(self) -> Dict[str, Any]:
        """Two-way merge: pull remote, merge with local, push back.

        Conflict resolution: remote wins for items that exist in both
        stores (by id). Local-only items are added. Remote-only items
        are preserved.
        """
        service = _get_sheets_service(self._credentials_path)
        if not service:
            return {"error": "Sheets API not available"}

        # Pull remote items
        sheets = service.spreadsheets()
        result = sheets.values().get(
            spreadsheetId=self._sheet_id,
            range=self._sheet_range,
        ).execute()
        rows = result.get("values", [])

        remote_items: Dict[str, BoardItem] = {}
        if len(rows) >= 2:
            for row in rows[1:]:
                item = _row_to_item(row)
                if item:
                    remote_items[item.id] = item

        # Get local items
        local_items = {item.id: item for item in self._store.list_items()}

        # Merge: remote wins for conflicts, add local-only
        merged: Dict[str, BoardItem] = dict(remote_items)
        for item_id, item in local_items.items():
            if item_id not in merged:
                merged[item_id] = item

        merged_list = sorted(merged.values(), key=lambda i: i.created_at)

        # Write merged result to both stores
        self._store._write_items(merged_list)

        push_rows = [HEADER_ROW] + [_item_to_row(item) for item in merged_list]
        sheets.values().clear(
            spreadsheetId=self._sheet_id,
            range=self._sheet_range,
        ).execute()
        sheets.values().update(
            spreadsheetId=self._sheet_id,
            range=f"{self._sheet_range}!A1",
            valueInputOption="RAW",
            body={"values": push_rows},
        ).execute()

        return {
            "local_count": len(local_items),
            "remote_count": len(remote_items),
            "merged_count": len(merged_list),
        }
