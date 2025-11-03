import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import clsx from "clsx";

const PRESETS = [
  { label: "8 MB", value: 8 },
  { label: "25 MB", value: 25 },
  { label: "50 MB", value: 50 },
  { label: "100 MB", value: 100 }
];

const VIDEO_CODECS = [
  { label: "AV1 (Best Quality)", value: "av1_nvenc" },
  { label: "HEVC", value: "hevc_nvenc" },
  { label: "H.264", value: "h264_nvenc" }
];

const AUDIO_CODECS = [
  { label: "Opus", value: "libopus" },
  { label: "AAC", value: "aac" }
];

const PRESETS_QUALITY = [
  { label: "Fast (P1)", value: "p1" },
  { label: "Balanced (P5)", value: "p5" },
  { label: "Best Quality (P7)", value: "p7" }
];

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

interface JobResponse {
  job_id: string;
  probe: {
    duration: number;
    bitrate_kbps: number;
    audio_bitrate_kbps: number;
    size_mb: number;
  };
  estimate: {
    target_total_bitrate_kbps: number;
    target_video_bitrate_kbps: number;
    warning?: string | null;
  };
  status: string;
}

interface JobResultPayload {
  status: string;
  output_path: string;
  output_size: number;
  output_basename: string;
}

export default function HomePage() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [targetSize, setTargetSize] = useState<number>(8);
  const [videoCodec, setVideoCodec] = useState("av1_nvenc");
  const [audioCodec, setAudioCodec] = useState("libopus");
  const [preset, setPreset] = useState("p7");
  const [audioBitrate, setAudioBitrate] = useState(128);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<string>("idle");
  const [progress, setProgress] = useState<number>(0);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [probe, setProbe] = useState<JobResponse["probe"] | null>(null);
  const [estimate, setEstimate] = useState<JobResponse["estimate"] | null>(null);
  const [result, setResult] = useState<JobResultPayload | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");

  const resolvedApiBase = useMemo(() => {
    if (API_BASE) {
      return API_BASE.replace(/\/$/, "");
    }
    if (typeof window !== "undefined") {
      return window.location.origin;
    }
    return "";
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const storedUser = window.localStorage.getItem("eightmblocal-basic-username");
    const storedPass = window.localStorage.getItem("eightmblocal-basic-password");
    if (storedUser) setAuthUsername(storedUser);
    if (storedPass) setAuthPassword(storedPass);
  }, []);

  const authConfig = useMemo(() => {
    if (!authUsername || !authPassword) return undefined;
    return { username: authUsername, password: authPassword };
  }, [authUsername, authPassword]);

  const websocketCredentials = useMemo(() => {
    if (!authUsername || !authPassword) return "";
    return `${encodeURIComponent(authUsername)}:${encodeURIComponent(authPassword)}@`;
  }, [authUsername, authPassword]);

  const resetState = useCallback(() => {
    setJobId(null);
    setJobStatus("idle");
    setProgress(0);
    setLogLines([]);
    setProbe(null);
    setEstimate(null);
    setResult(null);
    setWarning(null);
    setError(null);
  }, []);

  const handleFileChange = useCallback(
    (file: File | null) => {
      setSelectedFile(file);
      resetState();
    },
    [resetState]
  );

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLLabelElement>) => {
      event.preventDefault();
      if (event.dataTransfer.files && event.dataTransfer.files[0]) {
        handleFileChange(event.dataTransfer.files[0]);
      }
    },
    [handleFileChange]
  );

  const handleSaveCredentials = useCallback(() => {
    if (typeof window === "undefined") return;
    if (!authUsername || !authPassword) {
      window.localStorage.removeItem("eightmblocal-basic-username");
      window.localStorage.removeItem("eightmblocal-basic-password");
      return;
    }
    window.localStorage.setItem("eightmblocal-basic-username", authUsername);
    window.localStorage.setItem("eightmblocal-basic-password", authPassword);
  }, [authUsername, authPassword]);

  const handleClearCredentials = useCallback(() => {
    setAuthUsername("");
    setAuthPassword("");
    if (typeof window !== "undefined") {
      window.localStorage.removeItem("eightmblocal-basic-username");
      window.localStorage.removeItem("eightmblocal-basic-password");
    }
  }, []);

  const handleCompress = useCallback(async () => {
    if (!selectedFile) {
      setError("Please choose a video file first.");
      return;
    }
    try {
      setIsUploading(true);
      setError(null);
      const form = new FormData();
      form.append("file", selectedFile);
      form.append("target_size_mb", targetSize.toString());
      form.append("video_codec", videoCodec);
      form.append("audio_codec", audioCodec);
      form.append("preset", preset);
      form.append("audio_bitrate_kbps", audioBitrate.toString());

      const response = await axios.post<JobResponse>(`${resolvedApiBase}/api/jobs`, form, {
        headers: { "Content-Type": "multipart/form-data" },
        auth: authConfig,
      });

      setJobId(response.data.job_id);
      setProbe(response.data.probe);
      setEstimate(response.data.estimate);
      setJobStatus(response.data.status);
      setWarning(response.data.estimate.warning ?? null);
    } catch (uploadError: any) {
      setError(uploadError?.response?.data?.detail ?? "Upload failed. Check server logs.");
    } finally {
      setIsUploading(false);
    }
  }, [selectedFile, targetSize, videoCodec, audioCodec, preset, audioBitrate, resolvedApiBase, authConfig]);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    const base = resolvedApiBase || (typeof window !== "undefined" ? window.location.origin : "");
    if (!base) {
      return;
    }
    const protocol = base.startsWith("https") ? "wss" : "ws";
    const host = base.replace(/^https?:\/\//, "");
    const wsUrl = `${protocol}://${websocketCredentials}${host}/ws/jobs/${jobId}`;
    const ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "log") {
          setLogLines((lines) => [...lines.slice(-400), payload.payload.line]);
        }
        if (payload.type === "progress") {
          const ratio = payload.payload.ratio ?? 0;
          setProgress(Math.max(0, Math.min(1, ratio)));
        }
        if (payload.type === "status") {
          setJobStatus(payload.payload.status ?? "running");
        }
        if (payload.type === "result") {
          setResult(payload.payload as JobResultPayload);
        }
      } catch (err) {
        console.error("Failed to parse message", err);
      }
    };

    ws.onerror = () => {
      setError("WebSocket connection error. Progress updates may be unavailable.");
    };

    return () => {
      ws.close();
    };
  }, [jobId, resolvedApiBase, websocketCredentials]);

  const reductionSummary = useMemo(() => {
    if (!probe || !result) return null;
    const originalBytes = probe.size_mb * 1024 * 1024;
    const reduction = 1 - result.output_size / originalBytes;
    return {
      finalSizeMB: result.output_size / (1024 * 1024),
      reductionPercent: reduction * 100
    };
  }, [probe, result]);

  const primaryButtonDisabled = isUploading || !selectedFile || Boolean(result && jobStatus === "completed");

  return (
    <main className="min-h-screen flex items-center justify-center px-4 py-10">
      <div className="max-w-4xl w-full space-y-8">
        <header className="text-center space-y-2">
          <h1 className="text-4xl font-bold tracking-tight">8mb.local</h1>
          <p className="text-slate-400">Fire-and-forget GPU-accelerated video compression.</p>
        </header>

        <section className="bg-slate-900/60 border border-slate-800 rounded-3xl p-6 space-y-4">
          <h2 className="text-lg font-semibold text-slate-200">Access</h2>
          <p className="text-sm text-slate-400">
            Optional HTTP Basic credentials. Leave both fields blank to disable stored login.
          </p>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="flex flex-col gap-2 text-sm">
              <span>Username</span>
              <input
                type="text"
                value={authUsername}
                onChange={(event) => setAuthUsername(event.target.value)}
                className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 focus:border-primary-500 focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-2 text-sm">
              <span>Password</span>
              <input
                type="password"
                value={authPassword}
                onChange={(event) => setAuthPassword(event.target.value)}
                className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 focus:border-primary-500 focus:outline-none"
              />
            </label>
          </div>
          <div className="flex gap-3">
            <button
              type="button"
              className="px-4 py-2 rounded-xl bg-primary-600 hover:bg-primary-500 text-sm font-semibold"
              onClick={handleSaveCredentials}
            >
              Save Credentials
            </button>
            <button
              type="button"
              className="px-4 py-2 rounded-xl border border-slate-700 hover:border-primary-500 text-sm"
              onClick={handleClearCredentials}
            >
              Clear
            </button>
          </div>
        </section>

        <section>
          <label
            onDrop={handleDrop}
            onDragOver={(event) => event.preventDefault()}
            className={clsx(
              "flex flex-col items-center justify-center w-full h-48 border-2 border-dashed rounded-3xl transition",
              selectedFile ? "border-primary-500 bg-primary-500/10" : "border-slate-700 hover:border-primary-500"
            )}
          >
            <input
              type="file"
              accept="video/*"
              className="hidden"
              onChange={(event) => handleFileChange(event.target.files?.[0] ?? null)}
            />
            <span className="text-lg font-medium">{selectedFile ? selectedFile.name : "Drag & drop or browse a video"}</span>
            <span className="text-sm text-slate-400 mt-2">Supports AV1/HEVC/H.264 NVENC outputs</span>
          </label>
        </section>

        <section className="space-y-4">
          <div>
            <span className="text-sm uppercase text-slate-400">Discord presets</span>
            <div className="mt-2 grid grid-cols-2 md:grid-cols-4 gap-2">
              {PRESETS.map((presetOption) => (
                <button
                  key={presetOption.value}
                  className={clsx(
                    "px-4 py-2 rounded-xl border border-slate-700",
                    targetSize === presetOption.value ? "bg-primary-600 border-primary-500" : "hover:border-primary-500"
                  )}
                  onClick={() => setTargetSize(presetOption.value)}
                >
                  {presetOption.label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor="custom-size" className="text-sm text-slate-300">
              Custom Size (MB)
            </label>
            <input
              id="custom-size"
              type="number"
              min={1}
              step={1}
              value={targetSize}
              onChange={(event) => setTargetSize(Math.max(1, Number(event.target.value) || 1))}
              className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 w-32 focus:border-primary-500 focus:outline-none"
            />
          </div>
        </section>

        <details className="bg-slate-900/60 border border-slate-800 rounded-3xl p-6" open={showAdvanced}>
          <summary
            className="text-lg font-semibold cursor-pointer text-slate-200"
            onClick={(event) => {
              event.preventDefault();
              setShowAdvanced((value) => !value);
            }}
          >
            Advanced Options
          </summary>

          <div className="mt-6 grid gap-6 md:grid-cols-3">
            <div className="space-y-2">
              <span className="text-sm text-slate-400">Video Codec</span>
              <div className="space-y-2">
                {VIDEO_CODECS.map((option) => (
                  <label key={option.value} className="flex items-center gap-2 text-sm">
                    <input
                      type="radio"
                      name="videoCodec"
                      checked={videoCodec === option.value}
                      onChange={() => setVideoCodec(option.value)}
                    />
                    {option.label}
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <span className="text-sm text-slate-400">Audio Codec</span>
              <div className="space-y-2">
                {AUDIO_CODECS.map((option) => (
                  <label key={option.value} className="flex items-center gap-2 text-sm">
                    <input
                      type="radio"
                      name="audioCodec"
                      checked={audioCodec === option.value}
                      onChange={() => setAudioCodec(option.value)}
                    />
                    {option.label}
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-4">
              <div className="space-y-2">
                <span className="text-sm text-slate-400">Preset</span>
                <div className="space-y-2">
                  {PRESETS_QUALITY.map((option) => (
                    <label key={option.value} className="flex items-center gap-2 text-sm">
                      <input
                        type="radio"
                        name="preset"
                        checked={preset === option.value}
                        onChange={() => setPreset(option.value)}
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-sm text-slate-400" htmlFor="audio-bitrate">
                  Audio Bitrate (kbps)
                </label>
                <input
                  id="audio-bitrate"
                  type="number"
                  min={32}
                  step={16}
                  value={audioBitrate}
                  onChange={(event) => setAudioBitrate(Math.max(32, Number(event.target.value) || 32))}
                  className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 w-full focus:border-primary-500 focus:outline-none"
                />
              </div>
            </div>
          </div>
        </details>

        {probe && estimate && (
          <section className="bg-slate-900/60 border border-slate-800 rounded-3xl p-6 space-y-2">
            <h2 className="text-lg font-semibold text-slate-200">Estimates</h2>
            <p className="text-sm text-slate-400">
              Original: {probe.size_mb.toFixed(1)} MB @ {probe.bitrate_kbps.toFixed(0)} kbps · Target: {targetSize} MB @ {estimate.target_video_bitrate_kbps.toFixed(0)} kbps (video)
            </p>
            <p className="text-sm text-slate-400">
              Duration: {probe.duration.toFixed(1)} s · Audio bitrate: {audioBitrate} kbps
            </p>
            {warning && <p className="text-sm text-amber-400">Warning: {warning}</p>}
          </section>
        )}

        {error && <p className="text-sm text-rose-400">{error}</p>}

        <div className="flex gap-4">
          <button
            className="flex-1 px-6 py-3 rounded-2xl bg-primary-600 hover:bg-primary-500 font-semibold transition"
            onClick={handleCompress}
            disabled={primaryButtonDisabled}
          >
            {isUploading ? "Uploading…" : "Compress"}
          </button>
          <button
            className="px-6 py-3 rounded-2xl border border-slate-700 hover:border-primary-500"
            onClick={() => handleFileChange(null)}
          >
            Compress Another
          </button>
        </div>

        {jobId && (
          <section className="bg-slate-900/60 border border-slate-800 rounded-3xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-slate-200">Progress</h2>
              <span className="text-sm text-slate-400 uppercase">{jobStatus}</span>
            </div>
            <div className="h-3 bg-slate-800 rounded-full overflow-hidden">
              <div className="h-full bg-primary-500 transition-all" style={{ width: `${Math.round(progress * 100)}%` }} />
            </div>
            <details className="bg-slate-950/70 border border-slate-800 rounded-2xl">
              <summary className="px-4 py-2 cursor-pointer text-sm text-slate-300">FFmpeg Log</summary>
              <pre className="max-h-64 overflow-y-auto text-xs px-4 py-3 text-slate-400 whitespace-pre-wrap">
                {logLines.join("\n") || "Awaiting encoder output…"}
              </pre>
            </details>

            {result && (
              <div className="space-y-2 text-sm text-slate-300">
                <p>
                  Completed: {reductionSummary?.finalSizeMB.toFixed(2)} MB ({reductionSummary?.reductionPercent.toFixed(1)}% reduction)
                </p>
                <a
                  className="inline-flex items-center px-4 py-2 rounded-xl bg-primary-600 hover:bg-primary-500 text-sm font-semibold"
                  href={`${resolvedApiBase}/outputs/${encodeURIComponent(result.output_basename)}`}
                  download
                >
                  Download
                </a>
              </div>
            )}
          </section>
        )}
      </div>
    </main>
  );
}
