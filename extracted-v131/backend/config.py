from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    REDIS_URL: str = Field(default="redis://127.0.0.1:6379/0")
    FILE_RETENTION_HOURS: int = Field(default=1)
    AUTH_ENABLED: bool = Field(default=True)
    AUTH_USER: str = Field(default="admin")
    AUTH_PASS: str = Field(default="changeme")
    BACKEND_HOST: str = Field(default="0.0.0.0")
    BACKEND_PORT: int = Field(default=8001)

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
