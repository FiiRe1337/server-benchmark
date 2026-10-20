"""sb-progress/1 JSON Lines: incremental line splitting, replay, stream rules (tools/check_mock.py), ring buffer."""
import json, os, sys

import numpy as np
import pytest

from conftest import GUI, demo

sys.path.insert(0, os.path.join(GUI, "tools"))


@pytest.fixture
def source(qapp):
    from sbgui.sbench import EventSource
    s = EventSource(); s.events, s.errors = [], []
    s.event.connect(s.events.append); s.stderr.connect(s.errors.append)
    return s


def test_emit_lines_handles_partial_chunks(source):
    data = open(demo("progress.jsonl"), "rb").read()
    buf = b""
    for i in range(0, len(data), 37):                                 # arbitrary pipe chunking
        buf = source._emit_lines(data[i:i + 37], buf)
    assert buf.strip() == b""
    n = sum(1 for ln in data.splitlines() if ln.strip())
    assert len(source.events) == n and not source.errors
    assert source.events[0]["ev"] == "start" and source.events[-1]["ev"] == "done"


def test_emit_lines_crlf_and_bad_line(source):
    buf = source._emit_lines(b'{"ev":"start","run_id":"x","tests":1}\r\nnot json\n{"ev":', b"")
    assert [e["ev"] for e in source.events] == ["start"]
    assert len(source.errors) == 1 and "bad line" in source.errors[0]
    assert buf == b'{"ev":'                                          # kept for the next chunk


def test_replay_is_deterministic(qapp):
    from sbgui.sbench import Replay
    r = Replay(demo("progress.jsonl")); got, fin = [], []
    r.event.connect(got.append); r.finished.connect(lambda c, t: fin.append((c, t)))
    r.feed(5); assert len(got) == 5 and not fin
    r.feed_until(lambda e: e.get("ev") == "tick"); assert got[-1]["ev"] == "tick"
    r.feed(); assert len(got) == len(r.events) and fin == [(0, "ok")]


def test_stream_rules():
    import check_mock
    text = open(demo("progress.jsonl"), encoding="utf-8").read()
    assert check_mock._stream_errs(text) == []
    lines = text.splitlines()
    assert any("first event" in e for e in check_mock._stream_errs("\n".join(lines[1:])))
    assert any("done must be" in e for e in check_mock._stream_errs("\n".join(lines + [lines[-1]])))
    err_only = json.dumps({"ev": "error", "code": "E_SCHEMA", "msg": "x", "field": ""})
    assert check_mock._stream_errs(err_only) == []                  # pre-start failure: only error events


def test_ring_buffer_and_downsample():
    from sbgui.buffers import RingBuffer, minmax_downsample
    rb = RingBuffer(capacity=4, fields=("t", "v"))
    for i in range(6):
        rb.append((i, i * 10))
    assert rb.n == 4 and list(rb.col("t")) == [2, 3, 4, 5] and rb.last()[1] == 50
    x = np.arange(10_000, dtype=float); y = np.sin(x); y[1234] = 99
    xs, ys = minmax_downsample(x, y, 500)
    assert len(xs) <= 500 and 99 in ys and np.all(np.diff(xs) >= 0)
