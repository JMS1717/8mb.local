/** Codec labels and groups shared by the single-file and batch screens. */
export type CodecGroup = 'nvidia' | 'intel' | 'amd' | 'vaapi' | 'cpu';

export type CodecOption = {
  value: string;
  label: string;
  group: CodecGroup;
};

export const CODEC_DEFINITIONS: CodecOption[] = [
  { value: 'av1_nvenc', label: 'AV1 (NVIDIA)', group: 'nvidia' },
  { value: 'hevc_nvenc', label: 'HEVC (H.265, NVIDIA)', group: 'nvidia' },
  { value: 'h264_nvenc', label: 'H.264 (NVIDIA)', group: 'nvidia' },
  { value: 'av1_qsv', label: 'AV1 (Intel Quick Sync)', group: 'intel' },
  { value: 'hevc_qsv', label: 'HEVC (H.265, Intel Quick Sync)', group: 'intel' },
  { value: 'h264_qsv', label: 'H.264 (Intel Quick Sync)', group: 'intel' },
  { value: 'av1_amf', label: 'AV1 (AMD AMF)', group: 'amd' },
  { value: 'hevc_amf', label: 'HEVC (H.265, AMD AMF)', group: 'amd' },
  { value: 'h264_amf', label: 'H.264 (AMD AMF)', group: 'amd' },
  { value: 'av1_vaapi', label: 'AV1 (VAAPI)', group: 'vaapi' },
  { value: 'hevc_vaapi', label: 'HEVC (H.265, VAAPI)', group: 'vaapi' },
  { value: 'h264_vaapi', label: 'H.264 (VAAPI)', group: 'vaapi' },
  { value: 'libsvtav1', label: 'AV1 (SVT-AV1, CPU fallback)', group: 'cpu' },
  { value: 'libx265', label: 'HEVC (H.265, CPU)', group: 'cpu' },
  { value: 'libx264', label: 'H.264 (CPU)', group: 'cpu' },
];

export function availableCodecOptions(enabledCodecs: unknown): CodecOption[] {
  const enabled = new Set(
    Array.isArray(enabledCodecs) ? enabledCodecs.map((value) => String(value)) : [],
  );
  return CODEC_DEFINITIONS.filter((codec) => enabled.has(codec.value));
}

/** User-facing names for raw FFmpeg encoder tokens shown in diagnostics. */
export function encoderDisplayName(value: string): string {
  if (value === 'libsvtav1') return 'SVT-AV1 (FFmpeg: libsvtav1)';
  if (value === 'libx264') return 'H.264 CPU fallback (FFmpeg: libx264)';
  if (value === 'libx265') return 'HEVC CPU fallback (FFmpeg: libx265)';
  return value;
}

export function codecIcon(group: CodecGroup): string {
  if (group === 'nvidia') return '🟢';
  if (group === 'intel') return '🔵';
  if (group === 'amd') return '🔴';
  if (group === 'vaapi') return '🟣';
  return '⚪';
}

export function codecColor(group: string): string {
  if (group === 'nvidia') return '#22c55e';
  if (group === 'intel') return '#60a5fa';
  if (group === 'amd') return '#f87171';
  if (group === 'vaapi') return '#c084fc';
  return '#9ca3af';
}
