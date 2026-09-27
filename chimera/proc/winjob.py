"""A Windows Job Object around one process tree: stop reaches every descendant, and the tree dies
with this process.

`kill_tree` walks the tree with ``taskkill /T``, which follows parent-pid links. A descendant whose
parent already exited is no longer on that walk — a `cmd /c` that launched a worker and returned,
a launcher that re-spawns itself — and survives the stop. A Job Object holds every process started
inside it whatever became of its parent, so ``TerminateJobObject`` ends all of them.

``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` is the second property: when the last handle to the job is
closed — including by the kernel, because the process holding it died however it died — everything
in it is killed. A background job therefore cannot outlive the app that watches it, which is what
keeps its record honest and its maximum runtime enforceable.

When a job's own process ends normally the limit is cleared before the handle is closed, so what
the command deliberately left behind is treated as it is on POSIX: left alone.

The one gap, said rather than hidden: the process is assigned right after ``CreateProcess``
returns, not created suspended (``subprocess`` gives no way to resume a suspended child). Anything
it starts in that first instant is outside the job; ``taskkill /T`` still runs after the job is
terminated and catches those. Every call degrades to ``None`` / no-op when the platform refuses,
so a machine where job objects are unavailable behaves exactly as before.
"""

from __future__ import annotations

import sys
from contextlib import suppress
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("proc.winjob")


class JobObject:
    """One job object containing one background job's process tree. Never raises."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def terminate(self) -> None:
        """Kill every process in the job."""
        if self._handle is not None:
            _terminate(self._handle)

    def release(self) -> None:
        """Close the handle WITHOUT killing what is left in the job (the job ended on its own)."""
        handle, self._handle = self._handle, None
        if handle is not None:
            _set_kill_on_close(handle, False)
            _close(handle)

    def close(self) -> None:
        """Close the handle; with kill-on-close still set, whatever is in the job dies."""
        handle, self._handle = self._handle, None
        if handle is not None:
            _close(handle)


def contain(pid: int) -> JobObject | None:
    """Put ``pid`` in a new kill-on-close job object. ``None`` off Windows or when refused."""
    if sys.platform != "win32":
        return None
    try:
        return _contain(pid)
    except Exception as exc:  # noqa: BLE001 - a job object is an improvement, never a requirement
        _log.debug("job object unavailable for pid %s: %s", pid, exc)
        return None


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def _set_kill_on_close(handle: Any, on: bool) -> bool:
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE if on else 0
        return bool(
            _kernel32.SetInformationJobObject(
                handle, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
            )
        )

    def _contain(pid: int) -> JobObject | None:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        if not _set_kill_on_close(job, True):
            _kernel32.CloseHandle(job)
            return None
        process = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            _kernel32.CloseHandle(job)
            return None
        try:
            if not _kernel32.AssignProcessToJobObject(job, process):
                _kernel32.CloseHandle(job)
                return None
        finally:
            _kernel32.CloseHandle(process)
        return JobObject(job)

    def _terminate(handle: Any) -> None:
        with suppress(Exception):
            _kernel32.TerminateJobObject(handle, 1)

    def _close(handle: Any) -> None:
        with suppress(Exception):
            _kernel32.CloseHandle(handle)

else:

    def _set_kill_on_close(handle: Any, on: bool) -> bool:
        return False

    def _contain(pid: int) -> JobObject | None:
        return None

    def _terminate(handle: Any) -> None:
        return None

    def _close(handle: Any) -> None:
        return None
