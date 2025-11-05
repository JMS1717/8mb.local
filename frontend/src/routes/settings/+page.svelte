<script lang="ts">
  import { onMount } from 'svelte';

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
	libx264: boolean;
	libx265: boolean;
	libaom_av1: boolean;
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
  let targetMB = 25;
  let videoCodec = 'av1_nvenc';
  let audioCodec = 'libopus';
  let preset = 'p6';
  let audioKbps = 128;
  let container = 'mp4';
  let tune = 'hq';

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
	libx264: true,
	libx265: true,
	libaom_av1: true,
  };

	// New settings state
	let sizeButtons: number[] = [];
	let newSizeValue: number | null = null;
	let presetProfiles: any[] = [];
	let defaultPresetName: string | null = null;
	let newPresetName: string = '';
	let retentionHours: number = 1;
	let workerConcurrency: number = 4;

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
		const c: CodecVisibilitySettings = await codecsRes.json();
		codecSettings = c;
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
		if (wc.ok) { const js = await wc.json(); workerConcurrency = js.concurrency ?? 4; }
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

  async function saveAuth() {
	error = '';
	message = '';
	if (authEnabled && !username.trim()) {
	  error = 'Username is required when authentication is enabled';
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
			const res = await fetch('/api/settings/preset-profiles', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
				name: newPresetName.trim(), target_mb: targetMB, video_codec: videoCodec, audio_codec: audioCodec, preset, audio_kbps: audioKbps, container, tune
			})});
			if (res.ok){ message='Added preset'; presetProfiles = [...presetProfiles, { name:newPresetName.trim(), target_mb: targetMB, video_codec: videoCodec, audio_codec: audioCodec, preset, audio_kbps: audioKbps, container, tune }]; newPresetName=''; }
			else { const d = await res.json(); error = d.detail || 'Failed to add preset'; }
		} catch { error = 'Failed to add preset'; } finally { saving=false; }
	}
	async function deletePreset(name:string){
		saving=true; error=''; message='';
		try { const res = await fetch(`/api/settings/preset-profiles/${encodeURIComponent(name)}`, { method:'DELETE' });
			if (res.ok){ message='Deleted preset'; presetProfiles = presetProfiles.filter(p=>p.name!==name); if (defaultPresetName===name) defaultPresetName=null; }
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
		if (workerConcurrency < 1) { error='Concurrency must be at least 1'; saving=false; return; }
		if (workerConcurrency > 20) { error='Concurrency should not exceed 20'; saving=false; return; }
		try { const res = await fetch('/api/settings/worker-concurrency', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ concurrency: workerConcurrency }) });
			if (res.ok){ 
				const d = await res.json();
				message = d.message || 'Saved worker concurrency. Restart container to apply.'; 
			} else { const d = await res.json(); error = d.detail || 'Failed to save concurrency'; }
		} catch { error='Failed to save concurrency'; } finally { saving=false; }
	}
</script>

