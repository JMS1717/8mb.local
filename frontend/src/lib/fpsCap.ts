/**
 * Allowed max-output FPS caps (must stay in sync with worker/API max_output_fps).
 */
export const FPS_CAP_VALUES = ['24', '25', '30', '50', '60', '72', '90', '100', '120'] as const;

export type FpsCapChoice = (typeof FPS_CAP_VALUES)[number];

/** Empty string = same as input (no cap). */
export type FpsCap = '' | FpsCapChoice;

const AS_STRING = FPS_CAP_VALUES as readonly string[];

/** Restore from localStorage; returns null if missing or unknown legacy value. */
export function parseStoredFpsCap(raw: string | null): FpsCap | null {
	if (raw === null || raw === undefined) return null;
	if (raw === '') return '';
	if (AS_STRING.includes(raw)) return raw as FpsCapChoice;
	return null;
}

/** Map server preset max_output_fps to UI cap (only listed values). */
export function maxFpsFromProfile(max_output_fps: number | null | undefined): FpsCap {
	const v = max_output_fps;
	if (v == null || !(Number(v) > 0)) return '';
	const s = String(Math.round(Number(v)));
	return AS_STRING.includes(s) ? (s as FpsCapChoice) : '';
}
