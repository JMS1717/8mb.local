<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { goto } from '$app/navigation';
  import { uploadWithProgress, startCompress, openProgressStream, downloadUrl, getAvailableCodecs, cancelJob, getVersion, getDaemonStatus, getDefaultPresets } from '$lib/api';

  // State
  let file: File | null = null;
  let previewUrl: string | null = null;
  let isPlaying = true;
  let previewVideo: HTMLVideoElement | null = null;
  let aspectRatio = 56.25; // Default 16:9

  // Settings
  let targetSize = 10;
  let customSize = '';
  let isCustom = false;
  
  // Quality Quality
  type QualityPreset = 'p3' | 'p5' | 'p6' | 'p7';
  let quality: QualityPreset = 'p5';
  let perfectQualityOverride = false;
  
  // Codec (auto-assigned)
  let autoCodec = '';

  // App version
  let appVersion: string | null = null;

  // Daemon status
  let daemonStatus: { enabled: boolean; connected: boolean; codecs?: string[] } | null = null;

  // Processing state
  let status: 'idle' | 'analyzing' | 'compressing' | 'done' | 'error' = 'idle';
  let uploadProgress = 0;
  let compressProgress = 0;
  let errorMessage = '';
  let isDragging = false;
  let multiDropMessage = '';
  
  let jobInfo: any = null;
  let taskId: string | null = null;
  let esRef: EventSource | null = null;

  const sizePresets = [8, 10, 25, 50, 100];
  
  $: selectedSize = isCustom ? (parseFloat(customSize) || 8) : targetSize;
  $: canCompress = file && selectedSize > 0 && status === 'idle' && !(isCustom && !customSize.trim());
  $: finalPreset = perfectQualityOverride ? 'extraquality' : quality;

  function formatBytes(bytes: number): string {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  onMount(async () => {
    try {
      const v = await getVersion();
      appVersion = v?.version || null;
    } catch {}

    try {
      const codecData = await getAvailableCodecs();
      let defaultCodecOverride = '';
      try {
        const defaults = await getDefaultPresets();
        if (defaults && defaults.target_mb) targetSize = defaults.target_mb;
        if (defaults && defaults.preset) quality = defaults.preset;
        if (defaults && defaults.video_codec) defaultCodecOverride = defaults.video_codec;
      } catch (e) {
        console.warn("Failed to load default presets", e);
      }
      
      if (codecData?.enabled_codecs?.length > 0) {
        if (defaultCodecOverride && codecData.enabled_codecs.includes(defaultCodecOverride)) {
          autoCodec = defaultCodecOverride;
        } else {
          autoCodec = codecData.enabled_codecs[0];
        }
      }
    } catch (e) {
      console.warn("Failed to load codecs");
      autoCodec = 'libsvtav1';
    }

    // Check daemon status (best-effort, non-blocking)
    try {
      daemonStatus = await getDaemonStatus();
    } catch {}
  });

  onDestroy(() => {
    try { esRef?.close(); } catch {}
  });

  // Drag & drop handlers
  function handleDragOver(e: DragEvent) {
    e.preventDefault();
    if (status !== 'idle' && status !== 'error' && status !== 'done') return;
    isDragging = true;
  }
  function handleDragLeave() { isDragging = false; }
  function handleDrop(e: DragEvent) {
    e.preventDefault();
    isDragging = false;
    multiDropMessage = '';
    const files = e.dataTransfer?.files;
    if (!files?.length) return;
    if (files.length > 5) {
      multiDropMessage = 'Maximum 5 files. Please try again with fewer files.';
      return;
    }
    if (files.length > 1) {
      // Multi-file: redirect to batch
      redirectToBatch(Array.from(files));
      return;
    }
    setFile(files[0]);
  }
  function handleFileSelect(e: Event) {
    const input = e.target as HTMLInputElement;
    if (!input.files?.length) return;
    multiDropMessage = '';
    if (input.files.length > 5) {
      multiDropMessage = 'Maximum 5 files. Please try again with fewer files.';
      input.value = '';
      return;
    }
    if (input.files.length > 1) {
      redirectToBatch(Array.from(input.files));
      input.value = '';
      return;
    }
    setFile(input.files[0]);
    input.value = '';
  }
  function redirectToBatch(files: File[]) {
    // Store file names for the batch page to show a notice
    try {
      const names = files.map(f => f.name);
      sessionStorage.setItem('batchFromHome', JSON.stringify(names));
      // We can't transfer File objects via storage, so store in a global
      (window as any).__batchFilesFromHome = files;
    } catch {}
    goto('/batch');
  }
  function setFile(f: File) {
    clearFileCore(); // keep settings, clear state
    file = f;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(f);
    isPlaying = true;
    aspectRatio = 56.25;
  }
  function togglePreview(e: Event) {
    e.stopPropagation();
    if (previewVideo) {
      if (previewVideo.paused) { previewVideo.play(); isPlaying = true; }
      else { previewVideo.pause(); isPlaying = false; }
    }
  }
  function clearFileCore() {
    status = 'idle';
    uploadProgress = 0;
    compressProgress = 0;
    errorMessage = '';
    jobInfo = null;
    if (taskId) { cancelJob(taskId).catch(()=>{}); taskId = null; }
    try { esRef?.close(); esRef = null; } catch {}
  }
  function clearFile() {
    clearFileCore();
    file = null;
    if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = null; }
  }

  // Action flow
  async function doCompress() {
    if (!canCompress) return;
    try {
      // 1. Analyze / Upload
      status = 'analyzing';
      uploadProgress = 0;
      errorMessage = '';
      
      jobInfo = await uploadWithProgress(file as File, selectedSize, 128, {
        onProgress: (p) => { uploadProgress = p; }
      });

      if (!jobInfo || !jobInfo.job_id) {
        throw new Error("Upload failed to return job ID");
      }

      // 2. Compress
      status = 'compressing';
      compressProgress = 0;
      
      const payload = {
        job_id: jobInfo.job_id,
        filename: jobInfo.filename,
        target_size_mb: selectedSize,
        video_codec: autoCodec || 'libsvtav1',
        audio_codec: 'libopus',
        audio_bitrate_kbps: 128,
        preset: finalPreset,
        container: 'mp4',
        tune: 'hq',
        fast_mp4_finalize: true,
        force_hw_decode: true
      };

      const { task_id } = await startCompress(payload);
      taskId = task_id;
      
      // 3. Track progress
      esRef = openProgressStream(task_id);
      esRef.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.type === 'progress') {
            compressProgress = data.progress;
            if (data.progress >= 100 || data.phase === 'done') {
              finishJob();
            }
          } else if (data.type === 'done') {
            finishJob();
          } else if (data.type === 'error') {
            throw new Error(data.message || 'Compression failed');
          } else if (data.type === 'retry') {
            errorMessage = `Retrying: ${data.overage_percent?.toFixed(1)}% over target`;
          }
        } catch (err: any) {
          if (err.message) throw err;
        }
      };
      
      esRef.onerror = () => {
        if (status === 'compressing') throw new Error('Lost connection to progress stream');
      };

    } catch (err: any) {
      status = 'error';
      errorMessage = err.message || 'Something went wrong';
      try { esRef?.close(); } catch {}
    }
  }

  function finishJob() {
    status = 'done';
    compressProgress = 100;
    try { esRef?.close(); esRef = null; } catch {}
    
    // Play pleasant chime
    const audio = new Audio('data:audio/wav;base64,UklGRiQFAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAFAAB/goSGiIqMjo+RkpOUlZaXmJmam5ydnp+goaKjpKWmp6ipqqusra6vsLGys7S1tre4ubq7vL2+v8DBwsPExcbHyMnKy8zNzs/Q0dLT1NXW19jZ2tvc3d7f4OHi4+Tl5ufo6err7O3u7/Dx8vP09fb3+Pn6+/z9/v+AgYKDhIWGh4iJiouMjY6PkJGSk5SVlpeYmZqbnJ2en6ChoqOkpaanqKmqq6ytrq+wsbKztLW2t7i5uru8vb6/wMHCw8TFxsfIycrLzM3Oz9DR0tPU1dbX2Nna29zd3t/g4eLj5OXm5+jp6uvs7e7v8PHy8/T19vf4+fr7/P3+/4CBgoOEhYaHiImKi4yNjo+QkZKTlJWWl5iZmpucnZ6foKGio6SlpqeoqaqrrK2ur7CxsrO0tba3uLm6u7y9vr/AwcLDxMXGx8jJysvMzc7P0NHS09TV1tfY2drb3N3e3+Dh4uPk5ebn6Onq6+zt7u/w8fLz9PX29/j5+vv8/f7/gIGCg4SFhoeIiYqLjI2Oj5CRkpOUlZaXmJmam5ydnp+goaKjpKWmp6ipqqusra6vsLGys7S1tre4ubq7vL2+v8DBwsPExcbHyMnKy8zNzs/Q0dLT1NXW19jZ2tvc3d7f4OHi4+Tl5ufo6err7O3u7/Dx8vP09fb3+Pn6+/z9/v8=');
    audio.volume = 0.4;
    audio.play().catch(() => {});
    
    // Auto download
    if (taskId) {
      setTimeout(() => {
        window.location.href = downloadUrl(taskId!);
      }, 500);
    }
  }

