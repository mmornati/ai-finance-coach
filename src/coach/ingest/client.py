"""Enable Banking REST client (JWT RS256 auth, direct API calls, no LLM)."""
from __future__ import annotations

import time
from pathlib import Path

import jwt
import requests

from coach import egress
from coach.config import Config


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


def _purpose(method: str, path: str) -> str:
    """A coarse purpose for the journal (never the path itself: it holds session / account ids)."""
    p = path.lstrip("/").split("/")[0]
    if method.upper() == "DELETE":
        return "revoke"
    return {"auth": "connect", "sessions": "connect", "accounts": "sync", "aspsps": "banks", "application": "check"}.get(p, "api")


class EnableBankingClient:
    def __init__(self, app_id: str, private_key_path: str, api_url: str = "https://api.enablebanking.com", cfg=None):
        self.cfg = cfg                       # the [privacy] policy of this configuration (None: the process-wide one)
        self.app_id = app_id
        self.private_key_path = private_key_path
        self.api_url = api_url.rstrip("/")

    @classmethod
    def from_config(cls, cfg: Config) -> "EnableBankingClient":
        app_id, key_path = cfg.require_eb()
        return cls(app_id, key_path, cfg.eb_api_url, cfg=cfg)

    def token(self) -> str:
        key = Path(self.private_key_path).expanduser().read_text()
        now = int(time.time())
        return jwt.encode(
            {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 3600},
            key,
            algorithm="RS256",
            headers={"kid": self.app_id},
        )

    def call(self, method: str, path: str, **kw):
        # E11-1: the one gate of every Enable Banking request (refused when [privacy] offline = true); journal = host + size, no path
        egress.allow("enable_banking", {"host": egress.host_of(self.api_url), "bytes": len(str(kw.get("json") or kw.get("params") or "")),
                                        "purpose": _purpose(method, path)}, cfg=self.cfg)
        r = requests.request(
            method, f"{self.api_url}{path}", headers={"Authorization": f"Bearer {self.token()}"},
            timeout=60, **kw)
        if r.status_code >= 400:
            raise ApiError(r.status_code, r.text)
        return r.json() if r.content else {}
