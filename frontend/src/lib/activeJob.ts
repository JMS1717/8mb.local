export const ACTIVE_JOB_KEY = 'activeJobId';
export const ACTIVE_JOB_CHANGED_EVENT = '8mblocal:active-job-changed';
export const LAST_AUTO_DOWNLOAD_KEY = 'lastAutoDownloadedTaskId';

export function getActiveJobId(): string | null {
	if (typeof window === 'undefined') return null;
	try {
		return window.localStorage.getItem(ACTIVE_JOB_KEY);
	} catch {
		return null;
	}
}

export function setActiveJobId(taskId: string): void {
	if (typeof window === 'undefined') return;
	try {
		window.localStorage.setItem(ACTIVE_JOB_KEY, taskId);
	} catch {
		// The in-memory page tracker still works when storage is unavailable.
	}
	window.dispatchEvent(new CustomEvent(ACTIVE_JOB_CHANGED_EVENT, { detail: { taskId } }));
}

export function clearActiveJobId(taskId?: string): void {
	if (typeof window === 'undefined') return;
	let cleared = !taskId;
	try {
		const current = window.localStorage.getItem(ACTIVE_JOB_KEY);
		if (!taskId || current === taskId) {
			window.localStorage.removeItem(ACTIVE_JOB_KEY);
			cleared = true;
		}
	} catch {
		// If storage is unavailable, the caller's terminal event is authoritative.
		cleared = true;
	}
	if (cleared) {
		window.dispatchEvent(new CustomEvent(ACTIVE_JOB_CHANGED_EVENT, { detail: { taskId: null } }));
	}
}

export function triggerBrowserDownload(url: string): void {
	if (typeof document === 'undefined') return;
	const link = document.createElement('a');
	link.href = url;
	link.download = '';
	link.style.display = 'none';
	document.body.appendChild(link);
	link.click();
	link.remove();
}

export function autoDownloadOnce(taskId: string, url: string): boolean {
	if (typeof window === 'undefined') return false;
	try {
		if (window.localStorage.getItem('autoDownload') !== 'true') return false;
		if (window.localStorage.getItem(LAST_AUTO_DOWNLOAD_KEY) === taskId) return false;
		// Record before clicking so simultaneous SSE listeners cannot download twice.
		window.localStorage.setItem(LAST_AUTO_DOWNLOAD_KEY, taskId);
	} catch {
		return false;
	}
	triggerBrowserDownload(url);
	return true;
}
