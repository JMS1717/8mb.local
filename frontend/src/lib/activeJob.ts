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

/**
 * Start a normal browser/WebView2 download from the server URL.
 *
 * Do not fetch the response into a Blob first.  WebView2 does not reliably
 * route synthetic `blob:` links to the Windows download handler, while a
 * direct URL lets it honor the server's Content-Disposition filename and
 * stream the output without duplicating it in the WebView process.
 */
export async function downloadFile(url: string, fallbackName?: string): Promise<void> {
	triggerBrowserDownload(url, fallbackName);
}

export function triggerBrowserDownload(url: string, fallbackName?: string): void {
	if (typeof document === 'undefined') return;
	const link = document.createElement('a');
	link.href = url;
	link.download = fallbackName || '';
	link.style.display = 'none';
	document.body.appendChild(link);
	link.click();
	link.remove();
}

export async function autoDownloadOnce(taskId: string, url: string): Promise<boolean> {
	if (typeof window === 'undefined') return false;
	try {
		if (window.localStorage.getItem('autoDownload') !== 'true') return false;
		if (window.localStorage.getItem(LAST_AUTO_DOWNLOAD_KEY) === taskId) return false;
		await downloadFile(url);
		// Record only after a non-empty HTTP response was received. A failed
		// WebView download can therefore be retried after the job is complete.
		window.localStorage.setItem(LAST_AUTO_DOWNLOAD_KEY, taskId);
	} catch {
		return false;
	}
	return true;
}
