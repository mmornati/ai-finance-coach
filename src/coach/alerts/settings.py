"""``[alerts]`` of config.toml (E10): noise control, the privacy level of external messages and the channel sections.

Everything has a default and every external channel is OFF by default. A wrong VALUE (an ``http://`` URL, a TLS mode other than
``starttls`` / ``ssl``, an unknown kind) refuses to load; an ENABLED channel with a missing field is only reported
(``channel_problems``) and skipped, so a half-written config never stops the daily job.

    [alerts]
    enabled = true                  # evaluate the signals and keep the events (the in-app feed)
    min_severity = "low"            # low | medium | high: below it an event is kept but never sent outside the app
    external_detail = "minimal"     # minimal | summary: what the EXTERNAL channels (ntfy, e-mail, Telegram) may say
    quiet_hours = "22:00-08:00"     # nothing is sent by any channel in this local window ("" = off); it is sent after
    max_per_week = 7                # messages per channel in a rolling 7 days (0 = send nothing)
    disabled_kinds = []             # kinds that are not evaluated at all
    digest_only_kinds = ["price_increase", "unusual_charge"]   # default; kinds never sent one by one, only in the weekly digest
    external_min_severity = "medium"    # nothing below it is sent by any channel (in-app is unaffected)
    weekly_digest = true            # build the weekly summary (local, deterministic) into the in-app feed
    weekly_digest_day = "mon"
    weekly_digest_to_channels = false   # also send the (short) teaser to the enabled channels
    app_url = ""                    # optional link added to external messages ("" = just 'Open the app.')
    [alerts.thresholds]
    sync_failures = 3               # consecutive failed syncs of an account before an alert
    anomaly_min_severity = "medium"
    [alerts.macos] ...  [alerts.ntfy] ...  [alerts.email] ...  [alerts.telegram] ...   (see config.example.toml)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

KINDS = ("consent", "sync_failing", "unusual_charge", "price_increase", "low_balance", "budget", "loan_alert", "loa_end",
         "unused_subscription", "contract_notice", "decision_contradicted", "llm_usage_high", "kid_budget", "scheme_end", "scheme_check",
         "rent_missing")
# kinds that exist in the LOCAL feed only (the web app, `coach alerts list`): no channel ever receives them, not even as a count (E12-4)
LOCAL_ONLY_KINDS = ("llm_usage_high", "kid_budget")      # kid_budget (E14-6): a child's data never leaves this machine through an alert
SEVERITIES = ("low", "medium", "high")
RANK = {s: i for i, s in enumerate(SEVERITIES)}
CHANNELS = ("macos", "ntfy", "email", "telegram")
EXTERNAL = ("ntfy", "email", "telegram")           # third-party transports: minimal, guarded messages
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TOP_KEYS = {"enabled", "min_severity", "external_min_severity", "external_detail", "quiet_hours", "max_per_week", "disabled_kinds", "digest_only_kinds",
            "weekly_digest", "weekly_digest_day", "weekly_digest_to_channels", "app_url", "thresholds", "macos", "ntfy", "email",
            "telegram"}
SECRET_OF = {"ntfy": "ntfy_token", "email": "smtp_password", "telegram": "telegram_bot_token"}


class AlertConfigError(ValueError):
    pass


def _bool(name: str, v) -> bool:
    if not isinstance(v, bool):
        raise AlertConfigError(f"[alerts] {name} must be true or false (unquoted), got {v!r}")
    return v


def _str(name: str, v, allow_empty: bool = True) -> str:
    if not isinstance(v, str) or (not allow_empty and not v.strip()):
        raise AlertConfigError(f"[alerts] {name} must be a string, got {v!r}")
    return v.strip()


def _int(name: str, v, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise AlertConfigError(f"[alerts] {name} must be a whole number between {lo} and {hi}, got {v!r}")
    return v


def _kinds(name: str, v) -> tuple:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise AlertConfigError(f"[alerts] {name} must be a list of alert kinds, got {v!r}")
    bad = [x for x in v if x not in KINDS]
    if bad:
        raise AlertConfigError(f"[alerts] {name}: unknown kind(s) {', '.join(map(repr, bad))} (known: {', '.join(KINDS)})")
    return tuple(dict.fromkeys(v))


def _table(name: str, v, allowed: set) -> dict:
    if not isinstance(v, dict):
        raise AlertConfigError(f"[alerts.{name}] must be a table")
    extra = sorted(set(v) - allowed)
    if extra:
        raise AlertConfigError(f"unknown [alerts.{name}] setting(s): {', '.join(extra)} (known: {', '.join(sorted(allowed))})")
    return v


def parse_quiet(v: str):
    """'22:00-08:00' -> (minutes_from, minutes_to); '' -> None. The window may span midnight."""
    if not v:
        return None
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)", v)
    if not m:
        raise AlertConfigError(f"[alerts] quiet_hours must look like \"22:00-08:00\" (local time) or be empty, got {v!r}")
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    if a == b:
        raise AlertConfigError("[alerts] quiet_hours: the start and the end are the same time")
    return a, b


def https_url(name: str, v: str) -> str:
    u = urlparse(v)
    if u.scheme != "https" or not u.hostname:
        raise AlertConfigError(f"[alerts.{name}] url must be an https:// URL (nothing is ever sent over plain http), got {v!r}")
    if u.username or u.password:
        raise AlertConfigError(f"[alerts.{name}] url must not contain a user name or password (the token goes to the Keychain)")
    return v


@dataclass(frozen=True)
class MacosChannel:
    enabled: bool = False


@dataclass(frozen=True)
class NtfyChannel:
    enabled: bool = False
    url: str = ""                       # https://ntfy.sh/<topic> or your own server's topic URL


@dataclass(frozen=True)
class EmailChannel:
    enabled: bool = False
    host: str = ""
    port: int = 587
    tls: str = "starttls"               # starttls | ssl (required: a plain connection is refused)
    username: str = ""
    from_addr: str = ""
    to: str = ""


@dataclass(frozen=True)
class TelegramChannel:
    enabled: bool = False
    chat_id: str = ""


@dataclass(frozen=True)
class AlertSettings:
    enabled: bool = True
    min_severity: str = "low"
    external_detail: str = "minimal"
    quiet_hours: str = ""
    max_per_week: int = 7
    disabled_kinds: tuple = ()
    digest_only_kinds: tuple = ("price_increase", "unusual_charge")     # noisy kinds: in the app and the weekly summary, not sent one by one
    external_min_severity: str = "medium"                          # nothing below it is sent by any channel
    weekly_digest: bool = True
    weekly_digest_day: str = "mon"
    weekly_digest_to_channels: bool = False
    app_url: str = ""
    sync_failures: int = 3
    anomaly_min_severity: str = "medium"
    macos: MacosChannel = field(default_factory=MacosChannel)
    ntfy: NtfyChannel = field(default_factory=NtfyChannel)
    email: EmailChannel = field(default_factory=EmailChannel)
    telegram: TelegramChannel = field(default_factory=TelegramChannel)

    @classmethod
    def from_dict(cls, raw) -> "AlertSettings":
        raw = dict(raw or {})
        extra = sorted(set(raw) - TOP_KEYS)
        if extra:
            raise AlertConfigError(f"unknown [alerts] setting(s): {', '.join(extra)} (known: {', '.join(sorted(TOP_KEYS))})")
        kw: dict = {}
        if "enabled" in raw:
            kw["enabled"] = _bool("enabled", raw["enabled"])
        for k, allowed in (("min_severity", SEVERITIES), ("external_min_severity", SEVERITIES), ("external_detail", ("minimal", "summary")), ("weekly_digest_day", DAYS)):
            if k in raw:
                v = _str(k, raw[k], False).lower()
                if v not in allowed:
                    raise AlertConfigError(f"[alerts] {k} must be one of {', '.join(allowed)}, got {raw[k]!r}")
                kw[k] = v
        if "quiet_hours" in raw:
            kw["quiet_hours"] = _str("quiet_hours", raw["quiet_hours"])
            parse_quiet(kw["quiet_hours"])
        if "max_per_week" in raw:
            kw["max_per_week"] = _int("max_per_week", raw["max_per_week"], 0, 1000)
        for k in ("disabled_kinds", "digest_only_kinds"):
            if k in raw:
                kw[k] = _kinds(k, raw[k])
        for k in ("weekly_digest", "weekly_digest_to_channels"):
            if k in raw:
                kw[k] = _bool(k, raw[k])
        if "app_url" in raw:
            u = _str("app_url", raw["app_url"])
            if u and (urlparse(u).scheme not in ("http", "https") or not urlparse(u).hostname):
                raise AlertConfigError(f"[alerts] app_url must be an http(s) URL or empty, got {u!r}")
            kw["app_url"] = u
        if "thresholds" in raw:
            t = _table("thresholds", raw["thresholds"], {"sync_failures", "anomaly_min_severity"})
            if "sync_failures" in t:
                kw["sync_failures"] = _int("thresholds.sync_failures", t["sync_failures"], 1, 100)
            if "anomaly_min_severity" in t:
                v = _str("thresholds.anomaly_min_severity", t["anomaly_min_severity"], False).lower()
                if v not in SEVERITIES:
                    raise AlertConfigError(f"[alerts.thresholds] anomaly_min_severity must be one of {', '.join(SEVERITIES)}")
                kw["anomaly_min_severity"] = v
        if "macos" in raw:
            t = _table("macos", raw["macos"], {"enabled"})
            kw["macos"] = MacosChannel(_bool("macos.enabled", t.get("enabled", False)))
        if "ntfy" in raw:
            t = _table("ntfy", raw["ntfy"], {"enabled", "url"})
            url = _str("ntfy.url", t.get("url", ""))
            if url:
                https_url("ntfy", url)
            kw["ntfy"] = NtfyChannel(_bool("ntfy.enabled", t.get("enabled", False)), url)
        if "email" in raw:
            t = _table("email", raw["email"], {"enabled", "host", "port", "tls", "username", "from", "to"})
            tls = _str("email.tls", t.get("tls", "starttls"), False).lower()
            if tls not in ("starttls", "ssl"):
                raise AlertConfigError("[alerts.email] tls must be \"starttls\" or \"ssl\": a connection without TLS is never used")
            for k in ("from", "to"):
                v = _str(f"email.{k}", t.get(k, ""))
                if v and not re.fullmatch(r"[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+", v):
                    raise AlertConfigError(f"[alerts.email] {k} must be one e-mail address, got {v!r}")
            kw["email"] = EmailChannel(_bool("email.enabled", t.get("enabled", False)), _str("email.host", t.get("host", "")),
                                       _int("email.port", t.get("port", 587), 1, 65535), tls,
                                       _str("email.username", t.get("username", "")), _str("email.from", t.get("from", "")),
                                       _str("email.to", t.get("to", "")))
        if "telegram" in raw:
            t = _table("telegram", raw["telegram"], {"enabled", "chat_id"})
            chat = t.get("chat_id", "")
            if isinstance(chat, int) and not isinstance(chat, bool):
                chat = str(chat)
            chat = _str("telegram.chat_id", chat)
            if chat and not re.fullmatch(r"-?\d{1,20}|@[A-Za-z0-9_]{4,64}", chat):
                raise AlertConfigError("[alerts.telegram] chat_id must be a number (or @channelname)")
            kw["telegram"] = TelegramChannel(_bool("telegram.enabled", t.get("enabled", False)), chat)
        return cls(**kw)

    # ------------------------------------------------------------ helpers
    def quiet(self):
        return parse_quiet(self.quiet_hours)

    def channel(self, name: str):
        return getattr(self, name)

    def enabled_channels(self) -> list[str]:
        return [c for c in CHANNELS if self.channel(c).enabled]

    def missing(self, name: str) -> list[str]:
        """Config fields an ENABLED channel still needs (empty = ready); secrets are checked by the channel itself."""
        c = self.channel(name)
        need = {"macos": [], "ntfy": ["url"], "email": ["host", "username", "from_addr", "to"], "telegram": ["chat_id"]}[name]
        return [("from" if f == "from_addr" else f) for f in need if not getattr(c, f)]

    def rows(self) -> list[tuple[str, str]]:
        out = [("alerts.enabled", str(self.enabled).lower()), ("alerts.min_severity", self.min_severity),
               ("alerts.external_detail", self.external_detail), ("alerts.quiet_hours", self.quiet_hours or "(off)"),
               ("alerts.max_per_week", str(self.max_per_week)),
               ("alerts.disabled_kinds", ", ".join(self.disabled_kinds) or "(none)"),
               ("alerts.digest_only_kinds", ", ".join(self.digest_only_kinds) or "(none)"),
               ("alerts.weekly_digest", f"{str(self.weekly_digest).lower()} ({self.weekly_digest_day})"),
               ("alerts.weekly_digest_to_channels", str(self.weekly_digest_to_channels).lower()),
               ("alerts.thresholds.sync_failures", str(self.sync_failures)),
               ("alerts.thresholds.anomaly_min_severity", self.anomaly_min_severity)]
        for c in CHANNELS:
            out.append((f"alerts.{c}.enabled", str(self.channel(c).enabled).lower()))
        return out


def warnings_of(s: AlertSettings, allowed_hosts=()) -> list[str]:
    """Non-fatal advice: an `app_url` that is not this machine (or a host you allowed in [ui]) would put that host name in every
    external message, and so in front of the provider."""
    import ipaddress
    out = []
    if s.app_url:
        host = (urlparse(s.app_url).hostname or "").lower()
        ok = host in ("localhost", "127.0.0.1", "::1") or host in {h.lower() for h in allowed_hosts}
        if not ok:
            try:
                ipaddress.ip_address(host)
                ok = True
            except ValueError:
                pass
        if not ok:
            out.append(f"[alerts] app_url points at {host!r}, which is not localhost, an IP address or a host in [ui] allowed_hosts: "
                       "that name is written in every external message")
    return out
