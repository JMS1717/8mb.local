<script lang="ts">
  import '../../app.css';
  import { onMount, onDestroy } from 'svelte';
  
  interface Job {
    task_id: string;
    job_id: string;
    filename: string;
    target_size_mb: number;
    video_codec: string;
    state: 'queued' | 'running' | 'completed' | 'failed' | 'canceled';
    progress: number;
    phase?: 'queued' | 'encoding' | 'finalizing' | 'done';
    created_at: number;
    started_at?: number;
    completed_at?: number;
    error?: string;
    output_path?: string;
    final_size_mb?: number;
    last_progress_update?: number;
    estimated_completion_time?: number;
  }

  interface QueueStatus {
    active_jobs: Job[];
    queued_count: number;
    running_count: number;
    completed_count: number;
  }

  let queueStatus: QueueStatus = {
    active_jobs: [],
    queued_count: 0,
    running_count: 0,
    completed_count: 0
  };
  let loading = true;
  let error: string | null = null;
  let pollInterval: any = null;

  // SSE connections for each running job
  let sseConnections: Map<string, EventSource> = new Map();
  let jobLogs: Map<string, string[]> = new Map();
  let expandedJobs: Set<string> = new Set();

  function toggleJobExpansion(taskId: string) {
    if (expandedJobs.has(taskId)) {
      expandedJobs.delete(taskId);
    } else {
      expandedJobs.add(taskId);
      // Start SSE if running and not already connected
      const job = queueStatus.active_jobs.find(j => j.task_id === taskId);
      if (job && job.state === 'running' && !sseConnections.has(taskId)) {
        connectSSE(taskId);
      }
    }
    expandedJobs = expandedJobs;
  }

  function connectSSE(taskId: string) {
    if (sseConnections.has(taskId)) return;
    
    try {
      const es = new EventSource(`/api/stream/${taskId}`);
      sseConnections.set(taskId, es);
      
      if (!jobLogs.has(taskId)) {
        jobLogs.set(taskId, []);
      }

      es.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          const logs = jobLogs.get(taskId) || [];
          
          if (data.type === 'log' && data.message) {
            logs.push(data.message);
            jobLogs.set(taskId, logs.slice(-100)); // Keep last 100 lines
            jobLogs = jobLogs;
          } else if (data.type === 'progress') {
            // Update progress in real-time from SSE
            const job = queueStatus.active_jobs.find(j => j.task_id === taskId);
            if (job) {
              job.progress = data.progress || job.progress;
              job.phase = data.phase || job.phase;
              job.last_progress_update = Date.now() / 1000;
              queueStatus = queueStatus;
            }
          } else if (data.type === 'retry') {
            // Show retry notification in logs
            logs.push(`⚠️ Retry: File exceeded target by ${data.overage_percent?.toFixed(1)}%, adjusting bitrate...`);
            jobLogs.set(taskId, logs.slice(-100));
            jobLogs = jobLogs;
          } else if (data.type === 'done' || data.type === 'error' || data.type === 'canceled') {
            // Job finished, close connection
            setTimeout(() => {
              es.close();
              sseConnections.delete(taskId);
            }, 1000);
          }
        } catch {}
      };

      es.onerror = () => {
        es.close();
        sseConnections.delete(taskId);
      };
    } catch (e) {
      console.error('Failed to connect SSE:', e);
    }
  }

  function disconnectSSE(taskId: string) {
    const es = sseConnections.get(taskId);
    if (es) {
      try {
        es.close();
      } catch {}
      sseConnections.delete(taskId);
    }
  }

  async function fetchQueueStatus() {
    try {
      const res = await fetch('/api/queue/status');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      queueStatus = data;
      
      // Auto-connect SSE for ALL running jobs (not just expanded ones)
      for (const job of queueStatus.active_jobs) {
        if (job.state === 'running' && !sseConnections.has(job.task_id)) {
          connectSSE(job.task_id);
        }
      }
      
      // Disconnect SSE for jobs no longer running
      for (const [taskId, es] of sseConnections.entries()) {
        const job = queueStatus.active_jobs.find(j => j.task_id === taskId);
        if (!job || job.state !== 'running') {
          disconnectSSE(taskId);
        }
      }
      
      loading = false;
      error = null;
    } catch (e: any) {
      error = `Failed to load queue: ${e.message || e}`;
      loading = false;
    }
  }

  function formatTimestamp(ts?: number): string {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString();
  }

  function formatDuration(startTs?: number, endTs?: number): string {
    if (!startTs) return '—';
    const end = endTs || Date.now() / 1000;
    const sec = Math.round(end - startTs);
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  }

  function formatTimeEstimate(job: Job): string {
    if (job.state !== 'running' || !job.started_at || job.progress <= 0) return '—';
    
    const now = Date.now() / 1000;
    const elapsed = now - job.started_at;
    
    // If we have an estimated completion time from backend, use it
    if (job.estimated_completion_time && job.estimated_completion_time > now) {
      const remaining = Math.round(job.estimated_completion_time - now);
      const m = Math.floor(remaining / 60);
      const s = remaining % 60;
      return m > 0 ? `~${m}m ${s}s remaining` : `~${s}s remaining`;
    }
    
    // Fallback: calculate locally based on current progress
    if (job.progress < 100) {
      const estimatedTotal = elapsed / (job.progress / 100.0);
      const remaining = Math.max(0, Math.round(estimatedTotal - elapsed));
      const m = Math.floor(remaining / 60);
      const s = remaining % 60;
      return m > 0 ? `~${m}m ${s}s remaining` : `~${s}s remaining`;
    }
    
    return 'Finishing...';
  }

  function getPhaseDisplay(job: Job): string {
    if (job.state === 'completed') return '✅ Complete';
    if (job.state === 'failed') return '❌ Failed';
    if (job.state === 'canceled') return '🚫 Canceled';
    if (job.state === 'queued') return '⏳ Waiting in queue';
    
    // Running state - show phase
    switch (job.phase) {
      case 'encoding':
        return '🎬 Encoding video';
      case 'finalizing':
        return '⚙️ Finalizing output';
      case 'done':
        return '✅ Complete';
      default:
        return '▶️ Processing';
    }
  }

  function getStateColor(state: string): string {
    switch (state) {
      case 'queued': return 'text-yellow-400';
      case 'running': return 'text-blue-400';
      case 'completed': return 'text-green-400';
      case 'failed': return 'text-red-400';
      case 'canceled': return 'text-gray-400';
      default: return 'text-gray-400';
    }
  }

  function getStateIcon(state: string): string {
    switch (state) {
      case 'queued': return '⏳';
      case 'running': return '▶️';
      case 'completed': return '✅';
      case 'failed': return '❌';
      case 'canceled': return '🚫';
      default: return '❓';
    }
  }

  onMount(async () => {
    await fetchQueueStatus();
    pollInterval = setInterval(fetchQueueStatus, 2000);
  });

  onDestroy(() => {
    if (pollInterval) clearInterval(pollInterval);
    // Close all SSE connections
    for (const [taskId, es] of sseConnections.entries()) {
      try {
        es.close();
      } catch {}
    }
    sseConnections.clear();
  });
