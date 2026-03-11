"""MCP workspace server for querying Google Drive, Docs, and Sheets.

Exposes tools to search Drive, read Google Docs, and read Google Sheets,
allowing agents to conversationally query live workspace documents.

Usage (stdio transport):
    python -m g3lobster.mcp.workspace_server
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, Optional, Sequence

from g3lobster.chat.auth import token_path

logger = logging.getLogger(__name__)

WORKSPACE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

_MIME_TYPES = {
    "document": "application/vnd.google-apps.document",
    "spreadsheet": "application/vnd.google-apps.spreadsheet",
}


def _get_workspace_credentials(data_dir: Optional[str] = None):
    """Load and return Google OAuth2 credentials with workspace scopes."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path(data_dir)
    if not path.exists():
        raise RuntimeError(
            f"Token file not found at {path}. "
            "Run the g3lobster auth flow first."
        )

    creds = Credentials.from_authorized_user_file(str(path), WORKSPACE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds.valid:
        raise RuntimeError(
            "Google credentials are invalid or expired and could not be refreshed."
        )

    return creds


def _build_search_drive_tool_schema() -> Dict[str, Any]:
    return {
        "name": "search_drive",
        "description": (
            "Search Google Drive for files by title or query. "
            "Optionally filter by file type (document or spreadsheet)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to match against file names.",
                },
                "file_type": {
                    "type": "string",
                    "description": "Optional file type filter.",
                    "enum": ["document", "spreadsheet"],
                },
            },
            "required": ["query"],
        },
    }


def _build_read_doc_tool_schema() -> Dict[str, Any]:
    return {
        "name": "read_doc",
        "description": (
            "Read a Google Doc by its document ID and return the plain text content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "The Google Docs document ID.",
                },
            },
            "required": ["doc_id"],
        },
    }


def _build_read_sheet_tool_schema() -> Dict[str, Any]:
    return {
        "name": "read_sheet",
        "description": (
            "Read data from a Google Sheet by its spreadsheet ID. "
            "Optionally specify a range (defaults to 'Sheet1')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sheet_id": {
                    "type": "string",
                    "description": "The Google Sheets spreadsheet ID.",
                },
                "range": {
                    "type": "string",
                    "description": "The A1 range to read (default: 'Sheet1').",
                },
            },
            "required": ["sheet_id"],
        },
    }


class WorkspaceMCPHandler:
    """Handles MCP JSON-RPC requests for Google Workspace query tools."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single JSON-RPC request and return a response."""
        method = request.get("method", "")
        req_id = request.get("id")

        if method == "initialize":
            return self._respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "g3lobster-workspace",
                    "version": "0.1.0",
                },
            })

        if method == "notifications/initialized":
            # Notification — no response needed
            return {}

        if method == "tools/list":
            return self._respond(req_id, {
                "tools": [
                    _build_search_drive_tool_schema(),
                    _build_read_doc_tool_schema(),
                    _build_read_sheet_tool_schema(),
                ],
            })

        if method == "tools/call":
            return self._handle_tool_call(req_id, request.get("params", {}))

        return self._error(req_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(
        self, req_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "search_drive":
            return self._search_drive(req_id, arguments)
        if tool_name == "read_doc":
            return self._read_doc(req_id, arguments)
        if tool_name == "read_sheet":
            return self._read_sheet(req_id, arguments)

        return self._error(req_id, -32602, f"Unknown tool: {tool_name}")

    def _search_drive(
        self, req_id: Any, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            query = arguments.get("query", "")
            file_type = arguments.get("file_type")

            if not query:
                return self._respond(req_id, {
                    "content": [{"type": "text", "text": "Error: query is required"}],
                    "isError": True,
                })

            from googleapiclient.discovery import build

            creds = _get_workspace_credentials(self.data_dir)
            service = build("drive", "v3", credentials=creds)

            q = f"name contains '{query}'"
            if file_type and file_type in _MIME_TYPES:
                q += f" and mimeType='{_MIME_TYPES[file_type]}'"

            result = (
                service.files()
                .list(
                    q=q,
                    fields="files(id, name, mimeType, modifiedTime)",
                    pageSize=10,
                )
                .execute()
            )

            files = result.get("files", [])
            return self._respond(req_id, {
                "content": [{"type": "text", "text": json.dumps(files, indent=2)}],
            })
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"search_drive error: {exc}"}],
                "isError": True,
            })

    def _read_doc(
        self, req_id: Any, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            doc_id = arguments.get("doc_id", "")
            if not doc_id:
                return self._respond(req_id, {
                    "content": [{"type": "text", "text": "Error: doc_id is required"}],
                    "isError": True,
                })

            from googleapiclient.discovery import build

            creds = _get_workspace_credentials(self.data_dir)
            service = build("docs", "v1", credentials=creds)

            doc = service.documents().get(documentId=doc_id).execute()

            # Extract plain text from document body
            text_parts = []
            for element in doc.get("body", {}).get("content", []):
                paragraph = element.get("paragraph")
                if paragraph:
                    for elem in paragraph.get("elements", []):
                        text_run = elem.get("textRun")
                        if text_run:
                            text_parts.append(text_run.get("content", ""))

            text = "".join(text_parts)
            return self._respond(req_id, {
                "content": [{"type": "text", "text": text}],
            })
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"read_doc error: {exc}"}],
                "isError": True,
            })

    def _read_sheet(
        self, req_id: Any, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            sheet_id = arguments.get("sheet_id", "")
            if not sheet_id:
                return self._respond(req_id, {
                    "content": [{"type": "text", "text": "Error: sheet_id is required"}],
                    "isError": True,
                })

            range_val = arguments.get("range", "Sheet1") or "Sheet1"

            from googleapiclient.discovery import build

            creds = _get_workspace_credentials(self.data_dir)
            service = build("sheets", "v4", credentials=creds)

            result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=sheet_id, range=range_val)
                .execute()
            )

            rows = result.get("values", [])
            if not rows:
                text = "(empty sheet)"
            else:
                text = "\n".join("\t".join(str(cell) for cell in row) for row in rows)

            return self._respond(req_id, {
                "content": [{"type": "text", "text": text}],
            })
        except Exception as exc:
            return self._respond(req_id, {
                "content": [{"type": "text", "text": f"read_sheet error: {exc}"}],
                "isError": True,
            })

    @staticmethod
    def _respond(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def run_stdio(data_dir: Optional[str] = None) -> None:
    """Run the workspace MCP server on stdio transport."""
    handler = WorkspaceMCPHandler(data_dir=data_dir)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()
            continue

        response = handler.handle_request(request)
        if response:  # Skip empty responses (notifications)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="g3lobster workspace MCP server")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Data directory for auth token lookup.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    run_stdio(data_dir=args.data_dir)


if __name__ == "__main__":
    main()
