import { env } from '$env/dynamic/public';
const BACKEND = ((env.PUBLIC_BACKEND_URL as string | undefined) || 'http://localhost:8000').replace(/\/$/, '');

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

export async function startCompress(payload: any, auth?: {user: string, pass: string}) {
  const headers: Record<string,string> = { 'Content-Type': 'application/json' };
  if (auth) headers['Authorization'] = 'Basic ' + btoa(`${auth.user}:${auth.pass}`);
  const res = await fetch(`${BACKEND}/api/compress`, { method: 'POST', body: JSON.stringify(payload), headers });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export function openProgressStream(taskId: string, auth?: {user: string, pass: string}): EventSource {
  const url = new URL(`${BACKEND}/api/stream/${taskId}`);
  const es = new EventSource(url.toString());
  return es;
}

export function downloadUrl(taskId: string) {
  return `${BACKEND}/api/jobs/${taskId}/download`;
}
