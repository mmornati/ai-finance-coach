"""Configuration: non-secret settings from ``config.toml`` (see ``config.example.toml``).

Precedence for each Enable Banking value: process env (``EB_APP_ID``...) > ``config.toml`` >
legacy ``prototype/ingest/.env``.  Secrets never live here (see :mod:`coach.secrets`).
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

CONFIG_NAME = "config.toml"
LEGACY_ENV_REL = Path("prototype") / "ingest" / ".env"
DEFAULT_API_URL = "https://api.enablebanking.com"


class ConfigError(Exception):
    pass


ALLOWED_LLM_BACKENDS = ("claude-code", "anthropic-api", "ollama", "openai-compatible")


def _typed(name: str, value, typ):
    """Strict type check: a safety flag like insecure_plaintext_db = "false" must not be truthy."""
    ok = isinstance(value, typ) and not (typ is int and isinstance(value, bool))
    if not ok:
        want = {str: "a string", int: "an integer", bool: "true or false (unquoted)"}[typ]
        raise ConfigError(f"{name} must be {want}, got {value!r}")
    return value


def _opt_str(name: str, v):
    return None if v is None else _typed(name, v, str)


def _num(name: str, v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise ConfigError(f"{name} must be a number >= 0, got {v!r}")
    return float(v)


def _conf(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
        raise ConfigError(f"transfers.min_confidence must be a number between 0 and 1, got {v!r}")
    return float(v)


def _conf_knn(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1:
        raise ConfigError(f"classify.knn_threshold must be a number in (0, 1], got {v!r}")
    return float(v)


def _agentnames(v):
    if not isinstance(v, list) or not all(isinstance(x, str) and re.fullmatch(r"[\w .:@/-]{1,80}", x) for x in v):
        raise ConfigError(f"coach.allowed_builtin_agents must be a list of agent names, got {v!r}")
    return tuple(v)


def _envnames(v):
    if not isinstance(v, list) or not all(isinstance(x, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]{0,60}", x) for x in v):
        raise ConfigError(f"coach.claude_env must be a list of environment variable names, got {v!r}")
    return tuple(v)


def _names_re(v):
    import re as _re
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise ConfigError(f"classify.llm_allowlist must be a list of regular expressions, got {v!r}")
    for x in v:
        try:
            _re.compile(x)
        except _re.error as e:
            raise ConfigError(f"classify.llm_allowlist: bad regex {x!r}: {e}")
    return tuple(v)


def _names(v):
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise ConfigError(f"transfers.topup_merchants must be a list of names, got {v!r}")
    return tuple(x.strip().upper() for x in v)


def _hosts(v):
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise ConfigError(f"ui.allowed_hosts must be a list of host names, got {v!r}")
    return tuple(x.strip().lower() for x in v)


def _sso_users(v):
    """[ui.sso_users]: identity -> "owner" or a login id (`coach users add`)."""
    if not isinstance(v, dict):
        raise ConfigError(f"ui.sso_users must be a table of identity = login, got {v!r}")
    out = {}
    for k, login in v.items():
        if not isinstance(k, str) or not k.strip() or not isinstance(login, str) or not re.fullmatch(r"owner|[a-z0-9][a-z0-9_-]{0,30}", login):
            raise ConfigError(f"ui.sso_users: {k!r} must map to \"owner\" or a login id (lowercase letters, digits, '-' or '_'), got {login!r}")
        out[k.strip()] = login
    return out


def find_root(start: Path | None = None) -> Path:
    """Project root: $COACH_HOME, else nearest parent holding config.toml/pyproject.toml, else the
    checkout containing this package."""
    if env := os.getenv("COACH_HOME"):
        return Path(env).expanduser().resolve()
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / CONFIG_NAME).exists() or (d / "config.example.toml").exists():
            return d
    from coach import home as home_mod
    if home_mod.in_checkout():
        return home_mod.CHECKOUT_ROOT
    return home_mod.default_home()          # an installed package (uvx / pipx): ~/.ai-finance-coach unless $COACH_HOME


@dataclass
class Config:
    root: Path
    config_path: Path | None
    data_dir: Path
    memory_dir: Path
    db_path: Path
    insecure_plaintext_db: bool = False
    db_auto_migrate: bool = True         # connect() applies pending migrations; false = only `coach db migrate` does
    eb_app_id: str | None = None
    eb_redirect_url: str | None = None
    eb_private_key_path: str | None = None
    eb_api_url: str = DEFAULT_API_URL
    llm_backend: str = "claude-code"
    llm_model: str = "sonnet"
    llm_anthropic_model: str = "claude-haiku-4-5"
    llm_batch_api: bool = False          # anthropic-api: one asynchronous Message Batches request per run (50 % cheaper)
    llm_ollama_url: str = "http://localhost:11434"
    llm_ollama_model: str = "llama3.1"
    llm_ollama_allow_remote: bool = False    # the ollama server must be on this machine unless set
    llm_anthropic_base_url: str | None = None  # None = the official endpoint (ANTHROPIC_BASE_URL is ignored)
    llm_openai_base_url: str = "https://openrouter.ai/api/v1"  # openai-compatible: the provider's /v1 root (OpenRouter, Eden AI, vLLM ...)
    llm_openai_model: str = ""           # openai-compatible: the provider's model id (e.g. "anthropic/claude-haiku-4.5" on OpenRouter)
    llm_openrouter_deny_data_collection: bool = True   # OpenRouter: only route to providers that do not store / train on prompts
    llm_allowlist: tuple = ()            # regexes of transfer-like merchant keys that may be sent to the LLM
    knn_enabled: bool = True             # label near-duplicates of already-labelled merchants without the LLM
    knn_threshold: float = 0.92          # minimum cosine similarity for such an automatic label
    knn_examples: int = 5                # nearest labelled merchants passed to the LLM per item
    sync_daily_limit: int = 4
    schedule_time: str = "07:30"
    backup_dir: Path = Path("backups")
    backup_retention: int = 14
    callback_timeout: int = 600          # seconds the local redirect server waits for the bank
    callback_container_bind: bool = False   # [callback] container_bind: written by the image's `coach init` ONLY; in a real container the redirect server may listen on 0.0.0.0
    notify_macos: bool = False           # osascript notification on consent warnings
    transfer_window_days: int = 3        # +-days between the two legs of an internal transfer
    transfer_cross_bank_window_days: int = 5   # E14-7: the same, when the two legs are on DIFFERENT banks (weekends, holidays, instant credits)
    transfer_auto_link: bool = False     # the scheduled job only PROPOSES transfer pairs unless true
    transfer_min_confidence: float = 0.85
    transfer_topup_merchants: tuple = ("REVOLUT", "LYDIA", "PAYPAL")
    health_stale_days: int = 2           # no successful sync for longer than this = stale
    import_profiles_dir: Path = Path("config/import_profiles")
    memory_history: bool = True          # keep a git change history of memory/ (needs git); false = write without
    memory_stale_months: int = 6         # liabilities / contracts facts older than this are reported by `memory check`
    memory_asset_stale_months: int = 3   # assets.yaml values older than this are reported (value reminders)
    memory_big_tx_threshold: float = 2000.0   # `questions generate`: unexplained one-off payments above this (EUR)
    memory_question_min_stake: float = 300.0  # `questions generate`: merchants with less money at stake are not asked
    schedule_memory_check: bool = True   # `schedule run` ends with a (warn-only) memory check
    schedule_analytics: bool = True      # `schedule run` ends with a (warn-only) analytics refresh (E4)
    privacy_model_detail: str = "coarse"  # [privacy] model_detail: what the analytics registry tells a model (coarse | standard)
    privacy_local_only: bool = False     # [privacy] local_only (E11-4): every LLM path is ollama on this machine, no web search, no external alert channel
    privacy_offline: bool = False        # [privacy] offline (E11-4): nothing leaves this machine at all (implies local_only; no bank sync, file imports only)
    privacy_web_enrich: bool = False     # [privacy] web_enrich (E11-1): opt-in for `classify enrich` (web search with merchant descriptors)
    privacy_egress_journal: bool = True  # [privacy] egress_journal (E11-1): log every outbound call (no payload) to the local egress_journal table
    privacy_egress_journal_days: int = 365   # [privacy] egress_journal_days: `schedule run` deletes journal rows older than this
    ui_host: str = "127.0.0.1"           # [ui] host: the web app binds here (loopback only unless allow_remote)
    ui_port: int = 8765                  # [ui] port
    ui_allow_remote: bool = False        # [ui] allow_remote: accept a non-loopback host (use Tailscale / a VPN, never the open internet)
    ui_allowed_hosts: tuple = ()         # [ui] allowed_hosts: extra Host header values accepted when allow_remote is true
    ui_session_hours: int = 12           # [ui] session_hours: a browser session (cookie) lasts this long
    ui_key_rotation_days: int = 30       # [ui] key_rotation_days: the session secret is replaced when older than this
    ui_remote_tls_ack: bool = False      # [ui] remote_tls_ack: required with allow_remote: "an HTTPS proxy (tailscale serve) fronts this"
    ui_open_browser: bool = True         # [ui] open_browser: `coach ui` opens the page in the default browser
    ui_container_bind: bool = False      # [ui] container_bind: written by the container image's `coach init` ONLY; with a real container, allows 0.0.0.0 (E13 MJ-1)
    ui_passkeys: bool = False            # [ui] passkeys (E16): a passkey (Face ID, Touch ID, a security key) can open a session; enrolled from a session
    ui_sso: str = "none"                 # [ui] sso (E16): "authentik" = an identity-aware proxy signs every person in; the app verifies its signed token
    ui_sso_jwks_url: str = ""            # [ui] sso_jwks_url: the provider's JWKS URL, as the app reaches it (never taken from a header)
    ui_sso_issuer: str = ""              # [ui] sso_issuer: the token's expected `iss` ("" = not checked; the signature always is)
    ui_sso_audience: str = ""            # [ui] sso_audience: the token's expected `aud` (the provider's client id; "" = not checked)
    ui_sso_users: dict = field(default_factory=dict)   # [ui.sso_users]: identity (username, e-mail or subject) -> "owner" or a `coach users` login id
    coach_backend: str = "claude-code"   # [coach] backend: claude-code | anthropic-api | ollama | openai-compatible (the LLM coach, not the classifier)
    coach_model: str | None = None       # [coach] model; None = the backend's default (see coach_model_effective)
    coach_max_tool_calls: int = 12       # [coach] max_tool_calls per question (digests get twice as many)
    coach_max_tokens: int = 4096         # [coach] max_tokens per model answer (anthropic-api / ollama / openai-compatible)
    coach_timeout: int = 180             # [coach] timeout_seconds for one question (digests: three times)
    coach_schedule_weekly: bool = False  # [coach] schedule_weekly: `schedule run` also writes the weekly digest (opt-in)
    coach_schedule_monthly: bool = False  # [coach] schedule_monthly: ... and the monthly review (opt-in)
    coach_max_budget_usd: float = 1.0    # [coach] max_budget_usd: --max-budget-usd of `claude -p` per question (digests x their tool factor); 0 = off
    coach_allowed_builtin_agents: tuple = ("general-purpose", "Explore", "Plan", "statusline-setup", "claude-code-guide")
    coach_allowed_plugins: tuple = ()        # [coach] allowed_plugins: plugin ids the claude init event may list (default none)
    coach_allow_builtin_plugins: bool = True  # [coach] allow_builtin_plugins: plugins whose id ends with "@builtin" are Claude Code's own
    coach_claude_env: tuple = ()         # [coach] claude_env: extra environment variable NAMES passed to `claude` (a proxy, a config dir ...)
    coach_claude_restricted: bool = False  # [coach] claude_restricted: pass --restricted to `claude -p` (ignores user/project settings)
    schedule_eval: bool = True           # [schedule] eval (E12-2): `schedule run` runs the offline classification evaluation once a week (warn-only)
    usage_monthly_warn_usd: float = 0.0  # [usage] monthly_warn_usd (E12-4): `llm_usage_high` alert (local feed) when the month's LLM cost passes it; 0 = off
    usage_include_notional: bool = True  # [usage] include_notional: a subscription's notional cost counts toward the threshold
    log_max_kb: int = 512                # [logs] max_kb (E12): the JSON-lines run log (data_dir/logs/runs.jsonl) rotates at this size
    log_keep: int = 5                    # [logs] keep: rotated files kept per log
    log_max_age_days: int = 90           # [logs] max_age_days: rotated log files older than this are deleted
    analytics: dict = field(default_factory=dict)   # the [analytics] table, see coach.analytics.settings
    alerts: dict = field(default_factory=dict)      # the [alerts] table (E10), see coach.alerts.settings
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def coach_model_effective(self) -> str:
        if self.coach_model:
            return self.coach_model
        if self.coach_backend == "openai-compatible":
            return self.llm_openai_model
        return {"claude-code": "sonnet", "anthropic-api": "claude-sonnet-5-5"}.get(self.coach_backend, self.llm_ollama_model)

    @property
    def alert_settings(self):
        from coach.alerts.settings import AlertSettings
        return AlertSettings.from_dict(self.alerts)

    @property
    def tls_dir(self) -> Path:
        return self.data_dir / "tls"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def require_eb(self) -> tuple[str, str]:
        if not self.eb_app_id or not self.eb_private_key_path:
            raise ConfigError(
                "Enable Banking is not configured: set [enable_banking] app_id and private_key_path "
                "in config.toml (or run `coach config import-env` to migrate prototype/ingest/.env)")
        return self.eb_app_id, self.eb_private_key_path

    def require_redirect(self) -> str:
        if not self.eb_redirect_url:
            raise ConfigError("Set [enable_banking] redirect_url in config.toml "
                              "(must be whitelisted in your Enable Banking app)")
        return self.eb_redirect_url


def _path(root: Path, value: str | os.PathLike) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (root / p)


def legacy_env_values(root: Path) -> dict[str, str]:
    p = root / LEGACY_ENV_REL
    if not p.exists():
        return {}
    return {k: v for k, v in dotenv_values(p).items() if v}


def load_config(path: str | os.PathLike | None = None, env=None) -> Config:
    env = os.environ if env is None else env
    explicit = path or env.get("COACH_CONFIG")
    if explicit:
        cfg_path = Path(explicit).expanduser().resolve()
        if not cfg_path.exists():
            raise ConfigError(f"Config file not found: {cfg_path}")
        root = cfg_path.parent
    else:
        root = find_root()
        cfg_path = root / CONFIG_NAME
        if not cfg_path.exists():
            cfg_path = None
    raw = tomllib.loads(cfg_path.read_text()) if cfg_path else {}
    src: dict[str, str] = {}
    origin = CONFIG_NAME

    def pick(key: str, section: str | None, default, env_name: str | None = None):
        """env var > config.toml > default."""
        if env_name and env.get(env_name):
            src[key] = f"env {env_name}"
            return env[env_name]
        block = raw.get(section, {}) if section else raw
        if key.split(".")[-1] in block and block[key.split(".")[-1]] not in (None, ""):
            src[key] = origin
            return block[key.split(".")[-1]]
        src[key] = "default"
        return default

    data_dir = _path(root, _typed("data_dir", pick("data_dir", None, "data", "COACH_DATA_DIR"), str))
    memory_dir = _path(root, _typed("memory_dir", pick("memory_dir", None, "memory", "COACH_MEMORY_DIR"), str))
    db_raw = pick("db_path", None, None, "COACH_DB")
    if db_raw is not None:
        _typed("db_path", db_raw, str)
    db_path = _path(root, db_raw) if db_raw else data_dir / "finance.db"
    if not db_raw:
        src["db_path"] = "default (<data_dir>/finance.db)"

    legacy = legacy_env_values(root)

    def eb(key: str, env_name: str):
        v = pick(f"enable_banking.{key}", "enable_banking", None, env_name)
        if v is not None:
            _typed(f"enable_banking.{key}", v, str)
        if v is None and legacy.get(env_name):
            src[f"enable_banking.{key}"] = f"legacy {LEGACY_ENV_REL}"
            return legacy[env_name]
        return v

    cfg = Config(
        root=root, config_path=cfg_path, data_dir=data_dir, memory_dir=memory_dir, db_path=db_path,
        insecure_plaintext_db=_typed("insecure_plaintext_db", pick("insecure_plaintext_db", None, False), bool),
        db_auto_migrate=_typed("db.auto_migrate", pick("db.auto_migrate", "db", True), bool),
        eb_app_id=eb("app_id", "EB_APP_ID"),
        eb_redirect_url=eb("redirect_url", "EB_REDIRECT_URL"),
        eb_private_key_path=eb("private_key_path", "EB_PRIVATE_KEY_PATH"),
        eb_api_url=_typed("enable_banking.api_url", pick("enable_banking.api_url", "enable_banking", DEFAULT_API_URL, "EB_API_URL"), str),
        llm_backend=_typed("llm.backend", pick("llm.backend", "llm", "claude-code"), str),
        llm_model=_typed("llm.model", pick("llm.model", "llm", "sonnet"), str),
        llm_anthropic_model=_typed("llm.anthropic_model", pick("llm.anthropic_model", "llm", "claude-haiku-4-5"), str),
        llm_batch_api=_typed("llm.batch_api", pick("llm.batch_api", "llm", False), bool),
        llm_ollama_url=_typed("llm.ollama_url", pick("llm.ollama_url", "llm", "http://localhost:11434"), str),
        llm_ollama_model=_typed("llm.ollama_model", pick("llm.ollama_model", "llm", "llama3.1"), str),
        llm_ollama_allow_remote=_typed("llm.ollama_allow_remote", pick("llm.ollama_allow_remote", "llm", False), bool),
        llm_anthropic_base_url=pick("llm.anthropic_base_url", "llm", None),
        llm_openai_base_url=_typed("llm.openai_base_url", pick("llm.openai_base_url", "llm", "https://openrouter.ai/api/v1"), str),
        llm_openai_model=_typed("llm.openai_model", pick("llm.openai_model", "llm", ""), str),
        llm_openrouter_deny_data_collection=_typed("llm.openrouter_deny_data_collection",
                                                   pick("llm.openrouter_deny_data_collection", "llm", True), bool),
        llm_allowlist=_names_re(pick("classify.llm_allowlist", "classify", [])),
        knn_enabled=_typed("classify.knn_enabled", pick("classify.knn_enabled", "classify", True), bool),
        knn_threshold=_conf_knn(pick("classify.knn_threshold", "classify", 0.92)),
        knn_examples=_typed("classify.knn_examples", pick("classify.knn_examples", "classify", 5), int),
        sync_daily_limit=_typed("sync.daily_limit", pick("sync.daily_limit", "sync", 4), int),
        schedule_time=_typed("schedule.time", pick("schedule.time", "schedule", "07:30"), str),
        backup_dir=_path(root, _typed("backup.dir", pick("backup.dir", "backup", "backups"), str)),
        backup_retention=_typed("backup.retention", pick("backup.retention", "backup", 14), int),
        callback_timeout=_typed("callback.timeout_seconds", pick("callback.timeout_seconds", "callback", 600), int),
        callback_container_bind=_typed("callback.container_bind", pick("callback.container_bind", "callback", False), bool),
        notify_macos=_typed("notify.macos", pick("notify.macos", "notify", False), bool),
        transfer_window_days=_typed("transfers.window_days", pick("transfers.window_days", "transfers", 3), int),
        transfer_cross_bank_window_days=_typed("transfers.cross_bank_window_days", pick("transfers.cross_bank_window_days", "transfers", 5), int),
        transfer_auto_link=_typed("transfers.auto_link", pick("transfers.auto_link", "transfers", False), bool),
        transfer_topup_merchants=_names(pick("transfers.topup_merchants", "transfers", ["REVOLUT", "LYDIA", "PAYPAL"])),
        transfer_min_confidence=_conf(pick("transfers.min_confidence", "transfers", 0.85)),
        health_stale_days=_typed("health.stale_days", pick("health.stale_days", "health", 2), int),
        import_profiles_dir=_path(root, _typed("import.profiles_dir",
                                               pick("import.profiles_dir", "import", "config/import_profiles"), str)),
        memory_history=_typed("memory.history", pick("memory.history", "memory", True), bool),
        memory_stale_months=_typed("memory.stale_months", pick("memory.stale_months", "memory", 6), int),
        memory_asset_stale_months=_typed("memory.asset_stale_months", pick("memory.asset_stale_months", "memory", 3), int),
        memory_big_tx_threshold=_num("memory.big_tx_threshold", pick("memory.big_tx_threshold", "memory", 2000)),
        memory_question_min_stake=_num("memory.question_min_stake", pick("memory.question_min_stake", "memory", 300)),
        schedule_memory_check=_typed("schedule.memory_check", pick("schedule.memory_check", "schedule", True), bool),
        schedule_analytics=_typed("schedule.analytics", pick("schedule.analytics", "schedule", True), bool),
        schedule_eval=_typed("schedule.eval", pick("schedule.eval", "schedule", True), bool),
        usage_monthly_warn_usd=_num("usage.monthly_warn_usd", pick("usage.monthly_warn_usd", "usage", 0)),
        usage_include_notional=_typed("usage.include_notional", pick("usage.include_notional", "usage", True), bool),
        log_max_kb=_typed("logs.max_kb", pick("logs.max_kb", "logs", 512), int),
        log_keep=_typed("logs.keep", pick("logs.keep", "logs", 5), int),
        log_max_age_days=_typed("logs.max_age_days", pick("logs.max_age_days", "logs", 90), int),
        privacy_model_detail=_typed("privacy.model_detail", pick("privacy.model_detail", "privacy", "coarse"), str),
        privacy_local_only=_typed("privacy.local_only", pick("privacy.local_only", "privacy", False), bool),
        privacy_offline=_typed("privacy.offline", pick("privacy.offline", "privacy", False), bool),
        privacy_web_enrich=_typed("privacy.web_enrich", pick("privacy.web_enrich", "privacy", False), bool),
        privacy_egress_journal=_typed("privacy.egress_journal", pick("privacy.egress_journal", "privacy", True), bool),
        privacy_egress_journal_days=_typed("privacy.egress_journal_days", pick("privacy.egress_journal_days", "privacy", 365), int),
        ui_host=_typed("ui.host", pick("ui.host", "ui", "127.0.0.1"), str),
        ui_port=_typed("ui.port", pick("ui.port", "ui", 8765), int),
        ui_allow_remote=_typed("ui.allow_remote", pick("ui.allow_remote", "ui", False), bool),
        ui_allowed_hosts=_hosts(pick("ui.allowed_hosts", "ui", [])),
        ui_open_browser=_typed("ui.open_browser", pick("ui.open_browser", "ui", True), bool),
        ui_container_bind=_typed("ui.container_bind", pick("ui.container_bind", "ui", False), bool),
        ui_session_hours=_typed("ui.session_hours", pick("ui.session_hours", "ui", 12), int),
        ui_key_rotation_days=_typed("ui.key_rotation_days", pick("ui.key_rotation_days", "ui", 30), int),
        ui_remote_tls_ack=_typed("ui.remote_tls_ack", pick("ui.remote_tls_ack", "ui", False), bool),
        ui_passkeys=_typed("ui.passkeys", pick("ui.passkeys", "ui", False), bool),
        ui_sso=_typed("ui.sso", pick("ui.sso", "ui", "none"), str),
        ui_sso_jwks_url=_typed("ui.sso_jwks_url", pick("ui.sso_jwks_url", "ui", ""), str),
        ui_sso_issuer=_typed("ui.sso_issuer", pick("ui.sso_issuer", "ui", ""), str),
        ui_sso_audience=_typed("ui.sso_audience", pick("ui.sso_audience", "ui", ""), str),
        ui_sso_users=_sso_users(pick("ui.sso_users", "ui", {})),
        coach_backend=_typed("coach.backend", pick("coach.backend", "coach", "claude-code"), str),
        coach_model=_opt_str("coach.model", pick("coach.model", "coach", None)),
        coach_max_tool_calls=_typed("coach.max_tool_calls", pick("coach.max_tool_calls", "coach", 12), int),
        coach_max_tokens=_typed("coach.max_tokens", pick("coach.max_tokens", "coach", 4096), int),
        coach_timeout=_typed("coach.timeout_seconds", pick("coach.timeout_seconds", "coach", 180), int),
        coach_schedule_weekly=_typed("coach.schedule_weekly", pick("coach.schedule_weekly", "coach", False), bool),
        coach_schedule_monthly=_typed("coach.schedule_monthly", pick("coach.schedule_monthly", "coach", False), bool),
        coach_max_budget_usd=_num("coach.max_budget_usd", pick("coach.max_budget_usd", "coach", 1.0)),
        coach_allowed_builtin_agents=_agentnames(pick("coach.allowed_builtin_agents", "coach",
                                                      ["general-purpose", "Explore", "Plan", "statusline-setup", "claude-code-guide"])),
        coach_allowed_plugins=_agentnames(pick("coach.allowed_plugins", "coach", [])),
        coach_allow_builtin_plugins=_typed("coach.allow_builtin_plugins", pick("coach.allow_builtin_plugins", "coach", True), bool),
        coach_claude_env=_envnames(pick("coach.claude_env", "coach", [])),
        coach_claude_restricted=_typed("coach.claude_restricted", pick("coach.claude_restricted", "coach", False), bool),
        analytics=dict(raw.get("analytics") or {}) if isinstance(raw.get("analytics") or {}, dict) else {},
        alerts=dict(raw.get("alerts") or {}) if isinstance(raw.get("alerts") or {}, dict) else {},
        sources=src,
    )
    from coach.alerts.settings import AlertConfigError, AlertSettings
    try:
        AlertSettings.from_dict(cfg.alerts)
    except AlertConfigError as e:
        raise ConfigError(str(e))
    if not 16 <= cfg.log_max_kb <= 102400 or not 1 <= cfg.log_keep <= 50 or not 1 <= cfg.log_max_age_days <= 3650:
        raise ConfigError("logs.max_kb must be between 16 and 102400, logs.keep between 1 and 50, logs.max_age_days between 1 and 3650")
    if not 30 <= cfg.privacy_egress_journal_days <= 3650:
        raise ConfigError("privacy.egress_journal_days must be between 30 and 3650")
    if cfg.privacy_model_detail not in ("coarse", "standard"):
        raise ConfigError(f"privacy.model_detail must be 'coarse' or 'standard', got {cfg.privacy_model_detail!r}")
    from coach.analytics.settings import AnalyticsSettings
    try:
        AnalyticsSettings.from_dict(cfg.analytics)
    except ValueError as e:
        raise ConfigError(str(e))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", cfg.schedule_time):
        raise ConfigError(f"schedule.time must be HH:MM, got {cfg.schedule_time!r}")
    if cfg.llm_backend not in ALLOWED_LLM_BACKENDS:
        raise ConfigError(f"llm.backend {cfg.llm_backend!r} is not supported; allowed: "
                          f"{', '.join(ALLOWED_LLM_BACKENDS)}")
    if cfg.coach_backend not in ALLOWED_LLM_BACKENDS:
        raise ConfigError(f"coach.backend {cfg.coach_backend!r} is not supported; allowed: {', '.join(ALLOWED_LLM_BACKENDS)}")
    if cfg.coach_model is not None and not re.fullmatch(r"[\w.:/@-]{1,100}", cfg.coach_model):
        raise ConfigError(f"coach.model must be a model name or alias, got {cfg.coach_model!r}")
    if not 1 <= cfg.coach_max_tool_calls <= 100:
        raise ConfigError("coach.max_tool_calls must be between 1 and 100")
    if not 256 <= cfg.coach_max_tokens <= 64000:
        raise ConfigError("coach.max_tokens must be between 256 and 64000")
    if not 10 <= cfg.coach_timeout <= 3600:
        raise ConfigError("coach.timeout_seconds must be between 10 and 3600")
    from coach.classify.backends import check_ollama_url, check_openai_url
    try:
        check_ollama_url(cfg.llm_ollama_url, cfg.llm_ollama_allow_remote)
    except ValueError as e:
        raise ConfigError(str(e))
    if "openai-compatible" in (cfg.llm_backend, cfg.coach_backend):
        try:
            check_openai_url(cfg.llm_openai_base_url)
        except ValueError as e:
            raise ConfigError(f"llm.{e}")
        if cfg.llm_backend == "openai-compatible" and not cfg.llm_openai_model or (
                cfg.coach_backend == "openai-compatible" and not cfg.coach_model_effective):
            raise ConfigError('the openai-compatible backend needs a model: set [llm] openai_model (the provider\'s model id, '
                              'e.g. "anthropic/claude-haiku-4.5" on OpenRouter) or [coach] model')
    if cfg.llm_openai_model and not re.fullmatch(r"[\w.:/@-]{1,100}", cfg.llm_openai_model):
        raise ConfigError(f"llm.openai_model must be a model id, got {cfg.llm_openai_model!r}")
    if not 1 <= cfg.ui_port <= 65535:
        raise ConfigError(f"ui.port must be between 1 and 65535, got {cfg.ui_port}")
    if not 1 <= cfg.ui_session_hours <= 24 * 30 or cfg.ui_key_rotation_days < 1:
        raise ConfigError("ui.session_hours must be between 1 and 720 and ui.key_rotation_days >= 1")
    if cfg.ui_sso not in ("none", "authentik"):
        raise ConfigError(f'ui.sso must be "none" or "authentik", got {cfg.ui_sso!r}')
    if cfg.ui_sso != "none":
        if not (cfg.ui_allow_remote and cfg.ui_remote_tls_ack and cfg.ui_allowed_hosts):
            raise ConfigError("ui.sso needs ui.allow_remote = true, ui.remote_tls_ack = true and ui.allowed_hosts: the proxy that signs people in "
                              "is a remote, HTTPS front; the app verifies its signed token, never a plain header")
        if not re.fullmatch(r"https?://[^\s/]+/\S*", cfg.ui_sso_jwks_url or ""):
            raise ConfigError("ui.sso needs ui.sso_jwks_url: the provider's JWKS URL as this app reaches it "
                              '(authentik: "http://authentik:9000/application/o/<application slug>/jwks/")')
        if not cfg.ui_sso_users:
            raise ConfigError("ui.sso needs a [ui.sso_users] table: identity = \"owner\" (or a `coach users` login id) for every person allowed in")
    if cfg.sync_daily_limit < 1 or cfg.backup_retention < 1:
        raise ConfigError("sync.daily_limit and backup.retention must be >= 1")
    if cfg.memory_stale_months < 1 or cfg.memory_asset_stale_months < 1:
        raise ConfigError("memory.stale_months and memory.asset_stale_months must be >= 1")
    if cfg.callback_timeout < 1 or cfg.transfer_window_days < 0 or cfg.transfer_cross_bank_window_days < 0 or cfg.health_stale_days < 0:
        raise ConfigError("callback.timeout_seconds must be >= 1; transfers.window_days and health.stale_days >= 0")
    return cfg


# ---------------------------------------------------------------- show / import-env

def effective(cfg: Config) -> list[tuple[str, str, str]]:
    """(key, value, source) rows for display. Never includes secret values."""
    s = cfg.sources
    rows = [
        ("config file", str(cfg.config_path) if cfg.config_path else "(none, defaults only)", ""),
        ("root", str(cfg.root), ""),
        ("data_dir", str(cfg.data_dir), s.get("data_dir", "")),
        ("memory_dir", str(cfg.memory_dir), s.get("memory_dir", "")),
        ("db_path", str(cfg.db_path), s.get("db_path", "")),
        ("insecure_plaintext_db", str(cfg.insecure_plaintext_db).lower(), s.get("insecure_plaintext_db", "")),
        ("db.auto_migrate", str(cfg.db_auto_migrate).lower(), s.get("db.auto_migrate", "")),
        ("enable_banking.app_id", cfg.eb_app_id or "(unset)", s.get("enable_banking.app_id", "")),
        ("enable_banking.redirect_url", cfg.eb_redirect_url or "(unset)", s.get("enable_banking.redirect_url", "")),
        ("enable_banking.private_key_path", cfg.eb_private_key_path or "(unset)",
         s.get("enable_banking.private_key_path", "")),
        ("enable_banking.api_url", cfg.eb_api_url, s.get("enable_banking.api_url", "")),
        ("llm.backend", cfg.llm_backend, s.get("llm.backend", "")),
        ("llm.model", cfg.llm_model, s.get("llm.model", "")),
        ("llm.anthropic_model", cfg.llm_anthropic_model, s.get("llm.anthropic_model", "")),
        ("llm.batch_api", str(cfg.llm_batch_api).lower(), s.get("llm.batch_api", "")),
        ("llm.ollama_url", cfg.llm_ollama_url, s.get("llm.ollama_url", "")),
        ("llm.ollama_model", cfg.llm_ollama_model, s.get("llm.ollama_model", "")),
        ("llm.ollama_allow_remote", str(cfg.llm_ollama_allow_remote).lower(), s.get("llm.ollama_allow_remote", "")),
        ("llm.anthropic_base_url", cfg.llm_anthropic_base_url or "(official endpoint)", s.get("llm.anthropic_base_url", "")),
        ("llm.openai_base_url", cfg.llm_openai_base_url, s.get("llm.openai_base_url", "")),
        ("llm.openai_model", cfg.llm_openai_model or "(unset)", s.get("llm.openai_model", "")),
        ("llm.openrouter_deny_data_collection", str(cfg.llm_openrouter_deny_data_collection).lower(),
         s.get("llm.openrouter_deny_data_collection", "")),
        ("classify.llm_allowlist", ", ".join(cfg.llm_allowlist) or "(none)", s.get("classify.llm_allowlist", "")),
        ("classify.knn_enabled", str(cfg.knn_enabled).lower(), s.get("classify.knn_enabled", "")),
        ("classify.knn_threshold", str(cfg.knn_threshold), s.get("classify.knn_threshold", "")),
        ("classify.knn_examples", str(cfg.knn_examples), s.get("classify.knn_examples", "")),
        ("sync.daily_limit", str(cfg.sync_daily_limit), s.get("sync.daily_limit", "")),
        ("schedule.time", cfg.schedule_time, s.get("schedule.time", "")),
        ("backup.dir", str(cfg.backup_dir), s.get("backup.dir", "")),
        ("backup.retention", str(cfg.backup_retention), s.get("backup.retention", "")),
        ("callback.timeout_seconds", str(cfg.callback_timeout), s.get("callback.timeout_seconds", "")),
        ("callback.container_bind", str(cfg.callback_container_bind).lower(), s.get("callback.container_bind", "")),
        ("notify.macos", str(cfg.notify_macos).lower(), s.get("notify.macos", "")),
        ("transfers.window_days", str(cfg.transfer_window_days), s.get("transfers.window_days", "")),
        ("transfers.cross_bank_window_days", str(cfg.transfer_cross_bank_window_days), s.get("transfers.cross_bank_window_days", "")),
        ("transfers.auto_link", str(cfg.transfer_auto_link).lower(), s.get("transfers.auto_link", "")),
        ("transfers.min_confidence", str(cfg.transfer_min_confidence), s.get("transfers.min_confidence", "")),
        ("transfers.topup_merchants", ", ".join(cfg.transfer_topup_merchants), s.get("transfers.topup_merchants", "")),
        ("health.stale_days", str(cfg.health_stale_days), s.get("health.stale_days", "")),
        ("import.profiles_dir", str(cfg.import_profiles_dir), s.get("import.profiles_dir", "")),
        ("memory.history", str(cfg.memory_history).lower(), s.get("memory.history", "")),
        ("memory.stale_months", str(cfg.memory_stale_months), s.get("memory.stale_months", "")),
        ("memory.asset_stale_months", str(cfg.memory_asset_stale_months), s.get("memory.asset_stale_months", "")),
        ("memory.big_tx_threshold", str(cfg.memory_big_tx_threshold), s.get("memory.big_tx_threshold", "")),
        ("memory.question_min_stake", str(cfg.memory_question_min_stake), s.get("memory.question_min_stake", "")),
        ("schedule.memory_check", str(cfg.schedule_memory_check).lower(), s.get("schedule.memory_check", "")),
        ("schedule.analytics", str(cfg.schedule_analytics).lower(), s.get("schedule.analytics", "")),
        ("schedule.eval", str(cfg.schedule_eval).lower(), s.get("schedule.eval", "")),
        ("usage.monthly_warn_usd", str(cfg.usage_monthly_warn_usd), s.get("usage.monthly_warn_usd", "")),
        ("usage.include_notional", str(cfg.usage_include_notional).lower(), s.get("usage.include_notional", "")),
        ("logs.max_kb", str(cfg.log_max_kb), s.get("logs.max_kb", "")),
        ("logs.keep", str(cfg.log_keep), s.get("logs.keep", "")),
        ("logs.max_age_days", str(cfg.log_max_age_days), s.get("logs.max_age_days", "")),
        ("privacy.model_detail", cfg.privacy_model_detail, s.get("privacy.model_detail", "")),
        ("privacy.local_only", str(cfg.privacy_local_only).lower(), s.get("privacy.local_only", "")),
        ("privacy.offline", str(cfg.privacy_offline).lower(), s.get("privacy.offline", "")),
        ("privacy.web_enrich", str(cfg.privacy_web_enrich).lower(), s.get("privacy.web_enrich", "")),
        ("privacy.egress_journal", str(cfg.privacy_egress_journal).lower(), s.get("privacy.egress_journal", "")),
        ("privacy.egress_journal_days", str(cfg.privacy_egress_journal_days), s.get("privacy.egress_journal_days", "")),
        ("coach.backend", cfg.coach_backend, s.get("coach.backend", "")),
        ("coach.model", cfg.coach_model_effective + ("" if cfg.coach_model else " (backend default)"), s.get("coach.model", "")),
        ("coach.max_tool_calls", str(cfg.coach_max_tool_calls), s.get("coach.max_tool_calls", "")),
        ("coach.max_tokens", str(cfg.coach_max_tokens), s.get("coach.max_tokens", "")),
        ("coach.timeout_seconds", str(cfg.coach_timeout), s.get("coach.timeout_seconds", "")),
        ("coach.schedule_weekly", str(cfg.coach_schedule_weekly).lower(), s.get("coach.schedule_weekly", "")),
        ("coach.schedule_monthly", str(cfg.coach_schedule_monthly).lower(), s.get("coach.schedule_monthly", "")),
        ("coach.max_budget_usd", str(cfg.coach_max_budget_usd), s.get("coach.max_budget_usd", "")),
        ("coach.allowed_builtin_agents", ", ".join(cfg.coach_allowed_builtin_agents) or "(none)", s.get("coach.allowed_builtin_agents", "")),
        ("coach.allowed_plugins", ", ".join(cfg.coach_allowed_plugins) or "(none)", s.get("coach.allowed_plugins", "")),
        ("coach.allow_builtin_plugins", str(cfg.coach_allow_builtin_plugins).lower(), s.get("coach.allow_builtin_plugins", "")),
        ("coach.claude_env", ", ".join(cfg.coach_claude_env) or "(none)", s.get("coach.claude_env", "")),
        ("coach.claude_restricted", str(cfg.coach_claude_restricted).lower(), s.get("coach.claude_restricted", "")),
        ("ui.host", cfg.ui_host, s.get("ui.host", "")),
        ("ui.port", str(cfg.ui_port), s.get("ui.port", "")),
        ("ui.allow_remote", str(cfg.ui_allow_remote).lower(), s.get("ui.allow_remote", "")),
        ("ui.allowed_hosts", ", ".join(cfg.ui_allowed_hosts) or "(none)", s.get("ui.allowed_hosts", "")),
        ("ui.open_browser", str(cfg.ui_open_browser).lower(), s.get("ui.open_browser", "")),
        ("ui.container_bind", str(cfg.ui_container_bind).lower(), s.get("ui.container_bind", "")),
        ("ui.session_hours", str(cfg.ui_session_hours), s.get("ui.session_hours", "")),
        ("ui.key_rotation_days", str(cfg.ui_key_rotation_days), s.get("ui.key_rotation_days", "")),
        ("ui.remote_tls_ack", str(cfg.ui_remote_tls_ack).lower(), s.get("ui.remote_tls_ack", "")),
        ("ui.passkeys", str(cfg.ui_passkeys).lower(), s.get("ui.passkeys", "")),
        ("ui.sso", cfg.ui_sso, s.get("ui.sso", "")),
        ("ui.sso_jwks_url", cfg.ui_sso_jwks_url or "(none)", s.get("ui.sso_jwks_url", "")),
        ("ui.sso_issuer", cfg.ui_sso_issuer or "(not checked)", s.get("ui.sso_issuer", "")),
        ("ui.sso_audience", cfg.ui_sso_audience or "(not checked)", s.get("ui.sso_audience", "")),
        ("ui.sso_users", f"{len(cfg.ui_sso_users)} mapped" if cfg.ui_sso_users else "(none)", s.get("ui.sso_users", "")),
    ]
    from coach.analytics.settings import AnalyticsSettings
    eff = AnalyticsSettings.from_dict(cfg.analytics)
    rows += [(f"analytics.{k}", v, "config.toml" if k in cfg.analytics else "default") for k, v in eff.rows()]
    rows += [(k, v, "config.toml" if cfg.alerts else "default") for k, v in cfg.alert_settings.rows()]
    from coach.alerts.settings import warnings_of
    rows += [("alerts.WARNING", w, "") for w in warnings_of(cfg.alert_settings, cfg.ui_allowed_hosts)]
    return rows


def set_toml_value(text: str, section: str, key: str, value: str) -> str:
    """Set ``key = "value"`` inside ``[section]`` of a TOML text, preserving comments/layout."""
    return _set_toml_line(text, section, key, json.dumps(value))


def set_toml_bool(text: str, section: str, key: str, value: bool) -> str:
    """Set ``key = true|false`` inside ``[section]`` of a TOML text, preserving comments/layout."""
    return _set_toml_line(text, section, key, "true" if value else "false")


def _set_toml_line(text: str, section: str, key: str, rendered: str) -> str:
    new_line = f"{key} = {rendered}"
    lines = text.splitlines()
    hdr = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")          # a section header may carry a trailing comment
    start = next((i for i, ln in enumerate(lines) if (m := hdr.match(ln)) and m.group(1).strip() == section), None)
    if start is None:
        return text.rstrip("\n") + f"\n\n[{section}]\n{new_line}\n"
    end = next((i for i in range(start + 1, len(lines)) if hdr.match(lines[i])), len(lines))
    for i in range(start + 1, end):
        if re.match(rf"^\s*{re.escape(key)}\s*=", lines[i]):
            comment = re.search(r'^\s*(?:"(?:[^"\\]|\\.)*"|[^#"]*?)(\s+#.*)$', lines[i].split("=", 1)[1])   # keep the trailing comment
            lines[i] = new_line + (comment.group(1) if comment else "")
            return "\n".join(lines) + "\n"
    lines.insert(start + 1, new_line)
    return "\n".join(lines) + "\n"


def import_env(cfg: Config, dest: Path | None = None, force: bool = False) -> tuple[Path, list[str]]:
    """One-time migration: copy EB_* values from the legacy .env into config.toml."""
    legacy = legacy_env_values(cfg.root)
    if not legacy:
        raise ConfigError(f"No legacy env file with values found at {cfg.root / LEGACY_ENV_REL}")
    dest = dest or cfg.config_path or (cfg.root / CONFIG_NAME)
    if dest.exists():
        text = dest.read_text()
    else:
        example = cfg.root / "config.example.toml"
        if not example.exists():
            raise ConfigError("config.example.toml not found; cannot create config.toml")
        text = example.read_text()
    current = tomllib.loads(text).get("enable_banking", {}) if text.strip() else {}
    mapping = {"EB_APP_ID": "app_id", "EB_REDIRECT_URL": "redirect_url", "EB_PRIVATE_KEY_PATH": "private_key_path"}
    changed = []
    for env_key, key in mapping.items():
        if env_key not in legacy:
            continue
        if current.get(key) and not force:
            continue
        text = set_toml_value(text, "enable_banking", key, legacy[env_key])
        changed.append(key)
    dest.write_text(text)
    return dest, changed
