from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.status import HTTP_401_UNAUTHORIZED
from typing import Optional
from .config import settings

security = HTTPBasic(auto_error=False)

def basic_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    if not settings.AUTH_ENABLED:
        return
    if not credentials:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Basic"},
        )
    correct_username = credentials.username == settings.AUTH_USER
    correct_password = credentials.password == settings.AUTH_PASS
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
