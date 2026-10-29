"""Resource tests create and terminate only their own controlled child processes."""
import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from backend import resources


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "data" / "test-tmp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def _line(process: subprocess.Popen, timeout=8) -> str:
    output = queue.Queue()
    def reader():
        output.put(process.stdout.readline())
    threading.Thread(target=reader, daemon=True).start()
    try:
        return output.get(timeout=timeout).strip()
    except queue.Empty as exc:
        raise AssertionError("Controlled child did not respond within eight seconds.") from exc


class DiskBudgetTests(unittest.TestCase):
    def test_reserved_upload_counts_against_quota_without_deleting_files(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temporary:
            folder = Path(temporary)
            (folder / "upload").write_bytes(b"x" * 60)
            with self.assertRaises(resources.ResourceLimitError):
                resources.check_disk_budget(folder, reserve_bytes=50, max_bytes=100, min_free_bytes=0)
            self.assertEqual((folder / "upload").read_bytes(), b"x" * 60)
            accepted = resources.check_disk_budget(folder, reserve_bytes=30, max_bytes=100, min_free_bytes=0)
            self.assertEqual(accepted["remaining_budget_bytes"], 10)

    def test_directory_size_stops_when_budget_already_exceeded(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temporary:
            folder = Path(temporary)
            for index in range(4):
                (folder / str(index)).write_bytes(b"x" * 30)
            self.assertEqual(resources.directory_size(folder), 120)
            self.assertGreater(resources.directory_size(folder, stop_after=40), 40)

    def test_low_filesystem_space_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temporary:
            with mock.patch.object(resources.shutil, "disk_usage", return_value=resources.shutil._ntuple_diskusage(100, 90, 10)):
                with self.assertRaisesRegex(resources.ResourceLimitError, "free disk"):
                    resources.check_disk_budget(Path(temporary), reserve_bytes=5, max_bytes=100, min_free_bytes=8)

    def test_invalid_environment_limits_are_not_silently_unbounded(self):
        with mock.patch.dict(os.environ, {"MAX_WORKER_MB": "nan"}):
            with self.assertRaises(resources.ResourceLimitError):
                resources.worker_memory_limit()
        with mock.patch.dict(os.environ, {"MAX_DISK_GB": "-1"}):
            with self.assertRaises(resources.ResourceLimitError):
                resources.disk_limit()


@unittest.skipUnless(os.name == "nt", "Windows Job Object integration")
class WindowsContainmentTests(unittest.TestCase):
    @staticmethod
    def _spawn(code: str):
        # A Windows venv launcher starts a second Python process before the
        # test can attach containment. Run the actual interpreter directly,
        # retaining CPython's normal venv/site/.pth path initialization.
        environment = os.environ.copy()
        environment["__PYVENV_LAUNCHER__"] = sys.executable
        return subprocess.Popen([sys._base_executable, "-u", "-c", code], cwd=ROOT, env=environment,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, creationflags=subprocess.CREATE_NO_WINDOW)

    @staticmethod
    def _open_wait_handle(pid: int):
        kernel = resources._kernel32()
        kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel.WaitForSingleObject.restype = ctypes.c_uint32
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, one known test child.
        if not handle:
            raise AssertionError(f"Could not open controlled child {pid}.")
        return kernel, handle

    def test_structures_have_win64_layout(self):
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(resources._BasicLimitInformation), 64)
            self.assertEqual(ctypes.sizeof(resources._ExtendedLimitInformation), 144)
            self.assertEqual(ctypes.sizeof(resources._BasicAccountingInformation), 48)

    def test_direct_interpreter_retains_virtualenv_and_matches_popen_identity(self):
        process = self._spawn("import os,sys,json,PIL; print(json.dumps([os.getpid(),sys.prefix,PIL.__version__]),flush=True)")
        stdout, stderr = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 0, stderr)
        actual_pid, prefix, pillow_version = json.loads(stdout)
        self.assertEqual(actual_pid, process.pid)
        self.assertEqual(Path(prefix).resolve(), Path(sys.prefix).resolve())
        self.assertTrue(pillow_version)

    def test_unavailable_containment_fails_clearly_and_records_health_status(self):
        process = self._spawn("import time; time.sleep(30)")
        try:
            with mock.patch.object(resources, "_kernel32", side_effect=resources.ResourceLimitError("Restricted test host")):
                with self.assertRaisesRegex(resources.ResourceLimitError, "Restricted test host"):
                    resources.contain_process(process)
            status = resources.resource_status()["last_assignment"]
            self.assertEqual(status["method"], "unavailable")
            self.assertFalse(status["parent_exit_cleanup"])
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=8)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_closing_job_terminates_controlled_worker_and_descendant(self):
        code = "import subprocess,sys,time; sys.stdin.readline(); p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(p.pid,flush=True); time.sleep(30)"
        process = self._spawn(code)
        containment = None
        descendant_handle = None
        try:
            containment = resources.contain_process(process, memory_mb=128)
            self.assertTrue(containment.status["parent_exit_cleanup"])
            self.assertTrue(containment.status["memory_hard_limit"])
            process.stdin.write("go\n")
            process.stdin.flush()
            child_pid = int(_line(process))
            kernel, descendant_handle = self._open_wait_handle(child_pid)
            containment.close()
            self.assertTrue(containment.status["cleanup_complete"])
            self.assertTrue(resources.resource_status()["last_assignment"]["closed"])
            process.wait(timeout=8)
            self.assertIsNotNone(process.returncode)
            self.assertEqual(kernel.WaitForSingleObject(descendant_handle, 8000), 0)
            containment.close()  # Idempotent; must not touch a reused process ID.
        finally:
            if containment:
                containment.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=8)
            if descendant_handle:
                kernel.CloseHandle(descendant_handle)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_job_memory_limit_rejects_actual_allocation(self):
        code = "import sys; sys.stdin.readline(); chunks=[]\ntry:\n for _ in range(64): chunks.append(bytearray(8*1024*1024))\n print('UNBOUNDED',flush=True)\nexcept MemoryError:\n print('MEMORY_LIMIT:'+str(len(chunks)),flush=True)"
        process = self._spawn(code)
        containment = None
        try:
            containment = resources.contain_process(process, memory_mb=96)
            process.stdin.write("go\n")
            process.stdin.flush()
            stdout, stderr = process.communicate(timeout=12)
            self.assertNotIn("UNBOUNDED", stdout)
            self.assertIn("MEMORY_LIMIT:", stdout, (stdout, stderr))
            allocated_blocks = int(stdout.strip().split(":")[-1])
            self.assertLess(allocated_blocks * 8 * 1024**2, 96 * 1024**2)
            observed = containment.check()
            # The observed high-water counter can include an attempted commit
            # before it is rejected. Actual allocation denial is the oracle.
            self.assertGreater(observed["peak_committed_bytes"], 0)
        finally:
            if containment:
                containment.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=8)

    def test_abrupt_controller_exit_closes_job_and_kills_worker_tree(self):
        # The controller owns the sole non-inheritable Job Object handle. Killing
        # this one test controller exercises OS handle cleanup, with no finally.
        controller_code = """import json,os,subprocess,sys
from backend.resources import contain_process
worker_code='import subprocess,sys,time; sys.stdin.readline(); p=subprocess.Popen([sys.executable,\"-c\",\"import time; time.sleep(30)\"]); print(p.pid,flush=True); time.sleep(30)'
environment=os.environ.copy(); environment['__PYVENV_LAUNCHER__']=sys.executable
worker=subprocess.Popen([sys._base_executable,'-u','-c',worker_code],env=environment,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
guard=contain_process(worker,memory_mb=128)
worker.stdin.write('go\\n'); worker.stdin.flush()
descendant=int(worker.stdout.readline())
print(json.dumps([worker.pid,descendant]),flush=True)
sys.stdin.readline()
guard.close()
"""
        controller = self._spawn(controller_code)
        opened = []
        try:
            worker_pid, descendant_pid = json.loads(_line(controller))
            for pid in (worker_pid, descendant_pid):
                opened.append(self._open_wait_handle(pid))
            controller.kill()
            controller.wait(timeout=8)
            for kernel, handle in opened:
                self.assertEqual(kernel.WaitForSingleObject(handle, 8000), 0)
        finally:
            if controller.poll() is None:
                controller.kill()
                controller.wait(timeout=8)
            for kernel, handle in opened:
                kernel.CloseHandle(handle)
            for stream in (controller.stdin, controller.stdout, controller.stderr):
                stream.close()


if __name__ == "__main__":
    unittest.main()