</script>

<div class="container">
  <!-- Header -->
  <header class="header">
    <h1 class="logo">8mb<span class="accent">.local</span></h1>
    <p class="tagline">Compress videos to any size.</p>
  </header>

  <input id="file-input" type="file" accept="video/*,image/gif" multiple class="file-input-hidden" on:change={handleFileSelect} />

  <!-- Drop Zone -->
  <label for="file-input" class="drop-zone card" class:dragging={isDragging} class:has-file={!!file} class:processing={status === 'analyzing' || status === 'compressing'} on:dragover={handleDragOver} on:dragleave={handleDragLeave} on:drop={handleDrop}>
    {#if file}
      <div class="file-preview-card fade-in" style="aspect-ratio: 100 / {Math.min(aspectRatio, 75)};" on:click|preventDefault|stopPropagation={() => { if (['idle','done','error'].includes(status)) document.getElementById('file-input')?.click(); }} role="button" tabindex="0">
        {#if previewUrl}
          <video bind:this={previewVideo} src={previewUrl} muted playsinline loop autoplay class="bg-video"
            on:loadedmetadata={(e) => { const v=e.target; if(v.videoWidth) aspectRatio = (v.videoHeight/v.videoWidth)*100; }}></video>
          <div class="video-overlay"></div>
        {/if}
        <div class="preview-content">
          <button type="button" class="file-icon" class:clickable={!!previewUrl} on:click|preventDefault|stopPropagation={togglePreview} disabled={!previewUrl}>
            {#if previewUrl}
              {#if isPlaying}
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z"/></svg>
              {:else}
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
              {/if}
            {:else}
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/></svg>
            {/if}
          </button>
          <div class="file-details">
            <span class="file-name">{file.name}</span>
            <span class="file-size">{formatBytes(file.size)}</span>
          </div>
          <button class="btn-clear" on:click|preventDefault|stopPropagation={clearFile} aria-label="Cancel">×</button>
        </div>
      </div>
    {:else}
      <div class="drop-content">
        <div class="drop-icon">📁</div>
        <p class="drop-text"><strong>Drag & Drop</strong> your video here</p>
        <p class="drop-subtext">or click to choose — drop up to 5 for batch</p>
      </div>
    {/if}
  </label>
  {#if multiDropMessage}
    <p class="multi-drop-msg">{multiDropMessage}</p>
  {/if}

  <!-- Controls -->
  <div class="size-section">
    <div class="setting-label">Target Size</div>
    <div class="size-track">
      {#each sizePresets as size}
        <button class="size-option" class:active={!isCustom && targetSize === size} on:click={() => { targetSize = size; isCustom = false; }} disabled={status !== 'idle'}>
          <span class="size-value">{size}</span><span class="size-unit">MB</span>
        </button>
      {/each}
      <div class="size-option custom" class:active={isCustom}>
        <input type="text" inputmode="numeric" placeholder="..." bind:value={customSize} on:focus={() => { isCustom = true; }} on:input={(e) => { customSize = e.target.value.replace(/[^0-9.]/g, '').slice(0, 5); }} disabled={status !== 'idle'} />
        <span class="size-unit">MB</span>
      </div>
    </div>
  </div>

  <div class="size-section mt-1">
    <div class="setting-label flex justify-between">
      <span>Quality</span>
    </div>
    <div class="size-track">
      <button class="size-option" class:active={quality === 'p3'} on:click={() => { quality = 'p3'; perfectQualityOverride = false; }} disabled={status !== 'idle'}>Fast</button>
      <button class="size-option" class:active={quality === 'p5'} on:click={() => { quality = 'p5'; perfectQualityOverride = false; }} disabled={status !== 'idle'}>Balanced</button>
      <button class="size-option" class:active={quality === 'p6'} on:click={() => { quality = 'p6'; perfectQualityOverride = false; }} disabled={status !== 'idle'}>Quality</button>
      <button class="size-option" class:active={quality === 'p7'} on:click={() => { quality = 'p7'; perfectQualityOverride = false; }} disabled={status !== 'idle'}>Best</button>
    </div>
    <label class="flex items-center gap-2 mt-2 cursor-pointer opacity-80 hover:opacity-100 transition-opacity justify-end">
      <input type="checkbox" bind:checked={perfectQualityOverride} class="checkbox-ui" disabled={status !== 'idle'}>
      <span class="text-sm shadow-text">Perfect Quality Override</span>
    </label>
  </div>

  <!-- Progress -->
  {#if status !== 'idle'}
    <div class="progress-section fade-in">
      {#if status === 'analyzing'}
        <div class="progress-label"><span>Uploading & Analyzing...</span><span class="font-mono">{uploadProgress}%</span></div>
        <div class="progress-container"><div class="progress-bar" style="width: {uploadProgress}%"></div></div>
      {:else if status === 'compressing'}
        <div class="progress-label"><span>Compressing...</span><span class="font-mono">{compressProgress}%</span></div>
        <div class="progress-container"><div class="progress-bar" style="width: {compressProgress}%"></div></div>
        {#if errorMessage.includes('Retrying')}<p class="status-warning text-xs mt-1 text-center">{errorMessage}</p>{/if}
      {:else if status === 'done'}
        <div class="status-done"><span class="status-icon">✅</span><span>Done! Download started.</span></div>
      {:else if status === 'error'}
        <div class="status-error"><span class="status-icon">💔</span><span>{errorMessage || 'An error occurred'}</span><button class="btn-dismiss" on:click={() => { status = 'idle'; errorMessage = ''; }}>×</button></div>
      {/if}
    </div>
  {/if}

  <!-- Action -->
  {#if status === 'idle'}
    <div class="action-section">
      <button class="btn-primary compress-btn" on:click={doCompress} disabled={!canCompress}>
        UPLOAD AND COMPRESS
      </button>
    </div>
  {/if}

  <!-- Footer -->
  <footer class="footer">
    <a href="/advanced" class="advanced-link">Advanced options &rarr;</a>
    <div class="footer-icons">
      <a href="/batch" class="icon-link" aria-label="Batch" title="Batch compress">🗂</a>
      <a href="/history" class="icon-link" aria-label="History" title="History">📜</a>
      <a href="/settings" class="icon-link" aria-label="Settings" title="Settings">⚙️</a>
    </div>
    <div class="footer-status">
      {#if daemonStatus?.enabled}
        <span class="daemon-badge" title={daemonStatus.connected ? `Daemon: ${daemonStatus.codecs?.join(', ') || 'connected'}` : 'Daemon: unreachable'}>
          <span class="daemon-dot" class:connected={daemonStatus.connected} class:disconnected={!daemonStatus.connected}></span>
          <span class="daemon-label">{daemonStatus.connected ? 'HW Accel' : 'Daemon offline'}</span>
        </span>
      {/if}
      {#if appVersion}<span class="version">v{appVersion}</span>{/if}
    </div>
  </footer>
</div>

<style>
  :root {
    --bg-card: #111111;
    --bg-hover: #1a1a1a;
    --text-primary: #ffffff;
    --text-secondary: #888888;
    --text-muted: #555555;
    --accent: #6366f1;
    --accent-hover: #818cf8;
    --accent-glow: rgba(99, 102, 241, 0.3);
    --success: #22c55e;
    --warning: #f59e0b;
    --error: #ef4444;
    --border-radius: 12px;
    --glass-border: rgba(255, 255, 255, 0.08);
    --transition-fast: 0.15s ease;
  }

  .container {
    width: 100%;
    max-width: 480px;
    margin: 40px auto;
    display: flex;
    flex-direction: column;
    gap: 24px;
    padding: 0 16px;
  }

  .header { text-align: center; }
  .logo { font-size: 2.5rem; font-weight: 700; letter-spacing: -1px; margin: 0; }
  .accent { color: var(--accent); }
  .tagline { color: var(--text-secondary); margin-top: 8px; font-size: 0.95rem; }

  .file-input-hidden { position: fixed; top: -9999px; left: -9999px; }

  /* Drop Zone */
  .drop-zone {
    position: relative; min-height: 200px; display: flex; align-items: center; justify-content: center;
    cursor: pointer; transition: all var(--transition-fast); border: 2px dashed var(--glass-border);
    background: rgba(255, 255, 255, 0.02); border-radius: 16px;
  }
  .drop-zone:hover, .drop-zone.dragging { border-color: var(--accent); background: rgba(99, 102, 241, 0.05); }
  .drop-zone.has-file { border-style: solid; border-color: var(--glass-border); padding: 12px; min-height: 220px; }
  .drop-zone.processing { cursor: wait; border-color: var(--accent-glow); }

  .drop-content { text-align: center; padding: 20px; }
  .drop-icon { font-size: 3rem; margin-bottom: 12px; }
  .drop-text { font-size: 1.1rem; margin-bottom: 4px; }
  .drop-subtext { font-size: 0.85rem; color: var(--text-muted); }

  /* File Preview */
  .file-preview-card {
    display: flex; flex-direction: column; width: 100%; height: 100%; position: relative;
    overflow: hidden; border-radius: 12px; background: var(--bg-card); box-shadow: 0 4px 20px rgba(0,0,0,0.5);
  }
  .bg-video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0.5; }
  .video-overlay { position: absolute; inset: 0; background: linear-gradient(to top, rgba(0,0,0,0.9), rgba(0,0,0,0.2) 60%); }
  
  .preview-content { position: absolute; bottom: 0; left: 0; right: 0; display: flex; align-items: center; gap: 16px; padding: 16px; z-index: 10; }
  .file-icon { width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; color: white; cursor: pointer; backdrop-filter: blur(4px); }
  .file-icon:hover { background: rgba(255,255,255,0.25); transform: scale(1.05); }
  
  .file-details { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .file-name { font-weight: 600; font-size: 1rem; color: white; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-shadow: 0 1px 3px rgba(0,0,0,0.8); }
  .file-size { font-size: 0.8rem; color: rgba(255,255,255,0.7); }
  
  .btn-clear { width: 32px; height: 32px; border-radius: 50%; background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.1); color: white; font-size: 1.2rem; cursor: pointer; display: flex; align-items: center; justify-content: center; line-height: 0; backdrop-filter: blur(4px); }
  .btn-clear:hover { background: var(--error); border-color: var(--error); transform: scale(1.1); }

  /* Settings Track */
  .size-section { display: flex; flex-direction: column; gap: 8px; }
  .setting-label { font-size: 0.85rem; font-weight: 500; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-left: 4px; }
  .size-track { display: flex; background: var(--bg-card); border: 1px solid var(--glass-border); border-radius: var(--border-radius); padding: 4px; gap: 2px; }
  .size-option { flex: 1; padding: 10px 4px; border: none; background: transparent; color: var(--text-secondary); font-size: 0.9rem; font-weight: 500; border-radius: 8px; cursor: pointer; transition: all var(--transition-fast); display: flex; align-items: center; justify-content: center; gap: 2px; }
  .size-option:hover:not(:disabled):not(.active) { background: var(--bg-hover); color: var(--text-primary); }
  .size-option.active { background: linear-gradient(135deg, var(--accent), var(--accent-hover)); color: white; font-weight: 600; box-shadow: 0 2px 8px rgba(99, 102, 241, 0.3); }
  .size-option:disabled { opacity: 0.5; cursor: not-allowed; }
  .size-value { font-weight: inherit; }
  .size-unit { font-size: 0.65rem; opacity: 0.7; }
  
  .size-option.custom { flex: 0.8; min-width: 65px; gap: 2px; }
  .size-option.custom input { width: 36px; background: transparent; border: none; color: inherit; font-size: 0.9rem; font-weight: inherit; text-align: center; outline: none; }
  .size-option.custom input::placeholder { color: var(--text-muted); }
  .size-option.custom.active input { color: white; }

  /* Progress UI */
  .fade-in { animation: fadeIn 0.3s ease-out; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
  
  .progress-section { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
  .progress-label { display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-secondary); }
  .font-mono { font-family: monospace; }
  .progress-container { width: 100%; height: 6px; background: var(--bg-card); border-radius: 3px; overflow: hidden; }
  .progress-bar { height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-hover)); border-radius: 3px; transition: width 0.3s linear; }
  
  .status-done, .status-error { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 12px; border-radius: var(--border-radius); font-size: 0.9rem; font-weight: 500; }
  .status-done { background: rgba(34, 197, 94, 0.1); color: var(--success); }
  .status-error { background: rgba(239, 68, 68, 0.1); color: var(--error); position: relative; }
  .status-warning { color: var(--warning); }
  .btn-dismiss { background: none; border: none; color: inherit; position: absolute; right: 12px; font-size: 1.2rem; cursor: pointer; opacity: 0.7; }
  .btn-dismiss:hover { opacity: 1; }

  /* Buttons */
  .btn-primary { width: 100%; padding: 16px; border: none; border-radius: var(--border-radius); font-size: 1rem; font-weight: 700; letter-spacing: 1px; color: white; background: linear-gradient(to bottom, #6366f1, #4f46e5); box-shadow: inset 0 1px 0 rgba(255,255,255,0.2), 0 4px 12px rgba(0,0,0,0.2), 0 0 20px rgba(99,102,241,0.3); cursor: pointer; transition: all 0.3s; position: relative; overflow: hidden; }
  .btn-primary:hover:not(:disabled) { transform: translateY(-2px); box-shadow: inset 0 1px 0 rgba(255,255,255,0.3), 0 6px 20px rgba(99,102,241,0.5); }
  .btn-primary:active:not(:disabled) { transform: translateY(0); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; filter: grayscale(50%); }

  /* Footer */
  .footer { margin-top: 16px; display: flex; flex-direction: column; align-items: center; gap: 16px; border-top: 1px solid var(--glass-border); padding-top: 24px; }
  .advanced-link { color: var(--text-secondary); text-decoration: none; font-size: 0.9rem; font-weight: 500; transition: color 0.2s; }
  .advanced-link:hover { color: var(--accent); }
  
  .footer-icons { display: flex; justify-content: center; align-items: center; gap: 24px; margin-bottom: 8px; }
  .footer-status { display: flex; align-items: center; justify-content: center; gap: 16px; margin-top: 4px; }
  .version { font-size: 0.75rem; color: var(--text-muted); font-family: monospace; }
  .icon-link { color: var(--text-muted); text-decoration: none; font-size: 1.1rem; transition: all 0.2s; opacity: 0.7; }
  .icon-link:hover { opacity: 1; transform: rotate(15deg); }

  /* Utilities */
  .checkbox-ui { accent-color: var(--accent); width: 14px; height: 14px; }
  .shadow-text { text-shadow: 0 1px 2px rgba(0,0,0,0.5); }

  /* Daemon status badge */
  .daemon-badge { display: flex; align-items: center; gap: 6px; font-size: 0.75rem; color: var(--text-muted); padding: 2px 8px; border-radius: 10px; background: rgba(255,255,255,0.04); border: 1px solid var(--glass-border); cursor: default; }
  .daemon-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
  .daemon-dot.connected { background: #22c55e; box-shadow: 0 0 6px rgba(34,197,94,0.5); }
  .daemon-dot.disconnected { background: #ef4444; box-shadow: 0 0 6px rgba(239,68,68,0.4); }
  .daemon-label { white-space: nowrap; }

  /* Multi-drop message */
  .multi-drop-msg { text-align: center; font-size: 0.8rem; color: #fbbf24; margin-top: -8px; }
</style>
