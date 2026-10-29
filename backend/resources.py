"""Bound local worker resources without touching unrelated processes.

Windows uses a non-inheritable Job Object. Its last handle belongs to the API
process, so Windows closes it and terminates the worker tree even after an
abrupt API exit. The job-wide memory limit bounds committed private memory,
not just the root process RSS. A restricted host may reject nested jobs; the
default is to fail clearly instead of silently dropping containment.

References: Microsoft JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
JOBOBJECT_BASIC_LIMIT_INFORMATION, AssignProcessToJobObject, and Job Objects:
https://learn.microsoft.com/windows/win32/procthread/job-objects
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any


class ResourceLimitError(RuntimeError):
    """The configured resource budget cannot be provided or has been exceeded."""


_MIB = 1024 ** 2
_GIB = 1024 ** 3
_LAST_STATUS: dict[str, Any] | None = None
_STATUS_LOCK = threading.Lock()


def _positive_setting(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ResourceLimitError(f"{name} must be a positive finite number.") from exc
    if not math.isfinite(value) or value <= 0:
        raise ResourceLimitError(f"{name} must be a positive finite number.")
    return value


def worker_memory_limit() -> int:
    return int(_positive_setting("MAX_WORKER_MB", 2048) * _MIB)


def disk_limit() -> int:
    return int(_positive_setting("MAX_DISK_GB", 20) * _GIB)


def resource_status() -> dict:
    """Configured capabilities plus the last actual assignment, not a fake probe."""
    with _STATUS_LOCK:
        last = dict(_LAST_STATUS) if _LAST_STATUS else None
    return {"platform": os.name, "worker_memory_limit_bytes": worker_memory_limit(),
            "disk_limit_bytes": disk_limit(),
            "containment_available": os.name == "nt" or _has_psutil(),
            "containment_method": "windows-job-object" if os.name == "nt" else "process-tree-monitor",
            "last_assignment": last,
            "note": "Windows capability is confirmed on actual worker assignment; monitored fallback cannot clean up after abrupt parent termination."}


def _has_psutil() -> bool:
    try:
        import psutil  # noqa: F401
        return True
    except ImportError:
        return False


def directory_size(directory: Path, stop_after: int | None = None) -> int:
    """Sum logical file bytes; never follow symlinks, junctions or reparse points.

    stop_after gives a bounded early exit once the budget is already exceeded.
    Concurrent file deletion is normal. Unreadable paths fail explicitly so an
    incomplete traversal cannot be mistaken for a successful quota check.
    """
    directory = Path(directory)
    if not directory.exists():
        return 0
    if not directory.is_dir():
        raise ResourceLimitError("The data storage path must be a directory.")
    total, pending = 0, [directory]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += stat.st_size
                            if stop_after is not None and total > stop_after:
                                return total
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ResourceLimitError(f"Cannot verify the storage quota: {exc}") from exc
    return total


def check_disk_budget(directory: Path, reserve_bytes: int = 0, max_bytes: int | None = None,
                      min_free_bytes: int | None = None) -> dict:
    """Preflight/periodic check; callers must repeat while workers are writing.

    This is an application quota rather than an OS filesystem quota. It can
    overshoot between polling checks; it never deletes uploads to recover space.
    reserve_bytes is intended for a known incoming upload or anticipated output.
    """
    directory = Path(directory).resolve()
    maximum = disk_limit() if max_bytes is None else int(max_bytes)
    reserve = int(reserve_bytes)
    minimum_free = int(_positive_setting("MIN_FREE_DISK_MB", 256) * _MIB) if min_free_bytes is None else int(min_free_bytes)
    if maximum <= 0 or reserve < 0 or minimum_free < 0:
        raise ResourceLimitError("Disk limits must be positive and reserved bytes cannot be negative.")
    used = directory_size(directory, stop_after=max(0, maximum - reserve))
    if used + reserve > maximum:
        raise ResourceLimitError(f"The Framecraft storage budget of {maximum / _GIB:.2f} GiB would be exceeded. Delete unwanted projects or raise MAX_DISK_GB.")
    existing = directory
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    if free - reserve < minimum_free:
        raise ResourceLimitError(f"Insufficient free disk space. At least {minimum_free / _MIB:.0f} MiB must remain available after this operation.")
    return {"used_bytes": used, "reserved_bytes": reserve, "limit_bytes": maximum,
            "remaining_budget_bytes": maximum - used - reserve, "free_bytes": free,
            "minimum_free_bytes": minimum_free, "enforcement": "periodic-application-quota"}


# Fixed-width fields match Win32 on both 32- and 64-bit Python. In particular,
# Windows DWORD is 32-bit, while pointer-sized SIZE_T follows the interpreter.
class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimitInformation), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", ctypes.c_uint32), ("TotalProcesses", ctypes.c_uint32),
                ("ActiveProcesses", ctypes.c_uint32), ("TotalTerminatedProcesses", ctypes.c_uint32)]


def _kernel32():
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    library.CreateJobObjectW.restype = wintypes.HANDLE
    library.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    library.SetInformationJobObject.restype = wintypes.BOOL
    library.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    library.AssignProcessToJobObject.restype = wintypes.BOOL
    library.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
    library.QueryInformationJobObject.restype = wintypes.BOOL
    library.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    library.TerminateJobObject.restype = wintypes.BOOL
    library.CloseHandle.argtypes = (wintypes.HANDLE,)
    library.CloseHandle.restype = wintypes.BOOL
    return library


def _windows_error(action: str) -> ResourceLimitError:
    code = ctypes.get_last_error()
    return ResourceLimitError(f"Cannot {action} for the media worker (Windows error {code}: {ctypes.FormatError(code).strip()}). The host may forbid nested Job Objects; run Framecraft in a normal local terminal.")


class ProcessContainment:
    """Own exactly one worker containment handle; close is idempotent."""

    def __init__(self, process: subprocess.Popen, limit: int):
        self.process = process
        self.limit_bytes = limit
        self.status: dict = {}
        self._job = None
        self._kernel = None
        self._psutil_root = None
        self._closed = False
        self._lock = threading.Lock()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _publish_status(self) -> dict:
        global _LAST_STATUS
        snapshot = dict(self.status)
        with _STATUS_LOCK:
            if _LAST_STATUS and _LAST_STATUS.get("pid") == self.process.pid:
                _LAST_STATUS = snapshot
        return snapshot

    def check(self) -> dict:
        """Poll memory monitoring from the worker loop; raise on exceeded budget."""
        with self._lock:
            if self._closed:
                return self._publish_status()
            if self._job:
                info = _ExtendedLimitInformation()
                if self._kernel.QueryInformationJobObject(self._job, 9, ctypes.byref(info), ctypes.sizeof(info), None):
                    self.status["peak_committed_bytes"] = int(info.PeakJobMemoryUsed)
                return self._publish_status()
        if self._psutil_root is not None:
            try:
                import psutil
                processes = [self._psutil_root, *self._psutil_root.children(recursive=True)]
                used = 0
                for child in processes:
                    try:
                        used += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                self.status["observed_tree_rss_bytes"] = used
                if used > self.limit_bytes:
                    self.close()
                    raise ResourceLimitError(f"Media worker memory exceeded MAX_WORKER_MB ({self.limit_bytes / _MIB:.0f} MiB). Use a shorter source or a smaller model.")
            except psutil.NoSuchProcess:
                pass
        return self._publish_status()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            job = self._job
            self._job = None
            if job:
                # Job termination is asynchronous. Waiting only for Popen.pid
                # can leave an encoder holding pending-audio.wav open while
                # the parent tries to clean staging files. Retain this handle
                # until the *whole job* has no active processes, bounded at 5s.
                # Abrupt parent termination still invokes KILL_ON_JOB_CLOSE.
                began = time.monotonic()
                reaped = False
                try:
                    if self._kernel.TerminateJobObject(job, 1):
                        while time.monotonic() - began < 5:
                            accounting = _BasicAccountingInformation()
                            if not self._kernel.QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                                break
                            if accounting.ActiveProcesses == 0:
                                reaped = True
                                break
                            time.sleep(.02)
                finally:
                    self._kernel.CloseHandle(job)
                self.status["cleanup_complete"] = reaped
                self.status["cleanup_wait_seconds"] = round(time.monotonic() - began, 4)
                self.status["closed"] = True
                self._publish_status()
                return
        # Only a fallback path; normal Windows cleanup is handled atomically by
        # the kernel and also runs when Python cannot execute any finally block.
        if self._psutil_root is not None:
            import psutil
            try:
                children = self._psutil_root.children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
            processes = list(reversed(children)) + [self._psutil_root]
            for child in processes:
                try:
                    child.kill()  # psutil verifies process identity against PID reuse.
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            psutil.wait_procs(processes, timeout=2)
        elif self.process.poll() is None:
            self.process.kill()
        self.status["closed"] = True
        self._publish_status()


def contain_process(process: subprocess.Popen, memory_mb: float | None = None, strict: bool = True) -> ProcessContainment:
    """Assign immediately after Popen, retain until job completion, close in finally.

    Assignment must precede work which launches grandchildren. Do not pass an
    unrelated PID: this interface deliberately accepts the actual Popen object.
    strict=False is an explicit opt-in to a weaker monitored fallback on Windows.
    On other platforms aggregate RSS is monitored when psutil is installed;
    Linux additionally applies an inherited per-process address-space limit.
    """
    global _LAST_STATUS
    if not isinstance(process, subprocess.Popen):
        raise TypeError("contain_process requires the worker's subprocess.Popen object.")
    if memory_mb is not None:
        try:
            requested = float(memory_mb)
        except (TypeError, ValueError) as exc:
            raise ResourceLimitError("The worker memory limit must be positive and finite.") from exc
        if not math.isfinite(requested) or requested <= 0:
            raise ResourceLimitError("The worker memory limit must be positive and finite.")
        limit = int(requested * _MIB)
    else:
        limit = worker_memory_limit()
    if limit <= 0:
        raise ResourceLimitError("The worker memory limit must be positive.")
    containment = ProcessContainment(process, limit)
    try:
        if os.name == "nt":
            kernel = _kernel32()
            job = kernel.CreateJobObjectW(None, None)  # NULL security attributes: not inheritable.
            if not job:
                raise _windows_error("create a Job Object")
            containment._kernel, containment._job = kernel, job
            info = _ExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = 0x2000 | 0x0200  # KILL_ON_JOB_CLOSE | JOB_MEMORY
            info.JobMemoryLimit = limit
            if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise _windows_error("set the aggregate memory limit")
            if not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):
                raise _windows_error("assign the worker to its Job Object")
            containment.status = {"method": "windows-job-object", "pid": process.pid,
                                  "memory_limit_bytes": limit, "memory_hard_limit": True,
                                  "memory_scope": "aggregate-committed-private-memory",
                                  "parent_exit_cleanup": True, "closed": False}
        else:
            raise ResourceLimitError("Windows Job Objects are not available on this platform.")
    except (ResourceLimitError, OSError) as exc:
        if containment._job:
            containment._kernel.CloseHandle(containment._job)
            containment._job = None
        if os.name == "nt" and strict:
            with _STATUS_LOCK:
                _LAST_STATUS = {"method": "unavailable", "error": str(exc), "parent_exit_cleanup": False, "memory_hard_limit": False}
            raise ResourceLimitError(str(exc)) from exc
        try:
            import psutil
            containment._psutil_root = psutil.Process(process.pid)
        except ImportError as missing:
            raise ResourceLimitError("Install psutil to monitor memory on this platform; worker containment is unavailable.") from missing
        except psutil.NoSuchProcess as ended:
            raise ResourceLimitError("The worker exited before resource containment could be attached.") from ended
        hard_limit = False
        if os.name != "nt":
            try:
                import resource
                resource.prlimit(process.pid, resource.RLIMIT_AS, (limit, limit))
                hard_limit = True
            except (ImportError, AttributeError, OSError, ValueError):
                pass
        containment.status = {"method": "process-tree-monitor", "pid": process.pid,
                              "memory_limit_bytes": limit, "memory_hard_limit": hard_limit,
                              "memory_scope": "per-process-address-space + polled-aggregate-rss" if hard_limit else "polled-aggregate-rss",
                              "parent_exit_cleanup": False, "fallback_reason": str(exc), "closed": False}
    with _STATUS_LOCK:
        _LAST_STATUS = dict(containment.status)
    return containment
