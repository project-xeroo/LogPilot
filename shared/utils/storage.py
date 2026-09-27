"""S3-compatible object storage (raw/redacted log files)."""
from __future__ import annotations

import gzip
import io
from collections.abc import Iterator
from functools import lru_cache
from typing import BinaryIO

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from shared.config import settings


@lru_cache
def s3():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 5}),
    )


def ensure_bucket() -> None:
    try:
        s3().head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        s3().create_bucket(Bucket=settings.s3_bucket)


class _Sink:
    """Multipart-uploads whatever is written to it (streams up to 500MB without buffering it all)."""

    PART = 8 * 1024 * 1024

    def __init__(self, key: str, content_type: str = "application/gzip"):
        self.key, self.bucket = key, settings.s3_bucket
        self._buf = bytearray()
        self._parts: list[dict] = []
        self._n = 0
        self.bytes_written = 0
        self._mp = s3().create_multipart_upload(Bucket=self.bucket, Key=key, ContentType=content_type)["UploadId"]

    def write(self, data: bytes) -> int:
        self._buf += data
        self.bytes_written += len(data)
        while len(self._buf) >= self.PART:
            self._flush(bytes(self._buf[: self.PART]))
            del self._buf[: self.PART]
        return len(data)

    def flush(self) -> None:  # file-object protocol (gzip calls it)
        pass

    def _flush(self, chunk: bytes) -> None:
        self._n += 1
        r = s3().upload_part(Bucket=self.bucket, Key=self.key, UploadId=self._mp, PartNumber=self._n, Body=chunk)
        self._parts.append({"ETag": r["ETag"], "PartNumber": self._n})

    def complete(self) -> None:
        if self._buf or not self._parts:
            self._flush(bytes(self._buf))
            self._buf.clear()
        s3().complete_multipart_upload(
            Bucket=self.bucket, Key=self.key, UploadId=self._mp, MultipartUpload={"Parts": self._parts}
        )

    def abort(self) -> None:
        try:
            s3().abort_multipart_upload(Bucket=self.bucket, Key=self.key, UploadId=self._mp)
        except Exception:  # pragma: no cover
            pass


class GzipObjectWriter:
    """Write text lines -> gzip -> S3 multipart, as a context manager."""

    def __init__(self, key: str):
        self.key = key
        self._sink = _Sink(key)
        self._gz = gzip.GzipFile(fileobj=self._sink, mode="wb", compresslevel=5)  # type: ignore[arg-type]

    def write_line(self, line: str) -> None:
        self._gz.write(line.encode("utf-8", "replace"))
        self._gz.write(b"\n")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self._sink.abort()
            return False
        self._gz.close()
        self._sink.complete()
        return False

    @property
    def compressed_bytes(self) -> int:
        return self._sink.bytes_written


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    s3().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)


def put_gzip_lines(key: str, lines: list[str]) -> None:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write("\n".join(lines).encode("utf-8", "replace") + b"\n")
    put_bytes(key, buf.getvalue(), "application/gzip")


def open_stream(key: str) -> BinaryIO:
    return s3().get_object(Bucket=settings.s3_bucket, Key=key)["Body"]  # type: ignore[return-value]


def iter_gzip_lines(key: str) -> Iterator[str]:
    body = open_stream(key)
    with gzip.GzipFile(fileobj=body) as gz:  # type: ignore[arg-type]
        for raw in gz:
            yield raw.decode("utf-8", "replace").rstrip("\r\n")


def healthy() -> bool:
    try:
        s3().head_bucket(Bucket=settings.s3_bucket)
        return True
    except Exception:
        return False
