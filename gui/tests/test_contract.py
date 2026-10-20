"""Contract 1.1: schema discovery, validator set (incl. pending sb-bundle/1 overlay), demo data validity."""
import glob, json, os

import pytest

import sb_contract
from conftest import GUI, demo

EXPECTED = {"sb-job/1", "sb-result/1", "sb-progress/1", "sb-info/1", "sb-validate/1", "sb-runs/1", "sb-bundle/1"}


@pytest.fixture(scope="module")
def V():
    return sb_contract.validators()


def test_base_dir_is_repo_schema():
    d = sb_contract.base_dir()
    assert os.path.samefile(d, os.path.join(GUI, "..", "schema"))


def test_schema_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SBENCH_SCHEMA_DIR", str(tmp_path))      # empty dir -> ignored, falls back to repo schema
    assert os.path.isfile(os.path.join(sb_contract.base_dir(), "sb-job-1.schema.json"))


def test_validators_cover_all_formats(V):
    assert V is not None, "jsonschema missing"
    assert EXPECTED <= set(V)


def test_bundle_overlay_patch(V):
    s = V["sb-bundle/1"].schema
    assert "extra/" in s["properties"]["files"]["items"]["properties"]["path"]["pattern"]
    assert {"anonymized", "includes"} <= set(s["properties"])


@pytest.mark.parametrize("path", sorted(glob.glob(demo("*.json"))), ids=os.path.basename)
def test_demo_documents_valid(V, path):
    doc = json.load(open(path, encoding="utf-8"))
    errs = [e.message for e in V[doc["schema"]].iter_errors(doc)]
    assert not errs, errs[:5]


def test_result_requires_schema_field(V):
    doc = json.load(open(demo("result-oltp.json"), encoding="utf-8"))
    del doc["meta"]
    assert list(V["sb-result/1"].iter_errors(doc))
