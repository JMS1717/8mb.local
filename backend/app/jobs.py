from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from redis.asyncio import Redis

JOB_KEY_PREFIX = "8mblocal:jobs"
JOB_CHANNEL_PREFIX = "8mblocal:channel"


@dataclass
class JobMetadata:
    job_id: str
    status: str = "queued"
    progress: float = 0.0
    input_path: Optional[str] = None
    output_path: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "progress": self.progress,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobMetadata":
        return cls(
            job_id=data["job_id"],
            status=data.get("status", "queued"),
            progress=float(data.get("progress", 0.0)),
            input_path=data.get("input_path"),
            output_path=data.get("output_path"),
            payload=data.get("payload", {}),
            created_at=datetime.fromisoformat(data.get("created_at")),
            updated_at=datetime.fromisoformat(data.get("updated_at")),
        )


def job_key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}:{job_id}"


def channel_name(job_id: str) -> str:
    return f"{JOB_CHANNEL_PREFIX}:{job_id}"


async def store_job(redis: Redis, metadata: JobMetadata) -> None:
    data = metadata.to_dict()
    await redis.hset(job_key(metadata.job_id), mapping={"data": json.dumps(data)})


async def get_job(redis: Redis, job_id: str) -> Optional[JobMetadata]:
    stored = await redis.hget(job_key(job_id), "data")
    if not stored:
        return None
    data = json.loads(stored)
    return JobMetadata.from_dict(data)


async def update_job(redis: Redis, job_id: str, **changes: Any) -> Optional[JobMetadata]:
    metadata = await get_job(redis, job_id)
    if metadata is None:
        return None
    for key, value in changes.items():
        if hasattr(metadata, key):
            setattr(metadata, key, value)
    metadata.updated_at = datetime.utcnow()
    await store_job(redis, metadata)
    return metadata


async def publish(redis: Redis, job_id: str, message: Dict[str, Any]) -> None:
    await redis.publish(channel_name(job_id), json.dumps(message))
