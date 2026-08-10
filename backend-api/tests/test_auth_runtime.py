"""Regression coverage for live auth toggles and persisted credentials."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials

from app.auth import basic_auth


class TestAuthRuntime(unittest.TestCase):
    def test_live_enable_uses_credentials_stored_in_env_file(self):
        with patch.dict(os.environ, {"AUTH_ENABLED": "true"}, clear=False), \
             patch("app.auth.settings_manager.read_env_file", return_value={
                 "AUTH_USER": "stored-user",
                 "AUTH_PASS": "stored-pass",
                 "AUTH_ENABLED": "true",
             }), \
             patch("app.auth.settings_manager.get_auth_settings", return_value={
                 "auth_enabled": True,
                 "auth_user": "stored-user",
             }):
            basic_auth(HTTPBasicCredentials(username="stored-user", password="stored-pass"))

    def test_invalid_credentials_raise_401(self):
        with patch.dict(os.environ, {"AUTH_ENABLED": "true"}, clear=False), \
             patch("app.auth.settings_manager.read_env_file", return_value={
                 "AUTH_USER": "stored-user",
                 "AUTH_PASS": "stored-pass",
                 "AUTH_ENABLED": "true",
             }), \
             patch("app.auth.settings_manager.get_auth_settings", return_value={
                 "auth_enabled": True,
                 "auth_user": "stored-user",
             }):
            with self.assertRaisesRegex(Exception, "Invalid authentication"):
                basic_auth(HTTPBasicCredentials(username="stored-user", password="wrong"))

    def test_missing_credentials_include_browser_challenge(self):
        with patch.dict(os.environ, {
            "AUTH_ENABLED": "true",
            "AUTH_USER": "stored-user",
            "AUTH_PASS": "stored-pass",
        }, clear=False), \
             patch("app.auth.settings_manager.read_env_file", return_value={
                 "AUTH_USER": "stored-user",
                 "AUTH_PASS": "stored-pass",
                 "AUTH_ENABLED": "true",
             }), \
             patch("app.auth.settings_manager.get_auth_settings", return_value={
                 "auth_enabled": True,
                 "auth_user": "stored-user",
             }):
            with self.assertRaises(HTTPException) as raised:
                basic_auth(None)

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(
            raised.exception.headers.get("WWW-Authenticate"),
            'Basic realm="8mb.local"',
        )


if __name__ == "__main__":
    unittest.main()
