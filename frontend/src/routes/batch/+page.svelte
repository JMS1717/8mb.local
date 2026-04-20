<script lang="ts">
  import '../../app.css';
  import { onDestroy, onMount } from 'svelte';
  import {
    uploadBatchWithProgress,
    getBatchStatus,
    downloadUrl,
    batchZipDownloadUrl,
    getAvailableCodecs,
    getSizeButtons,
    getPresetProfiles,
    openProgressStream,
  } from '$lib/api';
  import { FPS_CAP_VALUES, maxFpsFromProfile, parseStoredFpsCap, type FpsCap } from '$lib/fpsCap';

  type BatchItem = {
    index: number;
    job_id: string;
    task_id: string;
    original_filename: string;
    stored_filename: string;
    output_filename: string;
    state: 'queued' | 'running' | 'completed' | 'failed' | 'canceled';
    progress: number;
    error?: string | null;
    output_path?: string | null;
    download_url: string;
  };

  type BatchStatus = {
    batch_id: string;
    state: 'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed';
    item_count: number;
    queued_count: number;
    running_count: number;
    completed_count: number;
    failed_count: number;
    overall_progress: number;
    items: BatchItem[];
    zip_download_url?: string | null;
  };

  type CodecOption = {
    value: string;
    label: string;
    group: string;
  };

  type PresetProfile = {
    name: string;
    target_mb: number;
    video_codec: string;
    audio_codec: 'libopus' | 'aac' | 'none';
    preset: 'p1'|'p2'|'p3'|'p4'|'p5'|'p6'|'p7'|'extraquality';
    audio_kbps: number;
    container: 'mp4' | 'mkv';
    tune: 'hq' | 'll' | 'ull' | 'lossless';
    max_output_fps?: number | null;
  };

  const VIDEO_EXTENSIONS_LIST = [
    '.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v', '.wmv', '.flv',
    '.mpeg', '.mpg', '.ts', '.m2ts', '.3gp', '.3g2', '.mts', '.mxf', '.ogv', '.vob',
    '.f4v', '.asf', '.rm', '.rmvb'
  ];
  const VIDEO_EXTENSIONS = new Set(VIDEO_EXTENSIONS_LIST);
  const VIDEO_ACCEPT = `video/*,${VIDEO_EXTENSIONS_LIST.join(',')}`;
  const ACTIVE_BATCH_KEY = 'activeBatchId';

  let filesInput: HTMLInputElement | null = null;
  let selectedFiles: File[] = [];
  let selectedCount = 0;
  let selectedSizeBytes = 0;
  let selectionNotice = '';
  let isDragActive = false;

  let targetMB = 9.7;
  let targetMode: 'size' | 'bitrate' = 'size';
  let targetVideoKbps = 2500;
  let preset: 'p1'|'p2'|'p3'|'p4'|'p5'|'p6'|'p7'|'extraquality' = 'p6';
  let videoCodec = 'av1_nvenc';
  let audioCodec: 'libopus'|'aac'|'none' = 'libopus';
  let audioKbps = 128;
  let container: 'mp4'|'mkv' = 'mp4';
  let tune: 'hq'|'ll'|'ull'|'lossless' = 'hq';
  let preferHwDecode = true;
  let fastMp4Finalize = false;
  let autoResolution = false;
  let minAutoHeight: 240|360|480|720 = 240;
  let explicitHeight: 2160|1440|1080|720|480|360|240|null = null;
  let maxWidth: number | null = null;
  let maxHeight: number | null = null;
  let startTime = '';
  let endTime = '';
  let audioOnly = false;
  let maxFpsCap: FpsCap = '';

  let availableCodecs: CodecOption[] = [];
  $: nvidiaCodecs = availableCodecs.filter((c) => c.group === 'nvidia');
  $: cpuCodecs = availableCodecs.filter((c) => c.group === 'cpu');
  $: nvencTuneApplies = videoCodec.endsWith('_nvenc');
  let presetProfiles: PresetProfile[] = [];
  let selectedPreset: string | null = null;
  let sizeButtons: number[] = [4, 5, 8, 9.7, 25, 50, 100];

  let isUploading = false;
  let uploadProgress = 0;
  let uploadLoadedBytes = 0;
  let uploadTotalBytes = 0;
  let uploadCurrentFileIndex = 0;
  let uploadCurrentFileProgress = 0;
  let errorText = '';

  let batchId: string | null = null;
  let batchStatus: BatchStatus | null = null;
  let pollTimer: any = null;

  let liveTaskId: string | null = null;
  let liveTaskFile: string | null = null;
  let liveTaskProgress = 0;
  let liveTaskPhase: string | null = null;
  let liveTaskSpeedX: number | null = null;
  let liveTaskEtaLabel: string | null = null;
  let liveTaskDecodeMethod: string | null = null;
  let liveTaskEncodeMethod: string | null = null;
  let liveTaskLogs: string[] = [];
  let liveTaskStream: EventSource | null = null;

  function isVideoFile(file: File): boolean {
    if ((file.type || '').toLowerCase().startsWith('video/')) return true;
    const dot = file.name.lastIndexOf('.');
    const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : '';
    return VIDEO_EXTENSIONS.has(ext);
  }

  function formatSize(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = bytes;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) {
      value /= 1024;
      i += 1;
    }
    return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
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

  function parseExplicitHeight(value: string): 2160|1440|1080|720|480|360|240|null {
    const parsed = Number(value);
    if (parsed === 2160 || parsed === 1440 || parsed === 1080 || parsed === 720 || parsed === 480 || parsed === 360 || parsed === 240) {
      return parsed;
    }
    return null;
  }

  function stateLabel(state: string): string {
    if (state === 'queued') return 'Queued';
    if (state === 'running') return 'Running';
    if (state === 'completed') return 'Completed';
    if (state === 'completed_with_errors') return 'Completed with errors';
    if (state === 'canceled') return 'Canceled';
    if (state === 'failed') return 'Failed';
    return state;
  }

  function getCodecIcon(group: string): string {
    if (group === 'nvidia') return '🟢';
    return '⚪';
  }

  function buildCodecList(codecData: any): CodecOption[] {
    const list: CodecOption[] = [];
    const enabledCodecs: string[] = codecData?.enabled_codecs || [];

    const codecDefinitions: CodecOption[] = [
      { value: 'av1_nvenc', label: 'AV1 (NVIDIA)', group: 'nvidia' },
      { value: 'hevc_nvenc', label: 'HEVC (H.265, NVIDIA)', group: 'nvidia' },
      { value: 'h264_nvenc', label: 'H.264 (NVIDIA)', group: 'nvidia' },
      { value: 'libaom-av1', label: 'AV1 (CPU)', group: 'cpu' },
      { value: 'libsvtav1', label: 'AV1 (SVT-AV1, CPU)', group: 'cpu' },
      { value: 'libx265', label: 'HEVC (H.265, CPU)', group: 'cpu' },
      { value: 'libx264', label: 'H.264 (CPU)', group: 'cpu' },
    ];

    for (const codec of codecDefinitions) {
      if (enabledCodecs.includes(codec.value)) {
        list.push(codec);
      }
    }

    return list;
  }

  function setPresetMB(mb: number) {
    targetMB = Number(mb);
  }

  function applyPreset(name: string) {
    const p = presetProfiles.find((x) => x.name === name);
    if (!p) return;
    selectedPreset = name;
    targetMB = Number(p.target_mb);
    videoCodec = p.video_codec;
    audioCodec = p.audio_codec;
    preset = p.preset;
    audioKbps = Number(p.audio_kbps);
    container = p.container;
    tune = p.tune;
    maxFpsCap = maxFpsFromProfile(p.max_output_fps);
  }

  function pushLiveLog(message: string) {
    const text = String(message || '').trim();
    if (!text) return;
    liveTaskLogs = [text, ...liveTaskLogs].slice(0, 200);
  }

  function closeLiveTaskStream() {
    if (liveTaskStream) {
      try { liveTaskStream.close(); } catch {}
      liveTaskStream = null;
    }
  }

  function attachLiveTaskStream(taskId: string, filename: string) {
    if (!taskId) return;
    if (liveTaskId === taskId && liveTaskStream) return;

    closeLiveTaskStream();

    liveTaskId = taskId;
    liveTaskFile = filename;
    liveTaskPhase = 'running';
    liveTaskProgress = 0;
    liveTaskSpeedX = null;
    liveTaskEtaLabel = null;
    liveTaskDecodeMethod = null;
    liveTaskEncodeMethod = null;
    liveTaskLogs = [];

    const es = openProgressStream(taskId);
    liveTaskStream = es;

    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);

        if (data.type === 'connected' || data.type === 'ping') {
          return;
        }

        if (data.type === 'progress') {
          if (typeof data.progress === 'number') {
            liveTaskProgress = Math.max(0, Math.min(100, Number(data.progress)));
          }
          if (data.phase) {
            liveTaskPhase = String(data.phase);
          }
          if (typeof data.speed_x === 'number' && isFinite(data.speed_x) && data.speed_x > 0) {
            liveTaskSpeedX = Number(data.speed_x);
          }
          if (typeof data.eta_seconds === 'number' && isFinite(data.eta_seconds) && data.eta_seconds > 0) {
            liveTaskEtaLabel = formatEta(Number(data.eta_seconds));
          } else {
            liveTaskEtaLabel = null;
          }
        }

        if (data.type === 'log' && data.message) {
          const msg = String(data.message);
          pushLiveLog(msg);

          const speedMatch = msg.match(/speed=([\d.]+)x/);
          if (speedMatch) {
            liveTaskSpeedX = Number(speedMatch[1]);
          }

          const decodeMatch = msg.match(/Decoder:\s*(?:using|forcing)\s*([\w_]+)/i) || msg.match(/Decoder:\s*([\w_]+)/i);
          if (decodeMatch) {
            liveTaskDecodeMethod = decodeMatch[1];
          }

          const encodeMatch = msg.match(/Using\s+encoder:\s*([\w_-]+)/i) || msg.match(/Encoder:\s*CPU\s*\(([^)]+)\)/i) || msg.match(/Encoder:\s*([\w_-]+)/i);
          if (encodeMatch) {
            liveTaskEncodeMethod = encodeMatch[1];
          }
        }

        if (data.type === 'retry' && data.message) {
          pushLiveLog(`RETRY: ${String(data.message)}`);
        }

        if (data.type === 'done') {
          liveTaskProgress = 100;
          liveTaskPhase = 'done';
          pushLiveLog('Compression complete');
          closeLiveTaskStream();
        }

        if (data.type === 'error') {
          liveTaskPhase = 'failed';
          pushLiveLog(`ERROR: ${String(data.message || 'Unknown error')}`);
          closeLiveTaskStream();
        }

        if (data.type === 'canceled') {
          liveTaskPhase = 'canceled';
          pushLiveLog('Task canceled');
          closeLiveTaskStream();
        }
      } catch {
        // Ignore malformed SSE frames.
      }
    };

    es.onerror = () => {
      pushLiveLog('Progress stream reconnecting...');
    };
  }

  function applySelection(rawFiles: File[]) {
    const videos: File[] = [];
    let rejected = 0;

    for (const f of rawFiles) {
      if (isVideoFile(f)) videos.push(f);
      else rejected += 1;
    }

    selectedFiles = videos;
    selectedCount = videos.length;
    selectedSizeBytes = videos.reduce((sum, f) => sum + f.size, 0);
    uploadProgress = 0;
    uploadLoadedBytes = 0;
    uploadTotalBytes = 0;
    uploadCurrentFileIndex = 0;
    uploadCurrentFileProgress = 0;

    batchId = null;
    batchStatus = null;
    closeLiveTaskStream();
    liveTaskId = null;
    liveTaskFile = null;
    liveTaskLogs = [];

    if (videos.length === 0) {
      selectionNotice = '';
      errorText = rawFiles.length > 0
        ? 'No supported video files were detected. MP4 and common video formats are supported.'
        : 'No files selected.';
      return;
    }

    errorText = '';
    selectionNotice = `Selected ${videos.length} video file(s)${rejected ? `, skipped ${rejected} non-video file(s)` : ''}.`;
  }

  function updateUploadFileProgress(loadedBytes: number) {
    uploadLoadedBytes = Math.max(0, loadedBytes);
    if (!selectedFiles.length) {
      uploadCurrentFileIndex = 0;
      uploadCurrentFileProgress = 0;
      return;
    }

    let acc = 0;
    for (let i = 0; i < selectedFiles.length; i += 1) {
      const fileSize = Math.max(1, selectedFiles[i].size || 1);
      const next = acc + fileSize;
      if (loadedBytes <= next || i === selectedFiles.length - 1) {
        uploadCurrentFileIndex = i + 1;
        const loadedWithin = Math.max(0, Math.min(fileSize, loadedBytes - acc));
        uploadCurrentFileProgress = Math.max(0, Math.min(100, (loadedWithin / fileSize) * 100));
        return;
      }
      acc = next;
    }
  }

  function onFilesPicked(e: Event) {
    const input = e.target as HTMLInputElement;
    const rawFiles = Array.from(input.files || []);
    applySelection(rawFiles);
  }

  function onDropFiles(e: DragEvent) {
    e.preventDefault();
    isDragActive = false;
    const rawFiles = Array.from(e.dataTransfer?.files || []);
    applySelection(rawFiles);
  }

  function onDragOverFiles(e: DragEvent) {
    e.preventDefault();
    isDragActive = true;
  }

  function onDragLeaveFiles(e: DragEvent) {
    e.preventDefault();
    isDragActive = false;
  }

  function openFilesPicker() {
    try { filesInput?.click(); } catch {}
  }

  function onDropZoneKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      openFilesPicker();
    }
  }

  function clearSelectedFiles() {
    selectedFiles = [];
    selectedCount = 0;
    selectedSizeBytes = 0;
    selectionNotice = '';
    uploadProgress = 0;
    uploadLoadedBytes = 0;
    uploadTotalBytes = 0;
    uploadCurrentFileIndex = 0;
    uploadCurrentFileProgress = 0;
    errorText = '';
    try {
      if (filesInput) filesInput.value = '';
    } catch {}
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function shouldStopPolling(state?: string): boolean {
    return state === 'completed' || state === 'completed_with_errors' || state === 'failed';
  }

  function syncLiveTaskFromBatch(status: BatchStatus) {
    const runningItem = status.items.find((item) => item.state === 'running');
    if (!runningItem) {
      if (shouldStopPolling(status.state)) {
        closeLiveTaskStream();
      }
      return;
    }

    if (liveTaskId !== runningItem.task_id || !liveTaskStream) {
      attachLiveTaskStream(runningItem.task_id, runningItem.original_filename);
    }

    liveTaskFile = runningItem.original_filename;
    liveTaskPhase = runningItem.state;
    liveTaskProgress = Math.max(0, Math.min(100, Number(runningItem.progress || 0)));
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(async () => {
      try {
        await refreshBatchStatus();
      } catch {
        // Ignore transient polling errors.
      }
    }, 2000);
  }

  async function refreshBatchStatus() {
    if (!batchId) return;
    const status = await getBatchStatus(batchId);
    batchStatus = status;
    syncLiveTaskFromBatch(status);

    if (shouldStopPolling(status?.state)) {
      stopPolling();
      closeLiveTaskStream();
      return;
    }

    if (!pollTimer) {
      startPolling();
    }
  }

  async function startBatchCompression() {
    if (isUploading || selectedFiles.length === 0) return;

    errorText = '';
    selectionNotice = '';
    isUploading = true;
    uploadProgress = 0;
    uploadLoadedBytes = 0;
    uploadTotalBytes = 0;
    uploadCurrentFileIndex = 0;
    uploadCurrentFileProgress = 0;
    batchId = null;
    batchStatus = null;

    closeLiveTaskStream();
    liveTaskId = null;
    liveTaskFile = null;
    liveTaskProgress = 0;
    liveTaskPhase = null;
    liveTaskSpeedX = null;
    liveTaskEtaLabel = null;
    liveTaskDecodeMethod = null;
    liveTaskEncodeMethod = null;
    liveTaskLogs = [];

    try {
      const resolvedMaxWidth = audioOnly ? undefined : ((autoResolution || explicitHeight) ? undefined : (maxWidth || undefined));
      const resolvedMaxHeight = audioOnly ? undefined : ((autoResolution && !explicitHeight) ? undefined : (explicitHeight || maxHeight || undefined));
      const payload = {
        target_size_mb: targetMB,
        target_video_bitrate_kbps: targetMode === 'bitrate' ? targetVideoKbps : undefined,
        video_codec: videoCodec,
        audio_codec: audioCodec,
        audio_bitrate_kbps: audioKbps,
        preset,
        container,
        tune,
        max_width: resolvedMaxWidth,
        max_height: resolvedMaxHeight,
        start_time: startTime.trim() || undefined,
        end_time: endTime.trim() || undefined,
        force_hw_decode: preferHwDecode,
        fast_mp4_finalize: fastMp4Finalize,
        auto_resolution: autoResolution,
        min_auto_resolution: minAutoHeight,
        target_resolution: explicitHeight || undefined,
        audio_only: audioOnly,
        max_output_fps: maxFpsCap === '' ? undefined : Number(maxFpsCap),
      };

      const result = await uploadBatchWithProgress(selectedFiles, payload, {
        onProgress: (pct: number, loadedBytes: number, totalBytes: number) => {
          uploadProgress = pct;
          uploadTotalBytes = Math.max(0, totalBytes || 0);
          updateUploadFileProgress(loadedBytes || 0);
        }
      });

      batchId = result.batch_id;
      try { localStorage.setItem(ACTIVE_BATCH_KEY, result.batch_id); } catch {}
      await refreshBatchStatus();
      startPolling();
    } catch (e: any) {
      errorText = e?.message || 'Batch upload failed';
    } finally {
      isUploading = false;
    }
  }

  function downloadBatchZip() {
    if (!batchId) return;
    window.location.href = batchZipDownloadUrl(batchId);
  }

  onMount(async () => {
    try {
      const res = await fetch('/api/settings/presets');
      if (res.ok) {
        const presets = await res.json();
        targetMB = Number(presets.target_mb || targetMB);
        videoCodec = String(presets.video_codec || videoCodec);
        audioCodec = String(presets.audio_codec || audioCodec) as 'libopus' | 'aac' | 'none';
        audioKbps = Number(presets.audio_kbps || audioKbps);
        preset = String(presets.preset || preset) as 'p1'|'p2'|'p3'|'p4'|'p5'|'p6'|'p7'|'extraquality';
        container = String(presets.container || container) as 'mp4' | 'mkv';
        tune = String(presets.tune || tune) as 'hq' | 'll' | 'ull' | 'lossless';
      }
    } catch {
      // Keep defaults if preset fetch fails.
    }

    try {
      const codecData = await getAvailableCodecs();
      const codecList = buildCodecList(codecData);
      if (codecList.length > 0) {
        availableCodecs = codecList;
      }
    } catch {
      availableCodecs = [
        { value: 'av1_nvenc', label: 'AV1 (NVIDIA)', group: 'nvidia' },
        { value: 'hevc_nvenc', label: 'HEVC (H.265, NVIDIA)', group: 'nvidia' },
        { value: 'h264_nvenc', label: 'H.264 (NVIDIA)', group: 'nvidia' },
        { value: 'libaom-av1', label: 'AV1 (CPU)', group: 'cpu' },
        { value: 'libsvtav1', label: 'AV1 (SVT-AV1, CPU)', group: 'cpu' },
        { value: 'libx265', label: 'HEVC (H.265, CPU)', group: 'cpu' },
        { value: 'libx264', label: 'H.264 (CPU)', group: 'cpu' },
      ];
    }

    if (availableCodecs.length > 0 && !availableCodecs.some((c) => c.value === videoCodec)) {
      videoCodec = availableCodecs[0].value;
    }

    try {
      const sb = await getSizeButtons();
      if (sb?.buttons?.length) {
        sizeButtons = sb.buttons;
      }
    } catch {
      // Keep defaults if size button fetch fails.
    }

    try {
      const pp = await getPresetProfiles();
      presetProfiles = pp?.profiles || [];
      selectedPreset = pp?.default || (presetProfiles[0]?.name ?? null);
      if (selectedPreset) {
        applyPreset(selectedPreset);
      }
    } catch {
      // Keep defaults if preset profile fetch fails.
    }

    try {
      const tracked = localStorage.getItem(ACTIVE_BATCH_KEY);
      if (tracked) {
        batchId = tracked;
        await refreshBatchStatus();
      }
    } catch {
      // Ignore localStorage access errors.
    }
    try {
      const parsedFps = parseStoredFpsCap(localStorage.getItem('maxOutputFpsCap'));
      if (parsedFps !== null) maxFpsCap = parsedFps;
    } catch {
      // Ignore
    }
  });

  onDestroy(() => {
    stopPolling();
    closeLiveTaskStream();
  });

  $: {
    try {
      localStorage.setItem('maxOutputFpsCap', maxFpsCap);
    } catch {
      // ignore
    }
  }
</script>

<div class="max-w-5xl mx-auto mt-8 space-y-6">
  <div class="flex items-center justify-between">
    <div>
      <h1 class="text-2xl font-bold">Batch Upload</h1>
      <p class="text-sm text-gray-400">Select multiple videos, choose a preset, and process everything in sequence.</p>
    </div>
    <div class="flex gap-2">
      <a href="/" class="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors text-sm">← Home</a>
      <a href="/queue" class="px-4 py-2 bg-blue-700 hover:bg-blue-600 text-white rounded-lg transition-colors text-sm">Queue</a>
    </div>
  </div>

  <div class="card space-y-4">
    <h2 class="font-semibold">1) Select Files</h2>
    <div
      class={`border-2 border-dashed rounded p-8 text-center transition-colors ${isDragActive ? 'border-emerald-400 bg-emerald-900/20' : 'border-gray-700'}`}
      role="button"
      tabindex="0"
      on:drop={onDropFiles}
      on:dragover={onDragOverFiles}
      on:dragleave={onDragLeaveFiles}
      on:click={openFilesPicker}
      on:keydown={onDropZoneKeydown}
    >
      <p class="mb-2">Drag & drop videos here</p>
      <p class="text-xs text-gray-400">or click to choose multiple files</p>
      <input
        id="batch-files-input"
        bind:this={filesInput}
        class="hidden"
        type="file"
        multiple
        accept={VIDEO_ACCEPT}
        on:change={onFilesPicked}
      />
      <div class="mt-3">
        <button class="btn" type="button" on:click|stopPropagation={openFilesPicker}>Choose Files</button>
      </div>

      {#if selectedCount > 0}
        <div class="mt-4 flex items-center justify-center gap-2 flex-wrap">
          <p class="text-sm text-gray-300">{selectedCount} file(s) selected • {formatSize(selectedSizeBytes)}</p>
          <button class="btn" type="button" on:click|stopPropagation={clearSelectedFiles}>Clear</button>
        </div>
      {/if}
    </div>

    {#if selectionNotice}
      <p class="text-xs text-emerald-300">{selectionNotice}</p>
    {/if}
    {#if errorText}
      <p class="text-sm text-red-400">{errorText}</p>
    {/if}
  </div>

  <div class="card space-y-4">
    <h2 class="font-semibold">2) Compression Settings</h2>

    <div class="flex flex-wrap gap-3 text-sm items-center mb-2">
      <label class="flex items-center gap-2 cursor-pointer">
        <input type="radio" name="batchTargetMode" value="size" bind:group={targetMode} disabled={audioOnly} />
        Target file size
      </label>
      <label class="flex items-center gap-2 cursor-pointer">
        <input type="radio" name="batchTargetMode" value="bitrate" bind:group={targetMode} disabled={audioOnly} />
        Target video bitrate
      </label>
    </div>

    {#if targetMode === 'size'}
      <div class="space-x-2 flex flex-wrap gap-2">
        {#each sizeButtons as b}
          <button class="btn" type="button" on:click={() => setPresetMB(Number(b))}>{b}MB</button>
        {/each}
      </div>
    {/if}

    {#if presetProfiles.length > 0}
      <label class="block text-sm">
        <span class="block mb-1">Preset profile</span>
        <select class="input w-full" bind:value={selectedPreset} on:change={(e:any) => applyPreset(e.target.value)}>
          {#each presetProfiles as p}
            <option value={p.name}>{p.name}</option>
          {/each}
        </select>
      </label>
    {/if}

    <div class="grid md:grid-cols-2 gap-3">
      <div class="space-y-2">
        {#if targetMode === 'size'}
          <label class="block text-sm">
            <span class="block mb-1">Target size (MB)</span>
            <input class="input w-full" type="number" min="1" step="0.1" bind:value={targetMB} disabled={audioOnly} />
          </label>
        {:else}
          <label class="block text-sm">
            <span class="block mb-1">Video bitrate (kbps)</span>
            <input class="input w-full" type="number" min="50" max="200000" step="50" bind:value={targetVideoKbps} disabled={audioOnly} />
          </label>
          <label class="block text-sm">
            <span class="block mb-1">Queue label (MB)</span>
            <input class="input w-full" type="number" min="1" step="0.1" bind:value={targetMB} disabled={audioOnly} />
            <span class="block mt-1 text-xs text-gray-500">Shown in the job queue only; encoding uses the bitrate above.</span>
          </label>
        {/if}
      </div>
      <label class="block text-sm">
        <span class="block mb-1">Encoder preset (speed ↔ quality)</span>
        <select class="input w-full" bind:value={preset} title="Encoder effort at your target size or bitrate; independent of NVENC tuning.">
          <option value="p1">Fastest (P1)</option>
          <option value="p2">Faster (P2)</option>
          <option value="p3">Fast (P3)</option>
          <option value="p4">Balanced (P4)</option>
          <option value="p5">Better (P5)</option>
          <option value="p6">High Quality (P6)</option>
          <option value="p7">Best Quality (P7)</option>
          <option value="extraquality">Extra Quality (constant quality — CQ)</option>
        </select>
        {#if preset === 'extraquality' && targetMode === 'bitrate'}
          <span class="block mt-1 text-xs text-amber-200/95 rounded border border-amber-700/40 bg-amber-950/35 px-2 py-1.5">
            Extra Quality needs CQ mode; with <strong>fixed video bitrate</strong> the job uses <strong>P6</strong> instead.
          </span>
        {:else if preset === 'extraquality'}
          <span class="block mt-1 text-xs text-sky-100/90 rounded border border-sky-800/50 bg-sky-950/30 px-2 py-1.5">
            <strong>Extra Quality</strong> = <strong>constant quality</strong> (CRF/CQ): quality is fixed, <strong>output size is not guaranteed</strong> vs your target MB. Slower than P7.
          </span>
        {:else}
          <span class="block mt-1 text-xs text-gray-500">P1 = fastest; P7 = slower ABR toward your target. Extra Quality is different (CQ). Not the same as NVENC tuning.</span>
        {/if}
      </label>
    </div>

    <div class="grid md:grid-cols-2 gap-3">
      <label class="block text-sm">
        <span class="block mb-1">Video codec</span>
        <select class="input w-full" bind:value={videoCodec}>
          {#if availableCodecs.length === 0}
            <option value={videoCodec}>{videoCodec}</option>
          {:else}
            <optgroup label="NVIDIA NVENC">
              {#each nvidiaCodecs as codec}
                <option value={codec.value}>{getCodecIcon(codec.group)} {codec.label}</option>
              {/each}
            </optgroup>
            <optgroup label="CPU / Software">
              {#each cpuCodecs as codec}
                <option value={codec.value}>{getCodecIcon(codec.group)} {codec.label}</option>
              {/each}
            </optgroup>
          {/if}
        </select>
      </label>
      <div class="block text-sm">
        <span class="block mb-1">NVENC tuning <span class="text-xs text-gray-500">(NVIDIA only)</span></span>
        {#if nvencTuneApplies}
          <select class="input w-full" bind:value={tune} title="NVENC: quality vs latency; separate from P-preset.">
            <option value="hq">High quality (files)</option>
            <option value="ll">Low latency</option>
            <option value="ull">Ultra-low latency</option>
            <option value="lossless">Lossless</option>
          </select>
          <span class="block mt-1 text-xs text-gray-500">For typical batches, use High quality. LL/ULL favor speed of encoding over efficiency.</span>
        {:else}
          <p class="text-xs text-gray-500 rounded border border-gray-700 bg-gray-900/40 px-2 py-2">Not used for CPU codecs—encoder preset above applies.</p>
        {/if}
      </div>
    </div>

    <label class="flex items-center gap-2 text-sm cursor-pointer">
      <input type="checkbox" bind:checked={audioOnly} />
      <span>Extract audio only (.m4a)</span>
    </label>

    {#if container === 'mp4' && audioCodec === 'libopus' && !audioOnly}
      <p class="text-xs text-amber-300">MP4 does not support Opus; audio will be encoded as AAC automatically.</p>
    {/if}

    <details>
      <summary class="cursor-pointer text-sm">Advanced options</summary>
      <div class="mt-4 space-y-3">
        <div class="grid md:grid-cols-3 gap-3">
          <label class="block text-sm">
            <span class="block mb-1">Audio codec</span>
            <select class="input w-full" bind:value={audioCodec} disabled={audioOnly}>
              <option value="libopus">Opus</option>
              <option value="aac">AAC</option>
              <option value="none">None</option>
            </select>
          </label>
          <label class="block text-sm">
            <span class="block mb-1">Audio kbps</span>
            <input class="input w-full" type="number" min="32" step="1" bind:value={audioKbps} disabled={audioOnly || audioCodec === 'none'} />
          </label>
          <label class="block text-sm">
            <span class="block mb-1">Container</span>
            <select class="input w-full" bind:value={container} disabled={audioOnly}>
              <option value="mp4">MP4</option>
              <option value="mkv">MKV</option>
            </select>
          </label>
        </div>

        <div class="grid md:grid-cols-2 gap-3">
          <label class="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" bind:checked={preferHwDecode} />
            <span>Prefer hardware decoding</span>
          </label>
          <label class="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" bind:checked={fastMp4Finalize} />
            <span>Fast MP4 finalize</span>
          </label>
        </div>

        <div class="grid md:grid-cols-3 gap-3 items-end">
          <label class="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" bind:checked={autoResolution} disabled={audioOnly} />
            <span>Auto resolution</span>
          </label>

          <label class="block text-sm">
            <span class="block mb-1">Minimum auto resolution</span>
            <select class="input w-full" bind:value={minAutoHeight} disabled={audioOnly || !autoResolution}>
              <option value={240}>240p</option>
              <option value={360}>360p</option>
              <option value={480}>480p</option>
              <option value={720}>720p</option>
            </select>
          </label>

          <label class="block text-sm">
            <span class="block mb-1">Explicit resolution</span>
            <select class="input w-full" disabled={audioOnly || autoResolution} on:change={(e:any) => { explicitHeight = parseExplicitHeight(e.target.value); }}>
              <option value="">Original</option>
              <option value="2160">2160p (4K)</option>
              <option value="1440">1440p</option>
              <option value="1080">1080p</option>
              <option value="720">720p</option>
              <option value="480">480p</option>
              <option value="360">360p</option>
              <option value="240">240p</option>
            </select>
          </label>
        </div>

        <div class="grid md:grid-cols-4 gap-3">
          <label class="block text-sm">
            <span class="block mb-1">Max Width (px)</span>
            <input class="input w-full" type="number" bind:value={maxWidth} placeholder="Original" min="1" disabled={audioOnly || autoResolution || !!explicitHeight} />
          </label>
          <label class="block text-sm">
            <span class="block mb-1">Max Height (px)</span>
            <input class="input w-full" type="number" bind:value={maxHeight} placeholder="Original" min="1" disabled={audioOnly || autoResolution || !!explicitHeight} />
          </label>
          <label class="block text-sm">
            <span class="block mb-1">Max frame rate</span>
            <select class="input w-full" bind:value={maxFpsCap} disabled={audioOnly}>
              <option value="">Same as input (default)</option>
              {#each FPS_CAP_VALUES as v}
                <option value={v}>{v} fps (cap)</option>
              {/each}
            </select>
          </label>
          <label class="block text-sm">
            <span class="block mb-1">Start Time</span>
            <input class="input w-full" type="text" bind:value={startTime} placeholder="0 or 00:00:00" disabled={audioOnly} />
          </label>
          <label class="block text-sm">
            <span class="block mb-1">End Time</span>
            <input class="input w-full" type="text" bind:value={endTime} placeholder="Full duration" disabled={audioOnly} />
          </label>
        </div>
      </div>
    </details>

    <div class="flex items-center gap-3 flex-wrap">
      <button
        class="btn"
        disabled={selectedCount === 0 || isUploading}
        on:click={startBatchCompression}
      >
        {isUploading ? 'Uploading files...' : 'Start Batch Compression'}
      </button>
      {#if isUploading}
        <div class="text-sm text-gray-300">
          Upload progress: {uploadProgress}%
          {#if uploadCurrentFileIndex > 0}
            <span class="text-xs text-gray-400 ml-2">
              File {uploadCurrentFileIndex}/{selectedCount} ({uploadCurrentFileProgress.toFixed(1)}%)
            </span>
          {/if}
        </div>
      {/if}
    </div>

    {#if isUploading}
      <div class="w-full h-2 bg-gray-800 rounded overflow-hidden">
        <div class="h-2 bg-blue-600" style={`width:${uploadProgress}%`}></div>
      </div>
      {#if uploadTotalBytes > 0}
        <p class="text-xs text-gray-500">
          Uploaded {formatSize(uploadLoadedBytes)} / {formatSize(uploadTotalBytes)}
        </p>
      {/if}
    {/if}
  </div>

  {#if liveTaskId || liveTaskLogs.length > 0}
    <div class="card space-y-4">
      <div class="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h2 class="font-semibold">3) Live Job Progress</h2>
          {#if liveTaskFile}
            <p class="text-sm text-gray-400">{liveTaskFile}</p>
          {/if}
        </div>
        {#if liveTaskId}
          <p class="text-xs text-gray-400">Task: {liveTaskId}</p>
        {/if}
      </div>

      <div class="flex items-center justify-between text-sm">
        <span>{stateLabel(liveTaskPhase || 'running')}</span>
        <span>{liveTaskProgress.toFixed(1)}%</span>
      </div>
      <div class="w-full h-2 bg-gray-800 rounded overflow-hidden">
        <div class="h-2 bg-indigo-500" style={`width:${Math.max(0, Math.min(100, liveTaskProgress))}%`}></div>
      </div>

      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
        <div class="bg-gray-950 border border-gray-800 rounded px-2 py-1">Phase: {liveTaskPhase || 'encoding'}</div>
        <div class="bg-gray-950 border border-gray-800 rounded px-2 py-1">Speed: {liveTaskSpeedX ? `${liveTaskSpeedX.toFixed(2)}x` : 'n/a'}</div>
        <div class="bg-gray-950 border border-gray-800 rounded px-2 py-1">ETA: {liveTaskEtaLabel || 'n/a'}</div>
        <div class="bg-gray-950 border border-gray-800 rounded px-2 py-1">Decoder/Encoder: {liveTaskDecodeMethod || '?'} / {liveTaskEncodeMethod || '?'}</div>
      </div>

      <div class="bg-gray-950 border border-gray-800 rounded p-3 max-h-56 overflow-auto">
        {#if liveTaskLogs.length === 0}
          <p class="text-xs text-gray-500">Waiting for detailed logs...</p>
        {:else}
          <ul class="space-y-1 text-xs font-mono">
            {#each liveTaskLogs as line}
              <li>{line}</li>
            {/each}
          </ul>
        {/if}
      </div>
    </div>
  {/if}

  {#if batchStatus}
    <div class="card space-y-4">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="font-semibold">4) Batch Progress</h2>
          <p class="text-sm text-gray-400">Batch ID: {batchStatus.batch_id}</p>
        </div>
        <div class="text-right">
          <div class="text-sm">State: <span class="font-semibold">{stateLabel(batchStatus.state)}</span></div>
          <div class="text-xs text-gray-400">Overall: {batchStatus.overall_progress.toFixed(1)}%</div>
        </div>
      </div>

      <div class="w-full h-2 bg-gray-800 rounded overflow-hidden">
        <div class="h-2 bg-emerald-500" style={`width:${batchStatus.overall_progress}%`}></div>
      </div>

      <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        <div class="bg-gray-950 border border-gray-800 rounded p-3">Queued: {batchStatus.queued_count}</div>
        <div class="bg-gray-950 border border-gray-800 rounded p-3">Running: {batchStatus.running_count}</div>
        <div class="bg-gray-950 border border-gray-800 rounded p-3">Completed: {batchStatus.completed_count}</div>
        <div class="bg-gray-950 border border-gray-800 rounded p-3">Failed: {batchStatus.failed_count}</div>
      </div>

      <div class="flex gap-3">
        <button
          class="btn"
          disabled={batchStatus.completed_count === 0}
          on:click={downloadBatchZip}
        >
          Download ZIP
        </button>
        <button class="btn" on:click={refreshBatchStatus}>Refresh</button>
      </div>

      <div class="overflow-x-auto border border-gray-800 rounded">
        <table class="w-full text-sm">
          <thead class="bg-gray-950">
            <tr>
              <th class="text-left p-2">#</th>
              <th class="text-left p-2">File</th>
              <th class="text-left p-2">State</th>
              <th class="text-left p-2">Progress</th>
              <th class="text-left p-2">Download</th>
            </tr>
          </thead>
          <tbody>
            {#each batchStatus.items as item}
              <tr class="border-t border-gray-800">
                <td class="p-2">{item.index + 1}</td>
                <td class="p-2 max-w-lg truncate" title={item.original_filename}>{item.original_filename}</td>
                <td class="p-2">
                  <div>{stateLabel(item.state)}</div>
                  {#if item.error}
                    <div class="text-xs text-red-400 mt-1" title={item.error}>{item.error}</div>
                  {/if}
                </td>
                <td class="p-2 min-w-40">
                  <div class="text-xs text-gray-300 mb-1">{item.progress.toFixed(1)}%</div>
                  <div class="h-2 bg-gray-800 rounded overflow-hidden">
                    <div class="h-2 bg-blue-600" style={`width:${Math.max(0, Math.min(100, item.progress))}%`}></div>
                  </div>
                </td>
                <td class="p-2">
                  {#if item.state === 'completed'}
                    <a class="text-blue-400 underline" href={downloadUrl(item.task_id)}>Download</a>
                  {:else}
                    <span class="text-gray-500">Pending</span>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </div>
  {/if}
</div>
