from __future__ import annotations

import base64

from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import get_settings

security = HTTPBasic()


def basic_auth(credentials: HTTPBasicCredentials = Depends(security)) -> HTTPBasicCredentials:
    settings = get_settings()
    username = settings.basic_auth_username
    password = settings.basic_auth_password
    if username is None or password is None:
        return credentials
    correct_username = credentials.username == username
    correct_password = credentials.password == password
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials


async def enforce_websocket_auth(websocket: WebSocket) -> bool:
    settings = get_settings()
    username = settings.basic_auth_username
    password = settings.basic_auth_password
    if not username or not password:
        return True
    header = websocket.headers.get("authorization")
    if not header or not header.lower().startswith("basic "):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    token = header.split(" ", 1)[1]
    expected = base64.b64encode(f"{username}:{password}".encode()).decode()
    if token != expected:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    return True
