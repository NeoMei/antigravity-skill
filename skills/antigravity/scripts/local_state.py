"""Atomic local JSON files used by the bridge."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile

UUID = re.compile(r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b")
UNSET = object()


@contextmanager
def locked(path):
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value, *, replace=False, expected=UNSET):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".accowork-", delete=False) as stream:
        temp = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            if replace:
                if path.exists():
                    temp.chmod(path.stat().st_mode & 0o777)
                if expected is not UNSET:
                    try:
                        current = path.read_bytes()
                    except FileNotFoundError:
                        current = None
                    if current != expected:
                        raise ValueError(f"配置刚被更新，请重试：{path}。")
                temp.replace(path)
            else:
                os.link(temp, path)
        finally:
            temp.unlink(missing_ok=True)
