import gzip
import io
import zipfile

import pytest

from app.ingest import _iter_upload_lines
from app.parsers import FILE_MARKER, Malformed, ParseContext, dominant_format, parse_stream
from app.parsers.base import extract_fields, parse_timestamp
from app.parsers.formats import CustomPatternParser
from app.validators import UploadRejected, looks_binary, validate_declared_size, validate_filename

CTX = ParseContext(default_service="svc")


def parse(lines, ctx=CTX):
    fmts: list[str] = []
    out = list(parse_stream(lines, ctx, fmts))
    return out, fmts


def test_generic_iso_level_service():
    recs, fmts = parse(["2026-09-27T10:00:00.123Z ERROR [checkout-service] Redis timeout req_id=abc123 version=v2.3.1 env=prod"])
    r = recs[0]
    assert fmts == ["generic"]
    assert (r.severity, r.service, r.request_id, r.deployment_version, r.environment) == ("ERROR", "checkout-service", "abc123", "v2.3.1", "prod")
    assert r.timestamp.year == 2026 and r.timestamp.tzinfo is not None


def test_apache_access_combined():
    line = '203.0.113.5 - bob [27/Sep/2026:10:00:01 +0000] "POST /api/checkout HTTP/1.1" 502 1234 "-" "curl/8" rt=0.512'
    recs, fmts = parse([line], ParseContext(default_service="nginx"))
    r = recs[0]
    assert fmts == ["access"]
    assert r.severity == "ERROR" and r.attributes["status"] == 502 and r.attributes["latency_ms"] == 512.0


def test_nginx_error_and_syslog():
    recs, fmts = parse(["2026/09/27 10:00:00 [error] 29#29: *1 connect() failed (111: Connection refused)"])
    assert fmts == ["nginx_error"] and recs[0].severity == "ERROR"
    recs, fmts = parse(["<11>Sep 27 10:00:00 host1 sshd[123]: Failed password for root"])
    assert fmts == ["syslog3164"] and recs[0].service == "sshd" and recs[0].severity == "ERROR"
    recs, fmts = parse(['<165>1 2026-09-27T10:00:00Z host app 42 ID47 - Something happened'])
    assert fmts == ["syslog5424"] and recs[0].service == "app"


def test_json_ndjson_array_and_pretty():
    nd = ['{"ts":"2026-09-27T10:00:00Z","level":"error","service":"pay","msg":"boom","trace_id":"t1"}',
          '{"timestamp":1790503201,"severity":"INFO","app":"pay","message":"ok","version":"1.2"}']
    recs, fmts = parse(nd)
    assert fmts == ["json"] and [r.severity for r in recs] == ["ERROR", "INFO"] and recs[0].trace_id == "t1"
    arr = ["[", '  {"time":"2026-09-27T10:00:00Z","level":"WARN","message":"a {brace} \\" quote"},', '  {"time":"2026-09-27T10:00:01Z","level":"INFO","message":"b"}', "]"]
    recs, _ = parse(arr)
    assert len(recs) == 2 and "{brace}" in recs[0].message
    pretty = ["{", '  "time": "2026-09-27T10:00:00Z",', '  "message": "multi",', '  "level": "error"', "}"]
    assert len(parse(pretty)[0]) == 1


def test_csv():
    recs, fmts = parse(["timestamp,level,service,message", "2026-09-27T10:00:00Z,ERROR,pay,\"db, down\"", "bad,row"])
    assert fmts == ["csv"] and len(recs) == 2
    assert isinstance(recs[1], Malformed)
    assert recs[0].message == "db, down"


def test_multiline_stack_trace_folds_into_one_record():
    lines = [
        "2026-09-27T10:00:00Z ERROR [pay] Unhandled exception",
        "Traceback (most recent call last):",
        '  File "app.py", line 1, in <module>',
        "ValueError: boom",
        "2026-09-27T10:00:01Z INFO [pay] recovered",
    ]
    recs, _ = parse(lines)
    assert len(recs) == 2 and "Traceback" in recs[0].message and "ValueError" in recs[0].message


def test_malformed_lines_flagged_not_dropped_silently():
    recs, _ = parse(["2026-09-27T10:00:00Z INFO [a] fine", "@@@@ garbage @@@@", "2026-09-27T10:00:02Z INFO [a] fine again"])
    kinds = [type(r).__name__ for r in recs]
    assert kinds == ["ParsedRecord", "Malformed", "ParsedRecord"]


def test_file_markers_reset_service_and_format():
    lines = [FILE_MARKER + "checkout-service.log", "2026-09-27T10:00:00Z ERROR boom",
             FILE_MARKER + "payments.json", '{"ts":"2026-09-27T10:00:01Z","message":"m","level":"warn"}']
    recs, fmts = parse(lines, ParseContext())
    assert [r.service for r in recs] == ["checkout-service", "payments"]
    assert fmts == ["generic", "json"] and dominant_format(fmts) in fmts


def test_custom_pattern():
    ctx = ParseContext(custom_pattern=r"^(?P<ts>\S+) \| (?P<level>\w+) \| (?P<service>\S+) \| (?P<msg>.*)$")
    recs, fmts = parse(["2026-09-27T10:00:00Z | error | billing | card declined"], ctx)
    assert fmts == ["custom"] and recs[0].service == "billing" and recs[0].severity == "ERROR"
    with pytest.raises(ValueError):
        CustomPatternParser(r"^(?P<x>.*)$")


def test_timestamp_variants():
    assert parse_timestamp(1790503200).year == 2026
    assert parse_timestamp("2026-09-27 10:00:00,123").microsecond == 123000
    assert parse_timestamp("not a date") is None


def test_extract_fields():
    f = extract_fields("done request_id=r-1 trace_id=t-9 took 250ms status=500")
    assert f["request_id"] == "r-1" and f["trace_id"] == "t-9" and f["attributes"]["latency_ms"] == 250.0


# ---- upload handling ---------------------------------------------------------------------------------
def _lines(data: bytes, name: str):
    errs: list[str] = []
    return list(_iter_upload_lines(io.BytesIO(data), name, errs)), errs


def test_gz_and_zip_and_plain():
    text = b"2026-09-27T10:00:00Z ERROR [a] x\n2026-09-27T10:00:01Z INFO [a] y\n"
    lines, _ = _lines(gzip.compress(text), "app.log.gz")
    assert lines[0] == FILE_MARKER + "app.log" and len(lines) == 3
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a/checkout.log", text)
        z.writestr("b/pay.txt", text)
        z.writestr("image.png", b"\x89PNG")
    lines, errs = _lines(buf.getvalue(), "bundle.zip")
    assert [l for l in lines if l.startswith(FILE_MARKER)] == [FILE_MARKER + "a/checkout.log", FILE_MARKER + "b/pay.txt"]
    assert any("Skipped 1 unsupported" in e for e in errs)
    lines, _ = _lines(text, "plain.log")
    assert len(lines) == 3


def test_validation_rejections():
    assert not validate_filename("virus.exe").ok
    assert validate_filename("a.LOG").ok
    assert not validate_declared_size(501 * 1024 * 1024).ok
    assert not validate_declared_size(0).ok
    assert looks_binary(b"\x00\x01\x02")
    with pytest.raises(UploadRejected):
        _lines(b"\x00\x01\x02\x03" * 100, "x.log")
    with pytest.raises(UploadRejected):
        _lines(b"not a zip", "x.zip")
    with pytest.raises(UploadRejected):
        _lines(b"   \n", "x.log")
