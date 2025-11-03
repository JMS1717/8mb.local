from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field
from fastapi import Form


CodecChoice = Literal["av1_nvenc", "hevc_nvenc", "h264_nvenc"]
AudioCodecChoice = Literal["libopus", "aac"]
PresetChoice = Literal["p1", "p5", "p7"]


class ProbeResponse(BaseModel):
    duration: float = Field(..., description="Video duration in seconds")
    bitrate_kbps: float = Field(..., description="Original video bitrate in kbps")
    audio_bitrate_kbps: float = Field(..., description="Original audio bitrate in kbps")
    size_mb: float = Field(..., description="Original file size in megabytes")


class JobRequest(BaseModel):
    @classmethod
    def as_form(
        cls,
        target_size_mb: float = Form(...),
        video_codec: CodecChoice = Form("av1_nvenc"),
        audio_codec: AudioCodecChoice = Form("libopus"),
        preset: PresetChoice = Form("p7"),
        audio_bitrate_kbps: int = Form(128),
    ) -> "JobRequest":
        return cls(
            target_size_mb=target_size_mb,
            video_codec=video_codec,
            audio_codec=audio_codec,
            preset=preset,
            audio_bitrate_kbps=audio_bitrate_kbps,
        )

    target_size_mb: float = Field(..., gt=0)
    video_codec: CodecChoice = "av1_nvenc"
    audio_codec: AudioCodecChoice = "libopus"
    preset: PresetChoice = "p7"
    audio_bitrate_kbps: int = Field(128, gt=0)


class JobEstimate(BaseModel):
    target_total_bitrate_kbps: float
    target_video_bitrate_kbps: float
    warning: Optional[str] = None


class JobCreateResponse(BaseModel):
    job_id: str
    probe: ProbeResponse
    estimate: JobEstimate
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: float
    output_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    payload: dict


class JobUpdateMessage(BaseModel):
    type: Literal["progress", "status", "log", "result"]
    payload: dict
