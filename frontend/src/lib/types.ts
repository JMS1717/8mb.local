/**
 * Shared TypeScript types for API payload shapes.
 *
 * Property names match the backend models exactly –
 * do NOT rename without updating the Python side.
 */

/** Response from POST /api/upload (ffprobe result + bitrate estimate). */
export interface ProbeResult {
	job_id: string;
	filename: string;
	duration_s: number;
	original_video_bitrate_kbps: number | null;
	original_audio_bitrate_kbps: number | null;
	original_width: number | null;
	original_height: number | null;
	/** Detected video frame rate (avg), when ffprobe reports it */
	original_video_fps?: number | null;
	estimate_total_kbps: number;
	estimate_video_kbps: number;
	warn_low_quality: boolean;
}

/** Request body for POST /api/compress. */
export interface CompressOptions {
	job_id: string;
	filename: string;
	target_size_mb: number;
	video_codec: string;
	audio_codec: string;
	audio_bitrate_kbps: number;
	preset: string;
	tune: string;
	container: 'mp4' | 'mkv';
	max_width?: number | null;
	max_height?: number | null;
	start_time?: string | null;
	end_time?: string | null;
	force_hw_decode?: boolean;
	fast_mp4_finalize?: boolean;
	auto_resolution?: boolean;
	min_auto_resolution?: number;
	target_resolution?: number | null;
	audio_only?: boolean;
	target_video_bitrate_kbps?: number | null;
	max_output_fps?: number | null;
}

/** Response from GET /api/jobs/{task_id}/status. */
export interface JobStatus {
	state: string;
	progress: number | null;
	detail: string | null;
}

/** Response from GET /api/codecs/available. */
export interface CodecInfo {
	hardware_type: string;
	available_encoders: Record<string, string>;
	enabled_codecs: string[];
}

/** Response from GET /api/settings/auth. */
export interface SettingsState {
	auth_enabled: boolean;
	auth_user: string;
}

/** Payload for batch uploads (sent as multipart form fields). */
export interface BatchUploadPayload {
	target_size_mb: number;
	video_codec: string;
	audio_codec: string;
	audio_bitrate_kbps: number;
	preset: string;
	container: 'mp4' | 'mkv';
	tune: string;
	max_width?: number | null;
	max_height?: number | null;
	start_time?: string | null;
	end_time?: string | null;
	force_hw_decode?: boolean;
	fast_mp4_finalize?: boolean;
	auto_resolution?: boolean;
	min_auto_resolution?: number;
	target_resolution?: number | null;
	audio_only?: boolean;
	target_video_bitrate_kbps?: number | null;
	max_output_fps?: number | null;
}

/** Individual item within a batch status response. */
export interface BatchItemStatus {
	index: number;
	job_id: string;
	task_id: string;
	original_filename: string;
	stored_filename: string;
	output_filename: string;
	output_path: string;
	state: string;
	progress: number;
	error: string | null;
	download_url: string;
}

/** Response from GET /api/batches/{batch_id}/status. */
export interface BatchStatusResponse {
	batch_id: string;
	state: string;
	item_count: number;
	queued_count: number;
	running_count: number;
	completed_count: number;
	failed_count: number;
	overall_progress: number;
	items: BatchItemStatus[];
	zip_download_url: string | null;
}

/** Compression statistics returned in the SSE "done" event. */
export interface CompressStats {
	input_path: string;
	output_path: string;
	duration_s: number;
	target_size_mb: number;
	final_size_mb: number;
	target_video_bitrate_kbps?: number | null;
}

/** Preset profile as returned by the settings API. */
export interface PresetProfile {
	name: string;
	target_mb: number;
	video_codec: string;
	audio_codec: string;
	preset: string;
	audio_kbps: number;
	container: 'mp4' | 'mkv';
	tune: string;
	/** Optional cap; omitted or null = same as source when applying preset */
	max_output_fps?: number | null;
}

/** Hardware info from GET /api/hardware. */
export interface HardwareInfo {
	type: string;
	available_encoders: Record<string, string>;
	preferred?: string;
}

/** Encoder test result from GET /api/system/encoder-tests. */
export interface EncoderTestResult {
	codec: string;
	actual_encoder: string;
	passed: boolean;
	encode_passed: boolean;
	encode_message: string;
	decode_passed: boolean | null;
	decode_message: string | null;
}
