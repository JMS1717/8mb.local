from pydantic import BaseModel, Field
from typing import Optional, Literal

class UploadResponse(BaseModel):
    job_id: str
    filename: str
    duration_s: float
    original_video_bitrate_kbps: Optional[float] = None
    original_audio_bitrate_kbps: Optional[float] = None
    original_width: Optional[int] = None
    original_height: Optional[int] = None
    original_video_fps: Optional[float] = None
    estimate_total_kbps: float
    estimate_video_kbps: float
    warn_low_quality: bool

class CompressRequest(BaseModel):
    job_id: str
    filename: str
    target_size_mb: float
    # When set (>0), worker uses this video bitrate (kbps) instead of deriving from target_size_mb.
    target_video_bitrate_kbps: Optional[float] = Field(default=None, ge=0, le=2_000_000)
    video_codec: Literal['av1_nvenc','hevc_nvenc','h264_nvenc','av1_qsv','hevc_qsv','h264_qsv','av1_vaapi','hevc_vaapi','h264_vaapi','libx264','libx265','libsvtav1','libaom-av1'] = 'av1_nvenc'
    audio_codec: Literal['libopus','aac','eac3','none'] = 'libopus'  # Added 'none' for mute
    audio_bitrate_kbps: int = 128
    preset: Literal['p1','p2','p3','p4','p5','p6','p7','extraquality'] = 'p6'  # Added 'extraquality'
    container: Literal['mp4','mkv'] = 'mp4'
    tune: Literal['hq','ll','ull','lossless'] = 'hq'
    max_width: Optional[int] = None
    max_height: Optional[int] = None
    start_time: Optional[str] = None  # Format: seconds (float) or "HH:MM:SS"
    end_time: Optional[str] = None    # Format: seconds (float) or "HH:MM:SS"
    # Prefer attempting GPU decoding (when available). Worker will still fall back if unsupported.
    force_hw_decode: Optional[bool] = False
    # For MP4 outputs, use fragmented MP4 to avoid long faststart finalization.
    fast_mp4_finalize: Optional[bool] = False
    # Automatic resolution selection based on original bitrate/size
    auto_resolution: Optional[bool] = False
    min_auto_resolution: Optional[int] = 240  # Do not downscale below this unless user overrides
    target_resolution: Optional[int] = None   # Explicit target height (e.g., 1080, 720); overrides auto selection
    audio_only: Optional[bool] = False        # Convert to audio-only output (.m4a) ignoring video settings
    # When set (>0), output frame rate is capped to this value if the source is faster (no increase for low-fps sources).
    max_output_fps: Optional[float] = Field(default=None, ge=0, le=1000)

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
    target_mb: float = 25
    video_codec: Literal['av1_nvenc','hevc_nvenc','h264_nvenc','av1_qsv','hevc_qsv','h264_qsv','av1_vaapi','hevc_vaapi','h264_vaapi','libx264','libx265','libsvtav1','libaom-av1'] = 'av1_nvenc'
    audio_codec: Literal['libopus','aac','eac3','none'] = 'libopus'  # Added 'none' for mute
    preset: Literal['p1','p2','p3','p4','p5','p6','p7','extraquality'] = 'p6'  # Added 'extraquality'
    audio_kbps: Literal[64,96,128,160,192,256] = 128
    container: Literal['mp4','mkv'] = 'mp4'
    tune: Literal['hq','ll','ull','lossless'] = 'hq'


class AvailableCodecsResponse(BaseModel):
    """Response containing hardware-detected codecs and user-enabled codecs."""
    hardware_type: str  # nvidia, qsv, vaapi, cpu
    available_encoders: dict  # {h264: "h264_nvenc", ...}
    enabled_codecs: list[str]  # ["h264_nvenc", "hevc_nvenc", ...]
    
class CodecVisibilitySettings(BaseModel):
    """Settings for which individual codecs to show in UI."""
    # NVIDIA
    h264_nvenc: bool = True
    hevc_nvenc: bool = True
    av1_nvenc: bool = True
    # Intel QSV (via VAAPI backend)
    h264_qsv: bool = True
    hevc_qsv: bool = True
    av1_qsv: bool = True
    # VAAPI (Intel iGPU, AMD)
    h264_vaapi: bool = True
    hevc_vaapi: bool = True
    av1_vaapi: bool = True
    # CPU
    libx264: bool = True
    libx265: bool = True
    libsvtav1: bool = True


class PresetProfile(BaseModel):
    name: str
    target_mb: float
    video_codec: Literal['av1_nvenc','hevc_nvenc','h264_nvenc','av1_qsv','hevc_qsv','h264_qsv','av1_vaapi','hevc_vaapi','h264_vaapi','libx264','libx265','libsvtav1','libaom-av1']
    audio_codec: Literal['libopus','aac','eac3','none']
    preset: Literal['p1','p2','p3','p4','p5','p6','p7','extraquality']
    audio_kbps: Literal[64,96,128,160,192,256]
    container: Literal['mp4','mkv']
    tune: Literal['hq','ll','ull','lossless']
    max_output_fps: Optional[float] = Field(default=None, ge=0, le=1000)


class PresetProfilesResponse(BaseModel):
    profiles: list[PresetProfile]
    default: str | None


class SetDefaultPresetRequest(BaseModel):
    name: str


class SizeButtons(BaseModel):
    buttons: list[float]


class RetentionHours(BaseModel):
    hours: int


# Queue system models
class JobMetadata(BaseModel):
    """Metadata for a single compression job in the queue."""
    task_id: str
    job_id: str  # Upload job_id
    filename: str
    target_size_mb: float
    video_codec: str
    state: Literal['queued', 'running', 'completed', 'failed', 'canceled'] = 'queued'
    progress: float = 0.0
    phase: Optional[Literal['queued', 'encoding', 'finalizing', 'done']] = 'queued'  # NEW: Current phase
    created_at: float  # Unix timestamp
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    output_path: Optional[str] = None
    final_size_mb: Optional[float] = None
    # Time estimation fields
    last_progress_update: Optional[float] = None  # Timestamp of last progress update
    estimated_completion_time: Optional[float] = None  # Estimated Unix timestamp when job will complete


class QueueStatusResponse(BaseModel):
    """Response showing all jobs in the system."""
    active_jobs: list[JobMetadata]
    queued_count: int
    running_count: int
    completed_count: int  # Recently completed (last hour)


# Batch processing models
class BatchItemStatus(BaseModel):
    index: int
    job_id: str
    task_id: str
    original_filename: str
    stored_filename: str
    output_filename: str
    state: Literal['queued', 'running', 'completed', 'failed', 'canceled'] = 'queued'
    progress: float = 0.0
    error: Optional[str] = None
    output_path: Optional[str] = None
    download_url: str


class BatchCreateResponse(BaseModel):
    batch_id: str
    item_count: int
    state: Literal['queued', 'running', 'completed', 'completed_with_errors', 'failed'] = 'queued'
    items: list[BatchItemStatus]


class BatchStatusResponse(BaseModel):
    batch_id: str
    state: Literal['queued', 'running', 'completed', 'completed_with_errors', 'failed'] = 'queued'
    item_count: int
    queued_count: int
    running_count: int
    completed_count: int
    failed_count: int
    overall_progress: float
    items: list[BatchItemStatus]
    zip_download_url: Optional[str] = None
