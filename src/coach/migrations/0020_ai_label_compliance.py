"""E11-5: every LLM-written insight is labelled AI-generated and carries the result of the compliance check; flagged answers are recorded.
Idempotent (SQLite has no ADD COLUMN IF NOT EXISTS): every step checks what is already there. Additive only.

* insights.ai_generated   1 for text written by a model (default), 0 for the deterministic ones (backend 'local' / 'code')
* insights.compliance     JSON list of flag codes (isin | product_name | recommendation) found in the text by coach.compliance
* compliance_events       one row per flagged text: when, where from, the codes and short snippets (local only)
Existing insights are checked once here."""
import json


def _has_column(con, table: str, col: str) -> bool:
    return any(r[1] == col for r in con.execute(f"PRAGMA table_info({table})"))


def run(con) -> None:
    if not _has_column(con, "insights", "ai_generated"):
        con.execute("ALTER TABLE insights ADD COLUMN ai_generated INTEGER NOT NULL DEFAULT 1")
        con.execute("UPDATE insights SET ai_generated=0 WHERE backend IN ('local','code')")
    if not _has_column(con, "insights", "compliance"):
        con.execute("ALTER TABLE insights ADD COLUMN compliance TEXT NOT NULL DEFAULT '[]'")
    con.execute("""CREATE TABLE IF NOT EXISTS compliance_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      source TEXT NOT NULL,              -- insight | answer | digest ...
      insight_id TEXT,
      codes TEXT NOT NULL DEFAULT '[]',
      flags TEXT NOT NULL DEFAULT '[]',  -- JSON [{code, snippet}]
      lang TEXT NOT NULL DEFAULT 'en')""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_compliance_ts ON compliance_events(ts)")
    # check the insights that already exist (a one-off pass; the text never leaves the database)
    from coach import compliance
    rows = con.execute("SELECT id, title, body FROM insights WHERE ai_generated=1 AND compliance='[]'").fetchall()
    for iid, title, body in rows:
        flags = compliance.check(f"{title}\n{body}")
        if flags:
            con.execute("UPDATE insights SET compliance=? WHERE id=?", (json.dumps(compliance.codes(flags)), iid))
            con.execute("INSERT INTO compliance_events(ts, source, insight_id, codes, flags, lang) VALUES (datetime('now'),?,?,?,?,?)",
                        ("insight", iid, json.dumps(compliance.codes(flags)),
                         json.dumps([f.as_dict() for f in flags], ensure_ascii=False), compliance.detect_lang(body or "")))
