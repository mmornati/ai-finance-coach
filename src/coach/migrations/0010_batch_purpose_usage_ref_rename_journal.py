"""Batch purpose, one usage row per batch result, taxonomy-rename tokens. Idempotent: every step checks what is
already there (SQLite has no ADD COLUMN IF NOT EXISTS), so a partly prepared database does not fail."""


def _has_column(con, table: str, col: str) -> bool:
    return any(r[1] == col for r in con.execute(f"PRAGMA table_info({table})"))


def run(con) -> None:
    # what a batch is for (label | compare): a run only collects the purposes it can handle
    if not _has_column(con, "llm_batches", "purpose"):
        con.execute("ALTER TABLE llm_batches ADD COLUMN purpose TEXT")
    # one usage row per (batch, request): two collectors can never count the same paid result twice
    if not _has_column(con, "llm_usage", "batch_ref"):
        con.execute("ALTER TABLE llm_usage ADD COLUMN batch_ref TEXT")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_batch_ref ON llm_usage(batch_ref) WHERE batch_ref IS NOT NULL")
    # committed together with a taxonomy rename: tells the recovery of an interrupted file swap whether the database
    # side of the rename happened
    con.execute("CREATE TABLE IF NOT EXISTS taxonomy_renames (token TEXT PRIMARY KEY, old_id TEXT, new_id TEXT, at TEXT)")
