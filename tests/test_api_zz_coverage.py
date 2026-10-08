"""Runs last (file name): what the API tests REALLY called, through the TestClient, against the OpenAPI schema."""
from __future__ import annotations

import pytest

from apihelpers import CALLS, ctx, missing_endpoints, world  # noqa: F401


def test_zz_every_endpoint_answered_successfully_in_these_tests(request, ctx):
    """Guard on what the tests REALLY called (not on their text): every endpoint of the OpenAPI schema must have had at least
    one successful request through the TestClient. Skipped when only some tests were selected.

    Under pytest-xdist (`-n`) the calls are spread over the workers: this test only hands the schema to the controller,
    which runs the same check on the union of every worker's calls when the session ends (`tests/conftest.py`)."""
    opt = request.config.option
    if opt.keyword or opt.markexpr or any("::" in str(a) for a in request.config.args):
        pytest.skip("partial run")
    spec = ctx.app.openapi()["paths"]
    workeroutput = getattr(request.config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput["api_spec"] = {path: list(ops) for path, ops in spec.items()}
        return
    missing = missing_endpoints(spec, CALLS)
    assert not missing, missing
