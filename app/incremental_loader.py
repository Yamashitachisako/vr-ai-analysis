"""新規追加分の抽出と日付期間フィルタ。"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

import pandas as pd

from app.import_history import (
    add_row_hashes,
    compute_content_hash,
    filter_new_rows,
    get_connection,
    mark_file_imported,
    mark_rows_imported,
    record_import_run,
)

logger = logging.getLogger(__name__)

DATE_COLUMN_CANDIDATES = [
    "session_date",
    "Session_Date",
    "record_date",
    "Record_Date",
    "date",
    "Date",
    "datetime",
    "DateTime",
    "created_at",
    "Created_At",
    "timestamp",
    "Timestamp",
    "Elapsed_Time",
]


def detect_date_columns(df: pd.DataFrame) -> list[str]:
    """カレンダー日付として使えそうなカラム候補を返す。"""
    found: list[str] = []
    for col in df.columns:
        name = str(col)
        if name in DATE_COLUMN_CANDIDATES or name.lower() in {c.lower() for c in DATE_COLUMN_CANDIDATES}:
            found.append(name)
            continue
        if re.search(r"date|time|日時|日付", name, re.IGNORECASE):
            found.append(name)
    # 重複除去（順序保持）
    seen = set()
    ordered = []
    for c in found:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def _looks_like_elapsed_seconds(series: pd.Series) -> bool:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return False
    # 0〜1日分の秒、または小さな経過値
    return bool(numeric.max() < 86400 * 7 and numeric.min() >= 0 and numeric.median() < 86400)


def parse_date_series(series: pd.Series) -> pd.Series:
    """様々な日付形式を Asia/Tokyo の date に正規化。経過秒は NaT 扱い。"""
    if _looks_like_elapsed_seconds(series):
        logger.info("column looks like elapsed seconds; not used as calendar date")
        return pd.Series([pd.NaT] * len(series), index=series.index)

    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    valid = parsed.dropna()
    if valid.empty:
        return pd.Series([pd.NaT] * len(series), index=series.index)

    if getattr(parsed.dt, "tz", None) is not None:
        return parsed.dt.tz_convert("Asia/Tokyo").dt.date
    return parsed.dt.date


def filter_by_date_range(
    df: pd.DataFrame,
    *,
    start_date: date | None,
    end_date: date | None,
    date_column: str | None,
) -> tuple[pd.DataFrame, str | None]:
    """指定期間で絞り込み。成功時 (df, used_column)、失敗時は元dfと理由。"""
    if start_date is None and end_date is None:
        return df, None

    candidates = [date_column] if date_column else detect_date_columns(df)
    candidates = [c for c in candidates if c and c in df.columns]
    if not candidates:
        logger.warning("no usable date column for range filter")
        return df, None

    for col in candidates:
        dates = parse_date_series(df[col])
        if dates.isna().all():
            continue
        mask = pd.Series(True, index=df.index)
        if start_date is not None:
            mask &= dates >= start_date
        if end_date is not None:
            mask &= dates <= end_date
        filtered = df.loc[mask].copy()
        logger.info(
            "date filter col=%s start=%s end=%s -> %s/%s rows",
            col,
            start_date,
            end_date,
            len(filtered),
            len(df),
        )
        return filtered, col

    logger.warning("date columns found but none parseable as calendar dates: %s", candidates)
    return df, None


def download_drive_content(download_url: str, timeout: int = 60) -> tuple[bytes, str | None]:
    """Google Drive から bytes と Last-Modified を取得。"""
    request = Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=timeout) as response:
        content = response.read()
        modified = response.headers.get("Last-Modified")
    return content, modified


def prepare_analysis_dataframe(
    raw_df: pd.DataFrame,
    *,
    file_id: str,
    file_name: str | None = None,
    content: bytes | None = None,
    modified_time: str | None = None,
    mode: str = "new_only",
    start_date: date | None = None,
    end_date: date | None = None,
    date_column: str | None = None,
    mark_imported: bool = True,
    db_path: Any = None,
) -> dict[str, Any]:
    """分析用 DataFrame を準備する。

    mode:
      - new_only: 未取り込み行のみ（日付指定なし時の既定）
      - date_range: 期間指定で抽出（履歴には任意で記録）
      - all: 全件（履歴無視・再分析用）
    """
    conn = get_connection(db_path) if db_path is not None else get_connection()
    try:
        total = len(raw_df)
        used_date_col = None
        note_parts: list[str] = []

        working = add_row_hashes(raw_df)

        if mode == "date_range" or (start_date is not None or end_date is not None):
            working, used_date_col = filter_by_date_range(
                working,
                start_date=start_date,
                end_date=end_date,
                date_column=date_column,
            )
            if used_date_col is None and (start_date or end_date):
                note_parts.append(
                    "日付カラムをカレンダー日付として解釈できなかったため、期間フィルタを適用できませんでした。"
                    "日付カラムを明示指定するか、Session_Date 等の列を追加してください。"
                )
            mode = "date_range"
            result_df = working
            rows_new = len(result_df)
        elif mode == "all":
            result_df = working
            rows_new = len(result_df)
            note_parts.append("全件モード（取り込み履歴を無視）")
        else:
            result_df = filter_new_rows(working, conn, file_id=file_id)
            rows_new = len(result_df)
            note_parts.append("新規追加分のみ")

        content_hash = compute_content_hash(content) if content is not None else None

        if mark_imported and mode in ("new_only", "date_range") and not result_df.empty:
            mark_file_imported(
                conn,
                file_id=file_id,
                file_name=file_name,
                content_hash=content_hash,
                modified_time=modified_time,
                row_count=total,
            )
            if mode == "new_only":
                mark_rows_imported(conn, result_df, file_id=file_id)

        record_import_run(
            conn,
            file_id=file_id,
            mode=mode,
            start_date=start_date.isoformat() if start_date else None,
            end_date=end_date.isoformat() if end_date else None,
            rows_loaded=total,
            rows_new=rows_new,
            note="; ".join(note_parts),
        )

        analysis_df = result_df.drop(columns=["_row_hash"], errors="ignore")

        return {
            "df": analysis_df,
            "mode": mode,
            "total_rows": total,
            "selected_rows": rows_new,
            "date_column_used": used_date_col,
            "notes": note_parts,
            "content_hash": content_hash,
            "modified_time": modified_time,
        }
    finally:
        conn.close()
