from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.status import HTTP_401_UNAUTHORIZED
from typing import Optional
import os
from . import settings_manager

security = HTTPBasic(auto_error=False)

def basic_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    """Runtime-aware Basic auth that respects Settings UI without restart."""
    # Prefer live environment first (updated by settings_manager), then .env
    env_enabled = os.getenv('AUTH_ENABLED')
    if env_enabled is None:
        # Fall back to reading .env to catch changes without restart
        auth_state = settings_manager.get_auth_settings()
        enabled = bool(auth_state.get('auth_enabled'))
        user = os.getenv('AUTH_USER') or (auth_state.get('auth_user') or '')
        pwd = os.getenv('AUTH_PASS') or ''
    else:
        enabled = env_enabled.lower() in ('true','1','yes')
        user = os.getenv('AUTH_USER', '')
        pwd = os.getenv('AUTH_PASS', '')

    if not enabled:
        return
    if not credentials:
        # Return 401 without WWW-Authenticate header to avoid native browser popup prompts (e.g., for EventSource)
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    correct_username = (credentials.username == user)
    correct_password = (credentials.password == pwd)
    if not (correct_username and correct_password):
        # Avoid WWW-Authenticate header to prevent browser basic auth dialog
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
