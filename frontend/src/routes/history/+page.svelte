<script lang="ts">
	import { onMount } from 'svelte';

	interface HistoryEntry {
		timestamp: string;
		filename: string;
		original_size_mb: number;
		compressed_size_mb: number;
		reduction_percent: number;
		video_codec: string;
		audio_codec: string;
		target_mb: number;
		preset: string;
		duration_seconds: number;
		task_id: string;
		container?: string;
		tune?: string;
		audio_bitrate_kbps?: number;
		max_width?: number;
		max_height?: number;
		start_time?: string;
		end_time?: string;
		encoder?: string;
		// Output mediainfo
		output_video_bitrate_kbps?: number;
		output_audio_bitrate_kbps?: number;
		output_width?: number;
		output_height?: number;
		output_duration_s?: number;
		output_video_codec?: string;
		output_audio_codec?: string;
		output_size_bytes?: number;
		compression_speed_x?: number;
		encoding_time_s?: number;
	}

	let entries: HistoryEntry[] = [];
	let historyEnabled = false;
	let loading = true;
	let error = '';

	async function fetchHistory() {
		loading = true;
		error = '';
		try {
			const response = await fetch('/api/history');
			if (!response.ok) throw new Error('Failed to fetch history');
			const data = await response.json();
			entries = data.entries || [];
			historyEnabled = data.enabled;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Unknown error';
		} finally {
			loading = false;
		}
	}

	async function clearAllHistory() {
		if (!confirm('Clear all compression history?')) return;
		try {
			const response = await fetch('/api/history', { method: 'DELETE' });
			if (!response.ok) throw new Error('Failed to clear history');
			entries = [];
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to clear history';
		}
	}

	async function deleteEntry(taskId: string) {
		try {
			const response = await fetch(`/api/history/${taskId}`, { method: 'DELETE' });
			if (!response.ok) throw new Error('Failed to delete entry');
			entries = entries.filter((e) => e.task_id !== taskId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to delete entry';
		}
	}

	function formatDate(isoString: string): string {
		return new Date(isoString).toLocaleString();
	}

	function formatDuration(seconds: number): string {
		if (seconds < 60) return `${seconds.toFixed(1)}s`;
		const minutes = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${minutes}m ${secs}s`;
	}

	function formatBytes(bytes: number): string {
		if (bytes < 1024) return `${bytes} B`;
		const kb = bytes / 1024;
		if (kb < 1024) return `${kb.toFixed(1)} KB`;
		const mb = kb / 1024;
		if (mb < 1024) return `${mb.toFixed(2)} MB`;
		return `${(mb / 1024).toFixed(2)} GB`;
	}

	function formatBitrate(kbps: number): string {
		if (kbps >= 1000) return `${(kbps / 1000).toFixed(1)} Mbps`;
		return `${Math.round(kbps)} kbps`;
	}

	function downloadUrl(taskId: string): string {
		return `/api/jobs/${encodeURIComponent(taskId)}/download`;
	}

	onMount(fetchHistory);
</script>

<svelte:head>
	<title>History</title>
	<link rel="preconnect" href="https://fonts.googleapis.com">
	<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="anonymous">
	<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
</svelte:head>

<div class="container">
	<header class="header">
		<div class="header-left">
			<h1 class="logo">📜 History</h1>
			<p class="tagline">Compression job history</p>
		</div>
		<div class="header-actions" style="display:flex; gap:8px; flex-wrap:wrap; justify-content: flex-end;">
			<a href="/" data-sveltekit-reload class="btn alt">🏠 Home</a>
			<a href="/advanced" class="btn alt">⚙️ Advanced</a>
			<a href="/batch" class="btn alt">🗂 Batch</a>
			<a href="/queue" class="btn alt">📋 Queue</a>
			<a href="/settings" class="btn alt">⚙️ Settings</a>
		</div>
	</header>

	{#if loading}
		<div class="card center-card fade-in">
			<div class="empty-icon">⏳</div>
			<p class="empty-text">Loading history…</p>
		</div>
	{:else if error}
		<div class="card center-card error-card fade-in">
			<p>{error}</p>
		</div>
	{:else if !historyEnabled}
		<div class="card center-card fade-in">
			<div class="empty-icon">📊</div>
			<p class="empty-text"><strong>History tracking is disabled</strong></p>
			<p class="empty-subtext">Enable it in <a href="/settings" class="accent-link">Settings</a> to track your compression jobs.</p>
		</div>
	{:else if entries.length === 0}
		<div class="card center-card fade-in">
			<div class="empty-icon">🎬</div>
			<p class="empty-text">No compression history yet</p>
			<p class="empty-subtext">Complete a compression to see it here.</p>
		</div>
	{:else}
		<div class="list-header">
			<span class="entry-count">{entries.length} {entries.length === 1 ? 'entry' : 'entries'}</span>
			<button class="btn-danger" on:click={clearAllHistory}>🗑️ Clear All</button>
		</div>

		<div class="history-list">
			{#each entries as entry}
				<div class="card entry-card fade-in">
					<!-- Top Row: File info + stats -->
					<div class="entry-top">
						<div class="entry-file">
							<span class="filename">{entry.filename}</span>
							<span class="timestamp">{formatDate(entry.timestamp)}</span>
						</div>
						<div class="entry-stats">
							<div class="stat">
								<span class="stat-value">{entry.original_size_mb.toFixed(1)}</span>
								<span class="stat-label">MB in</span>
							</div>
							<span class="stat-arrow">→</span>
							<div class="stat">
								<span class="stat-value">{entry.compressed_size_mb.toFixed(2)}</span>
								<span class="stat-label">MB out</span>
							</div>
							<div class="stat reduction">
								<span class="stat-value">-{entry.reduction_percent.toFixed(0)}%</span>
							</div>
						</div>
					</div>

					<!-- Tags Row -->
					<div class="tags-row">
						<span class="tag">{entry.encoder || entry.video_codec}</span>
						<span class="tag">{entry.audio_codec}</span>
						<span class="tag">{entry.preset}</span>
						{#if entry.container}<span class="tag">{entry.container}</span>{/if}
						{#if entry.encoding_time_s}
							<span class="tag time-tag">⏱ {formatDuration(entry.encoding_time_s)}</span>
						{:else}
							<span class="tag time-tag">⏱ {formatDuration(entry.duration_seconds)}</span>
						{/if}
						{#if entry.compression_speed_x}
							<span class="tag speed-tag">⚡ {entry.compression_speed_x.toFixed(1)}x</span>
						{/if}
					</div>

					<!-- Media Info (expandable) -->
					{#if entry.output_width || entry.output_video_bitrate_kbps || entry.output_size_bytes}
						<details class="mediainfo">
							<summary class="mediainfo-toggle">📋 Output Media Info</summary>
							<div class="mediainfo-grid">
								{#if entry.output_width && entry.output_height}
									<div class="mi-item">
										<span class="mi-label">Resolution</span>
										<span class="mi-value">{entry.output_width}×{entry.output_height}</span>
									</div>
								{/if}
								{#if entry.output_video_bitrate_kbps}
									<div class="mi-item">
										<span class="mi-label">Video Bitrate</span>
										<span class="mi-value">{formatBitrate(entry.output_video_bitrate_kbps)}</span>
									</div>
								{/if}
								{#if entry.output_audio_bitrate_kbps}
									<div class="mi-item">
										<span class="mi-label">Audio Bitrate</span>
										<span class="mi-value">{formatBitrate(entry.output_audio_bitrate_kbps)}</span>
									</div>
								{/if}
								{#if entry.output_video_codec}
									<div class="mi-item">
										<span class="mi-label">Video Codec</span>
										<span class="mi-value">{entry.output_video_codec}</span>
									</div>
								{/if}
								{#if entry.output_audio_codec}
									<div class="mi-item">
										<span class="mi-label">Audio Codec</span>
										<span class="mi-value">{entry.output_audio_codec}</span>
									</div>
								{/if}
								{#if entry.output_size_bytes}
									<div class="mi-item">
										<span class="mi-label">Size on Disk</span>
										<span class="mi-value">{formatBytes(entry.output_size_bytes)}</span>
									</div>
								{/if}
								{#if entry.output_duration_s}
									<div class="mi-item">
										<span class="mi-label">Duration</span>
										<span class="mi-value">{formatDuration(entry.output_duration_s)}</span>
									</div>
								{/if}
								{#if entry.audio_bitrate_kbps}
									<div class="mi-item">
										<span class="mi-label">Target Audio</span>
										<span class="mi-value">{entry.audio_bitrate_kbps} kbps</span>
									</div>
								{/if}
							</div>
						</details>
					{/if}

					<!-- Actions -->
					<div class="entry-actions">
						<a class="btn-download" href={downloadUrl(entry.task_id)} target="_blank" rel="noopener">⬇️ Download</a>
						<button class="btn-delete" on:click={() => deleteEntry(entry.task_id)}>🗑️</button>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>


<style>
  /* Layout */
  .container { max-width: 900px; margin: 40px auto; padding: 0 20px; }

  /* Header */
  .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 32px; }
  .header-left { display: flex; flex-direction: column; gap: 4px; }
  .logo { font-size: 28px; font-weight: 600; margin: 0; }
  .tagline { font-size: 14px; color: var(--text-secondary); margin: 0; }
  .header-actions { display: flex; gap: 12px; align-items: center; }
  .nav-link { color: var(--text-secondary); text-decoration: none; font-size: 14px; font-weight: 500; padding: 8px 16px; border: 1px solid var(--glass-border); border-radius: 6px; transition: all var(--transition-fast); }
  .nav-link:hover { color: var(--text-primary); background-color: var(--bg-hover); border-color: var(--accent); }

  /* List header */
  .list-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .entry-count { font-size: 14px; color: var(--text-secondary); font-weight: 500; }
  .btn-danger { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 6px; padding: 8px 16px; font-size: 13px; font-weight: 500; cursor: pointer; transition: all var(--transition-fast); }
  .btn-danger:hover { background: #dc2626; color: white; border-color: #dc2626; }

  /* Cards */
  .card { background-color: var(--bg-card); border: 1px solid var(--glass-border); border-radius: var(--border-radius); padding: 20px; animation: fadeIn 0.3s ease; }
  .center-card { text-align: center; padding: 48px 20px; }
  .error-card { border-color: rgba(239, 68, 68, 0.3); }

  /* Empty states */
  .empty-icon { font-size: 2.5rem; margin-bottom: 12px; }
  .empty-text { font-size: 16px; color: var(--text-primary); margin: 0 0 8px; }
  .empty-subtext { font-size: 14px; color: var(--text-secondary); margin: 0; }
  .accent-link { color: var(--accent); text-decoration: underline; }

  /* History list */
  .history-list { display: flex; flex-direction: column; gap: 16px; }
  .entry-card { display: flex; flex-direction: column; gap: 14px; }

  /* Entry top row */
  .entry-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; }
  .entry-file { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; }
  .filename { font-weight: 600; font-size: 15px; color: var(--text-primary); word-break: break-all; }
  .timestamp { font-size: 12px; color: var(--text-muted); }

  /* Stats row */
  .entry-stats { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
  .stat { display: flex; flex-direction: column; align-items: center; gap: 2px; }
  .stat-value { font-size: 16px; font-weight: 700; color: var(--text-primary); }
  .stat-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .stat-arrow { color: var(--text-muted); font-size: 16px; }
  .reduction .stat-value { color: var(--success); }

  /* Tags */
  .tags-row { display: flex; flex-wrap: wrap; gap: 6px; }
  .tag { display: inline-block; padding: 4px 10px; font-size: 12px; font-weight: 500; background: var(--bg-hover); border: 1px solid var(--glass-border); border-radius: 20px; color: var(--text-secondary); }
  .time-tag { color: var(--warning); border-color: rgba(245, 158, 11, 0.2); }
  .speed-tag { color: var(--accent); border-color: rgba(99, 102, 241, 0.2); }

  /* Media info expandable */
  .mediainfo { margin-top: 4px; }
  .mediainfo-toggle { font-size: 13px; color: var(--text-secondary); cursor: pointer; padding: 6px 0; list-style: none; }
  .mediainfo-toggle:hover { color: var(--accent); }
  .mediainfo-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-top: 12px; padding: 14px; background: var(--bg-hover); border-radius: 8px; border: 1px solid var(--glass-border); }
  .mi-item { display: flex; flex-direction: column; gap: 2px; }
  .mi-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .mi-value { font-size: 14px; font-weight: 500; color: var(--text-primary); }

  /* Entry actions */
  .entry-actions { display: flex; gap: 8px; align-items: center; padding-top: 12px; border-top: 1px solid var(--glass-border); }
  .btn-download { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; font-size: 13px; font-weight: 500; color: white; background: var(--accent); border-radius: 6px; text-decoration: none; transition: background var(--transition-fast); }
  .btn-download:hover { background: var(--accent-hover); }
  .btn-delete { background: transparent; border: 1px solid var(--glass-border); color: var(--text-muted); border-radius: 6px; padding: 8px 12px; font-size: 13px; cursor: pointer; transition: all var(--transition-fast); }
  .btn-delete:hover { background: rgba(239, 68, 68, 0.15); color: #f87171; border-color: rgba(239, 68, 68, 0.3); }

  /* Animation */
  .fade-in { animation: fadeIn 0.3s ease; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
</style>

