"""Tunable thresholds of the analytics engine (``[analytics]`` table of config.toml).

Every number has a default here; the config only overrides. The analytics functions take an :class:`AnalyticsSettings`
(never read the config themselves) so a result depends on its inputs only."""
from __future__ import annotations

from dataclasses import dataclass, field, fields

DEFAULT_VARIABLE_CATEGORIES = ("housing.energy", "housing.water", "subscriptions.telecom", "housing.property_charges",
                               "health.health_insurance", "taxes.taxes")


@dataclass(frozen=True)
class AnalyticsSettings:
    # averages / coverage
    average_window_months: int = 12        # a category average uses at most the last N covered months
    average_min_months: int = 3            # fewer covered months = the figure is flagged low_confidence
    coverage_min_share: float = 0.10       # an account narrows a category's month window only if it carries this share of it
    lumpy_categories: tuple = ("travel.", "housing.renovation", "taxes.")   # seasonal / lumpy: averaged over >= 12 covered months
    lumpy_min_months: int = 12
    household_mismatch_pct: float = 0.10   # household estimate vs sum of per-account averages: flag above this gap
    # recurring detection (E4-3)
    recurring_amount_tolerance: float = 0.15       # +-15 % around the median amount
    recurring_min_occurrences: int = 3             # 2 are enough for a yearly cadence (low confidence)
    recurring_loose_amount_categories: tuple = ("housing.rental_property_loan", "housing.mortgage", "housing.property_charges",
                                                "debt.", "insurance.")   # prefix when ending with '.': regular dates, amounts may vary
    recurring_discretionary_groups: tuple = ("food", "shopping", "leisure", "travel", "personal_care", "cash",
                                             "transport.fuel", "transport.parking_tolls", "transport.taxi_rideshare")
    recurring_discretionary_min_occurrences: int = 5   # restaurants / shopping / fuel-like: a regular rhythm needs more proof
    recurring_yearly_pair_groups: tuple = ("subscriptions", "insurance", "housing", "taxes", "fees", "health",
                                           "education", "charity", "debt", "income")   # a 2-occurrence yearly series only for these category groups
    recurring_ended_factor: float = 1.5            # no occurrence for > 1.5 x cadence = ended
    recurring_variable_categories: tuple = DEFAULT_VARIABLE_CATEGORIES   # amount may vary: utilities
    # price changes (E4-4)
    price_change_threshold_pct: float = 3.0        # relative change that is reported
    price_change_min_abs: float = 0.50             # ... and at least this many EUR
    price_change_income_pct: float = 5.0           # income: only confirmed raises / cuts of at least this (pay moves a little)
    price_change_variable_pct: float = 20.0        # variable-amount series (utilities): much wider threshold
    # anomalies (E4-5)
    anomaly_min_history_months: int = 6
    anomaly_z: float = 3.5                         # robust z-score (median / MAD) for a category month
    anomaly_min_excess: float = 50.0               # EUR above the median needed to report a category month
    anomaly_min_ratio: float = 1.5                 # ... and the month must be at least this multiple of the median
    anomaly_duplicate_days: int = 3
    anomaly_duplicate_min_amount: float = 10.0
    anomaly_new_merchant_days: int = 60
    anomaly_new_merchant_min: float = 150.0
    anomaly_large_tx_min: float = 100.0
    anomaly_large_tx_iqr_factor: float = 3.0
    anomaly_lookback_days: int = 90                # duplicates / new merchants / large payments: look back this far
    anomaly_months: int = 3                        # category spikes are looked for in the last N closed months
    # forecast (E4-6)
    forecast_band_z: float = 1.28                  # ~80 % band on the variable spend
    forecast_variable_months: int = 6              # covered months used for the variable-spend estimate
    forecast_balance_stale_days: int = 3
    # budgets (E4-7)
    budget_suggest_months: int = 6
    # calendar (E4-8)
    calendar_days: int = 60
    asset_stale_months: int = 3
    # loans (E9)
    loan_grace_days: int = 5                       # days after a due date before a missing loan payment is reported
    loan_reminder_months: int = 6                  # LOA / LLD end-of-contract reminders start this many months before the end
    # rental property (E15)
    rental_reminder_months: int = 12               # the end of a scheme commitment: reminders start this many months before it (then at 6 and 3)
    rental_rent_grace_days: int = 7                # days after the usual rent day before this month's missing rent is reported
    rental_rate_gap_pts: float = 0.5               # a loan rate this many points above the market rate you entered is flagged as worth a quote
    extra: dict = field(default_factory=dict, compare=False)

    @classmethod
    def from_dict(cls, raw: dict | None) -> "AnalyticsSettings":
        raw = dict(raw or {})
        known = {f.name for f in fields(cls)} - {"extra"}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise ValueError(f"unknown [analytics] setting(s): {', '.join(unknown)} (known: {', '.join(sorted(known))})")
        kw = {}
        defaults = cls()
        for k, v in raw.items():
            d = getattr(defaults, k)
            if isinstance(d, tuple):
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    raise ValueError(f"[analytics] {k} must be a list of strings, got {v!r}")
                kw[k] = tuple(v)
            elif isinstance(d, bool):
                raise ValueError("unexpected boolean setting")      # pragma: no cover
            elif isinstance(d, int):
                if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                    raise ValueError(f"[analytics] {k} must be a whole number >= 0, got {v!r}")
                kw[k] = v
            else:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                    raise ValueError(f"[analytics] {k} must be a number >= 0, got {v!r}")
                kw[k] = float(v)
        s = cls(**kw)
        if not 0 < s.recurring_amount_tolerance < 1:
            raise ValueError("[analytics] recurring_amount_tolerance must be between 0 and 1 (0.15 = +-15 %)")
        if s.recurring_min_occurrences < 2 or s.average_window_months < 1:
            raise ValueError("[analytics] recurring_min_occurrences must be >= 2 and average_window_months >= 1")
        return s

    def rows(self) -> list[tuple[str, str]]:
        return [(f.name, ", ".join(getattr(self, f.name)) if isinstance(getattr(self, f.name), tuple)
                 else str(getattr(self, f.name))) for f in fields(self) if f.name != "extra"]