</script>

<div class="max-w-6xl mx-auto mt-8 space-y-6 p-4">
  <div class="flex items-center justify-between mb-4">
    <h1 class="text-2xl font-bold">Compression Queue</h1>
    <div class="flex gap-4">
      <a href="/" class="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors text-sm">
        ← Back to Compress
      </a>
      <a href="/history" class="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors text-sm">
        History
      </a>
    </div>
  </div>

  <!-- Queue Summary -->
  <div class="card">
    <h2 class="font-semibold mb-3">Queue Summary</h2>
    <div class="grid grid-cols-3 gap-4">
      <div class="text-center">
        <div class="text-2xl font-bold text-yellow-400">{queueStatus.queued_count}</div>
        <div class="text-sm text-gray-400">Queued</div>
      </div>
      <div class="text-center">
        <div class="text-2xl font-bold text-blue-400">{queueStatus.running_count}</div>
        <div class="text-sm text-gray-400">Running</div>
      </div>
      <div class="text-center">
        <div class="text-2xl font-bold text-green-400">{queueStatus.completed_count}</div>
        <div class="text-sm text-gray-400">Completed (1h)</div>
      </div>
    </div>
  </div>

  {#if loading}
    <div class="card text-center">
      <p class="text-gray-400">Loading queue...</p>
    </div>
  {:else if error}
    <div class="card border-red-500">
      <p class="text-red-400">{error}</p>
      <button class="btn mt-2" on:click={fetchQueueStatus}>Retry</button>
    </div>
  {:else if queueStatus.active_jobs.length === 0}
    <div class="card text-center">
      <p class="text-gray-400">No active jobs. <a href="/" class="text-blue-400 underline">Start a compression</a></p>
    </div>
  {:else}
    <!-- Active Jobs List -->
    <div class="space-y-4">
      {#each queueStatus.active_jobs as job (job.task_id)}
        <div class="card">
          <div class="flex items-start justify-between gap-4">
            <div class="flex-1 min-w-0">
              <!-- Job header -->
              <div class="flex items-center gap-2 mb-2">
                <span class="text-lg">{getStateIcon(job.state)}</span>
                <span class="font-semibold {getStateColor(job.state)} uppercase text-sm">{job.state}</span>
                <span class="text-xs text-gray-500">•</span>
                <span class="text-sm text-gray-300">{getPhaseDisplay(job)}</span>
                <span class="text-xs text-gray-500">•</span>
                <span class="text-sm text-gray-400 truncate">{job.filename}</span>
              </div>
              
              <!-- Progress bar for running/queued -->
              {#if job.state === 'running' || job.state === 'queued'}
                <div class="mb-2">
                  <div class="flex items-center justify-between text-xs text-gray-400 mb-1">
                    <span>{job.progress.toFixed(1)}%</span>
                    {#if job.state === 'running'}
                      <span class="flex gap-3">
                        <span>Elapsed: {formatDuration(job.started_at, undefined)}</span>
                        <span class="text-blue-300">{formatTimeEstimate(job)}</span>
                      </span>
                    {/if}
                  </div>
                  <div class="h-2 bg-gray-800 rounded">
                    <div class="h-2 bg-blue-600 rounded transition-all" style={`width:${job.progress}%`}></div>
                  </div>
                </div>
              {/if}

              <!-- Job details -->
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs text-gray-400">
                <div>Target: {job.target_size_mb} MB</div>
                <div>Codec: {job.video_codec}</div>
                <div>Created: {formatTimestamp(job.created_at)}</div>
                {#if job.started_at}
                  <div>Started: {formatTimestamp(job.started_at)}</div>
                {/if}
                {#if job.completed_at}
                  <div>Completed: {formatTimestamp(job.completed_at)}</div>
                {/if}
                {#if job.final_size_mb}
                  <div>Final: {job.final_size_mb.toFixed(2)} MB</div>
                {/if}
              </div>

              <!-- Error message -->
              {#if job.error}
                <div class="mt-2 p-2 bg-red-900/20 border border-red-600/30 rounded text-sm text-red-300">
                  {job.error}
                </div>
              {/if}

              <!-- Expandable logs -->
              {#if job.state === 'running'}
                <button 
                  class="mt-2 text-xs text-blue-400 underline"
                  on:click={() => toggleJobExpansion(job.task_id)}
                >
                  {expandedJobs.has(job.task_id) ? '▼ Hide logs' : '▶ Show live logs'}
                </button>
                {#if expandedJobs.has(job.task_id)}
                  <div class="mt-2 p-2 bg-black/30 rounded max-h-48 overflow-y-auto">
                    <pre class="text-xs text-gray-300 whitespace-pre-wrap">{(jobLogs.get(job.task_id) || ['Connecting to live stream...']).join('\n')}</pre>
                  </div>
                {/if}
              {/if}
            </div>

            <!-- Actions -->
            <div class="flex flex-col gap-2">
              {#if job.state === 'completed' && job.progress >= 100 && job.output_path}
                <a 
                  class="btn bg-green-600 hover:bg-green-700 text-white text-sm px-4 py-2"
                  href={`/api/jobs/${job.task_id}/download`}
                  target="_blank"
                >
                  ⬇️ Download
                </a>
              {/if}
            </div>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  /* Ensure logs scroll smoothly */
  pre {
    font-family: 'Courier New', monospace;
  }
</style>
