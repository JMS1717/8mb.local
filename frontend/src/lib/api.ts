import { env } from '$env/dynamic/public';
import type { BatchUploadPayload } from './types';

export type { BatchUploadPayload };

// Re-export SSE helpers so existing imports from '$lib/api' keep working.
export { openProgressStream } from './sse';

// Prefer same-origin when PUBLIC_BACKEND_URL is empty or unset (for baked SPA inside the container)
const RAW = (env.PUBLIC_BACKEND_URL as string | undefined) || '';
const BACKEND = RAW && RAW.trim() !== '' ? RAW.replace(/\/$/, '') : '';

export async function upload(file: File, targetSizeMB: number, audioKbps = 128, auth?: {user: string, pass: string}) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('target_size_mb', String(targetSizeMB));
  fd.append('audio_bitrate_kbps', String(audioKbps));
  const headers: Record<string,string> = {};
  if (auth) headers['Authorization'] = 'Basic ' + btoa(`${auth.user}:${auth.pass}`);
  const res = await fetch(`${BACKEND}/api/upload`, { method: 'POST', body: fd, headers });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// XHR-based upload to report client-side progress
export function uploadWithProgress(
  file: File,
  targetSizeMB: number,
  audioKbps = 128,
  opts?: { auth?: { user: string; pass: string }; onProgress?: (percent: number) => void }
): Promise<any> {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('target_size_mb', String(targetSizeMB));
    fd.append('audio_bitrate_kbps', String(audioKbps));

    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${BACKEND}/api/upload`);
    if (opts?.auth) {
      xhr.setRequestHeader('Authorization', 'Basic ' + btoa(`${opts.auth.user}:${opts.auth.pass}`));
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && opts?.onProgress) {
        const pct = Math.max(0, Math.min(100, Math.round((e.loaded / e.total) * 100)));
        opts.onProgress(pct);
      }
    };
    xhr.onload = () => {
      try {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText || '{}'));
        } else {
          reject(new Error(xhr.responseText || `HTTP ${xhr.status}`));
        }
      } catch (err: any) {
        reject(err);
      }
    };
    xhr.onerror = () => reject(new Error('Network error'));
    xhr.send(fd);
  });
}


export function uploadBatchWithProgress(
  files: File[],
  payload: BatchUploadPayload,
  opts?: { auth?: { user: string; pass: string }; onProgress?: (percent: number, loadedBytes: number, totalBytes: number) => void }
): Promise<any> {
  return new Promise((resolve, reject) => {
    if (!files || files.length === 0) {
      reject(new Error('No files provided for batch upload'));
      return;
    }

    const fd = new FormData();
    for (const file of files) {
      fd.append('files', file, file.name);
    }

    const appendMaybe = (key: string, value: unknown) => {
      if (value === undefined || value === null || value === '') return;
      fd.append(key, String(value));
    };

    appendMaybe('target_size_mb', payload.target_size_mb);
    appendMaybe('video_codec', payload.video_codec);
    appendMaybe('audio_codec', payload.audio_codec);
    appendMaybe('audio_bitrate_kbps', payload.audio_bitrate_kbps);
    appendMaybe('preset', payload.preset);
    appendMaybe('container', payload.container);
    appendMaybe('tune', payload.tune);
    appendMaybe('max_width', payload.max_width);
    appendMaybe('max_height', payload.max_height);
    appendMaybe('start_time', payload.start_time);
    appendMaybe('end_time', payload.end_time);
    appendMaybe('force_hw_decode', payload.force_hw_decode);
    appendMaybe('fast_mp4_finalize', payload.fast_mp4_finalize);
    appendMaybe('auto_resolution', payload.auto_resolution);
    appendMaybe('min_auto_resolution', payload.min_auto_resolution);
    appendMaybe('target_resolution', payload.target_resolution);
    appendMaybe('audio_only', payload.audio_only);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${BACKEND}/api/batches/upload`);
    if (opts?.auth) {
      xhr.setRequestHeader('Authorization', 'Basic ' + btoa(`${opts.auth.user}:${opts.auth.pass}`));
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && opts?.onProgress) {
        const pct = Math.max(0, Math.min(100, Math.round((e.loaded / e.total) * 100)));
        opts.onProgress(pct, e.loaded, e.total);
      }
    };
    xhr.onload = () => {
      try {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText || '{}'));
        } else {
          reject(new Error(xhr.responseText || `HTTP ${xhr.status}`));
        }
      } catch (err: any) {
        reject(err);
      }
    };
    xhr.onerror = () => reject(new Error('Network error'));
    xhr.send(fd);
  });
}

export async function startCompress(payload: any, auth?: {user: string, pass: string}) {
  const headers: Record<string,string> = { 'Content-Type': 'application/json' };
  if (auth) headers['Authorization'] = 'Basic ' + btoa(`${auth.user}:${auth.pass}`);
  const res = await fetch(`${BACKEND}/api/compress`, { method: 'POST', body: JSON.stringify(payload), headers });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export type AutoResolutionOption = {
  mode: 'auto' | 'explicit' | 'original' | 'audio-only';
  targetHeight?: number;
  minAutoHeight?: number;
};

export function downloadUrl(taskId: string) {
  return `${BACKEND}/api/jobs/${taskId}/download`;
}

export function batchZipDownloadUrl(batchId: string) {
  return `${BACKEND}/api/batches/${encodeURIComponent(batchId)}/download.zip`;
}

export async function getBatchStatus(batchId: string, auth?: {user: string, pass: string}) {
  const headers: Record<string, string> = {};
  if (auth) headers['Authorization'] = 'Basic ' + btoa(`${auth.user}:${auth.pass}`);
  const res = await fetch(`${BACKEND}/api/batches/${encodeURIComponent(batchId)}/status`, { headers });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function cancelJob(taskId: string) {
  const res = await fetch(`${BACKEND}/api/jobs/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getAvailableCodecs() {
  const res = await fetch(`${BACKEND}/api/codecs/available`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getSystemCapabilities() {
  const res = await fetch(`${BACKEND}/api/system/capabilities`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getEncoderTestResults() {
  const res = await fetch(`${BACKEND}/api/system/encoder-tests`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getVersion() {
  const res = await fetch(`${BACKEND}/api/version`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// Settings APIs
export async function getPresetProfiles() {
  const res = await fetch(`${BACKEND}/api/settings/preset-profiles`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function setDefaultPreset(name: string) {
  const res = await fetch(`${BACKEND}/api/settings/preset-profiles/default`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function addPresetProfile(profile: any) {
  const res = await fetch(`${BACKEND}/api/settings/preset-profiles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updatePresetProfile(name: string, profile: any) {
  const res = await fetch(`${BACKEND}/api/settings/preset-profiles/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deletePresetProfile(name: string) {
  const res = await fetch(`${BACKEND}/api/settings/preset-profiles/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getSizeButtons() {
  const res = await fetch(`${BACKEND}/api/settings/size-buttons`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateSizeButtons(buttons: number[]) {
  const res = await fetch(`${BACKEND}/api/settings/size-buttons`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ buttons }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getRetentionHours() {
  const res = await fetch(`${BACKEND}/api/settings/retention-hours`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateRetentionHours(hours: number) {
  const res = await fetch(`${BACKEND}/api/settings/retention-hours`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hours }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
