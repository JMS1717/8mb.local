import os


def get_gpu_env() -> dict:
    """
    Construct an environment map that ensures NVIDIA GPU variables and CUDA/NVIDIA
    library paths are available to subprocesses (ffmpeg, nvidia-smi, etc.).

    This is critical when processes are launched under a supervisor or Celery,
    where inherited env may be reduced or late-mounted libraries need explicit
    LD_LIBRARY_PATH entries (e.g., WSL2's /usr/lib/wsl/lib).
    """
    env = os.environ.copy()
    # Ensure NVIDIA visibility/capabilities are present (non-destructive defaults)
    env['NVIDIA_VISIBLE_DEVICES'] = env.get('NVIDIA_VISIBLE_DEVICES', 'all')
    env['NVIDIA_DRIVER_CAPABILITIES'] = env.get('NVIDIA_DRIVER_CAPABILITIES', 'compute,video,utility')

    # CUDA/NVIDIA library search paths (append without clobbering existing)
    lib_paths = [
        '/usr/local/nvidia/lib64',
        '/usr/local/nvidia/lib',
        '/usr/local/cuda/lib64',
        '/usr/local/cuda/lib',
        '/usr/lib/wsl/lib',  # WSL2 libcuda.so location
        '/usr/lib/x86_64-linux-gnu',
    ]
    existing = env.get('LD_LIBRARY_PATH', '')
    add = ':'.join(p for p in lib_paths if p)
    env['LD_LIBRARY_PATH'] = (existing + (':' if existing and add else '') + add) if (existing or add) else ''

    # Ensure CUDA binaries are in PATH for tools like nvcc/nvidia-smi (if present)
    bin_paths = [
        '/usr/local/cuda/bin',
        '/usr/local/nvidia/bin',
    ]
    existing_path = env.get('PATH', '')
    add_path = ':'.join(p for p in bin_paths if p)
    env['PATH'] = (existing_path + (':' if existing_path and add_path else '') + add_path) if (existing_path or add_path) else ''

    return env
