<script lang="ts">
  import { onMount } from 'svelte';
  import { FPS_CAP_VALUES, type FpsCap } from '$lib/fpsCap';
  import { encoderDisplayName } from '$lib/codecs';

  type AuthSettings = { auth_enabled: boolean; auth_user: string | null };
  type DefaultPresets = {
	target_mb: number;
	video_codec: string;
	audio_codec: string;
	preset: string;
	audio_kbps: number;
	container: string;
	tune: string;
  };
  type CodecVisibilitySettings = {
	h264_nvenc: boolean;
	hevc_nvenc: boolean;
	av1_nvenc: boolean;
	h264_qsv: boolean;
	hevc_qsv: boolean;
	av1_qsv: boolean;
	h264_vaapi: boolean;
	hevc_vaapi: boolean;
	av1_vaapi: boolean;
	h264_amf: boolean;
	hevc_amf: boolean;
	av1_amf: boolean;
	libx264: boolean;
	libx265: boolean;
	libsvtav1: boolean;
	libaom_av1: boolean;
  };
  type FolderWatchSettings = {
	enabled: boolean;
	input_folder: string;
	profile: string | null;
	output_mode: 'same_folder' | 'specific_folder';
	output_folder: string;
	original_behavior: 'keep' | 'delete' | 'move';
	existing_files: 'new_only' | 'process_existing';
	recursive: boolean;
	stable_seconds: number;
	poll_interval_seconds: number;
  };

  let saving = false;
  let message = '';
  let error = '';
	// History toggle
	let historyEnabled = false;
	// Startup banner
	let showCodecSyncBanner = false;

  // Auth
  let authEnabled = false;
  let username = 'admin';
  let newPassword = '';
  let confirmPassword = '';

  // Presets
  let targetMB = 19.7;
  let videoCodec = 'av1_nvenc';
  let audioCodec = 'libopus';
  let preset = 'p4';
  let audioKbps = 128;
  let container = 'mp4';
  let tune = 'hq';
  /** Included in preset profiles created via “Add from current defaults”. */
  let profileMaxFpsCap: FpsCap = '';

  // Codec visibility - individual codecs
  let codecSettings: CodecVisibilitySettings = {
	h264_nvenc: true,
	hevc_nvenc: true,
	av1_nvenc: true,
	h264_qsv: true,
	hevc_qsv: true,
	av1_qsv: true,
	h264_vaapi: true,
	hevc_vaapi: true,
	av1_vaapi: true,
	h264_amf: true,
	hevc_amf: true,
	av1_amf: true,
	libx264: true,
	libx265: true,
	libsvtav1: true,
	libaom_av1: false,
  };

	// New settings state
	let sizeButtons: number[] = [];
	let newSizeValue: number | null = null;
	let presetProfiles: any[] = [];
	let defaultPresetName: string | null = null;
	let newPresetName: string = '';
	let retentionHours: number = 1;
	  let workerConcurrency: number = 4;
	  let workerConcurrencyMode: 'auto' | 'manual' = 'auto';
	  let folderWatch: FolderWatchSettings = {
		enabled: false, input_folder: '', profile: null, output_mode: 'same_folder',
		output_folder: '', original_behavior: 'keep', existing_files: 'new_only',
		recursive: false, stable_seconds: 5, poll_interval_seconds: 5,
	  };
	  // Hardware tests state
	  let hwTests: Array<any> = [];
	  let hwTestsLoading: boolean = false;
	  let hwTestsError: string = '';

	  onMount(async () => {
	try {
			  const [authRes, presetsRes, codecsRes, historyRes] = await Promise.all([
		fetch('/api/settings/auth'),
		fetch('/api/settings/presets'),
		fetch('/api/settings/codecs'),
		fetch('/api/settings/history')
	  ]);
	  if (authRes.ok) {
		const a: AuthSettings = await authRes.json();
		authEnabled = !!a.auth_enabled;
		username = a.auth_user || 'admin';
	  }
	  if (presetsRes.ok) {
		const p: DefaultPresets = await presetsRes.json();
		targetMB = p.target_mb;
		videoCodec = p.video_codec;
		audioCodec = p.audio_codec;
		preset = p.preset;
		audioKbps = p.audio_kbps;
		container = p.container;
		tune = p.tune;
	  }
	  if (codecsRes.ok) {
		const c = await codecsRes.json();
		codecSettings = {
			h264_nvenc: !!c.h264_nvenc,
			hevc_nvenc: !!c.hevc_nvenc,
			av1_nvenc: !!c.av1_nvenc,
			h264_qsv: c.h264_qsv !== undefined ? !!c.h264_qsv : true,
			hevc_qsv: c.hevc_qsv !== undefined ? !!c.hevc_qsv : true,
			av1_qsv: c.av1_qsv !== undefined ? !!c.av1_qsv : true,
			h264_vaapi: c.h264_vaapi !== undefined ? !!c.h264_vaapi : true,
			hevc_vaapi: c.hevc_vaapi !== undefined ? !!c.hevc_vaapi : true,
			av1_vaapi: c.av1_vaapi !== undefined ? !!c.av1_vaapi : true,
			h264_amf: c.h264_amf !== undefined ? !!c.h264_amf : true,
			hevc_amf: c.hevc_amf !== undefined ? !!c.hevc_amf : true,
			av1_amf: c.av1_amf !== undefined ? !!c.av1_amf : true,
			libx264: !!c.libx264,
			libx265: !!c.libx265,
			libsvtav1: c.libsvtav1 !== undefined ? !!c.libsvtav1 : true,
			libaom_av1: c.libaom_av1 !== undefined ? !!c.libaom_av1 : false,
		};
	  }
	  if (historyRes.ok) {
		const h = await historyRes.json();
		historyEnabled = h.enabled || false;
	  }
	  // Load JSON-backed size buttons and presets list and retention hours
	  try {
		const sb = await fetch('/api/settings/size-buttons');
		if (sb.ok) { const js = await sb.json(); sizeButtons = js.buttons || []; }
	  } catch {}
	  try {
		const pp = await fetch('/api/settings/preset-profiles');
		if (pp.ok) { const js = await pp.json(); presetProfiles = js.profiles || []; defaultPresetName = js.default || null; }
	  } catch {}
	  try {
		const rh = await fetch('/api/settings/retention-hours');
		if (rh.ok) { const js = await rh.json(); retentionHours = js.hours ?? 1; }
	  } catch {}
	  try {
		const wc = await fetch('/api/settings/worker-concurrency');
		if (wc.ok) { const js = await wc.json(); workerConcurrency = js.concurrency ?? 12; workerConcurrencyMode = js.mode === 'manual' ? 'manual' : 'auto'; }
	  } catch {}
	  try {
		const fw = await fetch('/api/settings/folder-watch');
		if (fw.ok) { const js = await fw.json(); folderWatch = { ...folderWatch, ...js }; }
	  } catch {}

			// Load initial hardware test results (best-effort)
			try {
				const t = await fetch('/api/system/encoder-tests');
				if (t.ok) {
					const js = await t.json();
					hwTests = js.results || [];
				}
			} catch {}

      // Startup info for first-boot banner
      try {
        const si = await fetch('/api/startup/info');
        if (si.ok) {
          const js = await si.json();
          const bootId = js.boot_id as string | null;
          const synced = !!js.codec_visibility_synced;
          const key = '8mblocal:lastSeenBootId';
          const lastSeen = window.localStorage.getItem(key);
          if (synced && bootId && bootId !== lastSeen) {
            showCodecSyncBanner = true;
          }
        }
      } catch {}
	} catch (e) {
	  error = 'Failed to load settings';
	}
  });

	async function rerunHardwareTests(){
		hwTestsError = '';
		hwTestsLoading = true;
		try {
			const res = await fetch('/api/system/encoder-tests/rerun', { method: 'POST' });
			if (res.ok) {
				const js = await res.json();
				hwTests = js.results || [];
				message = 'Hardware tests re-ran successfully';
			} else {
				const d = await res.json().catch(()=>({}));
				hwTestsError = d.detail || 'Failed to re-run hardware tests';
			}
		} catch (e) {
			hwTestsError = 'Failed to re-run hardware tests';
		} finally {
			hwTestsLoading = false;
		}
	}

  async function saveAuth() {
	error = '';
	message = '';
	const wasAuthEnabled = authEnabled;
	if (authEnabled && !username.trim()) {
	  error = 'Username is required when authentication is enabled';
	  return;
	}
	if (authEnabled && !wasAuthEnabled && !newPassword.trim()) {
	  error = 'Set a password before enabling authentication';
	  return;
	}
	if (authEnabled && newPassword && newPassword !== confirmPassword) {
	  error = 'Passwords do not match';
	  return;
	}
	saving = true;
	try {
	  const payload: any = { auth_enabled: authEnabled, auth_user: username.trim() };
	  if (authEnabled && newPassword) payload.auth_pass = newPassword;
	  const res = await fetch('/api/settings/auth', {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(payload)
	  });
	  if (res.ok) {
		const data = await res.json();
		message = data.message || 'Saved authentication settings';
		newPassword = '';
		confirmPassword = '';
		if (authEnabled && !wasAuthEnabled) {
		  // A page that was opened while auth was disabled will not reliably
		  // trigger the browser's Basic-auth challenge on a later fetch. Reload
		  // the protected SPA route so the browser can show its native prompt.
		  message = 'Authentication enabled. Reloading to sign in…';
		  window.setTimeout(() => window.location.reload(), 250);
		}
	  } else {
		const data = await res.json();
		error = data.detail || 'Failed to save authentication';
	  }
	} catch (e) {
	  error = 'Failed to save authentication';
	} finally {
	  saving = false;
	}
  }

  async function saveDefaults() {
	error = '';
	message = '';
	if (targetMB < 1) {
	  error = 'Target size must be at least 1 MB';
	  return;
	}
	saving = true;
	try {
	  const res = await fetch('/api/settings/presets', {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({
		  target_mb: targetMB,
		  video_codec: videoCodec,
		  audio_codec: audioCodec,
		  preset,
		  audio_kbps: audioKbps,
		  container,
		  tune
		})
	  });
	  if (res.ok) {
		const data = await res.json();
		message = data.message || 'Saved default presets';
	  } else {
		const data = await res.json();
		error = data.detail || 'Failed to save presets';
	  }
	} catch (e) {
	  error = 'Failed to save presets';
	} finally {
	  saving = false;
	}
  }

  async function saveCodecs() {
	error = '';
	message = '';
	saving = true;
	try {
	  const res = await fetch('/api/settings/codecs', {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(codecSettings)
	  });
	  if (res.ok) {
		const data = await res.json();
		message = data.message || 'Saved codec visibility settings';
	  } else {
		const data = await res.json();
		error = data.detail || 'Failed to save codec settings';
	  }
	} catch (e) {
	  error = 'Failed to save codec settings';
	} finally {
	  saving = false;
	}
  }

  async function saveHistorySettings() {
	error = '';
	message = '';
	saving = true;
	try {
	  const res = await fetch('/api/settings/history', {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ enabled: historyEnabled })
	  });
	  if (res.ok) {
		const data = await res.json();
		message = data.message || 'Saved history settings';
	  } else {
		const data = await res.json();
		error = data.detail || 'Failed to save history settings';
	  }
	} catch (e) {
	  error = 'Failed to save history settings';
	} finally {
	  saving = false;
	}
  }

	// Save size buttons
	async function saveSizeButtons(){
		error = ''; message = ''; saving = true;
		try {
			const res = await fetch('/api/settings/size-buttons', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ buttons: sizeButtons }) });
			if (res.ok) { message = 'Saved size buttons'; } else { const d = await res.json(); error = d.detail || 'Failed to save size buttons'; }
		} catch { error = 'Failed to save size buttons'; } finally { saving = false; }
	}
	function removeSizeButton(idx:number){ sizeButtons = sizeButtons.filter((_,i)=>i!==idx); }
	function addSizeButton(){ if (newSizeValue && newSizeValue>0){ sizeButtons = Array.from(new Set([...sizeButtons, Number(newSizeValue)])).sort((a,b)=>a-b); newSizeValue=null; } }

	// Preset profiles
	async function addPresetFromCurrent(){
		if (!newPresetName.trim()) { error='Preset name required'; return; }
		saving = true; error=''; message='';
		try {
			const maxFpsPayload = profileMaxFpsCap === '' ? null : Number(profileMaxFpsCap);
			const res = await fetch('/api/settings/preset-profiles', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
				name: newPresetName.trim(), target_mb: targetMB, video_codec: videoCodec, audio_codec: audioCodec, preset, audio_kbps: audioKbps, container, tune,
				max_output_fps: maxFpsPayload
			})});
			if (res.ok){ message='Added preset'; presetProfiles = [...presetProfiles, { name:newPresetName.trim(), target_mb: targetMB, video_codec: videoCodec, audio_codec: audioCodec, preset, audio_kbps: audioKbps, container, tune, max_output_fps: maxFpsPayload }]; newPresetName=''; }
			else { const d = await res.json(); error = d.detail || 'Failed to add preset'; }
		} catch { error = 'Failed to add preset'; } finally { saving=false; }
	}
	async function deletePreset(name:string){
		saving=true; error=''; message='';
		try { const res = await fetch(`/api/settings/preset-profiles/${encodeURIComponent(name)}`, { method:'DELETE' });
			if (res.ok){
				message='Deleted preset';
				presetProfiles = presetProfiles.filter(p=>p.name!==name);
				if (defaultPresetName===name) defaultPresetName = presetProfiles[0]?.name ?? null;
			}
			else { const d = await res.json(); error = d.detail || 'Failed to delete preset'; }
		} catch { error='Failed to delete preset'; } finally { saving=false; }
	}
	async function saveDefaultPreset(){
		if (!defaultPresetName) { error='Select a default preset'; return; }
		saving=true; error=''; message='';
		try { const res = await fetch('/api/settings/preset-profiles/default', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ name: defaultPresetName }) });
			if (res.ok){ message='Default preset updated'; }
			else { const d = await res.json(); error = d.detail || 'Failed to set default'; }
		} catch { error='Failed to set default'; } finally { saving=false; }
	}

	// Retention hours
	async function saveRetention(){
		saving=true; error=''; message='';
		try { const res = await fetch('/api/settings/retention-hours', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ hours: retentionHours }) });
			if (res.ok){ message='Saved retention hours'; } else { const d = await res.json(); error = d.detail || 'Failed to save retention'; }
		} catch { error='Failed to save retention'; } finally { saving=false; }
	}

	// Worker concurrency
	async function saveConcurrency(){
		saving=true; error=''; message='';
		if (workerConcurrencyMode === 'manual' && workerConcurrency < 1) { error='Concurrency must be at least 1'; saving=false; return; }
		if (workerConcurrencyMode === 'manual' && workerConcurrency > 20) { error='Concurrency should not exceed 20'; saving=false; return; }
		try { const res = await fetch('/api/settings/worker-concurrency', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ mode: workerConcurrencyMode, concurrency: workerConcurrency }) });
			if (res.ok){ 
				const d = await res.json();
				message = d.message || 'Saved worker concurrency. Restart container to apply.'; 
			} else { const d = await res.json(); error = d.detail || 'Failed to save concurrency'; }
		} catch { error='Failed to save concurrency'; } finally { saving=false; }
	}

	async function saveFolderWatch(){
		saving = true; error = ''; message = '';
		try {
			const res = await fetch('/api/settings/folder-watch', {
				method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(folderWatch)
			});
			if (res.ok) { const js = await res.json(); folderWatch = { ...folderWatch, ...js }; message = folderWatch.enabled ? 'Folder Watch enabled' : 'Folder Watch settings saved'; }
			else { const js = await res.json(); error = js.detail || 'Failed to save Folder Watch settings'; }
		} catch { error = 'Failed to save Folder Watch settings'; }
		finally { saving = false; }
	}
