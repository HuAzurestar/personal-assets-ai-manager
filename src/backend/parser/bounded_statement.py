"""Killable 30-second local parse boundary; never queue unbounded workers."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import BoundedSemaphore
from time import monotonic
from backend.error import TargetIntakeError

_parser_slot = BoundedSemaphore(1)


def parse_statement(content, filename, password=None, source=None, *, source_timezone, deadline=None):
    deadline = deadline if deadline is not None else monotonic() + 30
    if not _parser_slot.acquire(blocking=False):
        raise TargetIntakeError(503, "local parser is busy; reduce or retry upload", code="PARSE_BUSY")
    process = None
    try:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TargetIntakeError(422, "parse deadline exceeded", code="PARSE_LIMIT")
        request = dict(content=base64.b64encode(content).decode("ascii"), filename=filename, password=password,
                       source=source, timezone=str(source_timezone))
        env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("statement_worker.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        output, _stderr = process.communicate(input=json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                                             timeout=max(.001, deadline - monotonic()))
        if process.returncode or len(output) > 24 * 1024 * 1024:
            raise ValueError("PARSE_ERROR")
        result = json.loads(output)
        if result.get("error") == "PARSE_LIMIT":
            raise TargetIntakeError(422, "parsed content exceeds safety budget", code="PARSE_LIMIT")
        if "document" not in result:
            raise ValueError("PARSE_ERROR")
        return result["document"]
    except subprocess.TimeoutExpired:
        # Stop this exact disposable process and drain pipes; no background
        # parser remains alive after timeout or keeps the request's secrets.
        process.kill()
        process.communicate()
        raise TargetIntakeError(422, "parse deadline exceeded", code="PARSE_LIMIT") from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.communicate()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        _parser_slot.release()
