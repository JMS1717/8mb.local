/**
 * SSE (Server-Sent Events) connection management.
 *
 * Wraps EventSource creation and provides typed event shapes matching the
 * backend's Redis pub/sub messages.  The actual event-handling callbacks
 * remain in the consuming component so that component-local state stays
 * colocated with its UI.
 */

import { env } from '$env/dynamic/public';
import type { CompressStats } from './types';

const RAW = (env.PUBLIC_BACKEND_URL as string | undefined) || '';
const BACKEND = RAW && RAW.trim() !== '' ? RAW.replace(/\/$/, '') : '';

// ---------------------------------------------------------------------------
// SSE event payload types (mirror the backend JSON shapes exactly)
// ---------------------------------------------------------------------------

export interface SSEConnectedEvent {
	type: 'connected';
	task_id: string;
	ts: number;
}

export interface SSEProgressEvent {
	type: 'progress';
	progress: number;
	phase?: 'encoding' | 'finalizing' | 'done';
	eta_seconds?: number;
	speed_x?: number;
}

export interface SSELogEvent {
	type: 'log';
	message: string;
}

export interface SSEDoneEvent {
	type: 'done';
	stats: CompressStats;
}

export interface SSEErrorEvent {
	type: 'error';
	message: string;
}

export interface SSERetryEvent {
	type: 'retry';
	message: string;
	overage_percent?: number;
}

export interface SSECanceledEvent {
	type: 'canceled';
}

export interface SSEPingEvent {
	type: 'ping';
	ts: number;
}

export type SSEEvent =
	| SSEConnectedEvent
	| SSEProgressEvent
	| SSELogEvent
	| SSEDoneEvent
	| SSEErrorEvent
	| SSERetryEvent
	| SSECanceledEvent
	| SSEPingEvent;

// ---------------------------------------------------------------------------
// Connection helpers
// ---------------------------------------------------------------------------

/**
 * Open an SSE progress stream for a given task.
 *
 * Returns a native `EventSource`.  The caller attaches `onmessage` /
 * `onerror` handlers and is responsible for calling `.close()`.
 */
export function openProgressStream(
	taskId: string,
	auth?: { user: string; pass: string }
): EventSource {
	let sseUrl: string;
	if (BACKEND) {
		sseUrl = `${BACKEND}/api/stream/${taskId}`;
	} else {
		sseUrl = `/api/stream/${taskId}`;
	}

	if (auth) {
		const authParam = btoa(`${auth.user}:${auth.pass}`);
		sseUrl += `?auth=${encodeURIComponent(authParam)}`;
	}

	console.log('Opening SSE connection to:', sseUrl);
	return new EventSource(sseUrl);
}

/**
 * Type-safe helper to parse an SSE `MessageEvent.data` string into an
 * `SSEEvent`.  Returns `null` when the payload cannot be parsed.
 */
export function parseSSEData(raw: string): SSEEvent | null {
	try {
		return JSON.parse(raw) as SSEEvent;
	} catch {
		return null;
	}
}
