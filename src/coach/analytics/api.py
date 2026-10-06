"""Entry points shared by the CLI, the scheduler and (E6-1) the finance MCP server.

Every analytics function takes a :class:`~coach.analytics.dataset.Dataset` and returns a result dataclass with
``to_dict()`` (JSON-safe, money as strings, ``coverage`` and ``evidence`` included). The MCP tools will be thin:

    ds = build_dataset(con, cfg)                 # one read of the database + memory
    return cashflow(ds, months=6).to_dict()      # or recurring.detect_recurring(ds), forecast.forecast(ds), ...

The registry below is the list of read-only tools the MCP layer can expose, with the function behind each.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

from coach.analytics.settings import AnalyticsSettings


def settings_of(cfg) -> AnalyticsSettings:
    return AnalyticsSettings.from_dict(getattr(cfg, "analytics", None))


def build_dataset(con, cfg, today: Optional[dt.date] = None):
    from coach.analytics.dataset import load_dataset
    return load_dataset(con, cfg.memory_dir, today=today, settings=settings_of(cfg))


def registry() -> dict[str, Callable]:
    """name -> function(ds, **params) -> result (all pure, read-only)."""
    from coach.analytics import (anomalies, averages, budgets, cashflow, forecast, goals, pricechanges, recurring,
                                 review, upcoming)
    return {
        "coverage": lambda ds: [c.to_dict() for c in ds.coverage.table()],
        "category_averages": averages.category_averages,
        "cashflow": cashflow.cashflow,
        "recurring": recurring.detect_recurring,
        "price_changes": pricechanges.price_changes,
        "anomalies": anomalies.detect_anomalies,
        "forecast": forecast.forecast,
        "budget_suggestions": budgets.suggest_budgets,
        "budget_status": budgets.budget_status,
        "calendar": upcoming.calendar_items,
        "goals": goals.goal_progress,
        "year_review": review.year_review,
    }


def refresh_all(con, cfg, today: Optional[dt.date] = None) -> dict:
    """Recompute and store the recurring series and the anomalies, and compute the budget status (the scheduled
    job's analytics step). Returns a small summary; raises on error (the scheduler wraps it)."""
    from coach.analytics import anomalies, budgets, recurring
    ds = build_dataset(con, cfg, today)
    rec_res = recurring.detect_recurring(ds)
    rs = recurring.refresh_recurring(con, ds)
    ar = anomalies.refresh_anomalies(con, ds, recurring=rec_res)
    bs = budgets.budget_status(ds, recurring=rec_res)
    return {"recurring": rs.to_dict(), "anomalies": ar.to_dict(),
            "budgets": {"set": len(ds.memory.budgets), **bs.counts}}


def redacted_registry(con, cfg, today: Optional[dt.date] = None):
    """The registry for a MODEL (the finance MCP server uses only this): pseudonymised accounts and owners, hashed
    transaction keys, scrubbed free text, anomaly messages rebuilt from structured fields. See ``analytics.privacy``."""
    from coach.analytics.privacy import redacted_registry as build
    return build(con, cfg, today)
