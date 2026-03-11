"""Tests for g3lobster.chat.auth helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestCompleteAuthorizationUrlExtraction:
    """Verify that complete_authorization accepts both raw codes and full redirect URLs."""

    FAKE_CODE = "4/0AfrIepCdXBhKE2EjtXmlv9dPaIPYq-example"
    REDIRECT_URL = (
        f"http://localhost/?state=abc123&code={FAKE_CODE}"
        "&scope=https://www.googleapis.com/auth/chat.messages"
    )

    def _setup_mocks(self, tmp_path: Path):
        """Create minimal credential and state files so the function proceeds."""
        creds = tmp_path / "credentials.json"
        creds.write_text(json.dumps({
            "installed": {
                "client_id": "fake.apps.googleusercontent.com",
                "client_secret": "secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }))
        state_file = tmp_path / "oauth_state.json"
        state_file.write_text(json.dumps({"state": "abc123"}))
        return creds

    @patch("g3lobster.chat.auth.InstalledAppFlow" if False else "google_auth_oauthlib.flow.InstalledAppFlow")
    def test_raw_code_passed_through(self, mock_flow_cls, tmp_path):
        """A plain auth code should be passed to fetch_token unchanged."""
        self._setup_mocks(tmp_path)

        mock_flow = MagicMock()
        mock_flow.credentials.to_json.return_value = '{"token": "t"}'
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        from g3lobster.chat.auth import complete_authorization

        complete_authorization(str(tmp_path), self.FAKE_CODE)

        mock_flow.fetch_token.assert_called_once_with(code=self.FAKE_CODE)

    @patch("google_auth_oauthlib.flow.InstalledAppFlow")
    def test_full_url_extracts_code(self, mock_flow_cls, tmp_path):
        """A full redirect URL should have the code param extracted."""
        self._setup_mocks(tmp_path)

        mock_flow = MagicMock()
        mock_flow.credentials.to_json.return_value = '{"token": "t"}'
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        from g3lobster.chat.auth import complete_authorization

        complete_authorization(str(tmp_path), self.REDIRECT_URL)

        mock_flow.fetch_token.assert_called_once_with(code=self.FAKE_CODE)

    @patch("google_auth_oauthlib.flow.InstalledAppFlow")
    def test_url_without_code_raises(self, mock_flow_cls, tmp_path):
        """A URL missing the code param should raise ValueError."""
        self._setup_mocks(tmp_path)

        from g3lobster.chat.auth import complete_authorization

        with pytest.raises(ValueError, match="does not contain a 'code' parameter"):
            complete_authorization(str(tmp_path), "http://localhost/?state=abc123")

    @patch("google_auth_oauthlib.flow.InstalledAppFlow")
    def test_https_url_also_works(self, mock_flow_cls, tmp_path):
        """HTTPS redirect URLs should also be handled."""
        self._setup_mocks(tmp_path)

        mock_flow = MagicMock()
        mock_flow.credentials.to_json.return_value = '{"token": "t"}'
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        from g3lobster.chat.auth import complete_authorization

        https_url = f"https://localhost/?code={self.FAKE_CODE}"
        complete_authorization(str(tmp_path), https_url)

        mock_flow.fetch_token.assert_called_once_with(code=self.FAKE_CODE)

    @patch("google_auth_oauthlib.flow.InstalledAppFlow")
    def test_whitespace_around_url_handled(self, mock_flow_cls, tmp_path):
        """Leading/trailing whitespace around a URL should be stripped."""
        self._setup_mocks(tmp_path)

        mock_flow = MagicMock()
        mock_flow.credentials.to_json.return_value = '{"token": "t"}'
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        from g3lobster.chat.auth import complete_authorization

        complete_authorization(str(tmp_path), f"  {self.REDIRECT_URL}  ")

        mock_flow.fetch_token.assert_called_once_with(code=self.FAKE_CODE)
