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
    video_codec: Literal['av1_nvenc','hevc_nvenc','h264_nvenc','libx264','libx265','libsvtav1','libaom-av1','h264_qsv','hevc_qsv','av1_qsv','h264_vaapi','hevc_vaapi','av1_vaapi','h264_amf','hevc_amf','av1_amf'] = 'av1_nvenc'
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

class DefaultPresets(BaseModel):
    target_mb: int = 25
    video_codec: Literal['av1_nvenc','hevc_nvenc','h264_nvenc','libx264','libx265','libsvtav1','libaom-av1','h264_qsv','hevc_qsv','av1_qsv','h264_vaapi','hevc_vaapi','av1_vaapi','h264_amf','hevc_amf','av1_amf'] = 'av1_nvenc'
    audio_codec: Literal['libopus','aac'] = 'libopus'
    preset: Literal['p1','p2','p3','p4','p5','p6','p7'] = 'p6'
    audio_kbps: Literal[64,96,128,160,192,256] = 128
    container: Literal['mp4','mkv'] = 'mp4'
    tune: Literal['hq','ll','ull','lossless'] = 'hq'


class AvailableCodecsResponse(BaseModel):
    """Response containing hardware-detected codecs and user-enabled codec groups."""
    hardware_type: str  # nvidia, intel, amd, cpu
    available_encoders: dict  # {h264: "h264_nvenc", ...}
    enabled_groups: list[str]  # ["nvidia", "cpu", "intel", "amd"]
    
class CodecVisibilitySettings(BaseModel):
    """Settings for which codec groups to show in UI."""
    show_nvidia: bool = True
    show_intel: bool = True
    show_amd: bool = True
    show_cpu: bool = True
