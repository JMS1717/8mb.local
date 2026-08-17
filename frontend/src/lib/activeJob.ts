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

function responseFilename(response: Response, fallback = '8mblocal-download') : string {
	const header = response.headers.get('content-disposition') || '';
	const encoded = header.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
	const plain = header.match(/filename="?([^";]+)"?/i)?.[1];
	try {
		const value = decodeURIComponent(encoded || plain || '');
		if (value) return value.replace(/[\\/:*?"<>|]+/g, '_');
	} catch {}
	return fallback;
}

/** Download through fetch so WebView2 checks the response and reuses auth. */
export async function downloadFile(url: string, fallbackName?: string): Promise<void> {
	const response = await fetch(url, { credentials: 'include', cache: 'no-store' });
	if (!response.ok) {
		throw new Error(`Download failed (HTTP ${response.status})`);
	}
	const advertisedLength = Number(response.headers.get('content-length') || 0);
	// Do not materialize a very large batch ZIP in the WebView process. The
	// normal browser download path streams it to disk; smaller outputs use the
	// checked fetch/blob path so Windows gets a reliable retryable download.
	if (advertisedLength > 512 * 1024 * 1024) {
		triggerBrowserDownload(url);
		return;
	}
	const blob = await response.blob();
	if (!blob.size) throw new Error('Download returned an empty file');
	const objectUrl = URL.createObjectURL(blob);
	const link = document.createElement('a');
	link.href = objectUrl;
	link.download = responseFilename(response, fallbackName);
	link.style.display = 'none';
	document.body.appendChild(link);
	link.click();
	link.remove();
	window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
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
