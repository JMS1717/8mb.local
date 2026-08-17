<script lang="ts">
	import { onMount } from 'svelte';
	import { downloadUrl, openProgressStream } from '$lib/api';
	import {
		ACTIVE_JOB_CHANGED_EVENT,
		autoDownloadOnce,
		clearActiveJobId,
		getActiveJobId,
	} from '$lib/activeJob';

	let monitor: EventSource | null = null;
	let monitoredTaskId: string | null = null;

	function stopMonitoring(): void {
		try { monitor?.close(); } catch {}
		monitor = null;
		monitoredTaskId = null;
	}

	function monitorJob(taskId: string | null): void {
		if (!taskId || taskId === monitoredTaskId) return;
		stopMonitoring();
		monitoredTaskId = taskId;
		const stream = openProgressStream(taskId);
		monitor = stream;
		stream.onmessage = (event) => {
			if (monitoredTaskId !== taskId) return;
			try {
				const data = JSON.parse(event.data);
				if (data.type === 'done') {
				void autoDownloadOnce(taskId, downloadUrl(taskId));
					clearActiveJobId(taskId);
					stopMonitoring();
				} else if (data.type === 'error' || data.type === 'canceled') {
					clearActiveJobId(taskId);
					stopMonitoring();
				}
			} catch {
				// Ignore malformed progress frames; the stream can continue.
			}
		};
		// EventSource reconnects automatically. Do not clear the persisted job on
		// transient Wi-Fi, navigation, or backend restart failures.
	}

	onMount(() => {
		monitorJob(getActiveJobId());
		const handleActiveJobChange = (event: Event) => {
			const taskId = (event as CustomEvent<{ taskId: string | null }>).detail?.taskId ?? null;
			if (taskId) monitorJob(taskId);
			else stopMonitoring();
		};
		window.addEventListener(ACTIVE_JOB_CHANGED_EVENT, handleActiveJobChange);
		return () => {
			window.removeEventListener(ACTIVE_JOB_CHANGED_EVENT, handleActiveJobChange);
			stopMonitoring();
		};
	});
</script>

<slot />
