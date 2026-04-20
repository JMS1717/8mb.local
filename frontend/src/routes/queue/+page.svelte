<script lang="ts">
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

  async function clearQueue() {
    if (!confirm('Clear all jobs from the queue? This will cancel running jobs and remove all entries.')) {
      return;
    }
    
    try {
      const res = await fetch('/api/queue/clear', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await res.json();
      alert(`Queue cleared: ${result.cancelled} job(s) cancelled, ${result.removed} total removed`);
      // Refresh queue status
      await fetchQueueStatus();
    } catch (e: any) {
      alert(`Clear queue failed: ${e.message || e}`);
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
    
    // Running state - show phase with indicator
    switch (job.phase) {
      case 'encoding':
        return '🎬 Encoding video (RUNNING NOW)';
      case 'finalizing':
        return '⚙️ Finalizing output (RUNNING NOW)';
      case 'done':
        return '✅ Complete';
      default:
        return '▶️ Processing (RUNNING NOW)';
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

<div class="page-container">
  <div class="page-header">
    <h1 class="page-title">Compression Queue</h1>
    <div class="header-actions">
      {#if queueStatus.active_jobs.length > 0}
        <button 
          on:click={clearQueue}
          class="btn btn-danger"
        >
          🗑️ Clear Queue
        </button>
      {/if}
      <a href="/" data-sveltekit-reload class="btn alt">🏠 Home</a>
      <a href="/advanced" class="btn alt">⚙️ Advanced</a>
      <a href="/batch" class="btn alt">🗂 Batch</a>
      <a href="/history" class="btn alt">📜 History</a>
      <a href="/settings" class="btn alt">⚙️ Settings</a>
    </div>
  </div>

  <!-- Queue Summary -->
  <div class="card">
    <h2 class="card-title">Queue Summary</h2>
    <div class="grid grid-3">
      <div class="stat-box">
        <div class="stat-val text-yellow">{queueStatus.queued_count}</div>
        <div class="stat-label">Queued</div>
      </div>
      <div class="stat-box">
        <div class="stat-val text-blue">{queueStatus.running_count}</div>
        <div class="stat-label">Running</div>
      </div>
      <div class="stat-box">
        <div class="stat-val text-green">{queueStatus.completed_count}</div>
        <div class="stat-label">Completed (1h)</div>
      </div>
    </div>
  </div>

  {#if loading}
    <div class="card empty-state">
      <p class="text-gray-400">Loading queue...</p>
    </div>
  {:else if error}
    <div class="card border-red-500">
      <p class="text-red-400">{error}</p>
      <button class="btn mt-2" on:click={fetchQueueStatus}>Retry</button>
    </div>
  {:else if queueStatus.active_jobs.length === 0}
    <div class="card empty-state">
      <p class="text-gray-400">No active jobs. <a href="/" class="text-blue-400 underline">Start a compression</a></p>
    </div>
  {:else}
    <!-- Active Jobs List -->
    <div class="job-list">
      {#each queueStatus.active_jobs as job (job.task_id)}
          <div class="job-item {job.state === 'running' ? 'running' : ''}">
          <div>
            <div>
              <!-- Job header -->
              <div class="job-header">
                  {#if job.state === 'running'}
                    <span>⚡</span>
                  {/if}
                <span class="text-lg">{getStateIcon(job.state)}</span>
                <span class="job-status-badge {job.state}">{job.state}</span>
                <span class="text-gray">•</span>
                  <span style="font-weight: 500;">{getPhaseDisplay(job)}</span>
                <span class="text-gray">•</span>
                <span class="job-name">{job.filename}</span>
              </div>
              
              <!-- Progress bar for running/queued -->
              {#if job.state === 'running' || job.state === 'queued'}
                <div class="progress-wrap">
                  <div class="progress-text">
                    <span>{job.progress.toFixed(1)}%</span>
                    {#if job.state === 'running'}
                      <span class="flex gap-3">
                        <span>Elapsed: {formatDuration(job.started_at, undefined)}</span>
                        <span class="text-blue-300">{formatTimeEstimate(job)}</span>
                      </span>
                    {/if}
                  </div>
                  <div class="progress-bar-bg">
                    <div class="progress-bar-fill" style={`width:${job.progress}%`}></div>
                  </div>
                </div>
              {/if}

              <!-- Job details -->
              <div class="job-details">
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
                <div class="error-block">
                  {job.error}
                </div>
              {/if}

              <!-- Expandable logs -->
              {#if job.state === 'running'}
                <button 
                  class="logs-toggle"
                  on:click={() => toggleJobExpansion(job.task_id)}
                >
                  {expandedJobs.has(job.task_id) ? '▼ Hide logs' : '▶ Show live logs'}
                </button>
                {#if expandedJobs.has(job.task_id)}
                  <div class="logs-box">
                    <div>{(jobLogs.get(job.task_id) || ['Connecting to live stream...']).join('\n')}</div>
                  </div>
                {/if}
              {/if}
            </div>

            <!-- Actions -->
            <div style="margin-top: 12px;">
              {#if job.state === 'completed' && job.progress >= 100 && job.output_path}
                <a 
                  class="btn"
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
  .page-container { max-width: 1200px; margin: 40px auto; padding: 0 20px; }
  .page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
  .page-title { font-size: 28px; font-weight: 600; margin: 0; }
  .header-actions { display: flex; gap: 12px; justify-content: flex-end; flex-wrap: wrap; }
  
  .card { background-color: var(--bg-card); border: 1px solid var(--glass-border); border-radius: var(--border-radius); padding: 20px; margin-bottom: 20px; }
  .card-title { font-size: 18px; font-weight: 600; margin-bottom: 12px; }
  
  .grid { display: grid; gap: 16px; }
  .grid-3 { grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); }
  .grid-2 { grid-template-columns: 1fr 1fr; }
  
  .stat-box { text-align: center; padding: 16px; background: var(--bg-hover); border-radius: 8px; }
  .stat-val { font-size: 24px; font-weight: 700; }
  .stat-label { font-size: 13px; color: var(--text-muted); }
  
  .job-list { display: flex; flex-direction: column; gap: 16px; }
  .job-item { background: var(--bg-card); border: 1px solid var(--glass-border); border-radius: var(--border-radius); padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  .job-item.running { border-color: rgba(59, 130, 246, 0.5); background: rgba(59, 130, 246, 0.05); }
  
  .job-header { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .job-status-badge { padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; text-transform: uppercase; }
  .job-status-badge.queued { background: rgba(250, 204, 21, 0.1); color: #facc15; }
  .job-status-badge.running { background: rgba(96, 165, 250, 0.1); color: #60a5fa; }
  .job-status-badge.completed { background: rgba(74, 222, 128, 0.1); color: #4ade80; }
  .job-status-badge.failed { background: rgba(248, 113, 113, 0.1); color: #f87171; }
  .job-name { font-size: 14px; color: var(--text-secondary); word-break: break-all; }
  
  .progress-wrap { margin: 8px 0; }
  .progress-text { display: flex; justify-content: space-between; font-size: 12px; color: var(--text-muted); margin-bottom: 4px; }
  .progress-bar-bg { width: 100%; height: 6px; background: var(--bg-hover); border-radius: 3px; overflow: hidden; }
  .progress-bar-fill { height: 100%; background: var(--accent); transition: width 0.3s ease; }
  
  .job-details { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; font-size: 12px; color: var(--text-secondary); margin-top: 8px; padding-top: 12px; border-top: 1px solid var(--glass-border); }
  
  .text-yellow { color: #facc15; }
  .text-blue { color: #60a5fa; }
  .text-green { color: #4ade80; }
  .text-red { color: #f87171; }
  .text-gray { color: var(--text-muted); }
  
  .error-block { padding: 8px 12px; background: rgba(239, 68, 68, 0.1); border-left: 3px solid #ef4444; color: #fca5a5; font-size: 13px; margin-top: 8px; border-radius: 0 4px 4px 0; }
  
  .logs-toggle { background: transparent; border: none; color: var(--accent); font-size: 12px; cursor: pointer; padding: 0; text-decoration: underline; margin-top: 8px; }
  .logs-box { margin-top: 8px; padding: 12px; background: #000; border-radius: 6px; max-height: 200px; overflow-y: auto; font-family: monospace; font-size: 11px; color: #a1a1aa; white-space: pre-wrap; word-break: break-all; }
  
  .btn-danger { background: #dc2626; color: white; }
  .btn-danger:hover { background: #b91c1c; }
  
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 12px; font-size: 13px; color: var(--text-muted); border-bottom: 1px solid var(--glass-border); }
  td { padding: 16px 12px; font-size: 14px; border-bottom: 1px solid var(--glass-border); color: var(--text-secondary); }
  tr:last-child td { border-bottom: none; }
  
  .empty-state { text-align: center; padding: 40px; color: var(--text-muted); }
  .empty-state a { color: var(--accent); text-decoration: underline; }
</style>

