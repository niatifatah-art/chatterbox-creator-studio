from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class HardwareProfile:
    os_name: str
    cpu_name: str
    logical_cpus: int | None
    ram_gb: float | None
    physical_gpus: tuple[str, ...]
    torch_version: str | None
    cuda_available: bool
    mps_available: bool
    accelerator_name: str | None
    vram_gb: float | None
    recommended_compute: str
    acceleration_note: str

    @property
    def product_compute(self) -> str:
        return "gpu" if self.recommended_compute in {"cuda", "mps"} else "cpu"


def _ram_gb() -> float | None:
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.ullTotalPhys / 1024**3, 2)
            return None

        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        return round((page_size * pages) / 1024**3, 2)
    except Exception:
        return None


def _windows_gpus() -> tuple[str, ...]:
    if os.name != "nt":
        return ()
    commands = [
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
        ],
        [
            "wmic",
            "path",
            "win32_VideoController",
            "get",
            "name",
        ],
    ]
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip() and line.strip().lower() != "name"]
        if lines:
            return tuple(dict.fromkeys(lines))
    return ()


def _nvidia_smi_gpus() -> tuple[str, ...]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ()
    try:
        result = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    return tuple(dict.fromkeys(line.strip() for line in result.stdout.splitlines() if line.strip()))


def _physical_gpus() -> tuple[str, ...]:
    if os.name == "nt":
        return _windows_gpus()
    return _nvidia_smi_gpus()


def collect_hardware_profile() -> HardwareProfile:
    cpu_name = platform.processor().strip() or platform.machine() or "Unknown CPU"
    physical_gpus = _physical_gpus()
    torch_version: str | None = None
    cuda_available = False
    mps_available = False
    accelerator_name: str | None = None
    vram_gb: float | None = None

    try:
        import torch

        torch_version = str(torch.__version__)
        cuda_available = bool(torch.cuda.is_available())
        mps_available = bool(hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
        if cuda_available:
            accelerator_name = torch.cuda.get_device_name(0)
            try:
                vram_gb = round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2)
            except Exception:
                pass
        elif mps_available:
            accelerator_name = "Apple GPU"
    except Exception:
        pass

    if cuda_available:
        recommended = "cuda"
        note = f"GPU acceleration ready{f' · {accelerator_name}' if accelerator_name else ''}."
    elif mps_available:
        recommended = "mps"
        note = "Apple GPU acceleration ready."
    else:
        recommended = "cpu"
        if physical_gpus:
            names = ", ".join(physical_gpus[:2])
            note = f"Running on CPU. A graphics adapter was detected ({names}), but the current PyTorch build is not using it."
        else:
            note = "CPU mode is ready."

    return HardwareProfile(
        os_name=platform.platform(),
        cpu_name=cpu_name,
        logical_cpus=os.cpu_count(),
        ram_gb=_ram_gb(),
        physical_gpus=physical_gpus,
        torch_version=torch_version,
        cuda_available=cuda_available,
        mps_available=mps_available,
        accelerator_name=accelerator_name,
        vram_gb=vram_gb,
        recommended_compute=recommended,
        acceleration_note=note,
    )


def resolve_compute_preference(preference: str | None, profile: HardwareProfile) -> tuple[str, str]:
    """Resolve a human-facing Auto/GPU/CPU preference to a backend device."""
    normalized = (preference or "Auto").strip().lower()
    if normalized == "cpu":
        return "cpu", "CPU"
    if normalized == "gpu":
        if profile.cuda_available:
            return "cuda", f"GPU · {profile.accelerator_name or 'CUDA'}"
        if profile.mps_available:
            return "mps", "GPU · Apple MPS"
        raise ValueError("GPU mode was requested, but this installation cannot use a GPU yet. Choose Auto or CPU.")

    if profile.cuda_available:
        return "cuda", f"Auto · {profile.accelerator_name or 'CUDA GPU'}"
    if profile.mps_available:
        return "mps", "Auto · Apple GPU"
    return "cpu", "Auto · CPU"


def hardware_summary(profile: HardwareProfile) -> str:
    memory = f"{profile.ram_gb:.1f} GB RAM" if profile.ram_gb is not None else "RAM unknown"
    accelerator = profile.accelerator_name or (profile.physical_gpus[0] if profile.physical_gpus else "CPU")
    vram = f" · {profile.vram_gb:.1f} GB VRAM" if profile.vram_gb is not None else ""
    return f"**{accelerator}**{vram} · {memory}  \n{profile.acceleration_note}"
