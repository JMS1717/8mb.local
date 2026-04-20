<script lang="ts">
  import { onMount } from 'svelte';
  import { getSystemCapabilities } from '$lib/api';
  import { FPS_CAP_VALUES, type FpsCap } from '$lib/fpsCap';

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
	libx264: boolean;
	libx265: boolean;
	libsvtav1: boolean;
	libaom_av1: boolean;
  };

  let saving = false;
  let message = '';
  let error = '';
	// History toggle
	let historyEnabled = false;
	let showCodecSyncBanner = false;
	let settingsLoaded = false;

  // System & UI 
  let playSoundWhenDone = true;
  let autoDownload = false;
  let autoAudioBitrate = true;
  let fastMp4Finalize = true;
  let preferHwDecode = true;

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
  /** Included in preset profiles created via “Add from current defaults”. */
  let profileMaxFpsCap: FpsCap = '';

  // Codec visibility - individual codecs
  let codecSettings: CodecVisibilitySettings = {
	h264_nvenc: true,
	hevc_nvenc: true,
	av1_nvenc: true,
	libx264: true,
	libx265: true,
	libsvtav1: true,
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
	let filenameTag: string = '8mb.local';
	let filenameIncludeId: boolean = true;
	  // Hardware tests state
	  let hwTests: Array<any> = [];
	  let hwTestsLoading: boolean = false;
	  let hwTestsError: string = '';
	  // Daemon connection state
	  let daemonStatus: any = null;
	  let daemonTesting = false;
	  let daemonTestingMessage = '';
	  let daemonPort: string = '8000';
	  let daemonIp: string = '';
	  // System caps
	  let sysCaps: any = null;
	  let sysCapsError: string = '';

	  onMount(async () => {
	try {
			  const [authRes, presetsRes, codecsRes, historyRes, filenameRes] = await Promise.all([
		fetch('/api/settings/auth'),
		fetch('/api/settings/presets'),
		fetch('/api/settings/codecs'),
		fetch('/api/settings/history'),
		fetch('/api/settings/filename-format')
	  ]);
	  if (authRes.ok) {
		const a: AuthSettings = await authRes.json();
		authEnabled = !!a.auth_enabled;
		username = a.auth_user || 'admin';
	  }
	  if (presetsRes.ok) {
		const p: DefaultPresets & { max_output_fps?: number } = await presetsRes.json();
		targetMB = p.target_mb;
		videoCodec = p.video_codec;
		audioCodec = p.audio_codec;
		preset = p.preset;
		audioKbps = p.audio_kbps;
		container = p.container;
		tune = p.tune;
		profileMaxFpsCap = p.max_output_fps ? String(p.max_output_fps) as any : '';
	  }
	  if (codecsRes.ok) {
		const c = await codecsRes.json();
		codecSettings = {
			h264_nvenc: !!c.h264_nvenc,
			hevc_nvenc: !!c.hevc_nvenc,
			av1_nvenc: !!c.av1_nvenc,
			libx264: !!c.libx264,
			libx265: !!c.libx265,
			libsvtav1: !!c.libsvtav1,
			libaom_av1: !!c.libaom_av1,
		};
	  }
	  if (historyRes.ok) {
		const h = await historyRes.json();
		historyEnabled = h.enabled || false;
	  }
	  if (filenameRes.ok) {
		const f = await filenameRes.json();
		filenameTag = f.tag || '';
		filenameIncludeId = f.include_id !== false;
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

			// Load initial hardware test results (best-effort)
			try {
				const t = await fetch('/api/system/encoder-tests');
				if (t.ok) {
					const js = await t.json();
					hwTests = js.results || [];
				}
			} catch {}

			// Check daemon status (best-effort)
			try {
				const ds = await fetch('/api/system/daemon-status');
				if (ds.ok) daemonStatus = await ds.json();
			} catch {}

			// Fetch daemon port/address
			try {
				const dp = await fetch('/api/settings/daemon-port');
				if (dp.ok) {
					const js = await dp.json();
					const addr = js.port ? String(js.port) : '8000';
					if (addr.includes(':')) {
						const parts = addr.split(':');
						daemonIp = parts[0];
						daemonPort = parts[1];
					} else {
						if (addr.includes('.')) {
							daemonIp = addr;
							daemonPort = '8000';
						} else {
							daemonIp = '';
							daemonPort = addr;
						}
					}
				}
			} catch {}

			// Fetch host system capabilities
			try {
			  sysCaps = await getSystemCapabilities();
			} catch (e:any) {
			  sysCapsError = e?.message || 'Failed to fetch system capabilities';
			}

      // Startup info for first-boot banner
      try {
        const si = await fetch('/api/startup/info');
        if (si.ok) {
          const js = await si.json();
          const bootId = js.boot_id as string | null;
          const synced = !!js.codec_visibility_synced;
          const key = '8mb.local:lastSeenBootId';
          const lastSeen = window.localStorage.getItem(key);
          if (synced && bootId && bootId !== lastSeen) {
            showCodecSyncBanner = true;
          }
        }
      } catch {}

      // Load System & UI LocalStorage bound configurations
      try {
        const ps = localStorage.getItem('playSoundWhenDone');
        if (ps !== null) playSoundWhenDone = (ps === 'true');
        
        const ad = localStorage.getItem('autoDownload');
        if (ad !== null) autoDownload = (ad === 'true');
        else { autoDownload = true; localStorage.setItem('autoDownload', 'true'); }
        
        const aab = localStorage.getItem('autoAudioBitrate');
        if (aab !== null) autoAudioBitrate = (aab === 'true');
        else { autoAudioBitrate = true; localStorage.setItem('autoAudioBitrate', 'true'); }
        
        const fmf = localStorage.getItem('fastMp4Finalize');
        if (fmf !== null) fastMp4Finalize = (fmf === 'true');
        
        const phd = localStorage.getItem('preferHwDecode');
        if (phd !== null) preferHwDecode = (phd === 'true');
      } catch {}
	} catch (e) {
	  error = 'Failed to load settings';
	}
	settingsLoaded = true;
  });

  $: if (settingsLoaded) {
    try { localStorage.setItem('playSoundWhenDone', String(playSoundWhenDone)); } catch {}
    try { localStorage.setItem('autoDownload', String(autoDownload)); } catch {}
    try { localStorage.setItem('autoAudioBitrate', String(autoAudioBitrate)); } catch {}
    try { localStorage.setItem('fastMp4Finalize', String(fastMp4Finalize)); } catch {}
    try { localStorage.setItem('preferHwDecode', String(preferHwDecode)); } catch {}
  }

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

	async function testDaemon() {
		daemonTesting = true;
		daemonTestingMessage = '';
		try {
			const res = await fetch('/api/system/daemon-status');
			if (res.ok) {
				daemonStatus = await res.json();
				if (daemonStatus.connected) {
					daemonTestingMessage = `✅ Connected — ${daemonStatus.codecs?.join(', ') || 'no codecs reported'}`;
				} else {
					daemonTestingMessage = `❌ Unreachable: ${daemonStatus.error || 'Connection failed'}`;
				}
			} else {
				daemonTestingMessage = '❌ Failed to check daemon status';
			}
		} catch {
			daemonTestingMessage = '❌ Failed to check daemon status';
		} finally {
			daemonTesting = false;
		}
	}

	async function saveDaemonPort() {
		saving = true; error = ''; message = '';
		try {
			const finalAddr = daemonIp.trim() ? `${daemonIp.trim()}:${daemonPort.trim()}` : daemonPort.trim();
			const res = await fetch('/api/settings/daemon-port', {
				method: 'PUT',
				headers: {'Content-Type': 'application/json'},
				body: JSON.stringify({ port: finalAddr })
			});
			if (res.ok) {
				message = 'Saved daemon port';
			} else {
				const d = await res.json().catch(()=>({}));
				error = d.detail || 'Failed to save daemon port';
			}
		} catch {
			error = 'Failed to save daemon port';
		} finally {
			saving = false;
		}
	}

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
		  tune,
		  max_output_fps: parseFloat(profileMaxFpsCap as string) > 0 ? parseFloat(profileMaxFpsCap as string) : null
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
	// Filename settings
	async function saveFilenameSettings() {
		saving=true; error=''; message='';
		try {
			const res = await fetch('/api/settings/filename-format', {
				method: 'PUT',
				headers: {'Content-Type': 'application/json'},
				body: JSON.stringify({ tag: filenameTag, include_id: filenameIncludeId })
			});
			if (res.ok) {
				message = 'Saved filename format settings';
			} else {
				const d = await res.json();
				error = d.detail || 'Failed to save filename settings';
			}
		} catch { error = 'Failed to save filename settings'; } finally { saving = false; }
	}

	let activeTab = 'system';
</script>

<style>
  .settings-layout { display: flex; gap: 24px; max-width: 1100px; margin: 40px auto; padding: 0 20px; align-items: flex-start; }
  .sidebar { width: 220px; flex-shrink: 0; background-color: var(--bg-card); border: 1px solid var(--glass-border); border-radius: var(--border-radius); padding: 12px; position: sticky; top: 40px; }
  .sidebar-nav { display: flex; flex-direction: column; gap: 4px; }
  .nav-btn { background: transparent; color: var(--text-secondary); border: none; padding: 10px 16px; text-align: left; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500; transition: var(--transition-fast); }
  .nav-btn:hover { background-color: var(--bg-hover); color: var(--text-primary); }
  .nav-btn.active { background-color: var(--accent); color: white; }
  
  .content { flex-grow: 1; min-width: 0; }
  .page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
  .page-title { font-size: 28px; font-weight: 600; margin: 0; }
  .actions { display: flex; gap: 12px; justify-content: flex-end; }
  
  .section-title { font-size: 20px; font-weight: 600; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--glass-border); color: var(--text-primary); }
  .form-group { margin-bottom: 16px; }
  .form-label { display: block; font-size: 14px; font-weight: 500; color: var(--text-secondary); margin-bottom: 6px; }
  .form-desc { font-size: 13px; color: var(--text-muted); margin-bottom: 8px; margin-top: -4px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  
  .msg-banner { padding: 12px 16px; border-radius: var(--border-radius); margin-bottom: 20px; font-size: 14px; border: 1px solid transparent; }
  .msg-banner.success { background-color: rgba(34, 197, 94, 0.1); border-color: rgba(34, 197, 94, 0.2); color: #4ade80; }
  .msg-banner.error { background-color: rgba(239, 68, 68, 0.1); border-color: rgba(239, 68, 68, 0.2); color: #f87171; }
  .msg-banner.info { background-color: rgba(59, 130, 246, 0.1); border-color: rgba(59, 130, 246, 0.2); color: #60a5fa; display: flex; justify-content: space-between; align-items: center; }
  .msg-banner button { background: transparent; border: none; color: inherit; cursor: pointer; font-weight: 600; }
  
  .checkbox-row { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; cursor: pointer; }
  .checkbox-row label { cursor: pointer; font-size: 14px; color: var(--text-primary); margin: 0; }
  
  .card { margin-bottom: 24px; animation: fadeIn 0.3s ease; }
  .card-title { font-size: 18px; font-weight: 600; margin-bottom: 12px; }
  
  .status-indicator { display: flex; align-items: center; gap: 12px; padding: 12px 16px; background-color: var(--bg-hover); border: 1px solid var(--glass-border); border-radius: 8px; margin-bottom: 16px; }
  .dot { width: 12px; height: 12px; border-radius: 50%; }
  .dot.green { background-color: var(--success); box-shadow: 0 0 8px rgba(34, 197, 94, 0.4); }
  .dot.red { background-color: var(--error); box-shadow: 0 0 8px rgba(239, 68, 68, 0.4); }
  .dot.gray { background-color: var(--text-muted); }
  
  .hardware-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .size-btn-tag { display: inline-flex; align-items: center; gap: 8px; background-color: var(--bg-hover); border: 1px solid var(--glass-border); border-radius: 20px; padding: 6px 12px; font-size: 14px; }
  .size-btn-tag button { background: transparent; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; line-height: 1; padding: 0 2px; transition: color var(--transition-fast); }
  .size-btn-tag button:hover { color: var(--error); }
  
  .preset-list { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; }
  .preset-item { display: flex; justify-content: space-between; align-items: center; background-color: var(--bg-hover); border: 1px solid var(--glass-border); border-radius: 8px; padding: 12px 16px; }
  .preset-item-info { display: flex; flex-direction: column; gap: 4px; }
  .preset-item-title { font-weight: 600; color: var(--text-primary); }
  .preset-item-desc { font-size: 12px; color: var(--text-secondary); }
</style>

<div class="settings-layout">
  <div class="sidebar">
    <div class="sidebar-nav">
      <button class="nav-btn {activeTab === 'system' ? 'active' : ''}" on:click={() => activeTab = 'system'}>
        <span style="margin-right: 8px;">🖥️</span> System Options
      </button>
      <button class="nav-btn {activeTab === 'app' ? 'active' : ''}" on:click={() => activeTab = 'app'}>
        <span style="margin-right: 8px;">⚙️</span> App Management
      </button>
      <button class="nav-btn {activeTab === 'compress' ? 'active' : ''}" on:click={() => activeTab = 'compress'}>
        <span style="margin-right: 8px;">🗜️</span> Defaults & Options
      </button>
      <button class="nav-btn {activeTab === 'security' ? 'active' : ''}" on:click={() => activeTab = 'security'}>
        <span style="margin-right: 8px;">🔒</span> Security
      </button>
    </div>
  </div>
  
  <div class="content">
    <div class="page-header" style="flex-wrap:wrap;">
      <h1 class="page-title" style="margin:0;">Settings</h1>
      <div class="actions" style="display:flex; gap:8px; flex-wrap:wrap;">
        <a href="/" data-sveltekit-reload class="btn alt" style="text-decoration:none;">🏠 Home</a>
        <a href="/advanced" class="btn alt" style="text-decoration:none;">⚙️ Advanced</a>
        <a href="/batch" class="btn alt" style="text-decoration:none;">🗂 Batch</a>
        <a href="/queue" class="btn alt" style="text-decoration:none;">📋 Queue</a>
        <a href="/history" class="btn alt" style="text-decoration:none;">📜 History</a>
      </div>
    </div>

    {#if showCodecSyncBanner}
      <div class="msg-banner info">
        <span>Codec visibility synced from hardware tests</span>
        <button on:click={() => { try { const siPromise = fetch('/api/startup/info').then(r=>r.ok?r.json():null); siPromise.then(js => { const bootId = js?.boot_id; if (bootId) { window.localStorage.setItem('8mb.local:lastSeenBootId', bootId); } }); } catch {} showCodecSyncBanner = false; }}>Dismiss</button>
      </div>
    {/if}

    {#if message}<div class="msg-banner success">{message}</div>{/if}
    {#if error}<div class="msg-banner error">{error}</div>{/if}

    <!-- SYSTEM TAB -->
    {#if activeTab === 'system'}
      
      <!-- System capabilities -->
      <div class="card">
        <h2 class="card-title">System Information</h2>
        <div class="grid sm:grid-cols-3 gap-4">
          <div>
            <h3 style="font-weight:600; margin-bottom:8px;">System</h3>
            {#if sysCaps}
              <p style="font-size:14px;">CPU: {sysCaps.cpu?.model || 'Unknown'} ({sysCaps.cpu?.cores_physical}C/{sysCaps.cpu?.cores_logical}T)</p>
              <p style="font-size:14px;">Memory: {sysCaps.memory?.available_gb} GB free / {sysCaps.memory?.total_gb} GB</p>
              <p style="font-size:14px; margin-top:4px;">
                {#if sysCaps.gpus && sysCaps.gpus.length}
                  <span style="color:var(--success);">NVIDIA GPU detected. NVENC enabled.</span>
                {:else}
                  <span style="color:var(--text-muted);">CPU-only environment detected. NVENC disabled.</span>
                {/if}
              </p>
            {:else if sysCapsError}
              <p style="font-size:14px; color:var(--error);">{sysCapsError}</p>
            {:else}
              <p style="font-size:14px; opacity:0.7;">Detecting system capabilities…</p>
            {/if}
          </div>
          <div>
            <h3 style="font-weight:600; margin-bottom:8px;">GPUs</h3>
            {#if sysCaps?.gpus?.length}
              {#each sysCaps.gpus as gpu}
                <div style="font-size:14px; background:var(--bg-hover); padding:8px; border-radius:6px; margin-bottom:4px; border:1px solid var(--glass-border);">
                  <div style="font-weight:500;">{gpu.name}</div>
                  <div style="opacity:0.7; font-size:12px;">Memory: {gpu.memory_used_gb} GB / {gpu.memory_total_gb} GB</div>
                </div>
              {/each}
              {#if sysCaps.nvidia_driver}
                <p style="font-size:12px; margin-top:6px; opacity:0.6;">Driver: {sysCaps.nvidia_driver}</p>
              {/if}
            {:else}
              <p style="font-size:14px; color:var(--text-muted);">No dedicated GPUs detected internally.</p>
            {/if}
          </div>
          <div>
            <h3 style="font-weight:600; margin-bottom:8px;">Network Diagnostics</h3>
            {#if daemonStatus}
              {#if daemonStatus.connected}
                <div style="font-size:14px; background:var(--bg-hover); padding:10px; border-radius:6px; border:1px solid rgba(34, 197, 94, 0.2);">
                  <div style="font-weight:600; color:var(--success); margin-bottom:4px; display:flex; align-items:center; gap:6px;">
                    <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--success);"></span> Connected
                  </div>
                  {#if daemonStatus.host_cpu}
                    <div style="margin-top:6px; font-size:13px; color:var(--text-secondary);">Processor: <span style="color:var(--text-primary);">{daemonStatus.host_cpu.model || 'Unknown'} ({daemonStatus.host_cpu.cores_physical} Core)</span></div>
                  {/if}
                  {#if daemonStatus.host_memory}
                    <div style="margin-top:4px; font-size:13px; color:var(--text-secondary);">RAM: <span style="color:var(--text-primary);">{daemonStatus.host_memory.total_gb ? daemonStatus.host_memory.total_gb + ' GB' : daemonStatus.host_memory}</span></div>
                  {/if}
                  {#if daemonStatus.host_gpus && daemonStatus.host_gpus.length}
                    <div style="margin-top:4px; font-size:13px; color:var(--text-secondary);">Graphics: <span style="color:var(--text-primary);">{daemonStatus.host_gpus.map((g: any) => g.name || g).join(', ')}</span></div>
                  {/if}
                  {#if daemonStatus.platform}
                    <div style="margin-top:4px; font-size:12px; opacity:0.6;">Host env: {daemonStatus.platform}</div>
                  {/if}
                  {#if daemonStatus.codecs && daemonStatus.codecs.length}
                    <div style="margin-top:2px; font-size:12px; opacity:0.6;">Accelerators: {daemonStatus.codecs.join(', ')}</div>
                  {/if}
                </div>
              {:else}
                <div style="font-size:14px; color:var(--error); background:var(--bg-hover); padding:10px; border-radius:6px; border:1px solid rgba(239, 68, 68, 0.2);">
                  <div style="font-weight:600; margin-bottom:4px; display:flex; align-items:center; gap:6px;">
                    <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--error);"></span> Unreachable
                  </div>
                  {#if daemonStatus.error}
                     <div style="font-size:12px; color:var(--text-muted); line-height:1.4;">{daemonStatus.error}</div>
                  {/if}
                </div>
              {/if}
            {:else}
               <p style="font-size:14px; opacity:0.7; padding: 10px; background:var(--bg-hover); border-radius:6px;">Waiting for connection test…</p>
            {/if}
          </div>
        </div>
      </div>

      <!-- Advanced UI Processing Automation -->
      <div class="card">
        <h2 class="card-title">Profiles & Automation</h2>
        <p class="form-desc" style="margin-bottom:16px;">Configure how the user interface reacts pre-and-post-processing pipeline.</p>
        
        <div style="display:flex; flex-direction:column; gap:12px;">
          <label class="checkbox-row">
            <input type="checkbox" class="checkbox-ui" bind:checked={playSoundWhenDone} />
            <span>🔔 Play sound when done</span>
          </label>
          <label class="checkbox-row">
            <input type="checkbox" class="checkbox-ui" bind:checked={autoDownload} />
            <span>⬇️ Auto-download when done</span>
          </label>
          <label class="checkbox-row" title="Automatically reduce audio bitrate when target size is tight.">
            <input type="checkbox" class="checkbox-ui" bind:checked={autoAudioBitrate} />
            <span>🎚️ Adaptive auto audio bitrate downshifting</span>
          </label>
          <label class="checkbox-row" title="Fragmented MP4 eliminates the 'loading' block at 99%.">
            <input type="checkbox" class="checkbox-ui" bind:checked={fastMp4Finalize} />
            <span style="color:#6366f1;">🚀 Fast finalize (highly recommended)</span>
          </label>
          <label class="checkbox-row">
            <input type="checkbox" class="checkbox-ui" bind:checked={preferHwDecode} />
            <span style="color:#6366f1;">⚡ Force hardware decoding (when available)</span>
          </label>
        </div>
      </div>
      

      <!-- Hardware Tests -->
      <div class="card">
        <h2 class="card-title">Hardware Validation</h2>
        <p class="form-desc">Validate hardware encoders and decoders inside the worker container. Useful after driver updates.</p>
        
        {#if hwTestsError}<div class="msg-banner error" style="margin-bottom:12px;">{hwTestsError}</div>{/if}
        
        <div style="display:flex; gap:12px; margin-bottom:16px;">
          <button class="btn" on:click={rerunHardwareTests} disabled={hwTestsLoading}>{hwTestsLoading ? 'Running…' : 'Re-run Tests'}</button>
          <button class="btn alt" on:click={async()=>{ try{ const r=await fetch('/api/system/encoder-tests'); if(r.ok){ const js=await r.json(); hwTests = js.results||[]; message='Loaded latest test results'; } }catch{}}} disabled={hwTestsLoading}>Refresh</button>
        </div>
        
        {#if hwTests && hwTests.length}
          <div style="background-color: var(--bg-hover); border: 1px solid var(--glass-border); border-radius: 8px; padding: 12px; font-size: 14px;">
            {#each hwTests as t}
              <div style="display:flex; justify-content:space-between; margin-bottom:8px; border-bottom: 1px solid var(--glass-border); padding-bottom:4px;">
                <span>
                  {t.codec.replace('libsvtav1', 'AV1 (SVT)').replace('libaom-av1', 'AV1 (AOM)').replace('libx264', 'H.264').replace('libx265', 'HEVC (x265)').replace('_nvenc', ' (Hardware)')} 
                  <span style="opacity:0.6">({t.actual_encoder})</span>
                </span>
                {#if t.passed}
                  <span style="color:var(--success); font-weight:600">PASS</span>
                {:else}
                  <span style="color:var(--error); font-weight:600">FAIL</span>
                {/if}
              </div>
            {/each}
          </div>
        {:else}
          <p style="color:var(--text-muted); font-size: 14px;">No test results available yet.</p>
        {/if}
      </div>

      <!-- macOS Configuration -->
      <div class="card">
        <h2 class="card-title">macOS Configuration</h2>
        <p class="form-desc" style="margin-bottom:16px;">Configure the connection to the native macOS VideoToolbox daemon.</p>
        
        <div style="display: flex; flex-direction: column; gap: 12px; max-width: 500px;">
          <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 12px; align-items: end;">
            <div>
              <label class="form-label">Daemon Host / IP</label>
              <input class="input" type="text" placeholder="e.g. 192.168.1.100" bind:value={daemonIp} />
            </div>
            <div>
              <label class="form-label">Port</label>
              <input class="input" type="text" placeholder="8000" bind:value={daemonPort} />
            </div>
          </div>
          
          <div style="display:flex; gap:8px;">
            <button class="btn" on:click={saveDaemonPort} disabled={saving}>{saving ? 'Saving…' : 'Save Connection'}</button>
            <button class="btn alt" on:click={testDaemon} disabled={daemonTesting}>{daemonTesting ? 'Testing…' : 'Test Connection'}</button>
          </div>
          
          {#if daemonTestingMessage}
            <div style="background:var(--bg-hover); border:1px solid var(--glass-border); padding:8px 12px; border-radius:6px; font-size:13px; margin-top:4px;">
              {daemonTestingMessage}
            </div>
          {/if}
        </div>
      </div>

      <!-- Codecs -->
      <div class="card">
        <h2 class="card-title">Encoder Visibility</h2>
        <p class="form-desc">Select which codecs appear in the compression page dropdown. GPU options use NVIDIA NVENC; software options use CPU.</p>
        
        <h3 style="font-size: 15px; color: #4ade80; margin: 16px 0 12px; font-weight: 600;">NVIDIA (NVENC)</h3>
        <div class="hardware-grid {sysCaps && (!sysCaps.gpus || !sysCaps.gpus.length) ? 'opacity-50 pointer-events-none' : ''}">
          <label class="checkbox-row">
            <input type="checkbox" class="checkbox-ui" bind:checked={codecSettings.av1_nvenc} disabled={sysCaps && (!sysCaps.gpus || !sysCaps.gpus.length)} />
            <span>AV1 (RTX 40/50)</span>
          </label>
          <label class="checkbox-row">
            <input type="checkbox" class="checkbox-ui" bind:checked={codecSettings.hevc_nvenc} disabled={sysCaps && (!sysCaps.gpus || !sysCaps.gpus.length)} />
            <span>HEVC (H.265)</span>
          </label>
          <label class="checkbox-row">
            <input type="checkbox" class="checkbox-ui" bind:checked={codecSettings.h264_nvenc} disabled={sysCaps && (!sysCaps.gpus || !sysCaps.gpus.length)} />
            <span>H.264</span>
          </label>
        </div>

        <h3 style="font-size: 15px; color: var(--text-secondary); margin: 20px 0 12px; font-weight: 600;">CPU (Software Encoding)</h3>
        <div class="hardware-grid">
          <label class="checkbox-row">
            <input type="checkbox" class="checkbox-ui" bind:checked={codecSettings.libsvtav1} />
            <span>AV1 (SVT-AV1 Fast)</span>
          </label>
          <label class="checkbox-row">
            <input type="checkbox" class="checkbox-ui" bind:checked={codecSettings.libaom_av1} />
            <span>AV1 (Highest Quality)</span>
          </label>
          <label class="checkbox-row">
            <input type="checkbox" class="checkbox-ui" bind:checked={codecSettings.libx265} />
            <span>HEVC (H.265)</span>
          </label>
          <label class="checkbox-row">
            <input type="checkbox" class="checkbox-ui" bind:checked={codecSettings.libx264} />
            <span>H.264</span>
          </label>
        </div>

        <button class="btn" on:click={saveCodecs} disabled={saving}>{saving ? 'Saving…' : 'Save Extracted Codecs'}</button>
      </div>
    
    <!-- APP TAB -->
    {:else if activeTab === 'app'}
      <!-- Worker Concurrency -->
      <div class="card">
        <h2 class="card-title">Worker Concurrency</h2>
        <p class="form-desc">Maximum number of jobs that can compress simultaneously. Higher values allow more parallel jobs but require exponentially more resources.</p>
        <div class="row">
          <div>
            <label class="form-label">Concurrent Job Limit</label>
            <input class="input" type="number" min="1" max="20" bind:value={workerConcurrency} />
          </div>
          <div style="display:flex; align-items:flex-end">
            <button class="btn" on:click={saveConcurrency} disabled={saving}>{saving ? 'Saving…' : 'Save Limit'}</button>
          </div>
        </div>

        <details style="margin-top: 16px; background-color: var(--bg-hover); border: 1px solid var(--glass-border); border-radius: 8px; padding: 12px;">
          <summary style="cursor: pointer; font-weight: 600; color: var(--accent); user-select: none;">💡 Resource Guidelines</summary>
          <div style="margin-top: 8px; font-size: 13px; color: var(--text-secondary); line-height: 1.6;">
            <p style="margin-bottom: 6px;"><strong>Hardware Limits:</strong></p>
            <ul style="margin-left: 16px; margin-bottom: 12px; padding-left: 0;">
              <li>RTX 4000/3060+: 6-10 jobs</li>
              <li>RTX 2060/1660: 3-5 jobs</li>
              <li>CPU-only encoding: 1 job per 4 CPU cores is safe.</li>
            </ul>
            <p style="color: var(--warning); margin-bottom: 0;">⚠️ NVENC uses roughly 150MB of VRAM per encode session.</p>
          </div>
        </details>
      </div>

      <!-- History -->
      <div class="card">
        <h2 class="card-title">Compression Metrics</h2>
        <p class="form-desc">Track completed jobs over time for analytics. Video files themselves are not stored permanently by the tracker.</p>
        <label class="checkbox-row" style="margin-bottom: 16px;">
          <input type="checkbox" class="checkbox-ui" bind:checked={historyEnabled} />
          <span>Enable compression performance history</span>
        </label>
        
        <div style="display:flex; gap:12px;">
          <button class="btn" on:click={saveHistorySettings} disabled={saving}>{saving ? 'Saving…' : 'Update Metrics'}</button>
          {#if historyEnabled}
            <a href="/history" class="btn alt" style="text-decoration:none; display:flex; align-items:center;">View Complete Analytics →</a>
          {/if}
        </div>
      </div>

      <div class="card">
        <h2 class="card-title">File Retention Policy</h2>
        <p class="form-desc">Controls how long completed files remain accessible via download links before automatic deletion.</p>
        <div class="row" style="align-items: flex-end;">
          <div>
            <label class="form-label">Delay (Hours)</label>
            <input class="input" type="number" min="0" bind:value={retentionHours} />
          </div>
          <button class="btn" on:click={saveRetention} disabled={saving}>{saving ? 'Saving…' : 'Set Retention'}</button>
        </div>
      </div>

    <!-- COMPRESSION SETTINGS TAB -->
    {:else if activeTab === 'compress'}
      
      <!-- Filename Output -->
      <div class="card">
        <h2 class="card-title">Output Naming Format</h2>
        <p class="form-desc">Customize the resulting filename after a completed job.</p>
        
        <div class="form-group" style="max-width: 400px;">
          <label class="form-label">Preset Brand Tag</label>
          <input class="input" type="text" bind:value={filenameTag} placeholder="Leave empty for no tag" />
          <p class="form-desc" style="margin-top: 6px;">Appended string (e.g. video_<b>8mb.local</b>.mp4)</p>
        </div>
        
        <label class="checkbox-row" style="margin-top:16px;">
          <input type="checkbox" class="checkbox-ui" bind:checked={filenameIncludeId} />
          <span>Include short task ID (collision prevention)</span>
        </label>

        <p style="margin: 16px 0; padding: 10px; font-family: monospace; font-size: 13px; color: var(--text-secondary); background: var(--bg-hover); border-radius: 6px;">
          video_{filenameTag ? filenameTag + '_' : ''}{filenameIncludeId ? 'a1b2c3d4' : ''}{(!filenameTag && !filenameIncludeId) ? '(original)' : ''}.mp4
        </p>

        <button class="btn" on:click={saveFilenameSettings} disabled={saving}>{saving ? 'Saving…' : 'Update Format'}</button>
      </div>

      <div class="card">
        <h2 class="card-title">Quick Target Sizes</h2>
        <p class="form-desc">Customize the rapid-select MB size buttons found on the main submission view.</p>
        
        <div style="display:flex; flex-wrap:wrap; gap:10px; margin-bottom: 20px;">
          {#each sizeButtons as b, i}
            <div class="size-btn-tag">
              <span>{b} MB</span>
              <button on:click={() => removeSizeButton(i)}>×</button>
            </div>
          {/each}
        </div>
        
        <div class="row">
          <div>
            <label class="form-label">Add custom size</label>
            <input class="input" type="number" min="1" bind:value={newSizeValue} placeholder="25" />
          </div>
          <div style="display:flex; align-items:flex-end">
            <button class="btn alt" on:click={addSizeButton} disabled={saving}>Add Size</button>
          </div>
        </div>
        <button class="btn" style="margin-top: 8px;" on:click={saveSizeButtons} disabled={saving}>{saving ? 'Saving…' : 'Save Button Array'}</button>
      </div>

      <div class="card">
        <h2 class="card-title">Initial Profile Values</h2>
        <p class="form-desc">Configure the absolute default parameters pre-loaded when an unknown user opens the main page.</p>

        <div class="form-group" style="max-width: 250px;">
          <label class="form-label">Initial Size Slider (MB)</label>
          <input class="input" type="number" min="1" bind:value={targetMB} />
        </div>

        <div class="row">
          <div>
            <label class="form-label">Default Video Codec</label>
            <select class="input" bind:value={videoCodec}>
              <optgroup label="NVIDIA NVENC (Hardware)">
                <option value="av1_nvenc">AV1 (NVENC - RTX 40/50)</option>
                <option value="hevc_nvenc">HEVC (NVENC)</option>
                <option value="h264_nvenc">H.264 (NVENC)</option>
              </optgroup>
              <optgroup label="CPU (Software)">
                <option value="libsvtav1">AV1 (SVT Fast)</option>
                <option value="libaom-av1">AV1 (Highest Quality)</option>
                <option value="libx265">HEVC</option>
                <option value="libx264">H.264</option>
              </optgroup>
            </select>
          </div>
          <div>
            <label class="form-label">Default Audio Codec</label>
            <select class="input" bind:value={audioCodec}>
              <option value="libopus">Opus</option>
              <option value="aac">AAC</option>
              <option value="none">Muted</option>
            </select>
          </div>
        </div>

        <div class="row">
          <div>
            <label class="form-label">Default Performance Preset</label>
            <select class="input" bind:value={preset}>
              <option value="p1">P1 (Fastest)</option>
              <option value="p2">P2</option>
              <option value="p3">P3</option>
              <option value="p4">P4 (Balanced)</option>
              <option value="p5">P5</option>
              <option value="p6">P6</option>
              <option value="p7">P7 (Best Quality)</option>
              <option value="extraquality">Extra Quality (CPU intensive)</option>
            </select>
          </div>
          <div>
            <label class="form-label">Default Audio Kbps</label>
            <select class="input" bind:value={audioKbps}>
              <option value={64}>64 kbps</option>
              <option value={96}>96 kbps</option>
              <option value={128}>128 kbps</option>
              <option value={192}>192 kbps</option>
              <option value={256}>256 kbps</option>
              <option value={320}>320 kbps</option>
            </select>
          </div>
        </div>
        <div style="margin-top: 16px; display: flex; justify-content: flex-end;">
          <button class="btn" on:click={saveDefaults} disabled={saving}>{saving ? "Saving…" : "Save Default Blueprint"}</button>
        </div>
      </div>

      <div class="card">
        <h2 class="card-title">Saved Presets</h2>
        <p class="form-desc">Provide your users with a quick dropdown menu containing specific configurations (e.g. Discord 8MB, Twitter 25M).</p>
        
        <div class="row" style="align-items: flex-end;">
          <div>
            <label class="form-label">System Active Preset</label>
            <select class="input" bind:value={defaultPresetName}>
              {#each presetProfiles as p}
                <option value={p.name}>{p.name}</option>
              {/each}
            </select>
          </div>
          <button class="btn" on:click={saveDefaultPreset} disabled={saving}>{saving ? 'Saving…' : 'Make Active'}</button>
        </div>

        <hr style="border: 0; border-top: 1px solid var(--glass-border); margin: 24px 0;" />
        
        <h3 style="font-size: 15px; font-weight: 600; margin-bottom: 12px;">Create New Preset</h3>
        <p class="form-desc">A new preset captures the *Current Defaults* set directly above.</p>
        <div class="form-group">
          <label class="form-label">FPS Constraint</label>
          <select class="input" bind:value={profileMaxFpsCap} style="max-width: 300px;">
            <option value="">Respect Input FPS (Pass-through)</option>
            {#each FPS_CAP_VALUES as v}
              <option value={v}>Halt at {v} max fps (Downsample)</option>
            {/each}
          </select>
        </div>
        <div class="row" style="align-items: flex-end;">
          <div>
            <label class="form-label">Blueprint Name</label>
            <input class="input" type="text" bind:value={newPresetName} placeholder="Snapchat 5MB H264" />
          </div>
          <button class="btn" on:click={addPresetFromCurrent} disabled={saving}>{saving ? 'Saving…' : 'Capture New Preset'}</button>
        </div>

        {#if presetProfiles.length > 0}
          <div class="preset-list">
            {#each presetProfiles as p}
              <div class="preset-item">
                <div class="preset-item-info">
                  <span class="preset-item-title">{p.name} <span style="font-weight: 400; color: var(--text-muted);">({p.target_mb}MB)</span></span>
                  <span class="preset-item-desc">{p.video_codec} • {p.audio_codec} • {p.preset}{#if p.max_output_fps} • max {p.max_output_fps}fps{/if}</span>
                </div>
                <button class="btn alt" on:click={() => deletePreset(p.name)} disabled={saving} style="padding: 6px 12px; font-size: 13px;">Drop</button>
              </div>
            {/each}
          </div>
        {/if}
      </div>

    <!-- SECURITY TAB -->
    {:else if activeTab === 'security'}
      <div class="card">
        <h2 class="card-title">Instance Security</h2>
        {#if authEnabled}
          <label class="checkbox-row" style="margin-bottom: 24px;">
            <input type="checkbox" class="checkbox-ui" bind:checked={authEnabled} />
            <span>Require strong authentication to access Web UI and Rest APIs</span>
          </label>
          
          <div class="row">
            <div>
              <label class="form-label">Primary Username</label>
              <input class="input" type="text" bind:value={username} placeholder="admin" />
            </div>
            <div>
              <label class="form-label">Modify Password</label>
              <input class="input" type="password" bind:value={newPassword} placeholder="Leave empty to keep" />
            </div>
          </div>
          {#if newPassword}
            <div class="form-group" style="max-width: 48%; margin-top: -8px;">
              <label class="form-label">Confirm Passphrase</label>
              <input class="input" type="password" bind:value={confirmPassword} />
            </div>
          {/if}
          <button class="btn" on:click={saveAuth} disabled={saving} style="margin-top: 12px;">{saving ? 'Validating…' : 'Update Credentials'}</button>
        {:else}
          <div class="msg-banner info" style="display: block;">
            <span style="font-size: 16px; font-weight: 600; display: block; margin-bottom: 6px;">Authentication Inactive</span>
            <span style="color: rgba(255,255,255,0.7);">Basic Auth is currently disabled via environment files. To secure your docker instance properly, you must configure it using the runtime environment variables block.</span>
          </div>
        {/if}
      </div>
    
    {/if}
  </div>
</div>
