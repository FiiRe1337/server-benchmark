"""Pure helpers on sb-*/1 documents: units, labels, destructive-target detection, schema version gate."""
import json

import pytest

from conftest import demo, profile
from sbgui import model


@pytest.mark.parametrize("v,b", [(4096, 4096), ("4KiB", 4096), ("64KiB", 65536), ("1 MiB", 1 << 20), ("16GiB", 16 << 30)])
def test_size_bytes(v, b):
    assert model.size_bytes(v) == b


@pytest.mark.parametrize("v", ["4KB", "0KiB", "-1MiB", "abc", "4 kib"])
def test_size_bytes_rejects(v):
    with pytest.raises(ValueError):
        model.size_bytes(v)


@pytest.mark.parametrize("b,s", [(4096, "4KiB"), (1 << 20, "1MiB"), (3 << 30, "3GiB"), (1536, 1536)])
def test_size_str_roundtrip(b, s):
    assert model.size_str(b) == s
    if isinstance(s, str):
        assert model.size_bytes(s) == b


def test_short_and_human():
    assert model.short_size(4096) == "4K" and model.short_size(65536) == "64K" and model.short_size(512) == "512B"
    assert model.human_bytes(256 * 1024**3) == "256 GiB"
    assert model.human_bytes(int(1.5 * 1024**3)) == "1,5 GiB"          # German decimal comma
    assert model.human_bytes(1234 * 1024**3) == "1.234 GiB"           # German thousands separator


def test_spec_and_test_labels():
    s = {"weight": 60, "block_size": "4KiB", "read_pct": 100, "random_pct": 100}
    w = {"weight": 40, "block_size": "64KiB", "read_pct": 0, "random_pct": 0}
    assert model.spec_label(s) == "4K rand read"
    assert model.spec_label(w) == "64K seq write"
    assert model.spec_label({**s, "read_pct": 70}) == "4K rand 70/30"
    assert model.spec_label({**s, "name": "OLTP"}) == "OLTP"
    cfg = {"type": "storage", "access_specs": [s, w]}
    assert model.test_label(cfg) == "Mix: 60% 4K rand read + 40% 64K seq write"
    assert model.test_label({**cfg, "name": "Mix"}) == model.match_key({**cfg, "name": "Mix"}) == "Mix"
    assert model.test_label({"type": "cpu"}) == "CPU"


def test_destructive_targets():
    oltp = json.load(open(profile("oltp-mixed.json"), encoding="utf-8"))
    corners = json.load(open(profile("four-corners.json"), encoding="utf-8"))
    assert model.destructive_targets(oltp), "oltp-mixed writes to a raw disk"
    assert model.destructive_targets(corners) == []                     # file targets only


def test_check_schema_rejects_unknown_major():
    info = json.load(open(demo("info.json"), encoding="utf-8"))
    assert model.check_schema(info, "sb-info/1") is info
    with pytest.raises(model.SchemaError):
        model.check_schema({**info, "schema": "sb-info/2"}, "sb-info/1")


def test_host_label_and_storage_tests():
    r = json.load(open(demo("result-quad-vm.json"), encoding="utf-8"))
    assert model.host_label(r["meta"]).endswith("(VM)")
    assert all(t["type"] == "storage" for t in model.storage_tests(r))
