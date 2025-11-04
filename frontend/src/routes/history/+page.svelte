<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

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
		if (!confirm('Are you sure you want to clear all compression history?')) return;

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
		const date = new Date(isoString);
		return date.toLocaleString();
	}

	function formatDuration(seconds: number): string {
		if (seconds < 60) return `${seconds.toFixed(1)}s`;
		const minutes = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${minutes}m ${secs}s`;
	}

	onMount(fetchHistory);
</script>

<svelte:head>
	<title>Compression History - 8mb.local</title>
</svelte:head>

<div class="container">
	<div class="header">
		<h1>📜 Compression History</h1>
		<div class="actions">
			<button class="btn-secondary" on:click={() => goto('/settings')}>⚙️ Settings</button>
			<button class="btn-secondary" on:click={() => goto('/')}>🏠 Home</button>
		</div>
	</div>

	{#if loading}
		<div class="loading">Loading history...</div>
	{:else if error}
		<div class="error">{error}</div>
	{:else if !historyEnabled}
		<div class="info-box">
			<p>📊 History tracking is currently disabled.</p>
			<p>
				Enable it in <a href="/settings" class="link">Settings</a> to track your compression jobs.
			</p>
		</div>
	{:else if entries.length === 0}
		<div class="info-box">
			<p>No compression history yet.</p>
			<p>Complete a compression job to see it here!</p>
		</div>
	{:else}
		<div class="history-header">
			<p class="entry-count">{entries.length} {entries.length === 1 ? 'entry' : 'entries'}</p>
			<button class="btn-danger" on:click={clearAllHistory}>🗑️ Clear All</button>
		</div>

		<div class="history-list">
			{#each entries as entry}
				<div class="history-entry">
					<div class="entry-main">
						<div class="entry-info">
							<h3 class="filename">📹 {entry.filename}</h3>
							<p class="timestamp">🕐 {formatDate(entry.timestamp)}</p>
						</div>

						<div class="size-info">
							<div class="size-row">
								<span class="label">Original:</span>
								<span class="value">{entry.original_size_mb.toFixed(2)} MB</span>
							</div>
							<div class="size-row">
								<span class="label">Compressed:</span>
								<span class="value">{entry.compressed_size_mb.toFixed(2)} MB</span>
							</div>
							<div class="size-row reduction">
								<span class="label">Reduction:</span>
								<span class="value highlight"
									>{entry.reduction_percent.toFixed(1)}% smaller</span
								>
							</div>
						</div>

						<div class="codec-info">
							<div class="codec-row">
								<span class="label">Video:</span>
								<span class="badge">{entry.video_codec}</span>
							</div>
							<div class="codec-row">
								<span class="label">Audio:</span>
								<span class="badge">{entry.audio_codec}</span>
							</div>
							<div class="codec-row">
								<span class="label">Preset:</span>
								<span class="badge">{entry.preset}</span>
							</div>
							<div class="codec-row">
								<span class="label">Time:</span>
								<span class="badge">{formatDuration(entry.duration_seconds)}</span>
							</div>
						</div>
					</div>

					<button class="btn-delete" on:click={() => deleteEntry(entry.task_id)}>
						🗑️ Delete
					</button>
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	.container {
		max-width: 1200px;
		margin: 0 auto;
		padding: 2rem;
		color: #e5e7eb;
	}

	.header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 2rem;
		flex-wrap: wrap;
		gap: 1rem;
	}

	h1 {
		margin: 0;
		font-size: 2rem;
		font-weight: bold;
		color: white;
	}

	.actions {
		display: flex;
		gap: 0.5rem;
	}

	.btn-secondary,
	.btn-danger,
	.btn-delete {
		padding: 0.5rem 1rem;
		border: none;
		border-radius: 0.375rem;
		cursor: pointer;
		font-size: 0.875rem;
		font-weight: 500;
		transition: all 0.2s;
	}

	.btn-secondary {
		background-color: #4b5563;
		color: white;
	}

	.btn-secondary:hover {
		background-color: #374151;
	}

	.btn-danger {
		background-color: #ef4444;
		color: white;
	}

	.btn-danger:hover {
		background-color: #dc2626;
	}

	.btn-delete {
		background-color: #450a0a;
		color: #fca5a5;
		font-size: 0.75rem;
		padding: 0.375rem 0.75rem;
	}

	.btn-delete:hover {
		background-color: #7f1d1d;
	}

	.loading,
	.error {
		text-align: center;
		padding: 2rem;
		font-size: 1.125rem;
		color: #9ca3af;
	}

	.error {
		color: #ef4444;
	}

	.info-box {
		background-color: #1e3a5f;
		border: 1px solid #3b82f6;
		border-radius: 0.5rem;
		padding: 1.5rem;
		text-align: center;
		color: #bfdbfe;
	}

	.info-box p {
		margin: 0.5rem 0;
	}

	.link {
		color: #60a5fa;
		text-decoration: underline;
	}

	.history-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 1rem;
	}

	.entry-count {
		font-size: 0.875rem;
		color: #9ca3af;
		margin: 0;
	}

	.history-list {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.history-entry {
		background: #1f2937;
		border: 1px solid #374151;
		border-radius: 0.5rem;
		padding: 1.5rem;
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 1rem;
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
	}

	.entry-main {
		flex: 1;
		display: flex;
		gap: 2rem;
		flex-wrap: wrap;
	}

	.entry-info {
		flex: 1;
		min-width: 200px;
	}

	.filename {
		margin: 0 0 0.5rem 0;
		font-size: 1.125rem;
		font-weight: 600;
		word-break: break-word;
		color: white;
	}

	.timestamp {
		margin: 0;
		font-size: 0.875rem;
		color: #9ca3af;
	}

	.size-info,
	.codec-info {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		min-width: 150px;
	}

	.size-row,
	.codec-row {
		display: flex;
		justify-content: space-between;
		gap: 1rem;
		font-size: 0.875rem;
	}

	.label {
		color: #9ca3af;
		font-weight: 500;
	}

	.value {
		text-align: right;
		color: #d1d5db;
	}

	.highlight {
		color: #10b981;
		font-weight: 600;
	}

	.badge {
		background-color: #374151;
		padding: 0.125rem 0.5rem;
		border-radius: 0.25rem;
		font-size: 0.75rem;
		font-weight: 600;
		color: #d1d5db;
	}

	@media (max-width: 768px) {
		.container {
			padding: 1rem;
		}

		h1 {
			font-size: 1.5rem;
		}

		.entry-main {
			flex-direction: column;
			gap: 1rem;
		}

		.history-entry {
			flex-direction: column;
		}

		.btn-delete {
			align-self: flex-end;
		}
	}
</style>
