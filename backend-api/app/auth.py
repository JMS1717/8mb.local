from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.status import HTTP_401_UNAUTHORIZED
from typing import Optional
import os
import secrets
from . import settings_manager

security = HTTPBasic(auto_error=False)


def basic_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)) -> None:
    """Runtime-aware Basic auth that respects Settings UI without restart."""
    # Prefer live environment values updated by the Settings UI, then fall
    # back to the persisted .env values. Auth can be enabled at runtime
    # without rewriting AUTH_USER/AUTH_PASS into os.environ.
    env_vars = settings_manager.read_env_file()
    auth_state = settings_manager.get_auth_settings()
    env_enabled = os.getenv('AUTH_ENABLED')
    enabled_value = env_enabled if env_enabled is not None else env_vars.get('AUTH_ENABLED', 'false')
    enabled = str(enabled_value).lower() in ('true', '1', 'yes')
    user = os.getenv('AUTH_USER') or env_vars.get('AUTH_USER') or (auth_state.get('auth_user') or '')
    pwd = os.getenv('AUTH_PASS') or env_vars.get('AUTH_PASS') or ''

    if not enabled:
        return
    if not credentials:
        # The challenge is required for browsers to offer Basic-auth login.
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": 'Basic realm="8mb.local"'},
        )
    correct_username = secrets.compare_digest(credentials.username, user)
    correct_password = secrets.compare_digest(credentials.password, pwd)
    if not (correct_username and correct_password):
        # Keep the challenge on invalid credentials so the browser can retry.
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": 'Basic realm="8mb.local"'},
        )
