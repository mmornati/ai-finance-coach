"""Runs last (file name): what the API tests REALLY called, through the TestClient, against the OpenAPI schema."""
from __future__ import annotations

import pytest

from apihelpers import CALLS, ctx, world  # noqa: F401


def test_zz_every_endpoint_answered_successfully_in_these_tests(request, ctx):
    """Guard on what the tests REALLY called (not on their text): every endpoint of the OpenAPI schema must have had at least
    one successful request through the TestClient. Skipped when only some tests were selected."""
    opt = request.config.option
    if opt.keyword or opt.markexpr or any("::" in str(a) for a in request.config.args):
        pytest.skip("partial run")
    import re
    spec = ctx.app.openapi()["paths"]
    ok = [(m, p) for m, p, status in CALLS if status < 400]
    missing = []
    for path, ops in spec.items():
        rx = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")
        for method in ops:
            if not any(m == method.upper() and rx.match(p) for m, p in ok):
                missing.append(f"{method.upper()} {path}")
    assert not missing, missing
