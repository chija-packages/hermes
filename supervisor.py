"""Resolve / install / supervise hermes-channel-chija (ChiJa WSS sidecar)."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import signal
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONNECTOR_NAME = "hermes-channel-chija"
DEFAULT_RELEASE_TAG = "v0.2.9"
DEFAULT_RELEASE_BASE = "https://github.com/chija-packages/hermes/releases/download"


def state_root() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return config / "chija" / "hermes-channel"


def bin_dir() -> Path:
    return state_root() / "bin"


def installed_bin_path() -> Path:
    name = CONNECTOR_NAME
    if platform.system().lower() == "windows":
        name += ".exe"
    return bin_dir() / name


def pid_path() -> Path:
    return state_root() / "connector.pid"


def log_path() -> Path:
    return state_root() / "logs" / "connector.log"


def bindings_root() -> Path:
    return state_root() / "bindings"


def has_bindings() -> bool:
    root = bindings_root()
    if not root.is_dir():
        return False
    for child in root.iterdir():
        if child.is_dir() and (child / "credentials.json").is_file():
            return True
    return False


def _platform_asset() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        os_name = "darwin"
    elif system == "linux":
        os_name = "linux"
    elif system == "windows":
        os_name = "windows"
    else:
        raise RuntimeError(f"unsupported OS for {CONNECTOR_NAME}: {system}")
    if machine in {"x86_64", "amd64"}:
        arch = "amd64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise RuntimeError(f"unsupported arch for {CONNECTOR_NAME}: {machine}")
    return os_name, arch


def release_asset_name(os_name: str | None = None, arch: str | None = None) -> str:
    if os_name is None or arch is None:
        os_name, arch = _platform_asset()
    name = f"{CONNECTOR_NAME}-{os_name}-{arch}"
    if os_name == "windows":
        name += ".exe"
    return name


def release_asset_url(tag: str | None = None) -> str:
    tag = (tag or os.environ.get("HERMES_CHIJA_CONNECTOR_RELEASE") or DEFAULT_RELEASE_TAG).strip()
    base = (os.environ.get("HERMES_CHIJA_CONNECTOR_RELEASE_BASE") or DEFAULT_RELEASE_BASE).rstrip("/")
    return f"{base}/{tag}/{release_asset_name()}"


def find_connector_bin() -> str | None:
    env = os.environ.get("HERMES_CHIJA_CONNECTOR_BIN", "").strip()
    if env and Path(env).is_file():
        return env
    which = shutil.which(CONNECTOR_NAME)
    if which:
        return which
    installed = installed_bin_path()
    if installed.is_file() and os.access(installed, os.X_OK):
        return str(installed)
    here = Path(__file__).resolve().parent
    for candidate in (
        here.parents[1] / "tools" / "hermes-channel-chija" / "bin" / CONNECTOR_NAME,
        here.parents[1] / "tools" / "hermes-channel-chija" / "bin" / f"{CONNECTOR_NAME}.exe",
        # flat plugin next to monorepo checkout is uncommon; keep relative walk shallow
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def release_tag_path() -> Path:
    return installed_bin_path().with_name(f"{CONNECTOR_NAME}.release-tag")


def ensure_connector_bin(*, download: bool = True) -> str:
    env = os.environ.get("HERMES_CHIJA_CONNECTOR_BIN", "").strip()
    if env and Path(env).is_file():
        return env

    tag = (os.environ.get("HERMES_CHIJA_CONNECTOR_RELEASE") or DEFAULT_RELEASE_TAG).strip()
    dest = installed_bin_path()
    tag_file = release_tag_path()
    if (
        dest.is_file()
        and os.access(dest, os.X_OK)
        and tag_file.is_file()
        and tag_file.read_text(encoding="utf-8").strip() == tag
    ):
        return str(dest)

    which = shutil.which(CONNECTOR_NAME)
    if which and Path(which).resolve() != dest.resolve():
        # Explicit PATH install — do not overwrite.
        return which

    if not download:
        existing = find_connector_bin()
        if existing:
            return existing
        raise RuntimeError(
            f"{CONNECTOR_NAME} not found. Install from "
            f"{DEFAULT_RELEASE_BASE}/{DEFAULT_RELEASE_TAG}/ or set HERMES_CHIJA_CONNECTOR_BIN."
        )

    url = release_asset_url(tag)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".download")
    logger.info("downloading %s -> %s", url, dest)
    try:
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 — official release URL
    except urllib.error.URLError as err:
        raise RuntimeError(
            f"failed to download {CONNECTOR_NAME} from {url}: {err}. "
            f"Build locally or set HERMES_CHIJA_CONNECTOR_BIN."
        ) from err
    tmp.chmod(0o755)
    tmp.replace(dest)
    tag_file.write_text(tag + "\n", encoding="utf-8")
    try:
        os.chmod(tag_file, 0o600)
    except OSError:
        pass
    return str(dest)


def _spawn_kwargs() -> dict[str, Any]:
    """Detach the sidecar. Windows has no setsid; a new process group hides the console."""
    if platform.system().lower() == "windows":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags | no_window}
    return {"start_new_session": True}


# WaitForSingleObject / OpenProcess. Signal 0 is not a liveness probe on Windows:
# CPython maps it to CTRL_C_EVENT and GenerateConsoleCtrlEvent, which stops the
# connector or raises WinError 87 for a CREATE_NO_WINDOW process.
_WIN_SYNCHRONIZE = 0x00100000
_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_WAIT_OBJECT_0 = 0
_WIN_WAIT_TIMEOUT = 0x102
_WIN_ERROR_ACCESS_DENIED = 5


def _windows_liveness(*, opened: bool, last_error: int, wait_result: int | None) -> bool:
    """Map an OpenProcess + WaitForSingleObject(0) result to alive/dead."""
    if not opened:
        return last_error == _WIN_ERROR_ACCESS_DENIED
    if wait_result == _WIN_WAIT_TIMEOUT:
        return True
    if wait_result == _WIN_WAIT_OBJECT_0:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    wait_for = kernel32.WaitForSingleObject
    wait_for.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    access = _WIN_SYNCHRONIZE | _WIN_PROCESS_QUERY_LIMITED_INFORMATION
    handle = open_process(access, False, pid)
    if not handle:
        return _windows_liveness(opened=False, last_error=ctypes.get_last_error(), wait_result=None)
    try:
        waited = int(wait_for(handle, 0))
        return _windows_liveness(opened=True, last_error=0, wait_result=waited)
    finally:
        close_handle(handle)


def _pid_alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.system().lower() == "windows":
        return _pid_alive_windows(pid)
    return _pid_alive_posix(pid)


def connector_status() -> dict[str, Any]:
    pid_file = pid_path()
    pid: int | None = None
    running = False
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            running = _pid_alive(pid)
        except ValueError:
            pid = None
            running = False
    binary = find_connector_bin()
    return {
        "binary": binary,
        "pid": pid if running else None,
        "running": running,
        "pidFile": str(pid_file),
        "logFile": str(log_path()),
        "hasBindings": has_bindings(),
    }


def ensure_connector_running(*, download_bin: bool = True) -> dict[str, Any]:
    """Start hermes-channel-chija run in the background when bindings exist."""
    if not has_bindings():
        return {
            "started": False,
            "reason": "no_bindings",
            "status": connector_status(),
        }

    status = connector_status()
    if status["running"]:
        return {"started": False, "reason": "already_running", "status": status}

    binary = ensure_connector_bin(download=download_bin)
    log_file = log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file = pid_path()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    bindings = bindings_root()

    log_fh = open(log_file, "a", encoding="utf-8")  # noqa: SIM115 — kept open for child lifetime
    try:
        proc = subprocess.Popen(
            [binary, "run", "--bindings-dir", str(bindings)],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **_spawn_kwargs(),
        )
    finally:
        log_fh.close()

    pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
    try:
        os.chmod(pid_file, 0o600)
    except OSError:
        pass

    return {
        "started": True,
        "reason": "spawned",
        "pid": proc.pid,
        "binary": binary,
        "logFile": str(log_file),
        "status": connector_status(),
    }


def stop_connector() -> dict[str, Any]:
    status = connector_status()
    pid = status.get("pid")
    if not pid:
        return {"stopped": False, "reason": "not_running"}
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError as err:
        return {"stopped": False, "reason": str(err)}
    try:
        pid_path().unlink(missing_ok=True)
    except OSError:
        pass
    return {"stopped": True, "pid": pid}


def try_register_background_service(ctx: Any) -> bool:
    """Use Hermes register_background_service when available; else False."""
    register_fn = getattr(ctx, "register_background_service", None)
    if not callable(register_fn):
        return False

    def factory() -> Any:
        class _Svc:
            def start(self) -> None:
                ensure_connector_running(download_bin=True)

            def stop(self) -> None:
                stop_connector()

        return _Svc()

    def check_fn() -> bool:
        return True

    try:
        register_fn(name="chija-hermes-channel", factory=factory, check_fn=check_fn)
        logger.info("registered Hermes background service chija-hermes-channel")
        return True
    except Exception as err:  # noqa: BLE001
        logger.warning("register_background_service failed: %s", err)
        return False
