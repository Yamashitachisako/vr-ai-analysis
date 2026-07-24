"""取り込み履歴（SQLite）の管理。

判定キー:
- file_id / file_name / content_hash / modified_time（ファイル単位）
- row_hash（行単位・重複防止）
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "import_history.db"

ROW_HASH_COLUMNS = [
    "timestamp",
    "player_id",
    "event_type",
    "location",
    "target_object",
    "reaction_time",
    "data_value",
    "gaze_x",
    "gaze_y",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS imported_files (
            file_id TEXT PRIMARY KEY,
            file_name TEXT,
            content_hash TEXT,
            modified_time TEXT,
            row_count INTEGER,
            last_imported_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS imported_rows (
            row_hash TEXT PRIMARY KEY,
            file_id TEXT,
            imported_at TEXT NOT NULL,
            FOREIGN KEY (file_id) REFERENCES imported_files(file_id)
        );

        CREATE TABLE IF NOT EXISTS import_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT,
            mode TEXT,
            start_date TEXT,
            end_date TEXT,
            rows_loaded INTEGER,
            rows_new INTEGER,
            note TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def compute_content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def compute_row_hash(row: pd.Series) -> str:
    parts = []
    for col in ROW_HASH_COLUMNS:
        if col in row.index:
            val = row[col]
            parts.append(f"{col}={'' if pd.isna(val) else val}")
    # フォールバック: 全カラム
    if not parts:
        for col in sorted(row.index.astype(str)):
            val = row[col]
            parts.append(f"{col}={'' if pd.isna(val) else val}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def add_row_hashes(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["_row_hash"] = [compute_row_hash(row) for _, row in out.iterrows()]
    return out


def get_imported_row_hashes(conn: sqlite3.Connection, file_id: str | None = None) -> set[str]:
    if file_id:
        rows = conn.execute(
            "SELECT row_hash FROM imported_rows WHERE file_id = ?",
            (file_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT row_hash FROM imported_rows").fetchall()
    return {r["row_hash"] for r in rows}


def get_file_record(conn: sqlite3.Connection, file_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM imported_files WHERE file_id = ?",
        (file_id,),
    ).fetchone()
    return dict(row) if row else None


def filter_new_rows(df: pd.DataFrame, conn: sqlite3.Connection, file_id: str | None = None) -> pd.DataFrame:
    """取り込み済み行を除外した DataFrame を返す。"""
    if df.empty:
        return df
    working = add_row_hashes(df) if "_row_hash" not in df.columns else df.copy()
    known = get_imported_row_hashes(conn, file_id=None)  # 全ファイル横断で重複防止
    mask = ~working["_row_hash"].isin(known)
    new_df = working.loc[mask].copy()
    logger.info(
        "incremental filter: total=%s known=%s new=%s file_id=%s",
        len(working),
        len(known),
        len(new_df),
        file_id,
    )
    return new_df


def mark_file_imported(
    conn: sqlite3.Connection,
    *,
    file_id: str,
    file_name: str | None,
    content_hash: str | None,
    modified_time: str | None,
    row_count: int,
) -> None:
    conn.execute(
        """
        INSERT INTO imported_files (file_id, file_name, content_hash, modified_time, row_count, last_imported_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET
            file_name = excluded.file_name,
            content_hash = excluded.content_hash,
            modified_time = excluded.modified_time,
            row_count = excluded.row_count,
            last_imported_at = excluded.last_imported_at
        """,
        (file_id, file_name, content_hash, modified_time, row_count, _utc_now()),
    )
    conn.commit()


def mark_rows_imported(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    file_id: str,
) -> int:
    if df.empty:
        return 0
    working = add_row_hashes(df) if "_row_hash" not in df.columns else df
    now = _utc_now()
    payloads = [(h, file_id, now) for h in working["_row_hash"].tolist()]
    conn.executemany(
        """
        INSERT OR IGNORE INTO imported_rows (row_hash, file_id, imported_at)
        VALUES (?, ?, ?)
        """,
        payloads,
    )
    conn.commit()
    logger.info("marked %s rows imported for file_id=%s", len(payloads), file_id)
    return len(payloads)


def record_import_run(
    conn: sqlite3.Connection,
    *,
    file_id: str,
    mode: str,
    start_date: str | None,
    end_date: str | None,
    rows_loaded: int,
    rows_new: int,
    note: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO import_runs
            (file_id, mode, start_date, end_date, rows_loaded, rows_new, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (file_id, mode, start_date, end_date, rows_loaded, rows_new, note, _utc_now()),
    )
    conn.commit()


def list_import_runs(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM import_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_imported_files(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM imported_files ORDER BY last_imported_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def clear_history(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DELETE FROM imported_rows;
        DELETE FROM imported_files;
        DELETE FROM import_runs;
        """
    )
    conn.commit()
    logger.warning("import history cleared")