<style>
  /* Keep the page dead-simple and avoid any overlays that might block <select> popovers */
  .container { max-width: 760px; margin: 0 auto; padding: 24px; }
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
	.banner { display:flex; justify-content:space-between; align-items:center; gap:12px; background: rgba(59,130,246,.12); border:1px solid #3b82f6; color:#bfdbfe; padding:10px 12px; border-radius:8px; margin-bottom:12px; }
	.banner button { background: transparent; border: none; color:#93c5fd; cursor:pointer; font-size:14px; }
</style>

<div class="container">
  <div class="hdr">
	<h1 class="title" style="font-size:24px">Settings</h1>
	<a href="/" class="btn" style="text-decoration:none; background:#374151">← Back</a>
  </div>

	{#if showCodecSyncBanner}
		<div class="banner">
			<div>Codec visibility synced from hardware tests</div>
			<button on:click={() => { try { const siPromise = fetch('/api/startup/info').then(r=>r.ok?r.json():null); siPromise.then(js => { const bootId = js?.boot_id; if (bootId) { window.localStorage.setItem('8mblocal:lastSeenBootId', bootId); } }); } catch {} showCodecSyncBanner = false; }}>Dismiss</button>
		</div>
	{/if}

  {#if message}<div class="msg ok">{message}</div>{/if}
  {#if error}<div class="msg err">{error}</div>{/if}

  <!-- Authentication (only show if enabled) -->
  {#if authEnabled}
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
	</div>
  {:else}
	<!-- Note when auth is disabled -->
	<div class="card">
	  <div class="title">Authentication</div>
	  <p class="label" style="color:#9ca3af; margin-bottom:12px">
		Authentication is currently disabled. To enable authentication and secure your instance, you'll need to configure it via the Docker container environment variables or configuration file.
	  </p>
	  <p class="label" style="color:#9ca3af; font-size:13px">
		See the <a href="https://github.com/JMS1717/8mb.local" target="_blank" rel="noopener noreferrer" style="color:#3b82f6; text-decoration:underline">documentation</a> for setup instructions.
	  </p>
	</div>
  {/if}

  <!-- Codec Visibility -->
  <div class="card">
	<div class="title">Available Codecs</div>
	<p class="label" style="margin-bottom:16px; color:#9ca3af">
	  Select which codecs appear in the compression page dropdown. Enable codecs that your hardware supports.
	  <a href="/gpu-support" style="color:#3b82f6; text-decoration:underline">View GPU encoding support →</a>
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

	<!-- Intel Section -->
	<div style="margin-bottom:20px">
	  <h3 style="color:#3b82f6; font-weight:600; font-size:15px; margin-bottom:8px">Intel (Quick Sync / QSV)</h3>
	  <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px">
		<div class="switch">
		  <input id="av1_qsv" type="checkbox" bind:checked={codecSettings.av1_qsv} />
		  <label class="label" for="av1_qsv" style="margin:0">AV1 (Arc GPUs)</label>
		</div>
		<div class="switch">
		  <input id="hevc_qsv" type="checkbox" bind:checked={codecSettings.hevc_qsv} />
		  <label class="label" for="hevc_qsv" style="margin:0">HEVC (H.265)</label>
		</div>
		<div class="switch">
		  <input id="h264_qsv" type="checkbox" bind:checked={codecSettings.h264_qsv} />
		  <label class="label" for="h264_qsv" style="margin:0">H.264</label>
		</div>
	  </div>
	</div>

	<!-- AMD/Intel Section (VAAPI - Linux only) -->
	<div style="margin-bottom:20px">
	  <h3 style="color:#ef4444; font-weight:600; font-size:15px; margin-bottom:8px">AMD/Intel (VAAPI - Linux only)</h3>
	  <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px">
		<div class="switch">
		  <input id="av1_vaapi" type="checkbox" bind:checked={codecSettings.av1_vaapi} />
		  <label class="label" for="av1_vaapi" style="margin:0">AV1 VAAPI</label>
		</div>
		<div class="switch">
		  <input id="hevc_vaapi" type="checkbox" bind:checked={codecSettings.hevc_vaapi} />
		  <label class="label" for="hevc_vaapi" style="margin:0">HEVC VAAPI</label>
		</div>
		<div class="switch">
		  <input id="h264_vaapi" type="checkbox" bind:checked={codecSettings.h264_vaapi} />
		  <label class="label" for="h264_vaapi" style="margin:0">H.264 VAAPI</label>
		</div>
	  </div>
	</div>

	<!-- CPU Section -->
	<div style="margin-bottom:20px">
	  <h3 style="color:#9ca3af; font-weight:600; font-size:15px; margin-bottom:8px">CPU (Software Encoding)</h3>
	  <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px">
		<div class="switch">
		  <input id="libaom_av1" type="checkbox" bind:checked={codecSettings.libaom_av1} />
		  <label class="label" for="libaom_av1" style="margin:0">AV1 (Highest Quality)</label>
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
				<label class="label">Add size (MB)</label>
				<input class="input" type="number" min="1" bind:value={newSizeValue} />
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
			<label class="label">Default preset</label>
			<div class="row">
				<select class="select" bind:value={defaultPresetName}>
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
					<label class="label">New preset name</label>
					<input class="input" type="text" bind:value={newPresetName} placeholder="e.g., H265 9.7MB (NVENC)" />
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
							<div style="font-size:12px; color:#9ca3af">{p.video_codec} • {p.audio_codec} • {p.preset} • {p.target_mb}MB</div>
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
				<label class="label">Hours</label>
				<input class="input" type="number" min="0" bind:value={retentionHours} />
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
			Maximum number of jobs that can compress simultaneously. Higher values allow more parallel jobs but require more GPU/CPU resources.
		</p>
		
		<div class="row">
			<div>
				<label class="label">Max concurrent jobs</label>
				<input class="input" type="number" min="1" max="20" bind:value={workerConcurrency} />
			</div>
			<div style="display:flex; align-items:flex-end">
				<button class="btn" on:click={saveConcurrency} disabled={saving}>{saving ? 'Saving…' : 'Save concurrency'}</button>
			</div>
		</div>

		<details style="margin-top: 12px; background: #1f2937; border: 1px solid #374151; border-radius: 8px; padding: 12px;">
			<summary style="cursor: pointer; font-weight: 600; color: #60a5fa; user-select: none;">💡 Guidelines & Recommendations</summary>
			<div style="margin-top: 8px; font-size: 14px; color: #d1d5db; line-height: 1.6;">
				<p style="margin-bottom: 8px;"><strong>Hardware-based recommendations:</strong></p>
				<ul style="margin-left: 20px; margin-bottom: 12px;">
					<li><strong>Quadro RTX 4000 / RTX 3060+:</strong> 6-10 concurrent jobs (excellent NVENC throughput)</li>
					<li><strong>GTX 1660 / RTX 2060:</strong> 3-5 concurrent jobs (good NVENC performance)</li>
					<li><strong>GTX 1050 Ti / Entry-level:</strong> 2-3 concurrent jobs (basic NVENC)</li>
					<li><strong>CPU-only encoding:</strong> 1-2 jobs per 4 CPU cores (very slow, high CPU usage)</li>
					<li><strong>Intel/AMD VAAPI:</strong> 4-8 concurrent jobs (depends on iGPU/dGPU)</li>
				</ul>
				
				<p style="margin-bottom: 8px;"><strong>Performance considerations:</strong></p>
				<ul style="margin-left: 20px; margin-bottom: 12px;">
					<li><strong>NVENC hardware limit:</strong> Most NVIDIA GPUs support 2-3 NVENC sessions natively, but driver unlocks allow unlimited sessions</li>
					<li><strong>Memory usage:</strong> Each job uses ~200-500MB RAM. Monitor system memory with high concurrency</li>
					<li><strong>GPU memory:</strong> Each NVENC encode uses ~100-200MB VRAM. Check available VRAM</li>
					<li><strong>Disk I/O:</strong> Higher concurrency increases disk read/write. Use SSD for best performance</li>
				</ul>

				<p style="margin-bottom: 8px;"><strong>Testing recommendations:</strong></p>
				<ul style="margin-left: 20px;">
					<li>Start with 4 concurrent jobs and gradually increase while monitoring GPU utilization</li>
					<li>Watch for thermal throttling on high concurrency (GPU temps >80°C)</li>
					<li>Monitor job completion times - if they increase significantly, reduce concurrency</li>
					<li>Check queue page during high load to see which jobs are running simultaneously</li>
				</ul>

				<p style="margin-top: 12px; padding: 8px; background: #fef3c7; color: #92400e; border-radius: 4px;">
					⚠️ <strong>Note:</strong> Container restart required for changes to take effect. Current running jobs will complete before new setting applies.
				</p>
			</div>
		</details>
	</div>

  <!-- Defaults -->
  <div class="card">
	<div class="title">Default presets</div>
	<div>
	  <label class="label" for="targetmb">Default target size (MB)</label>
	  <input id="targetmb" class="input" type="number" min="1" bind:value={targetMB} />
	</div>

	<div class="row" style="margin-top:12px">
	  <div>
		<label class="label" for="vcodec">Video codec</label>
		<select id="vcodec" class="select" bind:value={videoCodec}>
		  <option value="av1_nvenc">AV1 (NVENC)</option>
		  <option value="hevc_nvenc">HEVC (NVENC)</option>
		  <option value="h264_nvenc">H.264 (NVENC)</option>
		  <option value="libaom-av1">AV1 (CPU)</option>
		  <option value="libx265">HEVC (CPU)</option>
		  <option value="libx264">H.264 (CPU)</option>
		</select>
	  </div>
	  <div>
		<label class="label" for="acodec">Audio codec</label>
		<select id="acodec" class="select" bind:value={audioCodec}>
		  <option value="libopus">Opus</option>
		  <option value="aac">AAC</option>
		</select>
	  </div>
	</div>

	<div class="row" style="margin-top:12px">
	  <div>
		<label class="label" for="preset">Speed/quality preset</label>
		<select id="preset" class="select" bind:value={preset}>
		  <option value="p1">P1 (Fastest)</option>
		  <option value="p2">P2</option>
		  <option value="p3">P3</option>
		  <option value="p4">P4</option>
		  <option value="p5">P5</option>
		  <option value="p6">P6 (Balanced)</option>
		  <option value="p7">P7 (Best quality)</option>
		</select>
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
		<label class="label" for="tune">Tune</label>
		<select id="tune" class="select" bind:value={tune}>
		  <option value="hq">High Quality</option>
		  <option value="ll">Low Latency</option>
		  <option value="ull">Ultra Low Latency</option>
		  <option value="lossless">Lossless</option>
		</select>
	  </div>
	</div>

	<div style="margin-top:12px">
	  <button class="btn alt" on:click={saveDefaults} disabled={saving}>{saving ? 'Saving…' : 'Save defaults'}</button>
	</div>
  </div>

	<!-- Support (collapsed by default) -->
	<div class="card" style="padding:8px">
		<details>
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
</div>
