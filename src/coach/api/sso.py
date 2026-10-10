"""Sign-in through an identity-aware reverse proxy (E16): authentik forward-auth.

With ``[ui] sso = "authentik"`` every request has already passed the proxy's own login before it reaches this app, and the proxy's
outpost adds ``X-authentik-jwt``: the signed OIDC token of the person. The app verifies that signature ITSELF against the provider's
JWKS, fetched from the URL of its OWN configuration (``[ui] sso_jwks_url``: never the URL a header suggests), checks the expiry, the
issuer and the audience when configured, maps ``preferred_username`` / ``email`` / ``sub`` to a login of this app (``[ui.sso_users]``)
and only then issues the ordinary session cookie. Nothing else in the session model changes: CSRF, rate limits, the child scope and the
audit log apply as before, and the one-time login link keeps working (the fallback when the proxy is down).

A plain ``X-authentik-username`` header is NEVER trusted: anything that can reach the app's port (another container on the proxy's
network, a process on the host) could send one. The signature cannot be forged without the provider's private key.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import jwt
import requests

from coach import egress

log = logging.getLogger("coach.api.sso")

PROVIDERS = {"authentik": {"header": "x-authentik-jwt", "sign_out": "/outpost.goauthentik.io/sign_out"}}
ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512")
OWNER = "owner"                     # the mapping value of the person at the machine (a session without a user)
KEYS_TTL = 3600                     # seconds the provider's keys are kept
REFETCH_MIN = 60                    # an unknown key id triggers a new fetch at most this often
MAX_TOKEN = 8192


class SsoVerifier:
    """Verifies the proxy's token and names the login it maps to. `fetch` (tests) replaces the HTTP GET of the JWKS."""

    def __init__(self, cfg, fetch=None):
        self.cfg = cfg
        p = PROVIDERS[cfg.ui_sso]
        self.provider = cfg.ui_sso
        self.header = p["header"]
        self.sign_out = p["sign_out"]
        self.jwks_url = cfg.ui_sso_jwks_url
        self.issuer = cfg.ui_sso_issuer or None
        self.audience = cfg.ui_sso_audience or None
        self.users = {str(k): str(v) for k, v in dict(cfg.ui_sso_users).items()}
        self._fetch = fetch or self._default_fetch
        self._keys: Optional[jwt.PyJWKSet] = None
        self._fetched = 0.0
        self._lock = threading.Lock()

    # -- the provider's public keys
    def _default_fetch(self) -> dict:
        egress.allow("ui.sso", {"host": urlparse(self.jwks_url).hostname or "", "purpose": "jwks"}, cfg=self.cfg)
        r = requests.get(self.jwks_url, timeout=5, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()

    def _keyset(self, refresh: bool = False) -> jwt.PyJWKSet:
        with self._lock:
            now = time.time()
            stale = self._keys is None or now - self._fetched > KEYS_TTL
            if stale or (refresh and now - self._fetched > REFETCH_MIN):
                self._keys = jwt.PyJWKSet.from_dict(self._fetch())
                self._fetched = now
            return self._keys

    def _key(self, kid: str):
        try:
            return self._keyset()[kid]
        except KeyError:
            return self._keyset(refresh=True)[kid]              # a rotated key: fetched again (rate limited)

    # -- the token
    def identity(self, token: str) -> tuple[Optional[str], Optional[str], str]:
        """``(login id, identity, reason)``. The login id is None when the token is refused; `reason` says why in one word and the
        identity (the token's own user name) is returned ONLY for a valid token that maps to no login, so that the person sees on the
        login page which name to map. Nothing of the token is logged."""
        if not token or len(token) > MAX_TOKEN:
            return None, None, "invalid"
        try:
            header = jwt.get_unverified_header(token)
            alg, kid = header.get("alg"), header.get("kid")
            if alg not in ALGORITHMS or not isinstance(kid, str) or not kid:
                return None, None, "algorithm"
            key = self._key(kid)
            claims = jwt.decode(token, key.key, algorithms=[alg], issuer=self.issuer, audience=self.audience, leeway=30,
                                options={"require": ["exp"], "verify_iss": self.issuer is not None, "verify_aud": self.audience is not None})
        except jwt.ExpiredSignatureError:
            return None, None, "expired"
        except jwt.InvalidIssuerError:
            # the issuer is the provider's own URL (no personal data): logged so that [ui] sso_issuer can be set right
            try:
                got = jwt.decode(token, options={"verify_signature": False}).get("iss")
            except jwt.PyJWTError:
                got = None
            log.warning("sso: token refused: issuer %r differs from [ui] sso_issuer %r", got, self.issuer)
            return None, None, "issuer"
        except jwt.InvalidAudienceError:
            log.warning("sso: token refused: audience differs from [ui] sso_audience")
            return None, None, "audience"
        except (egress.EgressDenied, requests.RequestException, OSError, ValueError) as e:
            log.warning("sso: could not load the provider's keys: %s", type(e).__name__)
            return None, None, "jwks"
        except (jwt.PyJWTError, KeyError, TypeError):
            return None, None, "invalid"
        names = [claims.get(c) for c in ("preferred_username", "email", "sub")]
        names = [n for n in names if isinstance(n, str) and n]
        for n in names:
            if n in self.users:
                return self.users[n], n, ""
        return None, (names[0] if names else None), "unmapped"
