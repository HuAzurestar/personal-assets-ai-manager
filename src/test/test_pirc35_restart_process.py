"""The restart harness must own the server process, not a venv redirector."""
import os
from pathlib import Path
import subprocess

from import_restart_browser import fixture_python


def test_fixture_handle_is_actual_python_and_releases_log(tmp_path):
    log_path = tmp_path / "owned-process.log"
    process = None
    with log_path.open("wb") as log:
        try:
            process = subprocess.Popen(
                [fixture_python(), "-u", "-c",
                 "import os,time; print(os.getpid(),flush=True); time.sleep(30)"],
                stdout=subprocess.PIPE, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            actual_pid = int(process.stdout.readline())
            assert process.pid == actual_pid, "Popen must own the server PID, not a redirector"
        finally:
            if process is not None:
                # If the negative case launched a redirector, avoid killing it
                # and orphaning the child: let its bounded script end instead.
                if 'actual_pid' in locals() and actual_pid != process.pid:
                    process.wait(timeout=40)
                elif process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                process.stdout.close()
    # Immediate rename is also an actual Windows open-handle check. No broad
    # process discovery/kill or suppressed TemporaryDirectory cleanup errors.
    renamed = tmp_path / "released-process.log"
    log_path.rename(renamed)
    assert renamed.is_file() and Path(fixture_python()).is_file()
