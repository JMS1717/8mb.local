<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { uploadWithProgress, startCompress, openProgressStream, downloadUrl, getAvailableCodecs, getSystemCapabilities, getPresetProfiles, getSizeButtons, cancelJob, getEncoderTestResults, getVersion, getBatchStatus, batchZipDownloadUrl, getDaemonStatus } from '$lib/api';
  import { FPS_CAP_VALUES, maxFpsFromProfile, parseStoredFpsCap, type FpsCap } from '$lib/fpsCap';

  let file: File | null = null;
  let uploadInput: HTMLInputElement | null = null; // reference to clear file input
  let uploadedFileName: string | null = null; // Track what file was uploaded
  let isAnalyzing: boolean = false; // Track analysis state for UI feedback
  let targetMB = 25;
  /** 'size' = target output file size (MB); 'bitrate' = fixed video bitrate (kbps). */
  let targetMode: 'size' | 'bitrate' = 'size';
  let targetVideoKbps = 2500;
  let videoCodec: string = 'av1_nvenc';
  let audioCodec: 'libopus' | 'aac' | 'none' = 'libopus';
  let preset: string = 'p6';
  let audioKbps: 32|48|64|96|128|160|192|256 = 128;
  // Auto audio bitrate control: downshift audio for extreme compressions
  let autoAudioBitrate: boolean = true;
  // User's preferred audio bitrate (used as an upper bound when auto is ON)
  let baseAudioKbps: 32|48|64|96|128|160|192|256 = 128;
  let fileSizeLabel: string | null = null;
  let container: 'mp4' | 'mkv' = 'mp4';
  let tune: 'hq'|'ll'|'ull'|'lossless' = 'hq';
  // Decoder preference
  let preferHwDecode: boolean = true; // Prefer hardware decoding when available
  // MP4 finalize preference - default OFF for broader compatibility
  let fastMp4Finalize: boolean = false;
  // New resolution and trim controls
  let maxWidth: number | null = null;
  let maxHeight: number | null = null;
  // Auto resolution options
  let autoResolution: boolean = false; // default OFF; will show recommendations instead
  let minAutoHeight: 240|360|480|720 = 240; // do not go below unless user changes
  let explicitHeight: 2160|1440|1080|720|480|360|240|null = null; // explicit override
  let audioOnly: boolean = false; // Audio-only conversion
  /** '' = no cap; otherwise max output fps when source is faster (persisted). */
  let maxFpsCap: FpsCap = '';
  let startTime: string = '';
  let endTime: string = '';
  // New UI options
  let playSoundWhenDone = true; // default ON
  let autoDownload = true;
  let warnText: string | null = null;
  let resolutionSuggestText: string | null = null;
  let resolutionSuggestHeight: 2160|1440|1080|720|480|360|240|null = null;
  let settingsLoaded = false;

  
  // Helper function to parse time string to seconds
  function parseTimeToSeconds(timeStr: string): number | null {
    if (!timeStr || timeStr.trim() === '') return null;
    const str = timeStr.trim();
    
    // Try HH:MM:SS or MM:SS format
    if (str.includes(':')) {
      const parts = str.split(':').map(p => parseFloat(p));
      if (parts.length === 3) {
        // HH:MM:SS
        return parts[0] * 3600 + parts[1] * 60 + parts[2];
      } else if (parts.length === 2) {
        // MM:SS
        return parts[0] * 60 + parts[1];
      }
    }
    
    // Try plain number (seconds)
    const num = parseFloat(str);
    return isNaN(num) ? null : num;
  }

  function parseExplicitHeight(value: string): 2160|1440|1080|720|480|360|240|null {
    const parsed = Number(value);
    if (parsed === 2160 || parsed === 1440 || parsed === 1080 || parsed === 720 || parsed === 480 || parsed === 360 || parsed === 240) {
      return parsed;
    }
    return null;
  }

  // Calculate effective duration based on trim settings
  $: effectiveDuration = (() => {
    if (!jobInfo) return 0;
    
    const fullDuration = jobInfo.duration_s;
    const startSec = parseTimeToSeconds(startTime);
    const endSec = parseTimeToSeconds(endTime);
    
    const effectiveStart = startSec !== null ? startSec : 0;
    const effectiveEnd = endSec !== null ? endSec : fullDuration;
    
    // Calculate trimmed duration
    const trimmedDuration = Math.max(0, effectiveEnd - effectiveStart);
    
    return trimmedDuration > 0 ? trimmedDuration : fullDuration;
  })();

  $: containerNote = (container === 'mp4' && audioCodec === 'libopus' && !audioOnly) ? 'MP4 does not support Opus; audio will be encoded as AAC automatically.' : null;
  $: estimated = jobInfo ? {
    duration_s: effectiveDuration,
    total_kbps: effectiveDuration > 0
      ? (targetMode === 'bitrate'
          ? (targetVideoKbps + (audioCodec === 'none' ? 0 : audioKbps))
          : (targetMB * 8192.0) / effectiveDuration)
      : 0,
    video_kbps: targetMode === 'bitrate'
      ? targetVideoKbps
      : (effectiveDuration > 0 ? Math.max(((targetMB * 8192.0) / effectiveDuration) - (audioCodec === 'none' ? 0 : audioKbps), 0) : 0),
    final_mb: targetMode === 'bitrate' && effectiveDuration > 0
      ? ((targetVideoKbps + (audioCodec === 'none' ? 0 : audioKbps)) * effectiveDuration) / 8192.0
      : targetMB
  } : null;
  // Update warning dynamically based on current estimate (no need to re-upload)
  $: warnText = estimated && estimated.video_kbps < 100 ? `Warning: Very low video bitrate (${Math.round(estimated.video_kbps)} kbps)` : null;
  // Resolution recommendation banner when Auto is OFF: suggest a lower height if bitrate density is low
  $: (() => {
    resolutionSuggestText = null;
    resolutionSuggestHeight = null;
    if (!jobInfo || !estimated) return;
    if (audioOnly) return;
    if (autoResolution) return; // Auto handles this
    const ow = jobInfo.original_width || 0;
    const oh = jobInfo.original_height || 0;
    if (!ow || !oh) return;
    const currentH = explicitHeight || oh;
    // Compute kbps per megapixel at the current selected height
    const mpAt = (h:number) => (ow * (h/oh) * h) / 1_000_000.0;
    const mp = mpAt(currentH);
    if (mp <= 0) return;
    const kv = estimated.video_kbps || 0;
    if (kv <= 0) return;
    const kbpsPerMpix = kv / mp;
    // Softer thresholds than backend to avoid aggressive prompts
    const LADDER: Array<2160|1440|1080|720|480|360|240> = [2160,1440,1080,720,480,360,240];
    // Only warn if density below 600 kbps/mpix
    const MIN_OK = 600;
    const MIN_FALLBACK = 380; // if really starved, accept this floor
    if (kbpsPerMpix >= MIN_OK) return; // no suggestion
    // Find first height meeting MIN_OK; else MIN_FALLBACK; else min rung
    const cappedOrigH = LADDER.find(h => h <= oh) ?? oh;
    let rec: any = null;
    for (const h of LADDER) {
      if (h > oh) continue;
      const d = kv / mpAt(h);
      if (d >= MIN_OK) { rec = h; break; }
    }
    if (!rec) {
      for (const h of LADDER) {
        if (h > oh) continue;
        const d = kv / mpAt(h);
        if (d >= MIN_FALLBACK) { rec = h; break; }
      }
    }
    if (!rec) rec = LADDER[LADDER.length-1];
    // If currently already at or below recommendation, no suggestion
    if (currentH <= rec) return;
    // Prefer 1080p when original >= 1440 and 1080p meets MIN_FALLBACK
    if (oh >= 1440 && kv / mpAt(1080) >= MIN_FALLBACK) {
      rec = 1080;
    }
    resolutionSuggestHeight = rec;
    const label = rec === 2160 ? '2160p (4K)' : rec === 1440 ? '1440p' : rec === 1080 ? '1080p' : `${rec}p`;
    resolutionSuggestText = `Recommended: lower resolution to ${label} for better quality at this size.`;
  })();
  
  // Removed auto-reupload on settings changes. Analysis is now separate from upload.
  // Changing target size or audio bitrate only updates client-side estimates.

  let jobInfo: any = null;
  let taskId: string | null = null;
  let progress = 0;
  let displayedProgress = 0;
  let logLines: string[] = [];
  let doneStats: any = null;
  let isCompressing = false;
  let esRef: EventSource | null = null;
  let errorText: string | null = null;
  let isUploading = false;
  let uploadProgress = 0;
  let isCancelling = false;
  // Download readiness
  let isReady: boolean = false;
  let readyFilename: string | null = null;
  let showTryDownload: boolean = false;
  let readyTimer: any = null;
  let tryDownloading: boolean = false; // UI state for Try Download button
  // ETA / status helpers
  let startedAt: number | null = null;
  let etaSeconds: number | null = null;
  let etaLabel: string | null = null;
  let currentSpeedX: number | null = null;
  let hasProgress = false;
  let decodeMethod: string | null = null;
  let encodeMethod: string | null = null;
  let isFinalizing = false; // Track if we're in the finalization phase
  let isRetrying = false; // Track if we're in automatic retry mode
  let retryMessage = ''; // Retry details to display
  let finalizePoller: any = null; // interval id for readiness polling during finalizing
  // Support widget state
  let showSupport = false;
  function toggleSupport(){ showSupport = !showSupport; }
  function closeSupport(){ showSupport = false; }
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeSupport(); };
  // App version (subtle badge)
  let appVersion: string | null = null;
  // Daemon status for host system info
  let daemonStatus: any = null;

  // Available codecs from backend
  let availableCodecs: Array<{value: string, label: string, group: string}> = [];
  let hardwareType = 'cpu';
  let sysCaps: any = null;
  let sysCapsError: string | null = null;
  // Encoder tests summary
  let encoderTests: Array<{ 
    codec: string; 
    actual_encoder: string; 
    passed: boolean; 
    encode_passed: boolean;
    encode_message?: string;
    decode_passed?: boolean;
    decode_message?: string;
  }>|null = null;
  let gpuOk: boolean = false;
  // Presets and size buttons
  let presetProfiles: Array<any> = [];
  let selectedPreset: string | null = null;
  let sizeButtons: any[] = [4,5,8,9.7,50,100];
  // Recent history
  let history: any[] = [];
  let historyEnabled = false;
  // Batch progress tracker (shared with /batch page)
  const ACTIVE_BATCH_KEY = 'activeBatchId';
  let activeBatchId: string | null = null;
  let activeBatchStatus: any = null;
  let activeBatchError: string | null = null;
  let activeBatchPoller: any = null;

  // Load default presets and available codecs on mount
  onMount(async () => {
    // Restore UI preferences
    try {
      const ps = localStorage.getItem('playSoundWhenDone');
      if (ps !== null) playSoundWhenDone = (ps === 'true');
      const ad = localStorage.getItem('autoDownload');
      if (ad !== null) autoDownload = (ad === 'true');
      // If not present in localStorage, default to true and set it
      if (ad === null) localStorage.setItem('autoDownload', 'true');
      const aab = localStorage.getItem('autoAudioBitrate');
      if (aab !== null) autoAudioBitrate = (aab === 'true');
      if (aab === null) localStorage.setItem('autoAudioBitrate', 'true');
      const fmf = localStorage.getItem('fastMp4Finalize');
      if (fmf !== null) fastMp4Finalize = (fmf === 'true');
      const phd = localStorage.getItem('preferHwDecode');
      if (phd !== null) preferHwDecode = (phd === 'true');
      const parsedFps = parseStoredFpsCap(localStorage.getItem('maxOutputFpsCap'));
      if (parsedFps !== null) maxFpsCap = parsedFps;
    } catch {}
    try {
      const res = await fetch('/api/settings/presets');
      if (res.ok) {
        const presets = await res.json();
        targetMB = presets.target_mb;
        videoCodec = presets.video_codec;
        audioCodec = presets.audio_codec;
        preset = presets.preset;
        audioKbps = presets.audio_kbps;
        container = presets.container;
        tune = presets.tune;
      }
    } catch (err) {
      console.warn('Failed to load default presets, using hardcoded defaults');
    }

    // Load available codecs
    try {
      const codecData = await getAvailableCodecs();
      // Tentatively set hardware based on worker-reported type; we'll refine after sysCaps
      hardwareType = codecData.hardware_type || 'cpu';
      availableCodecs = buildCodecList(codecData);
    } catch (err) {
      console.warn('Failed to load available codecs, using fallback');
      availableCodecs = [
        { value: 'libx264', label: 'H.264 (CPU)', group: 'cpu' },
        { value: 'libx265', label: 'HEVC (H.265, CPU)', group: 'cpu' },
        { value: 'libaom-av1', label: 'AV1 (CPU)', group: 'cpu' },
        { value: 'libsvtav1', label: 'AV1 (CPU - SVT-AV1)', group: 'cpu' },
      ];
    }

    // Load system capabilities (CPU, memory, GPUs)
    try {
      sysCaps = await getSystemCapabilities();
      // Use the worker-reported hardware type without forcing overrides
      const hw = sysCaps?.hardware?.type;
      if (hw) hardwareType = hw;
    } catch (e:any) {
      sysCapsError = e?.message || 'Failed to fetch system capabilities';
    }

    // Fetch daemon status to merge host system info
    try {
      daemonStatus = await getDaemonStatus();
      // Merge host system info into sysCaps when daemon is connected
      if (daemonStatus?.connected && sysCaps) {
        if (daemonStatus.host_cpu) {
          sysCaps.host_cpu = daemonStatus.host_cpu;
        }
        if (daemonStatus.host_memory) {
          sysCaps.host_memory = daemonStatus.host_memory;
        }
        if (daemonStatus.host_gpus?.length) {
          sysCaps.host_gpus = daemonStatus.host_gpus;
        }
      }
    } catch {}

    // Load encoder startup tests to report GPU availability
    try {
      const tests = await getEncoderTestResults();
      gpuOk = !!tests?.any_hardware_passed;
      encoderTests = (tests?.results || []);
    } catch {}

    // Fetch app version
    try {
      const v = await getVersion();
      appVersion = v?.version || null;
    } catch {}

    // Load preset profiles and size buttons
    try {
      const pp = await getPresetProfiles();
      presetProfiles = pp.profiles || [];
      selectedPreset = pp.default || (presetProfiles[0]?.name ?? null);
      if (selectedPreset) applyPreset(selectedPreset);
    } catch {}
    try {
      const sb = await getSizeButtons();
      if (sb?.buttons?.length) sizeButtons = sb.buttons;
    } catch {}

    // Fetch recent history (best-effort)
    try {
      const res = await fetch('/api/history');
      if (res.ok) {
        const data = await res.json();
        historyEnabled = !!data.enabled;
        history = (data.entries || []).slice(0,5);
      }
    } catch {}

    // Restore the currently tracked batch from the batch page
    try {
      const tracked = localStorage.getItem(ACTIVE_BATCH_KEY);
      if (tracked) {
        activeBatchId = tracked;
        await refreshActiveBatchStatus();
      }
    } catch {}
    settingsLoaded = true;
  });

  function applyPreset(name: string){
    const p = presetProfiles.find(x => x.name === name);
    if (!p) return;
    selectedPreset = name;
    targetMB = p.target_mb;
    videoCodec = p.video_codec;
    audioCodec = p.audio_codec;
    preset = p.preset;
    audioKbps = p.audio_kbps;
    container = p.container;
    tune = p.tune;
    maxFpsCap = maxFpsFromProfile(p.max_output_fps);
  }

  function formatDurationTime(seconds: number): string {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    
    if (h > 0) {
      return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    } else {
      return `${m}:${s.toString().padStart(2, '0')}`;
    }
  }

  function buildCodecList(codecData: any): Array<{value: string, label: string, group: string}> {
    const list: Array<{value: string, label: string, group: string}> = [];
    const enabledCodecs = codecData.enabled_codecs || [];
    
    // Build list of all possible codecs with labels
    const codecDefinitions = [
      // NVIDIA NVENC
      { value: 'av1_nvenc', label: 'AV1 (NVIDIA - RTX 40/50 series)', group: 'nvidia' },
      { value: 'hevc_nvenc', label: 'HEVC (H.265, NVIDIA)', group: 'nvidia' },
      { value: 'h264_nvenc', label: 'H.264 (NVIDIA)', group: 'nvidia' },
      // CPU / software
      { value: 'libaom-av1', label: 'AV1 (CPU - Highest Quality)', group: 'cpu' },
      { value: 'libsvtav1', label: 'AV1 (CPU - SVT-AV1)', group: 'cpu' },
      { value: 'libx265', label: 'HEVC (H.265, CPU)', group: 'cpu' },
      { value: 'libx264', label: 'H.264 (CPU)', group: 'cpu' },
    ];
    
    // Filter to only include codecs that are enabled in settings
    for (const codec of codecDefinitions) {
      if (enabledCodecs.includes(codec.value)) {
        list.push(codec);
      }
    }
    
    return list;
  }

  // Auto-adjust audio bitrate for extreme compressions:
  // Ensure at least minVideoKbps is left for video; lower audio down to a floor of 32 kbps if needed.
  $: (async () => {
    try {
      if (!autoAudioBitrate) return;
      if (audioCodec === 'none') return;
      const dur = effectiveDuration;
      if (!dur || dur <= 0) return;
      const totalKbps = (targetMB * 8192.0) / dur;
      if (!isFinite(totalKbps) || totalKbps <= 0) return;
      const minVideoKbps = 100; // keep at least ~100 kbps for video to avoid severe degradation
      const allowed: Array<32|48|64|96|128|160|192|256> = [256,192,160,128,96,64,48,32];
      const base = baseAudioKbps;
      let newAudio: 32|48|64|96|128|160|192|256 = base as any;
      if ((totalKbps - base) < minVideoKbps) {
        const maxAudio = Math.max(32, Math.floor(totalKbps - minVideoKbps));
        // Find the largest allowed bitrate not exceeding maxAudio and the user's base preference
        let chosen: 32|48|64|96|128|160|192|256 = 32;
        for (const v of allowed) {
          if (v <= maxAudio && v <= base) { chosen = v; break; }
        }
        newAudio = chosen;
      }
      if (newAudio !== audioKbps) {
        audioKbps = newAudio as any;
      }
    } catch {}
  })();

  // Smooth progress animation - gradually update displayedProgress towards actual progress
  $: (() => {
    // Direct update for significant changes or completion
    if (progress >= 100 || Math.abs(progress - displayedProgress) > 10) {
      displayedProgress = progress;
    } else if (progress > displayedProgress) {
      // Only animate forward, not backward
      const diff = progress - displayedProgress;
      if (diff > 0.1) {
        const step = Math.min(diff / 5, 1);
        displayedProgress = Math.min(displayedProgress + step, progress);
      } else {
        displayedProgress = progress;
      }
    }
  })();

  function getCodecColor(group: string): string {
    switch(group) {
      case 'nvidia': return '#22c55e'; // green
      case 'cpu': return '#6b7280';    // gray
      default: return '#6b7280';
    }
  }

  function getCodecIcon(group: string): string {
    switch(group) {
      case 'nvidia': return '🟢';
      case 'cpu': return '⚪';
      default: return '⚪';
    }
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    const kb = bytes / 1024;
    if (kb < 1024) return `${kb.toFixed(1)} KB`;
    const mb = kb / 1024;
    if (mb < 1024) return `${mb.toFixed(2)} MB`;
    const gb = mb / 1024;
    return `${gb.toFixed(2)} GB`;
  }

  function formatBatchState(state?: string): string {
    if (!state) return 'Unknown';
    if (state === 'completed_with_errors') return 'Completed with errors';
    return state.charAt(0).toUpperCase() + state.slice(1);
  }

  function isBatchTerminal(state?: string): boolean {
    return state === 'completed' || state === 'completed_with_errors' || state === 'failed';
  }

  function stopActiveBatchPolling() {
    if (activeBatchPoller) {
      clearInterval(activeBatchPoller);
      activeBatchPoller = null;
    }
  }

  function clearActiveBatchTracker() {
    activeBatchId = null;
    activeBatchStatus = null;
    activeBatchError = null;
    stopActiveBatchPolling();
    try { localStorage.removeItem(ACTIVE_BATCH_KEY); } catch {}
  }

  async function refreshActiveBatchStatus(silent = false) {
    if (!activeBatchId) return;
    try {
      const status = await getBatchStatus(activeBatchId);
      activeBatchStatus = status;
      activeBatchError = null;
      if (isBatchTerminal(status?.state)) {
        stopActiveBatchPolling();
      } else if (!activeBatchPoller) {
        activeBatchPoller = setInterval(() => {
          refreshActiveBatchStatus(true).catch(() => {});
        }, 3000);
      }
    } catch (e: any) {
      const msg = e?.message || 'Failed to load active batch status';
      if (!silent) activeBatchError = msg;
      const low = String(msg).toLowerCase();
      if (low.includes('404') || low.includes('batch not found')) {
        clearActiveBatchTracker();
      }
    }
  }

  function formatEta(sec: number): string {
    if (!isFinite(sec) || sec < 0) return '';
    const s = Math.round(sec);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${r}s`;
    return `${r}s`;
  }

  function setPresetMB(mb:number){ targetMB = mb; }
  // "10MB (Discord)" option: pick slightly under to ensure final stays below 10MB
  function setPresetMBSafe10(){ targetMB = 9.7; }

  async function onDrop(e: DragEvent){
    e.preventDefault();
    if (!e.dataTransfer) return;
    const f = e.dataTransfer.files?.[0];
    if (f) {
      file = f;
      fileSizeLabel = formatSize(f.size);
      // Auto-analyze on drop
      setTimeout(() => doUpload(), 100);
    }
  }
  function allowDrop(e: DragEvent){ e.preventDefault(); }

  async function doUpload(){
    if (!file) return;
    if (isUploading || isAnalyzing) return;
    // Skip re-upload when same file is already uploaded; recompute client-side estimates only
    if (uploadedFileName === file.name && jobInfo?.filename) {
      warnText = (estimated && estimated.video_kbps < 100) ? `Warning: Very low video bitrate (${Math.round(estimated.video_kbps)} kbps)` : null;
      return;
    }
    isAnalyzing = true;
    isUploading = true;
    uploadProgress = 0;
    errorText = null;
    try {
      console.log('Analyzing file...', file.name);
      jobInfo = await uploadWithProgress(file, targetMB, audioKbps, { onProgress: (p:number)=>{ uploadProgress = p; } });
      console.log('Analysis complete:', jobInfo);
      uploadedFileName = file.name; // Mark this file as uploaded
      // Set warn based on current client-side estimate
      warnText = (estimated && estimated.video_kbps < 100) ? `Warning: Very low video bitrate (${Math.round(estimated.video_kbps)} kbps)` : null;
    } catch (err: any) {
      console.error('Analysis failed:', err);
      errorText = `Analysis failed: ${err.message || err}`;
    } finally {
      isUploading = false;
      isAnalyzing = false;
    }
  }

  async function doCompress(){
    // Ensure we have analysis; if not, perform upload/analyze once
    if (!jobInfo) {
      if (!file) { errorText = 'Please select a file and analyze first.'; return; }
      await doUpload();
      if (!jobInfo) return; // if upload failed
    }
    if (isCompressing) return; // prevent double submission
    errorText = null;
    try {
      isCompressing = true;
      isReady = false;
      readyFilename = null;
      hasProgress = false;
      isFinalizing = false;
      startedAt = Date.now();
      etaSeconds = null;
      etaLabel = null;
      currentSpeedX = null;
      logLines = ['Starting compression…', ...logLines].slice(0, 500);
      const estMb =
        targetMode === 'bitrate' && effectiveDuration > 0
          ? ((targetVideoKbps + (audioCodec === 'none' ? 0 : audioKbps)) * effectiveDuration) / 8192.0
          : targetMB;
      const payload = {
        job_id: jobInfo.job_id,
        filename: jobInfo.filename,
        target_size_mb: estMb,
        target_video_bitrate_kbps: targetMode === 'bitrate' ? targetVideoKbps : undefined,
        video_codec: videoCodec,
        audio_codec: audioCodec,
        audio_bitrate_kbps: audioKbps,
        preset,
        container,
        tune,
        force_hw_decode: preferHwDecode,
  fast_mp4_finalize: fastMp4Finalize,
        // Optional resolution and trim parameters
        max_width: (autoResolution || explicitHeight) ? undefined : (maxWidth || undefined),
        max_height: (autoResolution && !explicitHeight) ? undefined : (explicitHeight || maxHeight || undefined),
        auto_resolution: autoResolution,
        min_auto_resolution: minAutoHeight,
        target_resolution: explicitHeight || undefined,
        audio_only: audioOnly,
        max_output_fps: maxFpsCap === '' ? undefined : Number(maxFpsCap),
        start_time: startTime.trim() || undefined,
        end_time: endTime.trim() || undefined,
      };
      console.log('Starting compression...', payload);
      const { task_id } = await startCompress(payload);
      taskId = task_id;
      
      console.log('🔴 [DEBUG] About to open SSE for task_id:', task_id);
      
      // Open SSE progress stream
      logLines = ['✓ Job started. Opening progress stream...', ...logLines].slice(0, 500);
      
      const es = openProgressStream(task_id);
      console.log('🔴 [DEBUG] openProgressStream returned:', es);
      esRef = es;
      
      es.onopen = () => {
        console.log('SSE connection opened for task:', task_id);
        logLines = ['✅ Connected to progress stream', ...logLines].slice(0, 500);
      };
      
      es.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          console.log('SSE event:', data.type, data);
          
          // Handle connection confirmation
          if (data.type === 'connected') {
            console.log('SSE connection confirmed, task_id:', data.task_id);
            return; // Just log, don't show to user
          }
          
          // Handle pings
          if (data.type === 'ping') {
            return; // Silent heartbeat
          }
          
          // Handle progress updates
          if (data.type === 'progress') {
            progress = data.progress;
            
            // ETA from server if present; otherwise hide (no client-side guess)
            if (typeof data.eta_seconds === 'number' && isFinite(data.eta_seconds) && data.eta_seconds > 0) {
              etaSeconds = data.eta_seconds;
              const s = Math.round(etaSeconds);
              const h = Math.floor(s / 3600);
              const m = Math.floor((s % 3600) / 60);
              const r = s % 60;
              etaLabel = h > 0 ? `${h}h ${m}m` : (m > 0 ? `${m}m ${r}s` : `${r}s`);
            } else {
              etaSeconds = null;
              etaLabel = null;
            }

            // Update current speed if provided
            if (typeof data.speed_x === 'number' && isFinite(data.speed_x) && data.speed_x > 0) {
              currentSpeedX = data.speed_x;
            }

            // If we hit 100%, mark as complete
            if (data.progress >= 100 || data.phase === 'done') {
              progress = 100;
              displayedProgress = 100;
              isCompressing = false;
              isFinalizing = false;
              logLines = ['✅ 100% - Waiting for final confirmation...', ...logLines].slice(0, 500);
            }
            
            // Mark that we've received at least one progress update
            else if (!hasProgress && data.progress > 0) {
              hasProgress = true;
            }
            
            // Detect finalization phase (95-100%)
            if (data.phase === 'finalizing' || (data.progress >= 95 && data.progress < 100)) {
              isFinalizing = true;
            } else if (data.phase === 'encoding') {
              isFinalizing = false;
            }
          }
          
          // Handle log messages
          if (data.type === 'log' && data.message) {
            logLines = [data.message, ...logLines].slice(0, 500);
            
            // Extract encoding speed
            const speedMatch = data.message.match(/speed=([\d.]+)x/);
            if (speedMatch) {
              currentSpeedX = parseFloat(speedMatch[1]);
            }
            
            // Detect hardware methods
            // Decoder detection: handle variations like "Decoder: using cuda" or "Decoder: forcing av1_cuvid"
            {
              const msg = data.message as string;
              const lower = msg.toLowerCase();
              if (lower.startsWith('decoder:')) {
                // Try to capture token after 'using' or 'forcing'
                const m1 = msg.match(/Decoder:\s*(?:using|forcing)\s*([\w_]+)/i);
                if (m1) {
                  decodeMethod = m1[1];
                } else {
                  // Fallback: capture first word after 'Decoder:'
                  const m2 = msg.match(/Decoder:\s*([\w_]+)/i);
                  if (m2) decodeMethod = m2[1];
                }
              }
            }
            // Encoder detection: handle "Using encoder: h264_nvenc" and "Encoder: CPU (libx264)"
            {
              const msg = data.message as string;
              const mUse = msg.match(/Using\s+encoder:\s*([\w_-]+)/i);
              if (mUse) {
                encodeMethod = mUse[1];
              }
              const mEnc = msg.match(/Encoder:\s*([\w_-]+)/i);
              if (mEnc) {
                // May be "CPU (libx264)"; extract inner encoder if present
                const val = mEnc[1];
                const inner = msg.match(/Encoder:\s*CPU\s*\(([^)]+)\)/i);
                encodeMethod = inner ? inner[1] : val;
              }
            }
          }
          
          // Do not handle early 'ready' events; download is enabled only after 'done'
          
          // Handle completion
          if (data.type === 'done') {
            console.log('Received done event, completing job');
            doneStats = data.stats;
            progress = 100;
            displayedProgress = 100;
            isCompressing = false;
            isFinalizing = false;
            isRetrying = false;
            retryMessage = '';
            isReady = true;
            hasProgress = false;
            
            logLines = ['✅ Compression complete!', ...logLines].slice(0, 500);
            
            try { esRef?.close(); esRef = null; } catch {}
            
            // Play sound if enabled (gentle success chime)
            if (playSoundWhenDone) {
              // Pleasant ascending chime (C-E-G major chord)
              const audio = new Audio('data:audio/wav;base64,UklGRiQFAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAFAAB/goSGiIqMjo+RkpOUlZaXmJmam5ydnp+goaKjpKWmp6ipqqusra6vsLGys7S1tre4ubq7vL2+v8DBwsPExcbHyMnKy8zNzs/Q0dLT1NXW19jZ2tvc3d7f4OHi4+Tl5ufo6err7O3u7/Dx8vP09fb3+Pn6+/z9/v+AgYKDhIWGh4iJiouMjY6PkJGSk5SVlpeYmZqbnJ2en6ChoqOkpaanqKmqq6ytrq+wsbKztLW2t7i5uru8vb6/wMHCw8TFxsfIycrLzM3Oz9DR0tPU1dbX2Nna29zd3t/g4eLj5OXm5+jp6uvs7e7v8PHy8/T19vf4+fr7/P3+/4CBgoOEhYaHiImKi4yNjo+QkZKTlJWWl5iZmpucnZ6foKGio6SlpqeoqaqrrK2ur7CxsrO0tba3uLm6u7y9vr/AwcLDxMXGx8jJysvMzc7P0NHS09TV1tfY2drb3N3e3+Dh4uPk5ebn6Onq6+zt7u/w8fLz9PX29/j5+vv8/f7/gIGCg4SFhoeIiYqLjI2Oj5CRkpOUlZaXmJmam5ydnp+goaKjpKWmp6ipqqusra6vsLGys7S1tre4ubq7vL2+v8DBwsPExcbHyMnKy8zNzs/Q0dLT1NXW19jZ2tvc3d7f4OHi4+Tl5ufo6err7O3u7/Dx8vP09fb3+Pn6+/z9/v8=');
              audio.volume = 0.4;
              audio.play().catch(() => {});
            }
            
            // Auto-download if enabled
            if (autoDownload && taskId) {
              setTimeout(() => {
                window.location.href = downloadUrl(taskId!);
              }, 500);
            }
          }
          
          // Handle errors
          if (data.type === 'error') {
            logLines = [`❌ Error: ${data.message}`, ...logLines];
            errorText = data.message;
            isCompressing = false;
            isFinalizing = false;
            try { esRef?.close(); } catch {}
          }
          
          // Handle retry
          if (data.type === 'retry') {
            isRetrying = true;
            retryMessage = `File was ${data.overage_percent?.toFixed(1)}% over target - Re-encoding with optimized bitrate`;
            
            logLines = [`🔄 RETRY: ${data.message}`, ...logLines].slice(0, 500);
            
            // Show prominent notification banner
            const retryMsg = `⚠️ File too large (${data.overage_percent?.toFixed(1)}% over target) - Re-encoding with adjusted bitrate...`;
            logLines = [
              '═══════════════════════════════════════════════════',
              `🔄 AUTOMATIC RETRY IN PROGRESS`,
              retryMsg,
              'This is normal - the system will automatically optimize the output.',
              '═══════════════════════════════════════════════════',
              ...logLines
            ].slice(0, 500);
            
            // Play distinct "retry" sound (warning tone - lower pitched)
            try {
              // Lower warning tone (F note) - distinct from success chime
              const audio = new Audio('data:audio/wav;base64,UklGRiQDAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQADAAB/fn18e3p5eHd2dXRzcnFwb25tbGtqaWhnZmVkY2JhYF9eXVxbWllYV1ZVVFNSUVBPTk1MS0pJSEdGRURDQkFAPz49PDo6OTg3NjU0MzIxMC8uLSwrKikoJyYlJCMiISAfHh0cGxoZGBcWFRQTEhEQDw4NDAsKCQgHBgUEAwIBAACAgYKDhIWGh4iJiouMjY6PkJGSk5SVlpeYmZqbnJ2en6ChoqOkpaanqKmqq6ytrq+wsbKztLW2t7i5uru8vb6/wMHCw8TFxsfIycrLzM3Oz9DR0tPU1dbX2Nna29zd3t/g4eLj5OXm5+jp6uvs7e7v8PHy8/T19vf4+fr7/P3+/4CBgoOEhYaHiImKi4yNjo+QkZKTlJWWl5iZmpucnZ6foKGio6SlpqeoqaqrrK2ur7CxsrO0tba3uLm6u7y9vr/AwcLDxMXGx8jJysvMzc7P0NHS09TV1tfY2drb3N3e3+Dh4uPk5ebn6Onq6+zt7u/w8fLz9PX29/j5+vv8/f7/gIGCg4SFhoeIiYqLjI2Oj5CRkpOUlZaXmJmam5ydnp+goaKjpKWmp6ipqqusra6vsLGys7S1tre4ubq7vL2+v8DBwsPExcbHyMnKy8zNzs/Q0dLT1NXW19jZ2tvc3d7f4OHi4+Tl5ufo6err7O3u7/Dx8vP09fb3+Pn6+/z9/v8=');
              audio.volume = 0.3;
              audio.play().catch(() => {});
              // Play second tone after brief pause
              setTimeout(() => {
                const audio2 = new Audio('data:audio/wav;base64,UklGRiQDAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQADAAB/fn18e3p5eHd2dXRzcnFwb25tbGtqaWhnZmVkY2JhYF9eXVxbWllYV1ZVVFNSUVBPTk1MS0pJSEdGRURDQkFAPz49PDo6OTg3NjU0MzIxMC8uLSwrKikoJyYlJCMiISAfHh0cGxoZGBcWFRQTEhEQDw4NDAsKCQgHBgUEAwIBAACAgYKDhIWGh4iJiouMjY6PkJGSk5SVlpeYmZqbnJ2en6ChoqOkpaanqKmqq6ytrq+wsbKztLW2t7i5uru8vb6/wMHCw8TFxsfIycrLzM3Oz9DR0tPU1dbX2Nna29zd3t/g4eLj5OXm5+jp6uvs7e7v8PHy8/T19vf4+fr7/P3+/4CBgoOEhYaHiImKi4yNjo+QkZKTlJWWl5iZmpucnZ6foKGio6SlpqeoqaqrrK2ur7CxsrO0tba3uLm6u7y9vr/AwcLDxMXGx8jJysvMzc7P0NHS09TV1tfY2drb3N3e3+Dh4uPk5ebn6Onq6+zt7u/w8fLz9PX29/j5+vv8/f7/gIGCg4SFhoeIiYqLjI2Oj5CRkpOUlZaXmJmam5ydnp+goaKjpKWmp6ipqqusra6vsLGys7S1tre4ubq7vL2+v8DBwsPExcbHyMnKy8zNzs/Q0dLT1NXW19jZ2tvc3d7f4OHi4+Tl5ufo6err7O3u7/Dx8vP09fb3+Pn6+/z9/v8=');
                audio2.volume = 0.3;
                audio2.play().catch(() => {});
              }, 250);
            } catch {}
            
            // Reset progress for retry
            displayedProgress = 1;
            progress = 1;
          }
          
          // Handle cancellation
          if (data.type === 'canceled') {
            logLines = ['🚫 Job canceled', ...logLines];
            isCompressing = false;
            isFinalizing = false;
            try { esRef?.close(); } catch {}
          }
          
        } catch (e) {
          console.error('Failed to parse SSE message:', e);
        }
      };
      
      es.onerror = (err) => {
        console.error('SSE error:', err);
        console.log('SSE readyState:', es.readyState, 'taskId:', taskId);
        
        // Don't immediately fail - the job might still be running
        // Only show warning, don't stop isCompressing
        logLines = ['⚠️ Progress stream connection issue. Checking queue for updates...', ...logLines].slice(0, 500);
        
        // Close this connection attempt
        try { esRef?.close(); esRef = null; } catch {}
        
        // Suggest checking queue page
        if (!doneStats && taskId) {
          logLines = [
            `💡 View live progress at: <a href="/queue" class="text-blue-400 underline">/queue</a>`,
            `Task ID: ${taskId}`,
            ...logLines
          ].slice(0, 500);
        }
      };
      
    } catch (err: any) {
      console.error('Compress failed:', err);
      errorText = `Compression failed: ${err.message || err}`;
      isCompressing = false;
    }
  }

  // Try Download handler: attempt download immediately via server's wait parameter
  async function tryDownloadNow(){
    if (!taskId) return;
    tryDownloading = true;
    const url = downloadUrl(taskId);
    // Just open the URL with ?wait=2 to give the backend time to finalize
    // If it's not ready, the backend will return a 404 with detail JSON, but at least
    // the finalization watchdog will keep polling and eventually succeed
    try {
      // Use window.location with wait parameter; if it fails, browser shows download or error
      window.location.href = `${url}?wait=2`;
    } finally {
      // Reset state after a moment (the page may navigate away if download succeeds)
      setTimeout(() => { tryDownloading = false; }, 1000);
    }
  }

  // Finalization watchdog: start/stop a short poller if we hit 100% but 'ready' hasn't arrived
  $: (async () => {
    // CRITICAL: Only trigger watchdog at EXACTLY 100%, not at high progress like 98%
    // This prevents "zombie stream" bug where client resets while server still encoding
    const shouldPoll = !!taskId && displayedProgress >= 99.9 && !isReady && !doneStats && isCompressing;
    console.log('[Watchdog] Reactive check - shouldPoll:', shouldPoll, 'displayedProgress:', displayedProgress, 'isReady:', isReady, 'isCompressing:', isCompressing);
    if (shouldPoll && !finalizePoller) {
      console.log('[Watchdog] Starting finalization poll for', taskId);
      finalizePoller = setInterval(async () => {
        if (!taskId) return;
        try {
          console.log('[Watchdog] Polling download endpoint...');
          // Try GET request with short wait instead of HEAD (more reliable)
          const dlRes = await fetch(`${downloadUrl(taskId)}?wait=2`, { 
            method: 'GET',
            cache: 'no-store',
            redirect: 'manual' // Don't follow redirects, just check response
          });
          console.log('[Watchdog] Response status:', dlRes.status, 'ok:', dlRes.ok);
          
          if (dlRes.ok && dlRes.status === 200) {
            console.log('[Watchdog] File ready! Auto-downloading...');
            isReady = true;
            isFinalizing = false;
            showTryDownload = false;
            isCompressing = false;
            clearInterval(finalizePoller);
            finalizePoller = null;
            // Trigger download by navigating to URL
            window.location.href = downloadUrl(taskId!);
          } else if (dlRes.status === 404) {
            const body = await dlRes.json().catch(() => ({}));
            console.log('[Watchdog] File not ready yet (404):', body.detail?.state || 'unknown state');
          } else {
            console.log('[Watchdog] Unexpected status, will retry...');
          }
        } catch (e) {
          console.log('[Watchdog] Poll error:', e);
        }
      }, 1000);
    } else if (!shouldPoll && finalizePoller) {
      console.log('[Watchdog] Stopping finalization poll (shouldPoll=false)');
      clearInterval(finalizePoller);
      finalizePoller = null;
    }
  })();

  function reconnectStream(){
    if (!taskId) return;
    errorText = null;
    try { esRef?.close(); } catch {}
    const es = openProgressStream(taskId);
    esRef = es;
    isCompressing = true;
    es.onmessage = (ev) => {
      try { const data = JSON.parse(ev.data);
        if (data.type === 'progress') { progress = data.progress; }
        if (data.type === 'log' && data.message) { logLines = [data.message, ...logLines].slice(0, 500); }
        if (data.type === 'done') { 
          doneStats = data.stats; 
          progress = 100;
          isCompressing = false;
          try { esRef?.close(); } catch {}
          // Play sound when done if enabled (gentle success chime)
          if (playSoundWhenDone) {
            // Pleasant ascending chime (C-E-G major chord)
            const audio = new Audio('data:audio/wav;base64,UklGRiQFAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAFAAB/goSGiIqMjo+RkpOUlZaXmJmam5ydnp+goaKjpKWmp6ipqqusra6vsLGys7S1tre4ubq7vL2+v8DBwsPExcbHyMnKy8zNzs/Q0dLT1NXW19jZ2tvc3d7f4OHi4+Tl5ufo6err7O3u7/Dx8vP09fb3+Pn6+/z9/v+AgYKDhIWGh4iJiouMjY6PkJGSk5SVlpeYmZqbnJ2en6ChoqOkpaanqKmqq6ytrq+wsbKztLW2t7i5uru8vb6/wMHCw8TFxsfIycrLzM3Oz9DR0tPU1dbX2Nna29zd3t/g4eLj5OXm5+jp6uvs7e7v8PHy8/T19vf4+fr7/P3+/4CBgoOEhYaHiImKi4yNjo+QkZKTlJWWl5iZmpucnZ6foKGio6SlpqeoqaqrrK2ur7CxsrO0tba3uLm6u7y9vr/AwcLDxMXGx8jJysvMzc7P0NHS09TV1tfY2drb3N3e3+Dh4uPk5ebn6Onq6+zt7u/w8fLz9PX29/j5+vv8/f7/gIGCg4SFhoeIiYqLjI2Oj5CRkpOUlZaXmJmam5ydnp+goaKjpKWmp6ipqqusra6vsLGys7S1tre4ubq7vL2+v8DBwsPExcbHyMnKy8zNzs/Q0dLT1NXW19jZ2tvc3d7f4OHi4+Tl5ufo6err7O3u7/Dx8vP09fb3+Pn6+/z9/v8=');
            audio.volume = 0.4;
            audio.play().catch(() => {});
          }
          // Auto-download if enabled
          if (autoDownload && taskId) {
            setTimeout(() => {
              window.location.href = downloadUrl(taskId!);
            }, 500);
          }
        }
        if (data.type === 'error') { logLines = [data.message, ...logLines]; isCompressing = false; try { esRef?.close(); } catch {} }
      } catch {}
    }
    es.onerror = () => {
      logLines = ['[SSE] Connection error: lost progress stream.', ...logLines].slice(0, 500);
      errorText = 'Lost connection to progress stream. Check server/network and try again.';
      isCompressing = false;
      try { esRef?.close(); } catch {}
    }
  }

  // Remove older reset; replace with one that clears readiness flags too
  
  function reset(){
    // Clear all job-related state but keep the selected file loaded so it can be reused
    uploadedFileName = null;
    jobInfo = null;
    taskId = null;
    progress = 0;
    displayedProgress = 0;
    logLines = [];
    doneStats = null;
    warnText = null;
    errorText = null;
    isUploading = false;
    isCompressing = false;
    isFinalizing = false;
    decodeMethod = null;
    encodeMethod = null;
    isReady = false;
    readyFilename = null;
    showTryDownload = false;
    if (readyTimer) { clearTimeout(readyTimer); readyTimer = null; }
    if (finalizePoller) { clearInterval(finalizePoller); finalizePoller = null; }
    try { esRef?.close(); } catch {}
    // Note: we intentionally do NOT clear `file` or `fileSizeLabel` here
  }

  function clearSelectedFile(){
    // Clear the chosen file and related analysis state
    file = null;
    fileSizeLabel = null;
    uploadedFileName = null;
    jobInfo = null;
    warnText = null;
    errorText = null;
    // Reset the input element so the same file can be selected again
    try { if (uploadInput) uploadInput.value = ''; } catch {}
  }
  $: (() => { /* clear ETA when not compressing */ if (!isCompressing) { startedAt = null; etaSeconds = null; etaLabel = null; currentSpeedX = null; hasProgress = false; isFinalizing = false; } })();

  async function onCancel(){
    if (!taskId || isCancelling) return;
    isCancelling = true;
    try {
      await cancelJob(taskId);
      logLines = ['Cancellation requested…', ...logLines].slice(0, 500);
    } catch (e:any) {
      errorText = e?.message || 'Failed to cancel';
    } finally {
      isCancelling = false;
    }
  }

  onDestroy(() => {
    stopActiveBatchPolling();
  });

  // Persist UI preferences
  $: if (settingsLoaded) {
    try { localStorage.setItem('playSoundWhenDone', String(playSoundWhenDone)); } catch {}
    try { localStorage.setItem('autoDownload', String(autoDownload)); } catch {}
    try { localStorage.setItem('autoAudioBitrate', String(autoAudioBitrate)); } catch {}
    try { localStorage.setItem('maxOutputFpsCap', maxFpsCap); } catch {}
  }
</script>

<svelte:head>
  <title>Advanced</title>
</svelte:head>

<main class="page-container">
  <div class="page-header mt-4">
    <div style="display:flex; align-items:center; gap:16px;">
      <h1 class="page-title" style="margin:0;">Advanced{#if appVersion}<span class="version-badge">v{appVersion}</span>{/if}</h1>
    </div>
    <div class="header-actions" style="display:flex; gap:8px; flex-wrap:wrap; justify-content: flex-end;">
      <a href="/" data-sveltekit-reload class="btn alt">🏠 Home</a>
      <a href="/batch" class="btn alt">🗂 Batch</a>
      <a href="/queue" class="btn alt">📋 Queue</a>
      <a href="/history" class="btn alt">📜 History</a>
      <a href="/settings" class="btn alt">⚙️ Settings</a>
    </div>
  </div>

  {#if activeBatchId}
    <div class="card batch-tracker">
      <div class="tracker-header">
        <div>
          <h3 class="card-title">Batch Tracker</h3>
          <p class="text-muted">Batch ID: {activeBatchId}</p>
        </div>
        <div class="header-actions">
          <a href="/batch" class="btn alt">Open</a>
          {#if activeBatchStatus?.completed_count > 0}
            <a href={batchZipDownloadUrl(activeBatchId)} class="btn">ZIP</a>
          {/if}
          <button class="btn alt" on:click={() => refreshActiveBatchStatus(false)}>Refresh</button>
          <button class="btn alt" on:click={clearActiveBatchTracker}>Clear</button>
        </div>
      </div>

      {#if activeBatchStatus}
        <div class="mt-3">
          <div class="flex items-center justify-between text-sm mb-1">
            <span>{formatBatchState(activeBatchStatus.state)}</span>
            <span>{Number(activeBatchStatus.overall_progress || 0).toFixed(1)}%</span>
          </div>
          <div class="progress-bar-bg">
            <div class="progress-bar-fill" style={`width:${Math.max(0, Math.min(100, Number(activeBatchStatus.overall_progress || 0)))}%`}></div>
          </div>
          <div class="tracker-stats">
            <div class="card">Queued: {activeBatchStatus.queued_count}</div>
            <div class="card">Running: {activeBatchStatus.running_count}</div>
            <div class="card">Done: {activeBatchStatus.completed_count}</div>
            <div class="card">Failed: {activeBatchStatus.failed_count}</div>
          </div>
        </div>
      {:else if activeBatchError}
        <p class="text-sm text-amber-300 mt-2">{activeBatchError}</p>
      {:else}
        <p class="text-sm opacity-70 mt-2">Loading active batch status…</p>
      {/if}
    </div>
  {/if}

  <div class="card">
    <div class="drop-zone"
         on:drop={onDrop} on:dragover={allowDrop}>
      <p>Drag & drop a video here</p>
  <input bind:this={uploadInput} type="file" accept="video/*" on:change={(e:any)=>{ const f=e.target.files?.[0]||null; file=f; fileSizeLabel = f? formatSize(f.size): null; if(f) setTimeout(()=>doUpload(), 100); }} />
      {#if file}
        <div class="mt-2 flex items-center gap-2">
          <p class="text-sm text-gray-400">{file.name} {#if fileSizeLabel}<span class="opacity-70">• {fileSizeLabel}</span>{/if}</p>
          <button class="btn" on:click={clearSelectedFile} title="Clear selected file">Clear</button>
        </div>
      {/if}
      {#if isUploading}
        <div class="upload-progress">
          <p class="text-muted">Analyzing video… {uploadProgress}%</p>
          <div class="progress-bar-bg">
            <div class="progress-bar-fill" style={`width:${uploadProgress}%`}></div>
          </div>
          <p class="text-xs text-gray-500 mt-1">Reading file properties and calculating optimal bitrates...</p>
        </div>
      {/if}
    </div>
  </div>

  <div class="card form-grid-3 items-center">
    <div class="flex flex-col gap-3 text-sm justify-center h-full">
      <label class="flex items-center gap-2 cursor-pointer">
        <input type="radio" name="targetMode" value="size" bind:group={targetMode} disabled={audioOnly} />
        Target file size
      </label>
      <label class="flex items-center gap-2 cursor-pointer">
        <input type="radio" name="targetMode" value="bitrate" bind:group={targetMode} disabled={audioOnly} />
        Target video bitrate
      </label>
    </div>
    
    <div class="space-y-3">
      {#if targetMode === 'size'}
        <div class="size-grid">
          {#each sizeButtons as b}
            <button class="btn" type="button" on:click={()=>setPresetMB(Number(b.mb || b))}>{b.label || b + ' MB'}</button>
          {/each}
        </div>
        <div class="flex items-center gap-2 flex-wrap">
          <label class="text-sm text-gray-400">Custom (MB)</label>
          <input class="input w-28" type="number" bind:value={targetMB} min="1" disabled={audioOnly} />
        </div>
      {:else}
        <div class="flex items-center gap-2 flex-wrap h-full">
          <label class="text-sm">Video bitrate (kbps)</label>
          <input class="input w-32" type="number" bind:value={targetVideoKbps} min="50" max="200000" step="50" disabled={audioOnly} />
        </div>
      {/if}
    </div>

    <div class="text-sm text-gray-400 sm:text-right h-full flex flex-col justify-center">
      {#if targetMode === 'bitrate'}
        <p class="text-xs text-gray-500 mb-1">Audio is still set separately below; total size is not capped in this mode.</p>
        {#if estimated && effectiveDuration > 0}
          <p>≈ {estimated.final_mb.toFixed(2)} MB at this bitrate for the selected duration (estimate).</p>
        {/if}
      {/if}
    </div>
  </div>

  <!-- Primary controls: Codec and Speed/Quality preset (visible without expanding) -->
  <div class="card mt-2">
    <h4 class="text-lg font-medium mb-4 text-gray-200">Video Configuration</h4>
    <div class="grid sm:grid-cols-3 gap-4">
      <div>
        <label class="block mb-1 text-sm">Video Codec</label>
      <select class="input w-full codec-select" bind:value={videoCodec}>
        {#each availableCodecs as codec}
          <option value={codec.value} data-group={codec.group}>
            {getCodecIcon(codec.group)} {codec.label}
          </option>
        {/each}
      </select>
      {#if hardwareType !== 'cpu'}
        <p class="text-xs text-gray-400 mt-1">
          <span class="inline-block w-2 h-2 rounded-full mr-1" style="background-color: {getCodecColor(hardwareType)}"></span>
          Detected: {hardwareType.toUpperCase()} acceleration
        </p>
      {:else}
        <p class="text-xs text-gray-400 mt-1">
          <span class="inline-block w-2 h-2 rounded-full bg-gray-500 mr-1"></span>
          CPU encoding (no GPU detected)
        </p>
      {/if}
      <!-- Audio-only toggle moved here (out of Advanced) -->
      <div class="mt-2">
        <label class="flex items-center gap-2 cursor-pointer text-sm"><input type="checkbox" bind:checked={audioOnly} /><span>Extract audio only (.m4a)</span></label>
      </div>
    </div>
    <div>
      <label class="block mb-1 text-sm">Resolution</label>
      <div class="flex items-center gap-2">
        <select class="input w-full" disabled={audioOnly || autoResolution}
          on:change={(e:any)=>{ const v = e.target.value; explicitHeight = parseExplicitHeight(v); }}>
          <option value="">Original</option>
          <option value="2160">2160p (4K)</option>
          <option value="1440">1440p</option>
          <option value="1080">1080p</option>
          <option value="720">720p</option>
          <option value="480">480p</option>
          <option value="360">360p</option>
          <option value="240">240p</option>
        </select>
      </div>
      
      <div class="mt-4 pt-4 border-t border-gray-800 flex items-center justify-between text-sm">
        <label class="flex items-center gap-2 cursor-pointer mb-0">
          <input type="checkbox" bind:checked={autoResolution} disabled={audioOnly} style="margin: 0;" />
          <span>Auto (Resolution)</span>
        </label>
        <select class="input py-1 px-2 text-sm" style="width: 100px; height: 32px;" bind:value={minAutoHeight} disabled={audioOnly || !autoResolution}>
          <option value={240}>240p</option>
          <option value={360}>360p</option>
          <option value={480}>480p</option>
          <option value={720}>720p</option>
        </select>
      </div>
      {#if jobInfo?.original_width && jobInfo?.original_height}
        <p class="text-xs opacity-70 mt-1">Input: {jobInfo.original_width}×{jobInfo.original_height}{#if jobInfo.original_video_fps != null && jobInfo.original_video_fps > 0}&nbsp;· ~{Number(jobInfo.original_video_fps).toFixed(2).replace(/\.?0+$/, '')} fps{/if} → Output: {explicitHeight ? `${explicitHeight}p (max height)` : (autoResolution ? `auto (≥${minAutoHeight}p)` : (maxHeight || 'original'))}{#if maxFpsCap} · max {maxFpsCap} fps{/if}</p>
      {/if}
    </div>
    <div>
      <label class="block mb-1 text-sm">Quality preset</label>
      <select class="input w-full" bind:value={preset}>
        <option value="p1">Fast (P1)</option>
        <option value="p5">Balanced (P5)</option>
        <option value="p6">Default (P6)</option>
        <option value="p7">Best Quality (P7)</option>
        <option value="extraquality">🌟 Extra Quality</option>
      </select>
    </div>
  </div>
</div>

  <!-- AUDIO SETTINGS CARD -->
  <div class="card mt-2">
    <h4 class="text-lg font-medium mb-4 text-gray-200">Audio Configuration</h4>
    <div class="grid sm:grid-cols-2 gap-4">
      <div>
        <div class="flex justify-between items-center mb-1" style="min-height: 20px;">
          <label class="text-sm m-0">Audio Codec</label>
        </div>
        <select class="input w-full" bind:value={audioCodec}>
          <option value="libopus">Opus (Default)</option>
          <option value="aac">AAC</option>
          <option value="none">🔇 None (Mute)</option>
        </select>
      </div>
      <div>
        <div class="flex justify-between items-center mb-1" style="min-height: 20px;">
          <label class="text-sm m-0">Audio Bitrate (kbps)</label>
        </div>
        <select class="input w-full" bind:value={audioKbps} disabled={audioCodec === 'none' || autoAudioBitrate} on:change={(e:any)=>{ const v = parseInt(e.target.value); if (!Number.isNaN(v)) baseAudioKbps = v as any; }}>
          <option value={32}>32</option>
          <option value={48}>48</option>
          <option value={64}>64</option>
          <option value={96}>96</option>
          <option value={128}>128</option>
          <option value={160}>160</option>
          <option value={192}>192</option>
          <option value={256}>256</option>
        </select>
        {#if audioCodec === 'none'}
          <p class="text-xs text-gray-400 mt-1">Disabled (audio muted)</p>
        {:else}
          <div class="flex items-center gap-2 mt-1">
            <label class="flex items-center gap-1 text-xs cursor-pointer opacity-80 hover:opacity-100 m-0">
              <input type="checkbox" bind:checked={autoAudioBitrate} style="margin: 0;" />
              Auto
            </label>
            {#if autoAudioBitrate}
              <p class="text-xs text-gray-400 m-0">Adaptive bitrate downshifting is active.</p>
            {/if}
          </div>
        {/if}
      </div>
    </div>
  </div>

  <!-- TRIM & FORMAT CARD -->
  <div class="card mt-2">
    <h4 class="text-lg font-medium mb-4 text-gray-200">Container & Framing</h4>
    <div class="grid sm:grid-cols-2 gap-4">
      <div>
        <label class="block mb-1 text-sm">Container</label>
        <select class="input w-full" bind:value={container}>
          <option value="mp4">MP4 (Most compatible)</option>
          <option value="mkv">MKV (Best with Opus)</option>
        </select>
        {#if containerNote}
          <p class="mt-2 text-xs text-amber-400">{containerNote}</p>
        {/if}
      </div>
      <div>
        <label class="block mb-1 text-sm flex items-center gap-1">
          Tune <span class="text-[11px] opacity-70">(what to prioritize)</span>
        </label>
        <select class="input w-full" bind:value={tune} title="Tune tells the encoder what to optimize for.">
          <option value="hq">Best Quality (HQ)</option>
          <option value="ll">Low Latency (faster)</option>
          <option value="ull">Ultra‑Low Latency (fastest)</option>
          <option value="lossless">Lossless (no quality loss)</option>
        </select>
      </div>
      <div class="sm:col-span-2">
        <label class="block mb-1 text-sm">Max frame rate</label>
        <select class="input w-full" bind:value={maxFpsCap} disabled={audioOnly}>
          <option value="">Same as input (default)</option>
          {#each FPS_CAP_VALUES as v}
            <option value={v}>{v} fps (cap)</option>
          {/each}
        </select>
        <p class="text-xs opacity-70 mt-1">Slower sources stay unchanged. If source is faster, output is reduced to cap.</p>
      </div>
    </div>
    
    <div class="mt-6 pt-4 border-t border-gray-800">
      <h4 class="text-sm font-medium mb-3 text-gray-300">Resolution Bounds (Overrides Auto)</h4>
      <div class="grid sm:grid-cols-2 gap-4">
        <div>
          <label class="block mb-1 text-sm">Max Width (px)</label>
          <input class="input w-full" type="text" inputmode="numeric" pattern="[0-9]*" on:input={(e) => e.currentTarget.value = e.currentTarget.value.replace(/[^0-9]/g, '')} bind:value={maxWidth} placeholder="Original" disabled={autoResolution || !!explicitHeight} />
        </div>
        <div>
          <label class="block mb-1 text-sm">Max Height (px)</label>
          <input class="input w-full" type="text" inputmode="numeric" pattern="[0-9]*" on:input={(e) => e.currentTarget.value = e.currentTarget.value.replace(/[^0-9]/g, '')} bind:value={maxHeight} placeholder="Original" disabled={autoResolution || !!explicitHeight} />
        </div>
      </div>
    </div>

    <div class="mt-6 pt-4 border-t border-gray-800">
      <h4 class="text-sm font-medium mb-3 text-gray-300">Trimming</h4>
      <div class="grid sm:grid-cols-2 gap-4">
        <div>
          <label class="block mb-1 text-sm">Start Time</label>
          <input class="input w-full" type="text" bind:value={startTime} placeholder="0 or 00:00:00" />
        </div>
        <div>
          <label class="block mb-1 text-sm">End Time</label>
          <input class="input w-full" type="text" bind:value={endTime} placeholder="Full duration" />
        </div>
      </div>
      <p class="mt-1 text-xs opacity-70">Format: seconds or HH:MM:SS</p>
    </div>
  </div>

  {#if jobInfo}
    <div class="card">
      <p class="text-sm">
        {#if effectiveDuration !== jobInfo.duration_s}
          <span class="text-blue-400">Duration: {formatDurationTime(effectiveDuration)} (trimmed from {formatDurationTime(jobInfo.duration_s)})</span>
          <br />
        {/if}
        Original: {Math.round((jobInfo.original_video_bitrate_kbps||0)+(jobInfo.original_audio_bitrate_kbps||0))} kbps
        Target: {estimated ? Math.round(estimated.total_kbps) : Math.round(jobInfo.estimate_total_kbps)} kbps -> Video ~{estimated ? Math.round(estimated.video_kbps) : Math.round(jobInfo.estimate_video_kbps)} kbps
      </p>
      {#if estimated}
        <p class="text-xs opacity-80">Estimated final size: ~{estimated.final_mb.toFixed(2)} MB</p>
      {/if}
      {#if warnText}<p class="text-amber-400 text-sm mt-1">{warnText}</p>{/if}
      {#if resolutionSuggestText}
        <div class="mt-2 p-3 border border-amber-700/40 bg-amber-900/20 rounded">
          <div class="text-sm text-amber-200">{resolutionSuggestText}</div>
          {#if resolutionSuggestHeight}
            <button class="btn mt-2 text-xs" on:click={() => { explicitHeight = resolutionSuggestHeight; }}>
              Apply {resolutionSuggestHeight}p
            </button>
          {/if}
        </div>
      {/if}
    </div>
  {/if}

  {#if errorText}
    <div class="card border-red-500">
      <p class="text-red-400">{errorText}</p>
      {#if taskId && !doneStats}
        <div class="mt-2 flex gap-2 items-center">
          <button class="btn" on:click={reconnectStream}>Reconnect to progress</button>
          <span class="text-xs opacity-70">This just reopens the live log/progress stream; the job may still be running on the server.</span>
        </div>
      {/if}
    </div>
  {/if}

  <div class="flex gap-2">
    <button class="btn" on:click={doUpload} disabled={!file || isUploading || isAnalyzing}>
      {#if jobInfo}
        Re-analyze
      {:else}
        Analyze
      {/if}
    </button>
  <button class="btn" on:click={doCompress} disabled={!jobInfo || isCompressing}>
      {#if isCompressing}
        {#if hasProgress}
          Compressing… {progress}%{#if etaLabel} — ~{etaLabel} left{/if}
        {:else}
          Starting…
        {/if}
      {:else}
        Compress
      {/if}
    </button>
    {#if taskId && isCompressing}
      <button class="btn" on:click={onCancel} disabled={isCancelling}>{isCancelling ? 'Canceling…' : 'Cancel'}</button>
    {/if}
    <button class="btn" on:click={reset} disabled={!file && !taskId}>Reset</button>
  </div>

  <!-- Download Ready Card - Prominent when file is ready -->
  {#if taskId && doneStats}
    <div class="card done-card">
      <div class="done-header">
        <div class="done-info">
          <h3 class="done-title">✓ Compression Complete!</h3>
          {#if doneStats}
            <p class="text-sm text-gray-300">Final size: <span class="font-semibold text-white">{doneStats.final_size_mb} MB</span></p>
          {:else}
            <p class="text-sm text-gray-300">Your file is ready to download</p>
          {/if}
        </div>
        <a 
          class="btn done-download"
          href={downloadUrl(taskId)} 
          target="_blank"
        >
          ⬇️ Download
        </a>
      </div>
    </div>
  {/if}

  {#if taskId}
    <div class="card">
      <div class="flex items-center justify-between mb-2">
        {#if decodeMethod || encodeMethod}
          <div class="text-sm text-gray-300">
            Pipeline: {decodeMethod || 'auto'} → {encodeMethod || (videoCodec || 'auto')}
          </div>
        {/if}
        {#if encodeMethod}
          {#if /_nvenc$/.test(encodeMethod)}
            <span class="text-xs px-2 py-1 rounded bg-green-900/40 text-green-300 border border-green-700/40">Encoder: NVIDIA NVENC</span>
          {:else}
            <span class="text-xs px-2 py-1 rounded bg-slate-800 text-slate-200 border border-slate-600/40">Encoder: CPU ({encodeMethod})</span>
          {/if}
        {:else}
          <span class="text-xs px-2 py-1 rounded bg-slate-800 text-slate-300 border border-slate-600/40">Encoder: detecting…</span>
        {/if}
      </div>
      {#if encodeMethod && encodeMethod.startsWith('lib') && hardwareType !== 'cpu'}
        <div class="text-xs text-amber-300 mt-1">Hardware encoder unavailable for this job — using CPU fallback.</div>
      {/if}
      
      {#if isRetrying}
        <div class="mb-3 p-3 bg-amber-900/30 border-2 border-amber-500 rounded-lg animate-pulse">
          <div class="flex items-center gap-2">
            <span class="text-2xl">🔄</span>
            <div class="flex-1">
              <div class="font-bold text-amber-300 text-lg">AUTOMATIC RETRY IN PROGRESS</div>
              <div class="text-sm text-amber-200 mt-1">{retryMessage}</div>
              <div class="text-xs text-gray-400 mt-1">This is normal - optimizing output to meet target size</div>
            </div>
          </div>
        </div>
      {/if}
      
      <div class="progress-bar-bg" style="height:10px">
        <div class="progress-bar-fill" style={`width:${displayedProgress}%; height:10px`}></div>
      </div>
      <div class="progress-info">
        <span>
          {displayedProgress.toFixed(1)}%
          {#if isCompressing && isFinalizing && !doneStats} 
            <span class="text-blue-300">(finalizing…)</span>
          {:else if currentSpeedX && displayedProgress < 95}
            <span class="text-green-300">• {currentSpeedX.toFixed(2)}x</span>
          {/if}
        </span>
        {#if isCompressing && displayedProgress<99 && etaLabel}
          <span>~{etaLabel} remaining</span>
        {:else if isCompressing && isFinalizing && !doneStats}
          <span class="text-gray-400">Saving metadata...</span>
        {/if}
      </div>

      {#if !isReady && !doneStats && showTryDownload}
        <div class="mt-4 text-sm bg-amber-900/20 border border-amber-600/30 rounded p-3">
          <p class="text-amber-300">Finalizing… You can try downloading now.</p>
          <button class="btn inline-block mt-2" on:click={tryDownloadNow} disabled={tryDownloading}>
            {tryDownloading ? 'Trying…' : 'Try Download'}
          </button>
        </div>
      {/if}

      <details class="mt-3">
        <summary class="cursor-pointer">FFmpeg log</summary>
        <pre class="mt-2 text-xs whitespace-pre-wrap">{logLines.join('\n')}</pre>
      </details>
    </div>
  {/if}



</main>

<style>
  /* Layout */
  .page-container { max-width: 800px; margin: 40px auto; padding: 0 20px; display: flex; flex-direction: column; gap: 20px; }
  .page-header { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 16px; }
  .page-title { font-size: 28px; font-weight: 600; margin: 0; }
  .header-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .back-link { font-size: 14px; color: var(--text-secondary); text-decoration: none; margin-top: 4px; display: inline-block; transition: color var(--transition-fast); }
  .back-link:hover { color: var(--text-primary); }
  .version-badge { font-size: 12px; color: var(--text-muted); border: 1px solid var(--glass-border); border-radius: 4px; padding: 2px 8px; margin-left: 8px; vertical-align: middle; }

  /* Cards */
  .card { background-color: var(--bg-card); border: 1px solid var(--glass-border); border-radius: var(--border-radius); padding: 16px; }
  .card-title { font-size: 16px; font-weight: 600; margin: 0 0 8px; }

  /* Batch tracker */
  .batch-tracker { border-color: rgba(34, 197, 94, 0.25); }
  .tracker-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
  .tracker-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 10px; font-size: 12px; }
  .text-muted { font-size: 12px; color: var(--text-muted); }

  /* Drop zone */
  .drop-zone { border: 2px dashed var(--glass-border); border-radius: var(--border-radius); padding: 32px; text-align: center; cursor: pointer; transition: all var(--transition-fast); }
  .drop-zone:hover { border-color: var(--accent); background: rgba(99, 102, 241, 0.05); }

  /* Upload progress */
  .upload-progress { margin-top: 16px; }

  /* Progress bars */
  .progress-bar-bg { width: 100%; height: 6px; background: var(--bg-hover); border-radius: 4px; overflow: hidden; }
  .progress-bar-fill { height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-hover)); border-radius: 4px; transition: width 0.3s ease; }

  /* Progress info */
  .progress-info { margin-top: 8px; font-size: 12px; color: var(--text-secondary); display: flex; justify-content: space-between; align-items: center; }

  /* Done card */
  .done-card { background: linear-gradient(135deg, rgba(34, 197, 94, 0.1), rgba(99, 102, 241, 0.1)); border: 2px solid rgba(34, 197, 94, 0.4); }
  .done-header { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
  .done-info { flex: 1; }
  .done-title { font-size: 18px; font-weight: 700; color: var(--success); margin: 0 0 4px; }
  .done-download { background: #16a34a !important; padding: 12px 32px !important; font-size: 16px !important; font-weight: 700 !important; text-decoration: none; box-shadow: 0 4px 12px rgba(34, 197, 94, 0.3); }
  .done-download:hover { background: #15803d !important; }

  /* Form helpers */
  .form-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .form-grid-3 { display: grid; grid-template-columns: calc(50% - 20px) 1fr; gap: 16px; }
  .size-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }

  @media (max-width: 640px) {
    .tracker-stats { grid-template-columns: repeat(2, 1fr); }
    .form-grid-2, .form-grid-3 { grid-template-columns: 1fr; }
    .header-actions { width: 100%; justify-content: flex-start; }
  }
</style>
