"""Create one Docker credential-encryption key without printing its contents."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from cryptography.fernet import Fernet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="new key file outside PAAM_DATA_DIR")
    args = parser.parse_args()
    path = args.path.resolve()
    if not path.parent.is_dir():
        parser.error("the parent directory must already exist")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error("key file already exists; refusing to replace it")
    with os.fdopen(descriptor, "wb") as output:
        output.write(Fernet.generate_key() + b"\n")
        output.flush()
        os.fsync(output.fileno())
    print(f"Created credential key at {path}; keep it separate from the data volume")


if __name__ == "__main__":
    main()
