import { env } from '$env/dynamic/public';
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

export async function startCompress(payload: any, auth?: {user: string, pass: string}) {
  const headers: Record<string,string> = { 'Content-Type': 'application/json' };
  if (auth) headers['Authorization'] = 'Basic ' + btoa(`${auth.user}:${auth.pass}`);
  const res = await fetch(`${BACKEND}/api/compress`, { method: 'POST', body: JSON.stringify(payload), headers });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export function openProgressStream(taskId: string, auth?: {user: string, pass: string}): EventSource {
  // Build absolute URL for SSE
  let sseUrl: string;
  if (BACKEND) {
    // External backend specified
    sseUrl = `${BACKEND}/api/stream/${taskId}`;
  } else {
    // Same-origin: use relative path
    sseUrl = `/api/stream/${taskId}`;
  }
  
  // EventSource doesn't support custom headers, so if auth is needed,
  // append credentials as query params (backend must support this)
  if (auth) {
    const authParam = btoa(`${auth.user}:${auth.pass}`);
    sseUrl += `?auth=${encodeURIComponent(authParam)}`;
  }
  
  console.log('Opening SSE connection to:', sseUrl);
  const es = new EventSource(sseUrl);
  return es;
}

export function downloadUrl(taskId: string) {
  return `${BACKEND}/api/jobs/${taskId}/download`;
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
