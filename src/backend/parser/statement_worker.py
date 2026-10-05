"""Disposable local parser process. Input/evidence never goes to logs or disk."""
import base64
import json
from pathlib import Path
import sys


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from zoneinfo import ZoneInfo
    from backend.parser.statement_parser import parse_statement
    from backend.error.statement_parse import public_parse_code
    try:
        request = json.loads(sys.stdin.buffer.read(27965000))
        content = base64.b64decode(request["content"], validate=True)
        if len(content) > 20 * 1024 * 1024:
            raise ValueError("INPUT_LIMIT")
        document = parse_statement(content, request["filename"], request["password"], request["source"],
                                   source_timezone=ZoneInfo(request["timezone"]))
        output = json.dumps(dict(document=document), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(output) > 24 * 1024 * 1024:
            output = b'{"error":"PARSE_LIMIT"}'
    except Exception as error:
        output = json.dumps(dict(error=public_parse_code(error))).encode("ascii")
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
