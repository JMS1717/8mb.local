<script lang="ts">
  import '../app.css';
  import { upload, startCompress, openProgressStream, downloadUrl } from '$lib/api';

  let file: File | null = null;
  let targetMB = 25;
  let videoCodec: 'av1_nvenc' | 'hevc_nvenc' | 'h264_nvenc' = 'av1_nvenc';
  let audioCodec: 'libopus' | 'aac' = 'libopus';
  let preset: 'p1'|'p2'|'p3'|'p4'|'p5'|'p6'|'p7' = 'p6';
  let audioKbps = 128;

  let jobInfo: any = null;
  let taskId: string | null = null;
  let progress = 0;
  let logLines: string[] = [];
  let doneStats: any = null;
  let warnText: string | null = null;
  let errorText: string | null = null;
  let isUploading = false;

  function setPresetMB(mb:number){ targetMB = mb; }

  function onDrop(e: DragEvent){
    e.preventDefault();
    if (!e.dataTransfer) return;
    const f = e.dataTransfer.files?.[0];
    if (f) file = f;
  }
  function allowDrop(e: DragEvent){ e.preventDefault(); }

  async function doUpload(){
    if (!file) return;
    isUploading = true;
    errorText = null;
    try {
      console.log('Uploading to backend...', file.name);
      jobInfo = await upload(file, targetMB, audioKbps);
      console.log('Upload response:', jobInfo);
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
    errorText = null;
    try {
      const payload = {
        job_id: jobInfo.job_id,
        filename: jobInfo.filename,
        target_size_mb: targetMB,
        video_codec: videoCodec,
        audio_codec: audioCodec,
        audio_bitrate_kbps: audioKbps,
        preset
      };
      console.log('Starting compression...', payload);
      const { task_id } = await startCompress(payload);
      taskId = task_id;
      const es = openProgressStream(taskId);
      es.onmessage = (ev) => {
        try { const data = JSON.parse(ev.data);
          if (data.type === 'progress') { progress = data.progress; }
          if (data.type === 'log' && data.message) { logLines = [data.message, ...logLines].slice(0, 500); }
          if (data.type === 'done') { doneStats = data.stats; progress = 100; }
          if (data.type === 'error') { logLines = [data.message, ...logLines]; }
        } catch {}
      }
    } catch (err: any) {
      console.error('Compress failed:', err);
      errorText = `Compression failed: ${err.message || err}`;
    }
  }

  function reset(){ file=null; jobInfo=null; taskId=null; progress=0; logLines=[]; doneStats=null; warnText=null; errorText=null; isUploading=false; }
</script>

<div class="max-w-3xl mx-auto mt-8 space-y-6">
  <h1 class="text-2xl font-bold">SmartDrop</h1>

  <div class="card">
    <div class="border-2 border-dashed border-gray-700 rounded p-8 text-center"
         on:drop={onDrop} on:dragover={allowDrop}>
      <p class="mb-2">Drag & drop a video here</p>
      <input type="file" accept="video/*" on:change={(e:any)=>file=e.target.files?.[0]||null} />
      {#if file}<p class="mt-2 text-sm text-gray-400">{file.name}</p>{/if}
    </div>
  </div>

  <div class="card grid grid-cols-2 gap-4">
    <div class="space-x-2">
      <button class="btn" on:click={()=>setPresetMB(8)}>8MB</button>
      <button class="btn" on:click={()=>setPresetMB(25)}>25MB</button>
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
      <div class="mt-4 grid sm:grid-cols-3 gap-4">
        <div>
          <label class="block mb-1 text-sm">Video Codec</label>
          <select class="input w-full" bind:value={videoCodec}>
            <option value="av1_nvenc">AV1 (Best Quality - new RTX)</option>
            <option value="hevc_nvenc">HEVC (H.265)</option>
            <option value="h264_nvenc">H.264 (Compatibility)</option>
          </select>
        </div>
        <div>
          <label class="block mb-1 text-sm">Audio Codec</label>
          <select class="input w-full" bind:value={audioCodec}>
            <option value="libopus">Opus (Default)</option>
            <option value="aac">AAC</option>
          </select>
        </div>
        <div>
          <label class="block mb-1 text-sm">Speed/Quality</label>
          <select class="input w-full" bind:value={preset}>
            <option value="p1">Fast (P1)</option>
            <option value="p5">Balanced (P5)</option>
            <option value="p7">Best Quality (P7)</option>
            <option value="p6">Default (P6)</option>
          </select>
        </div>
      </div>
    </details>
  </div>

  {#if jobInfo}
    <div class="card">
      <p class="text-sm">Original: {Math.round((jobInfo.original_video_bitrate_kbps||0)+(jobInfo.original_audio_bitrate_kbps||0))} kbps
        Target: {Math.round(jobInfo.estimate_total_kbps)} kbps -> Video ~{Math.round(jobInfo.estimate_video_kbps)} kbps</p>
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
      {isUploading ? 'Analyzing...' : 'Analyze'}
    </button>
    <button class="btn" on:click={doCompress} disabled={!jobInfo}>Compress</button>
    <button class="btn" on:click={reset} disabled={!file && !taskId}>Reset</button>
  </div>

  {#if taskId}
    <div class="card">
      <div class="h-3 bg-gray-800 rounded">
        <div class="h-3 bg-indigo-600 rounded" style={`width:${progress}%`}></div>
      </div>
      <details class="mt-3">
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
</div>
