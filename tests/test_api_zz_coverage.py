"""Runs last (file name): what the API tests REALLY called, through the TestClient, against the OpenAPI schema."""
from __future__ import annotations

import pytest

from apihelpers import CALLS, SPEC, ctx, missing_endpoints, world  # noqa: F401


def test_zz_every_endpoint_answered_successfully_in_these_tests(request, ctx):
    """Guard on what the tests REALLY called (not on their text): every endpoint of the OpenAPI schema must have had at least
    one successful request through the TestClient. Skipped when only some tests were selected.

    Under pytest-xdist (`-n`) or a CI shard (`--shard K/N`) the calls are spread over processes or jobs: this test only
    records the schema, and the same check runs on the union of every call at the end of the session or, for shards, in
    `tests/check_api_coverage.py` (see `tests/conftest.py`)."""
    opt = request.config.option
    if opt.keyword or opt.markexpr or any("::" in str(a) for a in request.config.args):
        pytest.skip("partial run")
    spec = ctx.app.openapi()["paths"]
    if hasattr(request.config, "workeroutput") or request.config.getoption("--api-calls-out"):
        SPEC.update({path: list(ops) for path, ops in spec.items()})
        return
    missing = missing_endpoints(spec, CALLS)
    assert not missing, missing
