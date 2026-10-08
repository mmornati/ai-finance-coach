"""tests/check_api_coverage.py: the endpoint-coverage guard over the CI shards (synthetic shard files)."""
from __future__ import annotations

import json

from check_api_coverage import check

SPEC = {"/api/v1/health": ["get"], "/api/v1/items/{item_id}": ["get", "delete"]}


def _write(tmp_path, shard, calls, spec=None):
    f = tmp_path / f"api-calls-{shard.replace('/', '-')}.json"
    f.write_text(json.dumps({"shard": shard, "calls": calls, "spec": spec or {}}))
    return f


def test_the_union_of_the_shards_covers_every_endpoint(tmp_path):
    files = [_write(tmp_path, "1/3", [["GET", "/api/v1/health", 200]]),
             _write(tmp_path, "2/3", [["GET", "/api/v1/items/7", 200]], SPEC),
             _write(tmp_path, "3/3", [["DELETE", "/api/v1/items/7", 204]])]
    assert check(files) == []


def test_an_endpoint_no_shard_answered_successfully_fails(tmp_path):
    files = [_write(tmp_path, "1/2", [["GET", "/api/v1/health", 200], ["DELETE", "/api/v1/items/7", 404]], SPEC),
             _write(tmp_path, "2/2", [["GET", "/api/v1/items/7", 200]])]
    assert check(files) == ["no successful call to DELETE /api/v1/items/{item_id}"]


def test_a_missing_shard_or_a_missing_schema_fails(tmp_path):
    assert check([_write(tmp_path, "1/2", [], SPEC)]) == ["shards missing: got ['1/2'] of 2"]
    assert check([_write(tmp_path, "1/1", [["GET", "/api/v1/health", 200]])]) == [
        "no shard ran test_api_zz_coverage.py: the schema is missing"]
