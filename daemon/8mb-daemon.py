import asyncio
import json
import os
import platform
import subprocess
import re
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
try:
    import setproctitle
    setproctitle.setproctitle("8mb.local")
except ImportError:
    pass

app = FastAPI(title="8mb.local macOS Hardware Encoding Daemon")

# Supported macOS hardware encoders
SUPPORTED_CODECS = ["hevc_videotoolbox", "h264_videotoolbox"]

class EncodeRequest(BaseModel):
    input_path: str
    output_path: str
    target_size_mb: float = None
    target_video_bitrate_kbps: float = None
    video_codec: str
    audio_codec: str
    audio_bitrate_kbps: int = 128
    preset: str = 'p6'
    max_height: int = None
    max_width: int = None
    max_output_fps: int = None

@app.get("/health")
def health_check():
    info = {
        "status": "ok",
        "codecs": SUPPORTED_CODECS,
        "platform": "darwin",
    }

    # Gather host system info for the container's system capabilities display
    try:
        import psutil as _psutil
        info["cpu"] = {
            "model": _get_mac_cpu_model(),
            "cores_physical": _psutil.cpu_count(logical=False) or 0,
            "cores_logical": _psutil.cpu_count(logical=True) or 0,
        }
        mem = _psutil.virtual_memory()
        info["memory"] = {
            "total_gb": round(mem.total / (1024**3), 2),
            "available_gb": round(mem.available / (1024**3), 2),
        }
    except ImportError:
        # psutil not available — use basic detection
        info["cpu"] = {
            "model": _get_mac_cpu_model(),
            "cores_physical": os.cpu_count() or 0,
            "cores_logical": os.cpu_count() or 0,
        }
    except Exception:
        pass

    # GPU / Apple Silicon chip info
    try:
        gpu_name = _get_mac_gpu_name()
        if gpu_name:
            info["gpus"] = [{"name": gpu_name, "type": "apple_silicon"}]
    except Exception:
        pass

    return info


def _get_mac_cpu_model() -> str:
    """Get the CPU/chip brand string on macOS."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return platform.processor() or "Unknown"


def _get_mac_gpu_name():
    """Get the GPU name on macOS (Apple Silicon or discrete)."""
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("Chipset Model:") or line.startswith("Chip:"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None

@app.post("/encode")
async def encode_video(req: EncodeRequest):
    # This assumes input_path and output_path map 1:1 between host and docker container via bind mounts
    # We will run ffmpeg natively.
    
    # Validation
    if not os.path.exists(req.input_path):
        return {"error": f"Input file not found: {req.input_path}"}
        
    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-i", req.input_path,
        "-c:v", req.video_codec,
    ]

    # Map presets to VideoToolbox profile/quality
    if req.video_codec.endswith("_videotoolbox"):
        # VT doesn't have p1-p7, so we translate:
        if req.preset == 'p7' or req.preset == 'extraquality':
            cmd.extend(["-profile:v", "main", "-q:v", "65"])
        elif req.preset == 'p6':
            cmd.extend(["-profile:v", "main", "-q:v", "55"])
        else:
            cmd.extend(["-profile:v", "main", "-q:v", "45"]) # faster/lower quality

    if req.target_video_bitrate_kbps:
        cmd.extend(["-b:v", f"{int(req.target_video_bitrate_kbps)}k"])

    # Resolution scaling
    if req.max_width or req.max_height:
        vf = f"scale={req.max_width or -2}:{req.max_height or -2}"
        cmd.extend(["-vf", vf])

    # FPS cap
    if req.max_output_fps:
        cmd.extend(["-r", str(req.max_output_fps)])

    # Audio
    if req.audio_codec == 'none':
        cmd.extend(["-an"])
    else:
        cmd.extend([
            "-c:a", "aac" if req.audio_codec == "aac" else "libopus", # Note macOS default ffmpeg might not have libopus, typically VT pushes people to aac
            "-b:a", f"{req.audio_bitrate_kbps}k"
        ])

    cmd.append(req.output_path)

    async def run_ffmpeg():
        try:
            # We add pipe:1 (stdout) and pipe:2 (stderr)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Send initial connected log via chunked stream
            yield json.dumps({"type": "log", "message": f"Native Daemon: Starting encode with {req.video_codec}"}) + "\n"
            
            # ffmpeg writes progress to stderr
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                
                decoded_line = line.decode('utf-8').strip()
                
                # Check for progress data e.g. frame=  123 fps= 30 q=...
                if "frame=" in decoded_line:
                    # Simple regex to fetch speed
                    speed_match = re.search(r"speed=\s*([\d.]+)x", decoded_line)
                    speed_x = float(speed_match.group(1)) if speed_match else None
                    
                    # We can push this back as raw log, the docker container worker handles ETA calculation
                    yield json.dumps({"type": "ffmpeg_log", "message": decoded_line, "speed_x": speed_x}) + "\n"
                    
            await process.wait()
            
            if process.returncode == 0:
                size_bytes = os.path.getsize(req.output_path) if os.path.exists(req.output_path) else 0
                yield json.dumps({"type": "done", "size_bytes": size_bytes}) + "\n"
            else:
                yield json.dumps({"type": "error", "message": f"FFmpeg failed with code {process.returncode}"}) + "\n"
                
        except Exception as e:
             yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    return StreamingResponse(run_ffmpeg(), media_type="application/x-ndjson")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("DAEMON_PORT", 7998))
    uvicorn.run(app, host="0.0.0.0", port=port)
