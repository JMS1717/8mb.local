<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { uploadWithProgress, startCompress, openProgressStream, downloadUrl, getAvailableCodecs } from '$lib/api';

  let file: File | null = null;
  let uploadedFileName: string | null = null; // Track what file was uploaded
  let targetMB = 25;
  let videoCodec: string = 'av1_nvenc';
  let audioCodec: 'libopus' | 'aac' | 'none' = 'libopus';
  let preset: 'p1'|'p2'|'p3'|'p4'|'p5'|'p6'|'p7'|'extraquality' = 'p6';
  let audioKbps: 64|96|128|160|192|256 = 128;
  let fileSizeLabel: string | null = null;
  let container: 'mp4' | 'mkv' = 'mp4';
  let tune: 'hq'|'ll'|'ull'|'lossless' = 'hq';
  // New resolution and trim controls
  let maxWidth: number | null = null;
  let maxHeight: number | null = null;
  let startTime: string = '';
  let endTime: string = '';
  // New UI options
  let playSoundWhenDone = true; // default ON
  let autoDownload = false;
  
  $: containerNote = (container === 'mp4' && audioCodec === 'libopus') ? 'MP4 does not support Opus; audio will be encoded as AAC automatically.' : null;
  $: estimated = jobInfo ? {
    duration_s: jobInfo.duration_s,
    total_kbps: jobInfo.duration_s > 0 ? (targetMB * 8192.0) / jobInfo.duration_s : 0,
    video_kbps: jobInfo.duration_s > 0 ? Math.max(((targetMB * 8192.0) / jobInfo.duration_s) - (audioCodec === 'none' ? 0 : audioKbps), 0) : 0,
    final_mb: targetMB
  } : null;

  let jobInfo: any = null;
  let taskId: string | null = null;
  let progress = 0;
  let logLines: string[] = [];
  let doneStats: any = null;
  let isCompressing = false;
  let esRef: EventSource | null = null;
  let warnText: string | null = null;
  let errorText: string | null = null;
  let isUploading = false;
  let uploadProgress = 0;
  // Support widget state
  let showSupport = false;
  function toggleSupport(){ showSupport = !showSupport; }
  function closeSupport(){ showSupport = false; }
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeSupport(); };

  // Available codecs from backend
  let availableCodecs: Array<{value: string, label: string, group: string}> = [];
  let hardwareType = 'cpu';

  // Load default presets and available codecs on mount
  onMount(async () => {
    // Restore UI preferences
    try {
      const ps = localStorage.getItem('playSoundWhenDone');
      if (ps !== null) playSoundWhenDone = (ps === 'true');
      const ad = localStorage.getItem('autoDownload');
      if (ad !== null) autoDownload = (ad === 'true');
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
      hardwareType = codecData.hardware_type;
      availableCodecs = buildCodecList(codecData);
    } catch (err) {
      console.warn('Failed to load available codecs, using fallback');
      availableCodecs = [
        { value: 'libx264', label: 'H.264 (CPU)', group: 'cpu' },
        { value: 'libx265', label: 'HEVC (H.265, CPU)', group: 'cpu' },
        { value: 'libaom-av1', label: 'AV1 (CPU)', group: 'cpu' },
      ];
    }
  });

  function buildCodecList(codecData: any): Array<{value: string, label: string, group: string}> {
    const list: Array<{value: string, label: string, group: string}> = [];
    const enabledCodecs = codecData.enabled_codecs || [];
    
    // Build list of all possible codecs with labels
    const codecDefinitions = [
      // NVIDIA
      { value: 'av1_nvenc', label: 'AV1 (NVIDIA - RTX 40/50 series)', group: 'nvidia' },
      { value: 'hevc_nvenc', label: 'HEVC (H.265, NVIDIA)', group: 'nvidia' },
      { value: 'h264_nvenc', label: 'H.264 (NVIDIA)', group: 'nvidia' },
      // Intel QSV
      { value: 'av1_qsv', label: 'AV1 (Intel Arc/QSV)', group: 'intel' },
      { value: 'hevc_qsv', label: 'HEVC (H.265, Intel QSV)', group: 'intel' },
      { value: 'h264_qsv', label: 'H.264 (Intel QSV)', group: 'intel' },
      // Intel/AMD VAAPI (Linux)
      { value: 'av1_vaapi', label: 'AV1 (VAAPI)', group: 'vaapi' },
      { value: 'hevc_vaapi', label: 'HEVC (H.265, VAAPI)', group: 'vaapi' },
      { value: 'h264_vaapi', label: 'H.264 (VAAPI)', group: 'vaapi' },
      // AMD AMF
      { value: 'av1_amf', label: 'AV1 (AMD)', group: 'amd' },
      { value: 'hevc_amf', label: 'HEVC (H.265, AMD)', group: 'amd' },
      { value: 'h264_amf', label: 'H.264 (AMD)', group: 'amd' },
      // CPU
      { value: 'libaom-av1', label: 'AV1 (CPU - Highest Quality)', group: 'cpu' },
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

  function getCodecColor(group: string): string {
    switch(group) {
      case 'nvidia': return '#22c55e'; // green
      case 'intel': return '#3b82f6';  // blue
      case 'amd': return '#f97316';    // orange
      case 'vaapi': return '#8b5cf6';  // purple (for generic VAAPI)
      case 'cpu': return '#6b7280';    // gray
      default: return '#6b7280';
    }
  }

  function getCodecIcon(group: string): string {
    switch(group) {
      case 'nvidia': return '🟢';
      case 'intel': return '🔵';
      case 'amd': return '🟠';
      case 'vaapi': return '🟣';
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
      // Only upload if it's a different file
      if (uploadedFileName !== f.name && !isUploading) {
        await doUpload();
      }
    }
  }
  function allowDrop(e: DragEvent){ e.preventDefault(); }

  async function doUpload(){
    if (!file) return;
    if (isUploading) return;
    isUploading = true;
    uploadProgress = 0;
    errorText = null;
    try {
      console.log('Uploading to backend...', file.name);
      jobInfo = await uploadWithProgress(file, targetMB, audioKbps, { onProgress: (p:number)=>{ uploadProgress = p; } });
      console.log('Upload response:', jobInfo);
      uploadedFileName = file.name; // Mark this file as uploaded
      warnText = jobInfo.warn_low_quality ? `Warning: Very low video bitrate (${Math.round(jobInfo.estimate_video_kbps)} kbps)` : null;
    } catch (err: any) {
      console.error('Upload failed:', err);
      errorText = `Upload failed: ${err.message || err}`;
    } finally {
      isUploading = false;
    }
  }

  async function doCompress(){
    if (!jobInfo) return;
    if (isCompressing) return; // prevent double submission
    errorText = null;
    try {
      isCompressing = true;
      const payload = {
        job_id: jobInfo.job_id,
        filename: jobInfo.filename,
        target_size_mb: targetMB,
        video_codec: videoCodec,
        audio_codec: audioCodec,
        audio_bitrate_kbps: audioKbps,
        preset,
        container,
        tune,
        // Optional resolution and trim parameters
        max_width: maxWidth || undefined,
        max_height: maxHeight || undefined,
        start_time: startTime.trim() || undefined,
        end_time: endTime.trim() || undefined,
      };
      console.log('Starting compression...', payload);
      const { task_id } = await startCompress(payload);
      taskId = task_id;
      try { esRef?.close(); } catch {}
      const es = openProgressStream(taskId);
      esRef = es;
      es.onmessage = (ev) => {
        try { const data = JSON.parse(ev.data);
          if (data.type === 'progress') { progress = data.progress; }
          if (data.type === 'log' && data.message) { logLines = [data.message, ...logLines].slice(0, 500); }
          if (data.type === 'done') { 
            doneStats = data.stats; 
            progress = 100;
            isCompressing = false;
            try { esRef?.close(); } catch {}
            
            // Play sound when done if enabled
            if (playSoundWhenDone) {
              const audio = new Audio('data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQoGAACBhYqFbF1fdJivrJBhNjVgodDbq2EcBj+a2/LDciUFLIHO8tiJNwgZaLvt559NEAxQp+PwtmMcBjiR1/LMeSwFJHfH8N2QQAoUXrTp66hVFApGn+DyvmwhBTGH0fPTgjMGHm7A7+OZURE');
              audio.play().catch(() => {});
            }
            
            // Auto-download if enabled
            if (autoDownload && doneStats?.output_filename) {
              setTimeout(() => {
                window.location.href = downloadUrl(doneStats.output_filename);
              }, 500);
            }
          }
          if (data.type === 'error') { logLines = [data.message, ...logLines]; isCompressing = false; try { esRef?.close(); } catch {} }
        } catch {}
      }
    } catch (err: any) {
      console.error('Compress failed:', err);
      errorText = `Compression failed: ${err.message || err}`;
    }
  }

  function reset(){ file=null; uploadedFileName=null; jobInfo=null; taskId=null; progress=0; logLines=[]; doneStats=null; warnText=null; errorText=null; isUploading=false; isCompressing=false; try { esRef?.close(); } catch {} }

  // Persist UI preferences
  $: (() => { try { localStorage.setItem('playSoundWhenDone', String(playSoundWhenDone)); } catch {} })();
  $: (() => { try { localStorage.setItem('autoDownload', String(autoDownload)); } catch {} })();
</script>

<div class="max-w-3xl mx-auto mt-8 space-y-6">
  <div class="flex items-center justify-between mb-4">
    <h1 class="text-2xl font-bold">8mb.local</h1>
    <a href="/settings" class="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors text-sm">
      ⚙️ Settings
    </a>
  </div>

  <div class="card">
    <div class="border-2 border-dashed border-gray-700 rounded p-8 text-center"
         on:drop={onDrop} on:dragover={allowDrop}>
      <p class="mb-2">Drag & drop a video here</p>
      <input type="file" accept="video/*" on:change={async (e:any)=>{ const f=e.target.files?.[0]||null; file=f; fileSizeLabel = f? formatSize(f.size): null; if (f && uploadedFileName !== f.name) { await doUpload(); } }} />
      {#if file}
        <p class="mt-2 text-sm text-gray-400">{file.name} {#if fileSizeLabel}<span class="opacity-70">• {fileSizeLabel}</span>{/if}</p>
      {/if}
      {#if isUploading}
        <div class="mt-4">
          <p class="text-xs text-gray-400 mb-1">Uploading… {uploadProgress}%</p>
          <div class="h-2 bg-gray-800 rounded">
            <div class="h-2 bg-blue-600 rounded" style={`width:${uploadProgress}%`}></div>
          </div>
        </div>
      {/if}
    </div>
  </div>

  <div class="card grid grid-cols-2 gap-4">
    <div class="space-x-2">
      <button class="btn" on:click={()=>setPresetMB(4)}>4MB</button>
      <button class="btn" on:click={()=>setPresetMB(5)}>5MB</button>
      <button class="btn" on:click={()=>setPresetMB(8)}>8MB</button>
      <button class="btn" on:click={setPresetMBSafe10}>10MB (Discord)</button>
      <button class="btn" on:click={()=>setPresetMB(50)}>50MB</button>
      <button class="btn" on:click={()=>setPresetMB(100)}>100MB</button>
    </div>
    <div class="flex items-center gap-2 justify-end">
      <label class="text-sm">Custom size (MB)</label>
      <input class="input w-28" type="number" bind:value={targetMB} min="1" />
    </div>
  </div>

  <div class="card">
    <details>
      <summary class="cursor-pointer">Advanced Options</summary>
      <div class="mt-4 grid sm:grid-cols-4 gap-4">
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
          <div class="mt-2 text-[10px] space-y-0.5 opacity-75">
            <div><span class="inline-block w-2 h-2 rounded-full bg-green-500 mr-1"></span> Green = NVIDIA GPU</div>
            <div><span class="inline-block w-2 h-2 rounded-full bg-blue-500 mr-1"></span> Blue = Intel GPU</div>
            <div><span class="inline-block w-2 h-2 rounded-full bg-orange-500 mr-1"></span> Orange = AMD GPU</div>
            <div><span class="inline-block w-2 h-2 rounded-full bg-gray-500 mr-1"></span> Gray = CPU (slower)</div>
          </div>
        </div>
        <div>
          <label class="block mb-1 text-sm">Audio Codec</label>
          <select class="input w-full" bind:value={audioCodec}>
            <option value="libopus">Opus (Default)</option>
            <option value="aac">AAC</option>
            <option value="none">🔇 None (Mute)</option>
          </select>
        </div>
        <div>
          <label class="block mb-1 text-sm">Speed/Quality</label>
          <select class="input w-full" bind:value={preset}>
            <option value="p1">Fast (P1)</option>
            <option value="p5">Balanced (P5)</option>
            <option value="p7">Best Quality (P7)</option>
            <option value="p6">Default (P6)</option>
            <option value="extraquality">🌟 Extra Quality (Slowest)</option>
          </select>
        </div>
        <div>
          <label class="block mb-1 text-sm">Container</label>
          <select class="input w-full" bind:value={container}>
            <option value="mp4">MP4 (Most compatible)</option>
            <option value="mkv">MKV (Best with Opus)</option>
          </select>
        </div>
        <div>
          <label class="block mb-1 text-sm">Audio Bitrate (kbps)</label>
          <select class="input w-full" bind:value={audioKbps} disabled={audioCodec === 'none'}>
            <option value={64}>64</option>
            <option value={96}>96</option>
            <option value={128}>128</option>
            <option value={160}>160</option>
            <option value={192}>192</option>
            <option value={256}>256</option>
          </select>
          {#if audioCodec === 'none'}
            <p class="text-xs text-gray-400 mt-1">Disabled (audio muted)</p>
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
          <p class="mt-1 text-xs opacity-70">Quality = best visuals. Low/Ultra‑low latency = faster encodes (good for screen/streams). Lossless = huge files.</p>
        </div>
      </div>
      
      <!-- Resolution and Trim Controls -->
      <div class="mt-4 pt-4 border-t border-gray-700">
        <h4 class="text-sm font-medium mb-3">Resolution & Trimming</h4>
        <div class="grid sm:grid-cols-4 gap-4">
          <div>
            <label class="block mb-1 text-sm">Max Width (px)</label>
            <input class="input w-full" type="number" bind:value={maxWidth} placeholder="Original" min="1" />
          </div>
          <div>
            <label class="block mb-1 text-sm">Max Height (px)</label>
            <input class="input w-full" type="number" bind:value={maxHeight} placeholder="Original" min="1" />
          </div>
          <div>
            <label class="block mb-1 text-sm">Start Time</label>
            <input class="input w-full" type="text" bind:value={startTime} placeholder="0 or 00:00:00" />
            <p class="mt-1 text-xs opacity-70">Format: seconds or HH:MM:SS</p>
          </div>
          <div>
            <label class="block mb-1 text-sm">End Time</label>
            <input class="input w-full" type="text" bind:value={endTime} placeholder="Full duration" />
            <p class="mt-1 text-xs opacity-70">Format: seconds or HH:MM:SS</p>
          </div>
        </div>
        <p class="mt-2 text-xs opacity-70">
          Leave resolution blank to keep original. Aspect ratio is maintained.
          Leave times blank to use full duration.
        </p>
      </div>
      
      <!-- UI Options -->
      <div class="mt-4 pt-4 border-t border-gray-700">
        <h4 class="text-sm font-medium mb-3">UI Options</h4>
        <div class="flex flex-wrap gap-4">
          <label class="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" bind:checked={playSoundWhenDone} class="w-4 h-4" />
            <span class="text-sm">🔔 Play sound when done</span>
          </label>
          <label class="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" bind:checked={autoDownload} class="w-4 h-4" />
            <span class="text-sm">⬇️ Auto-download when done</span>
          </label>
        </div>
      </div>
      
      {#if containerNote}
        <p class="mt-2 text-xs text-amber-400">{containerNote}</p>
      {/if}
    </details>
  </div>

  {#if jobInfo}
    <div class="card">
      <p class="text-sm">Original: {Math.round((jobInfo.original_video_bitrate_kbps||0)+(jobInfo.original_audio_bitrate_kbps||0))} kbps
        Target: {estimated ? Math.round(estimated.total_kbps) : Math.round(jobInfo.estimate_total_kbps)} kbps -> Video ~{estimated ? Math.round(estimated.video_kbps) : Math.round(jobInfo.estimate_video_kbps)} kbps</p>
      {#if estimated}
        <p class="text-xs opacity-80">Estimated final size: ~{estimated.final_mb.toFixed(2)} MB</p>
      {/if}
      {#if warnText}<p class="text-amber-400 text-sm mt-1">{warnText}</p>{/if}
    </div>
  {/if}

  {#if errorText}
    <div class="card border-red-500">
      <p class="text-red-400">{errorText}</p>
    </div>
  {/if}

  <div class="flex gap-2">
    <button class="btn" on:click={doUpload} disabled={!file || isUploading}>
      {isUploading ? `Uploading… ${uploadProgress}%` : 'Analyze'}
    </button>
    <button class="btn" on:click={doCompress} disabled={!jobInfo}>Compress</button>
    <button class="btn" on:click={reset} disabled={!file && !taskId}>Reset</button>
  </div>

  {#if taskId}
    <div class="card">
      <div class="h-3 bg-gray-800 rounded">
        <div class="h-3 bg-indigo-600 rounded" style={`width:${progress}%`}></div>
      </div>
      <details class="mt-3" open>
        <summary>FFmpeg log</summary>
        <pre class="mt-2 text-xs whitespace-pre-wrap">{logLines.join('\n')}</pre>
      </details>

      {#if doneStats}
        <div class="mt-4 text-sm">
          <p>Completed. Final size: {doneStats.final_size_mb} MB</p>
          <a class="btn inline-block mt-2" href={downloadUrl(taskId)} target="_blank">Download</a>
        </div>
      {/if}
    </div>
  {/if}

  <!-- Support badge moved to corner (smaller, unobtrusive) -->
</div>

<!-- Floating support widget -->
<button
  class="fixed bottom-4 right-4 bg-gray-800/90 hover:bg-gray-700 text-xs px-3 py-1.5 rounded-full shadow-lg border border-gray-700 backdrop-blur-sm flex items-center gap-1 z-50"
  on:click={toggleSupport}
  aria-expanded={showSupport}
  aria-controls="support-popover"
  title="Support the project"
>
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="w-4 h-4 text-rose-400">
    <path d="M11.645 20.91l-.007-.003-.022-.01a15.247 15.247 0 01-.383-.184 25.18 25.18 0 01-4.244-2.62C4.688 16.197 2.25 13.614 2.25 10.5 2.25 8.014 4.237 6 6.75 6c1.56 0 2.927.802 3.75 2.016C11.323 6.802 12.69 6 14.25 6 16.763 6 18.75 8.014 18.75 10.5c0 3.114-2.438 5.697-4.739 7.593a25.175 25.175 0 01-4.244 2.62 15.247 15.247 0 01-.383.184l-.022.01-.007.003-.003.001a.75.75 0 01-.614 0l-.003-.001z" />
  </svg>
  <span>Support</span>
  <span class="sr-only">the project</span>
</button>

{#if showSupport}
  <div
    id="support-popover"
    class="fixed bottom-16 right-4 w-64 bg-gray-900/95 text-gray-100 border border-gray-700 rounded-lg shadow-xl p-3 z-50"
    role="dialog"
    aria-label="Support the project"
    on:keydown={onKey}
  >
    <div class="flex items-start justify-between gap-2">
      <p class="text-xs leading-relaxed">
        Thanks for using <span class="font-semibold">8mb.local</span>! If this saved you time and you'd like to chip in, tips are appreciated (never expected).
      </p>
      <button class="text-gray-400 hover:text-gray-200 text-sm" on:click={closeSupport} title="Close" aria-label="Close">×</button>
    </div>
    <div class="mt-2">
      <a class="underline text-xs hover:text-rose-300" href="https://paypal.me/jasonselsley" target="_blank" rel="noopener noreferrer">paypal.me/jasonselsley</a>
    </div>
  </div>
{/if}

<style>
  /* Color-code codec options based on hardware type */
  .codec-select option[data-group="nvidia"] {
    color: #22c55e;
    font-weight: 500;
  }
  .codec-select option[data-group="intel"] {
    color: #3b82f6;
    font-weight: 500;
  }
  .codec-select option[data-group="amd"] {
    color: #f97316;
    font-weight: 500;
  }
  .codec-select option[data-group="vaapi"] {
    color: #8b5cf6;
    font-weight: 500;
  }
  .codec-select option[data-group="cpu"] {
    color: #9ca3af;
  }
</style>
