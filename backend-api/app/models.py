from pydantic import BaseModel, Field
from typing import Optional, Literal

class UploadResponse(BaseModel):
    job_id: str
    filename: str
    duration_s: float
    original_video_bitrate_kbps: Optional[float] = None
    original_audio_bitrate_kbps: Optional[float] = None
    estimate_total_kbps: float
    estimate_video_kbps: float
    warn_low_quality: bool

class CompressRequest(BaseModel):
    job_id: str
    filename: str
    target_size_mb: float
    video_codec: Literal['av1_nvenc','hevc_nvenc','h264_nvenc'] = 'av1_nvenc'
    audio_codec: Literal['libopus','aac'] = 'libopus'
    audio_bitrate_kbps: int = 128
    preset: Literal['p1','p2','p3','p4','p5','p6','p7'] = 'p6'
    container: Literal['mp4','mkv'] = 'mp4'
    tune: Literal['hq','ll','ull','lossless'] = 'hq'
    max_width: Optional[int] = None
    max_height: Optional[int] = None
    start_time: Optional[str] = None  # Format: seconds (float) or "HH:MM:SS"
    end_time: Optional[str] = None    # Format: seconds (float) or "HH:MM:SS"

class StatusResponse(BaseModel):
    state: str
    progress: Optional[float] = None
    detail: Optional[str] = None

class ProgressEvent(BaseModel):
    type: Literal['progress','log','done','error']
    task_id: str
    progress: Optional[float] = None
    message: Optional[str] = None
    stats: Optional[dict] = None
    download_url: Optional[str] = None

class AuthSettings(BaseModel):
    auth_enabled: bool
    auth_user: Optional[str] = None

class AuthSettingsUpdate(BaseModel):
    auth_enabled: bool
    auth_user: Optional[str] = None
    auth_pass: Optional[str] = None  # Only include when changing password

class PasswordChange(BaseModel):
    current_password: str
    new_password: str