</script>

<style>
  /* Keep the page dead-simple and avoid any overlays that might block <select> popovers */
  .container { max-width: 760px; margin: 0 auto; padding: 24px; display: flex; flex-direction: column; }
  .card { background: #111827; border: 1px solid #374151; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .label { display: block; color: #d1d5db; margin-bottom: 6px; font-size: 14px; }
  .input, .select { width: 100%; padding: 8px 10px; color: #e5e7eb; background: #1f2937; border: 1px solid #374151; border-radius: 8px; }
  .btn { padding: 10px 12px; color: white; background: #2563eb; border: none; border-radius: 8px; cursor: pointer; }
  .btn:disabled { background: #4b5563; cursor: not-allowed; }
  .btn.alt { background: #059669; }
  .title { color: white; font-size: 20px; font-weight: 600; margin-bottom: 10px; }
  .hdr { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
  .msg { padding: 10px; border-radius: 8px; margin-bottom: 12px; }
  .msg.ok { background: rgba(16,185,129,.15); border: 1px solid #10b981; color: #a7f3d0; }
  .msg.err { background: rgba(239,68,68,.15); border: 1px solid #ef4444; color: #fecaca; }
  .switch { display:flex; align-items:center; gap:8px; }
  .switch input { transform: scale(1.2); }
	.folder-watch-summary { display:flex; align-items:center; justify-content:space-between; gap:16px; cursor:pointer; list-style:none; }
	.folder-watch-summary::-webkit-details-marker { display:none; }
	.folder-watch-title { color:white; font-size:20px; font-weight:600; }
	.folder-watch-badge { display:inline-block; margin-left:8px; padding:3px 8px; border-radius:999px; background:#374151; color:#bfdbfe; font-size:11px; font-weight:600; letter-spacing:.04em; text-transform:uppercase; }
	.folder-watch-status { display:inline-block; margin-left:6px; padding:3px 8px; border-radius:999px; background:#374151; color:#d1d5db; font-size:11px; font-weight:600; }
	.folder-watch-status.enabled { background:rgba(16,185,129,.18); color:#a7f3d0; }
	.folder-watch-summary-text { display:block; margin-top:6px; color:#9ca3af; font-size:13px; line-height:1.45; }
	.folder-watch-chevron { color:#93c5fd; font-size:22px; transition: transform .15s ease; }
	.folder-watch-card details[open] .folder-watch-chevron { transform: rotate(180deg); }
	.folder-watch-content { margin-top:16px; padding-top:16px; border-top:1px solid #374151; }
	.folder-watch-help { margin:0 0 14px; padding:10px 12px; border-left:3px solid #3b82f6; border-radius:6px; background:rgba(59,130,246,.1); color:#cbd5e1; font-size:13px; line-height:1.5; }
	.banner { display:flex; justify-content:space-between; align-items:center; gap:12px; background: rgba(59,130,246,.12); border:1px solid #3b82f6; color:#bfdbfe; padding:10px 12px; border-radius:8px; margin-bottom:12px; }
	.banner button { background: transparent; border: none; color:#93c5fd; cursor:pointer; font-size:14px; }
</style>

<div class="container">
  <div class="hdr">
	<h1 class="title" style="font-size:24px">Settings</h1>
	<a href="/" class="btn" style="text-decoration:none; background:#374151">← Back</a>
  </div>

	<!-- Support is intentionally first and expanded by default. -->
	<div class="card" style="padding:8px">
		<details open>
			<summary style="cursor:pointer; list-style:none; display:flex; align-items:center; gap:8px">
				<span class="title" style="margin:0; font-size:18px">Support the Project</span>
				<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
			</summary>
			<div style="margin-top:12px">
				<p class="label" style="color:#cbd5e1">If 8mb.local helps you, a small gesture goes a long way. Thank you for supporting continued development!</p>
				<div style="display:flex; flex-wrap:wrap; gap:10px; margin-top:10px">
					<a class="btn" style="text-decoration:none" href="https://www.paypal.com/paypalme/jasonselsley" target="_blank" rel="noopener noreferrer">Support via PayPal</a>
					<a class="btn" style="text-decoration:none; background:#374151" href="https://github.com/JMS1717/8mb.local" target="_blank" rel="noopener noreferrer">Star on GitHub</a>
				</div>
			</div>
		</details>
	</div>

	{#if showCodecSyncBanner}
		<div class="banner">
			<div>Hardware availability checked; your saved codec choices were preserved</div>
			<button on:click={() => { try { const siPromise = fetch('/api/startup/info').then(r=>r.ok?r.json():null); siPromise.then(js => { const bootId = js?.boot_id; if (bootId) { window.localStorage.setItem('8mblocal:lastSeenBootId', bootId); } }); } catch {} showCodecSyncBanner = false; }}>Dismiss</button>
		</div>
	{/if}

  {#if message}<div class="msg ok">{message}</div>{/if}
  {#if error}<div class="msg err">{error}</div>{/if}

	<!-- Authentication -->
	<div class="card">
	  <div class="title">Authentication</div>
	  <div class="switch" style="margin-bottom:12px">
		<input id="auth_enabled" type="checkbox" bind:checked={authEnabled} />
		<label class="label" for="auth_enabled" style="margin:0">Require authentication</label>
	  </div>

	  <div class="row">
		<div>
		  <label class="label" for="username">Username</label>
		  <input id="username" class="input" type="text" bind:value={username} placeholder="admin" />
		</div>
		<div>
		  <label class="label" for="newpass">New password (optional)</label>
		  <input id="newpass" class="input" type="password" bind:value={newPassword} />
		</div>
	  </div>
	  {#if newPassword}
		<div style="margin-top:12px">
		  <label class="label" for="confirmpass">Confirm new password</label>
		  <input id="confirmpass" class="input" type="password" bind:value={confirmPassword} />
		</div>
	  {/if}

	  <div style="margin-top:12px">
		<button class="btn" on:click={saveAuth} disabled={saving}>{saving ? 'Saving…' : 'Save authentication'}</button>
	  </div>
	  <p class="label" style="color:#9ca3af; margin-top:12px; margin-bottom:0">
		{#if authEnabled}
		  Basic authentication is active. Saving changes reloads the page only when it is first enabled so the browser can ask for credentials.
		{:else}
		  Authentication is disabled. Set a username and password, then enable it to protect the UI and API.
		{/if}
	  </p>
	</div>

  <!-- Codec Visibility -->
  <div class="card">
	<div class="title">Available Codecs</div>
	<p class="label" style="margin-bottom:16px; color:#9ca3af">
	  Select which codecs appear in the compression page dropdown. Hardware options
	  use NVIDIA NVENC, Intel QSV, AMD AMF on Windows, or Linux VAAPI; software options use the CPU.
	  <a href="/gpu-support" style="color:#3b82f6; text-decoration:underline">View hardware encoding support →</a>
	</p>

	<!-- NVIDIA Section -->
	<div style="margin-bottom:20px">
	  <h3 style="color:#10b981; font-weight:600; font-size:15px; margin-bottom:8px">NVIDIA (NVENC)</h3>
	  <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px">
		<div class="switch">
		  <input id="av1_nvenc" type="checkbox" bind:checked={codecSettings.av1_nvenc} />
		  <label class="label" for="av1_nvenc" style="margin:0">AV1 (RTX 40/50)</label>
		</div>
		<div class="switch">
		  <input id="hevc_nvenc" type="checkbox" bind:checked={codecSettings.hevc_nvenc} />
		  <label class="label" for="hevc_nvenc" style="margin:0">HEVC (H.265)</label>
		</div>
		<div class="switch">
		  <input id="h264_nvenc" type="checkbox" bind:checked={codecSettings.h264_nvenc} />
		  <label class="label" for="h264_nvenc" style="margin:0">H.264</label>
		</div>
	  </div>
	</div>

	<!-- Intel QSV / Linux VAAPI Section -->
	<div style="margin-bottom:20px">
	  <h3 style="color:#60a5fa; font-weight:600; font-size:15px; margin-bottom:8px">Intel Quick Sync / VAAPI</h3>
	  <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px">
		<div class="switch"><input id="av1_qsv" type="checkbox" bind:checked={codecSettings.av1_qsv} /><label class="label" for="av1_qsv" style="margin:0">AV1 (Intel QSV)</label></div>
		<div class="switch"><input id="hevc_qsv" type="checkbox" bind:checked={codecSettings.hevc_qsv} /><label class="label" for="hevc_qsv" style="margin:0">HEVC (Intel QSV)</label></div>
		<div class="switch"><input id="h264_qsv" type="checkbox" bind:checked={codecSettings.h264_qsv} /><label class="label" for="h264_qsv" style="margin:0">H.264 (Intel QSV)</label></div>
		<div class="switch"><input id="av1_vaapi" type="checkbox" bind:checked={codecSettings.av1_vaapi} /><label class="label" for="av1_vaapi" style="margin:0">AV1 (VAAPI)</label></div>
		<div class="switch"><input id="hevc_vaapi" type="checkbox" bind:checked={codecSettings.hevc_vaapi} /><label class="label" for="hevc_vaapi" style="margin:0">HEVC (VAAPI)</label></div>
		<div class="switch"><input id="h264_vaapi" type="checkbox" bind:checked={codecSettings.h264_vaapi} /><label class="label" for="h264_vaapi" style="margin:0">H.264 (VAAPI)</label></div>
	  </div>
	</div>

	<!-- Windows AMD Section -->
	<div style="margin-bottom:20px">
	  <h3 style="color:#f87171; font-weight:600; font-size:15px; margin-bottom:8px">AMD AMF (Windows)</h3>
	  <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px">
		<div class="switch"><input id="av1_amf" type="checkbox" bind:checked={codecSettings.av1_amf} /><label class="label" for="av1_amf" style="margin:0">AV1 (AMD AMF)</label></div>
		<div class="switch"><input id="hevc_amf" type="checkbox" bind:checked={codecSettings.hevc_amf} /><label class="label" for="hevc_amf" style="margin:0">HEVC (AMD AMF)</label></div>
		<div class="switch"><input id="h264_amf" type="checkbox" bind:checked={codecSettings.h264_amf} /><label class="label" for="h264_amf" style="margin:0">H.264 (AMD AMF)</label></div>
	  </div>
	</div>

	<!-- CPU Section -->
	<div style="margin-bottom:20px">
	  <h3 style="color:#9ca3af; font-weight:600; font-size:15px; margin-bottom:8px">CPU (Software Encoding)</h3>
	  <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px">
		<div class="switch">
		  <input id="libsvtav1" type="checkbox" bind:checked={codecSettings.libsvtav1} />
		  <label class="label" for="libsvtav1" style="margin:0">AV1 (SVT-AV1, fast CPU fallback)</label>
		</div>
		<div class="switch">
		  <input id="libx265" type="checkbox" bind:checked={codecSettings.libx265} />
		  <label class="label" for="libx265" style="margin:0">HEVC (H.265)</label>
		</div>
		<div class="switch">
		  <input id="libx264" type="checkbox" bind:checked={codecSettings.libx264} />
		  <label class="label" for="libx264" style="margin:0">H.264</label>
		</div>
	  </div>
	</div>

	<div style="margin-top:16px">
	  <button class="btn" on:click={saveCodecs} disabled={saving}>{saving ? 'Saving…' : 'Save codec settings'}</button>
	</div>
  </div>

	<!-- Hardware Tests -->
	<div class="card">
		<div class="title">Hardware Tests</div>
		<p class="label" style="margin-bottom:12px; color:#9ca3af">Validate hardware encoders/decoders inside the worker. Useful after driver updates or container restarts.</p>
		{#if hwTestsError}
			<div class="msg err">{hwTestsError}</div>
		{/if}
		<div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:12px;">
			<button class="btn" on:click={rerunHardwareTests} disabled={hwTestsLoading}>{hwTestsLoading ? 'Running…' : 'Re-run hardware tests'}</button>
			<button class="btn alt" on:click={async()=>{ try{ const r=await fetch('/api/system/encoder-tests'); if(r.ok){ const js=await r.json(); hwTests = js.results||[]; message='Loaded latest test results'; } }catch{}}} disabled={hwTestsLoading}>Refresh results</button>
		</div>
		{#if hwTests && hwTests.length}
			<ul class="text-sm space-y-1">
				{#each hwTests as t}
					<li class="flex items-center justify-between">
						<span>{encoderDisplayName(t.codec)}{#if t.actual_encoder !== t.codec} <span class="opacity-60">({t.actual_encoder})</span>{/if}</span>
					{#if t.passed === true}
						<span class="text-green-400">PASS</span>
					{:else if t.passed === null}
						<span class="text-gray-400">N/A</span>
					{:else}
							<span class="text-red-400">FAIL</span>
						{/if}
					</li>
				{/each}
			</ul>
		{:else}
			<p class="label" style="color:#9ca3af">No test results available yet.</p>
		{/if}
	</div>

  <!-- Compression History -->
  <div class="card">
	<div class="title">📊 Compression History</div>
	<p class="label" style="margin-bottom:16px; color:#9ca3af">
	  Track completed compression jobs with metadata (filenames, sizes, codecs, presets). No video files are stored.
	</p>

	<div class="switch" style="margin-bottom:16px">
	  <input id="history_enabled" type="checkbox" bind:checked={historyEnabled} />
	  <label class="label" for="history_enabled" style="margin:0">Enable compression history tracking</label>
	</div>

	<div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap">
	  <button class="btn" on:click={saveHistorySettings} disabled={saving}>
		{saving ? 'Saving…' : 'Save history settings'}
	  </button>
	  {#if historyEnabled}
		<a href="/history" class="btn alt" style="text-decoration:none; display:inline-block">
		  View History →
		</a>
	  {/if}
	</div>
  </div>

	<!-- File size buttons -->
	<div class="card">
		<div class="title">File size buttons</div>
		<p class="label" style="color:#9ca3af">Customize the quick-select file size buttons shown on the main screen.</p>
		<div style="display:flex; flex-wrap:wrap; gap:8px; margin:10px 0">
			{#each sizeButtons as b, i}
				<span style="display:inline-flex; align-items:center; gap:6px; background:#1f2937; border:1px solid #374151; border-radius:8px; padding:6px 8px">
					{b} MB
					<button class="btn" style="background:#374151; padding:4px 8px" on:click={()=>removeSizeButton(i)}>Remove</button>
				</span>
			{/each}
		</div>
		<div class="row">
			<div>
				<label class="label" for="new-size-value">Add size (MB)</label>
				<input id="new-size-value" class="input" type="number" min="1" bind:value={newSizeValue} />
			</div>
			<div style="display:flex; align-items:flex-end">
				<button class="btn" on:click={addSizeButton} disabled={saving}>Add</button>
			</div>
		</div>
		<div style="margin-top:12px">
			<button class="btn alt" on:click={saveSizeButtons} disabled={saving}>{saving ? 'Saving…' : 'Save size buttons'}</button>
		</div>
	</div>

	<!-- Preset profiles -->
	<div class="card">
		<div class="title">Preset profiles</div>
		<p class="label" style="color:#9ca3af">Create multiple presets you can select on the main screen (at least 5 supported).</p>
		<div style="margin-bottom:8px">
			<label class="label" for="default-preset">Default preset</label>
			<div class="row">
				<select id="default-preset" class="select" bind:value={defaultPresetName}>
					{#each presetProfiles as p}
						<option value={p.name}>{p.name}</option>
					{/each}
				</select>
				<button class="btn" on:click={saveDefaultPreset} disabled={saving}>{saving ? 'Saving…' : 'Set default'}</button>
			</div>
		</div>
		<div style="margin-top:12px">
			<div class="row">
				<div>
					<label class="label" for="profile-max-fps">Max frame rate (stored in new presets)</label>
					<p class="label" style="color:#6b7280; font-size:12px; margin:4px 0 8px">Defaults to same as input unless you choose a cap.</p>
					<select id="profile-max-fps" class="select" bind:value={profileMaxFpsCap}>
						<option value="">Same as input (default)</option>
						{#each FPS_CAP_VALUES as v}
							<option value={v}>{v} fps cap</option>
						{/each}
					</select>
				</div>
			</div>
			<div class="row" style="margin-top:12px">
				<div>
					<label class="label" for="new-preset-name">New preset name</label>
					<input id="new-preset-name" class="input" type="text" bind:value={newPresetName} placeholder="e.g., H265 9.7MB (NVENC)" />
				</div>
				<div style="display:flex; align-items:flex-end">
					<button class="btn" on:click={addPresetFromCurrent} disabled={saving}>{saving ? 'Saving…' : 'Add from current defaults'}</button>
				</div>
			</div>
		</div>
		{#if presetProfiles.length}
			<div style="margin-top:12px">
				<div class="row" style="grid-template-columns: 1fr auto; gap:8px">
					{#each presetProfiles as p}
						<div style="background:#1f2937; border:1px solid #374151; border-radius:8px; padding:10px">
							<div style="font-weight:600">{p.name}</div>
							<div style="font-size:12px; color:#9ca3af">{p.video_codec} • {p.audio_codec} • {p.preset} • {p.target_mb}MB{#if p.max_output_fps != null && p.max_output_fps > 0} • max {p.max_output_fps} fps{/if}</div>
						</div>
						<div style="display:flex; align-items:center"><button class="btn" style="background:#374151" on:click={()=>deletePreset(p.name)} disabled={saving}>Delete</button></div>
					{/each}
				</div>
			</div>
		{/if}
	</div>

	<!-- Retention -->
	<div class="card">
		<div class="title">File retention</div>
		<p class="label" style="color:#9ca3af">How long files remain on the server before automatic deletion.</p>
		<div class="row">
			<div>
				<label class="label" for="retention-hours">Hours</label>
				<input id="retention-hours" class="input" type="number" min="0" bind:value={retentionHours} />
			</div>
			<div style="display:flex; align-items:flex-end">
				<button class="btn" on:click={saveRetention} disabled={saving}>{saving ? 'Saving…' : 'Save retention'}</button>
			</div>
		</div>
	</div>

	<!-- Worker Concurrency -->
	<div class="card">
		<div class="title">🚀 Worker Concurrency</div>
		<p class="label" style="color:#9ca3af; margin-bottom: 12px;">
			Choose a safe starting limit for simultaneous jobs. Automatic mode uses available CPU, RAM, and detected GPU memory; it does not reserve that memory.
		</p>
		
		<div class="row">
			<div>
				<label class="label" for="worker-concurrency-mode">Concurrency mode</label>
				<select id="worker-concurrency-mode" class="select" bind:value={workerConcurrencyMode}>
					<option value="auto">Automatic (recommended)</option>
					<option value="manual">Manual limit</option>
				</select>
				{#if workerConcurrencyMode === 'auto'}
					<div style="font-size:12px; color:#9ca3af; margin-top:6px">Current live estimate: {workerConcurrency} job(s). Automatic mode rechecks free VRAM/RAM as jobs start.</div>
				{/if}
			</div>
			{#if workerConcurrencyMode === 'manual'}
				<div>
					<label class="label" for="worker-concurrency">Manual max concurrent jobs</label>
					<input id="worker-concurrency" class="input" type="number" min="1" max="20" bind:value={workerConcurrency} />
				</div>
			{/if}
			<div style="display:flex; align-items:flex-end">
				<button class="btn" on:click={saveConcurrency} disabled={saving}>{saving ? 'Saving…' : 'Save concurrency'}</button>
			</div>
		</div>

		<details style="margin-top: 12px; background: #1f2937; border: 1px solid #374151; border-radius: 8px; padding: 12px;">
			<summary style="cursor: pointer; font-weight: 600; color: #60a5fa; user-select: none;">💡 Guidelines & Recommendations</summary>
			<div style="margin-top: 8px; font-size: 14px; color: #d1d5db; line-height: 1.6;">
				<p style="margin-bottom: 8px;"><strong>Hardware-based recommendations:</strong></p>
				<ul style="margin-left: 20px; margin-bottom: 12px;">
					<li><strong>Large modern NVIDIA GPU:</strong> automatic mode can reach the tested ceiling when free VRAM and RAM allow it</li>
					<li><strong>Mid-range NVIDIA GPU:</strong> automatic mode starts lower from total VRAM and scales down if free VRAM is limited</li>
					<li><strong>Entry-level or CPU-only:</strong> automatic mode stays conservative to protect the system</li>
					<li><strong>CPU-only encoding:</strong> 1-2 jobs per 4 CPU cores (very slow, high CPU usage)</li>
				</ul>
				
				<p style="margin-bottom: 8px;"><strong>Performance considerations:</strong></p>
				<ul style="margin-left: 20px; margin-bottom: 12px;">
					<li><strong>Admission:</strong> automatic mode uses a VRAM headroom reserve and a per-job estimate; it does not reserve the whole GPU</li>
					<li><strong>Memory usage:</strong> each job varies by input, codec, and FFmpeg filters, so the live gate is intentionally conservative</li>
					<li><strong>GPU memory:</strong> external GPU workloads reduce the active automatic limit until free VRAM returns</li>
					<li><strong>Disk I/O:</strong> Higher concurrency increases disk read/write. Use SSD for best performance</li>
				</ul>

				<p style="margin-bottom: 8px;"><strong>Testing recommendations:</strong></p>
				<ul style="margin-left: 20px;">
					<li>Use automatic mode first; it raises or lowers admission as resources change</li>
					<li>Watch completion time and GPU temperature for unusually large or CPU-heavy inputs</li>
					<li>Use a manual limit only after measuring the target workload</li>
					<li>Check the queue page during high load to see which jobs are running simultaneously</li>
				</ul>

				<p style="margin-top: 12px; padding: 8px; background: #fef3c7; color: #92400e; border-radius: 4px;">
					⚠️ <strong>Note:</strong> Automatic resource changes apply to new encode starts. A configured manual limit still requires a restart; current jobs finish normally.
				</p>
			</div>
		</details>
	</div>

  <!-- Defaults -->
  <div class="card">
	<div class="title">Default presets</div>
	<p class="label" style="margin-bottom:12px; color:#9ca3af">
	  These values are loaded when the main page opens. Changes update the current default profile.
	</p>
	<div>
	  <label class="label" for="targetmb">Default target size (MB)</label>
	  <input id="targetmb" class="input" type="number" min="1" bind:value={targetMB} />
	</div>

	<div class="row" style="margin-top:12px">
	  <div>
		<label class="label" for="vcodec">Video codec</label>
		<select id="vcodec" class="select" bind:value={videoCodec}>
		  <optgroup label="NVIDIA NVENC (Hardware)">
			<option value="av1_nvenc">AV1 (NVENC - RTX 40/50)</option>
			<option value="hevc_nvenc">HEVC / H.265 (NVENC)</option>
			<option value="h264_nvenc">H.264 (NVENC)</option>
		  </optgroup>
		  <optgroup label="Intel Quick Sync / VAAPI (Hardware)">
			<option value="av1_qsv">AV1 (Intel QSV)</option>
			<option value="hevc_qsv">HEVC / H.265 (Intel QSV)</option>
			<option value="h264_qsv">H.264 (Intel QSV)</option>
			<option value="av1_vaapi">AV1 (VAAPI)</option>
			<option value="hevc_vaapi">HEVC / H.265 (VAAPI)</option>
			<option value="h264_vaapi">H.264 (VAAPI)</option>
		  </optgroup>
		  <optgroup label="AMD AMF (Windows Hardware)">
			<option value="av1_amf">AV1 (AMD AMF)</option>
			<option value="hevc_amf">HEVC / H.265 (AMD AMF)</option>
			<option value="h264_amf">H.264 (AMD AMF)</option>
		  </optgroup>
		  <optgroup label="CPU (Software)">
			<option value="libsvtav1">AV1 (SVT-AV1, fast CPU fallback)</option>
			<option value="libx265">HEVC / H.265 (CPU)</option>
			<option value="libx264">H.264 (CPU)</option>
		  </optgroup>
		</select>
	  </div>
	  <div>
		<label class="label" for="acodec">Audio codec</label>
		<select id="acodec" class="select" bind:value={audioCodec}>
		  <option value="libopus">Opus</option>
		  <option value="aac">AAC</option>
		  <option value="none">No audio</option>
		</select>
	  </div>
	</div>

	<div class="row" style="margin-top:12px">
	  <div>
		<label class="label" for="preset">Encoder preset (speed ↔ quality)</label>
		<select id="preset" class="select" bind:value={preset}>
		  <option value="p1">P1 (Fastest)</option>
		  <option value="p2">P2</option>
		  <option value="p3">P3</option>
		  <option value="p4">P4 (Balanced)</option>
		  <option value="p5">P5</option>
		  <option value="p6">P6 (High Quality)</option>
		  <option value="p7">P7 (Best quality)</option>
		</select>
		<p class="label" style="margin-top:6px; font-size:12px; color:#9ca3af">How much effort the encoder spends per frame. Separate from NVENC tuning (NVIDIA only).</p>
	  </div>
	  <div>
		<label class="label" for="kbps">Audio bitrate (kbps)</label>
		<select id="kbps" class="select" bind:value={audioKbps}>
		  <option value={64}>64</option>
		  <option value={96}>96</option>
		  <option value={128}>128</option>
		  <option value={160}>160</option>
		  <option value={192}>192</option>
		  <option value={256}>256</option>
		</select>
	  </div>
	</div>

	<div class="row" style="margin-top:12px">
	  <div>
		<label class="label" for="container">Container</label>
		<select id="container" class="select" bind:value={container}>
		  <option value="mp4">MP4</option>
		  <option value="mkv">MKV</option>
		</select>
	  </div>
	  <div>
		<label class="label" for="tune">NVENC tuning <span style="color:#6b7280; font-size:12px">(NVIDIA only)</span></label>
		<select id="tune" class="select" bind:value={tune}>
		  <option value="hq">High quality (files)</option>
		  <option value="ll">Low latency</option>
		  <option value="ull">Ultra-low latency</option>
		  <option value="lossless">Lossless</option>
		</select>
		<p class="label" style="margin-top:6px; font-size:12px; color:#9ca3af">Quality vs turnaround for NVENC; ignored when the default codec is CPU-only.</p>
	  </div>
	</div>

	<div style="margin-top:12px">
	  <button class="btn alt" on:click={saveDefaults} disabled={saving}>{saving ? 'Saving…' : 'Save defaults'}</button>
	</div>
  </div>

	<!-- Folder Watch is intentionally last and collapsed by default. -->
	<div class="card folder-watch-card">
		<details>
			<summary class="folder-watch-summary">
				<span>
					<span class="folder-watch-title">Folder Watch</span><span class="folder-watch-badge">Advanced</span><span class="folder-watch-status" class:enabled={folderWatch.enabled}>{folderWatch.enabled ? 'Enabled' : 'Disabled'}</span>
					<span class="folder-watch-summary-text">Automatically send finished videos from a local, UNC, or mounted Linux folder through the normal compression queue.</span>
				</span>
				<span class="folder-watch-chevron" aria-hidden="true">⌄</span>
			</summary>
			<div class="folder-watch-content">
				<p class="folder-watch-help"><strong>How it works:</strong> the watcher checks the folder on the polling interval and waits until a file's size and modified time stop changing for the stable-seconds value. This prevents processing a video while it is still being copied. Five seconds is a good default; use a longer value for slow network copies.</p>
				<div class="switch" style="margin-bottom:12px">
					<input id="folder-watch-enabled" type="checkbox" bind:checked={folderWatch.enabled} />
					<label class="label" for="folder-watch-enabled" style="margin:0">Enable Folder Watch</label>
				</div>
				<div class="row">
					<div>
						<label class="label" for="folder-watch-input">Input folder</label>
						<input id="folder-watch-input" class="input" type="text" bind:value={folderWatch.input_folder} placeholder="C:\\Videos or /media/videos" />
					</div>
					<div>
						<label class="label" for="folder-watch-profile">Compression profile</label>
						<select id="folder-watch-profile" class="select" bind:value={folderWatch.profile}>
							<option value={null}>Current default profile</option>
							{#each presetProfiles as p}<option value={p.name}>{p.name}</option>{/each}
						</select>
					</div>
				</div>
				<div class="row" style="margin-top:12px">
					<div>
						<label class="label" for="folder-watch-output-mode">Output location</label>
						<select id="folder-watch-output-mode" class="select" bind:value={folderWatch.output_mode}>
							<option value="same_folder">Same folder as input</option>
							<option value="specific_folder">Specific folder</option>
						</select>
					</div>
					<div>
						<label class="label" for="folder-watch-output">Output folder (when selected)</label>
						<input id="folder-watch-output" class="input" type="text" bind:value={folderWatch.output_folder} disabled={folderWatch.output_mode !== 'specific_folder'} />
					</div>
				</div>
				<div class="row" style="margin-top:12px">
					<div>
						<label class="label" for="folder-watch-original">After successful validated output</label>
						<select id="folder-watch-original" class="select" bind:value={folderWatch.original_behavior}>
							<option value="keep">Keep original</option><option value="delete">Delete original</option><option value="move">Move original to processed</option>
						</select>
					</div>
					<div>
						<label class="label" for="folder-watch-existing">Existing files</label>
						<select id="folder-watch-existing" class="select" bind:value={folderWatch.existing_files}>
							<option value="new_only">New files only</option><option value="process_existing">Process existing files</option>
						</select>
					</div>
				</div>
				<div class="row" style="margin-top:12px">
					<div class="switch"><input id="folder-watch-recursive" type="checkbox" bind:checked={folderWatch.recursive} /><label class="label" for="folder-watch-recursive" style="margin:0">Include subfolders</label></div>
					<div>
						<label class="label" for="folder-watch-stable">Wait until file stops changing (seconds)</label>
						<input id="folder-watch-stable" class="input" type="number" min="2" max="60" bind:value={folderWatch.stable_seconds} aria-describedby="folder-watch-stable-help" />
						<p id="folder-watch-stable-help" class="label" style="margin-top:6px; color:#9ca3af; font-size:12px">The file must keep the same size and modified time for this long before processing starts. It is a quiet period, not the video duration.</p>
					</div>
				</div>
				<div class="row" style="margin-top:12px">
					<div><label class="label" for="folder-watch-poll">Polling interval (seconds)</label><input id="folder-watch-poll" class="input" type="number" min="2" max="300" bind:value={folderWatch.poll_interval_seconds} /><p class="label" style="margin-top:6px; color:#9ca3af; font-size:12px">How often the watcher scans the folder; this is separate from the stable-file delay.</p></div>
				</div>
				<div style="margin-top:12px"><button type="button" class="btn alt" on:click={saveFolderWatch} disabled={saving}>{saving ? 'Saving…' : 'Save Folder Watch'}</button></div>
			</div>
		</details>
	</div>
</div>
