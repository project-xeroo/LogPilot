"""Upload validation: extension, size, emptiness, binary content, archive-bomb limits."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from shared.config import settings

ALLOWED_EXTENSIONS = (".log", ".txt", ".json", ".csv", ".zip", ".gz", ".ndjson", ".jsonl")
INNER_EXTENSIONS = (".log", ".txt", ".json", ".csv", ".ndjson", ".jsonl", ".gz")
MAX_COMPRESSION_RATIO = 1000  # decompressed / compressed; anything beyond is treated as a bomb


class UploadRejected(Exception):
    """Raised with one or more human-readable validation errors."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class ValidationResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)


def validate_filename(filename: str | None) -> ValidationResult:
    r = ValidationResult()
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    if not name:
        r.add("File name is missing.")
    elif not name.lower().endswith(ALLOWED_EXTENSIONS):
        r.add(f"Unsupported file type '{name}'. Supported: .log, .txt, .json, .csv, .zip, .gz.")
    return r


def validate_declared_size(size: int | None) -> ValidationResult:
    r = ValidationResult()
    if size is not None and size > settings.max_upload_bytes:
        r.add(f"File is {size / 1024 / 1024:.0f}MB; the maximum is {settings.max_upload_bytes // 1024 // 1024}MB.")
    if size == 0:
        r.add("File is empty.")
    return r


def looks_binary(sample: bytes) -> bool:
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    nontext = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return nontext / len(sample) > 0.30


def inner_name_ok(name: str) -> bool:
    n = name.lower()
    return n.endswith(INNER_EXTENSIONS) and not n.startswith("__macosx/") and not PurePosixPath(n).name.startswith(".")
