"""VR端末ごとの最新CSV取得・統合。

Google Drive上のCSV一覧を毎回再取得し、
端末（Player_ID）ごとに modifiedTime が最新の1ファイルだけを使う。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from app.drive_latest import (
    CsvFileInfo,
    download_drive_file,
    extract_drive_file_id,
    extract_drive_folder_id,
    is_folder_url,
    list_drive_folder_csvs,
    list_local_csvs,
    log_latest_csv_selection,
    pick_latest_csv,
)

logger = logging.getLogger(__name__)

# Drive上の実CSVヘッダー（可視化・集計はこの名前を使う）
VR_CSV_HEADERS = [
    "Elapsed_Time",
    "Event_Type",
    "Player_ID",
    "Target_Object",
    "Data_Value",
    "WorldX",
    "WorldY",
    "WorldZ",
    "LocalX",
    "LocalY",
    "LocalZ",
]

DEFAULT_VR_DEVICES = ["A", "B", "C", "D"]


@dataclass
class DeviceCsvSelection:
    device_id: str
    file: CsvFileInfo
    df: pd.DataFrame
    record_count: int = 0


@dataclass
class VrLoadResult:
    mode: str  # all | individual
    selected_device: str | None
    df: pd.DataFrame
    selections: list[DeviceCsvSelection] = field(default_factory=list)
    candidates: list[CsvFileInfo] = field(default_factory=list)
    selection_log: str = ""
    record_count: int = 0


def prepare_vr_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """ヘッダーはリネームせず、空白除去と数値化のみ。"""
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    for col in ("Elapsed_Time", "Data_Value", "WorldX", "WorldY", "WorldZ", "LocalX", "LocalY", "LocalZ"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "Player_ID" in out.columns:
        out["Player_ID"] = out["Player_ID"].astype(str).str.strip()
    return out


def read_csv_bytes(content: bytes) -> pd.DataFrame:
    if content[:15].lower().startswith(b"<!doctype") or content[:6].lower().startswith(b"<html"):
        raise ValueError(
            "CSVではなくHTMLが返されました。"
            "Google Driveの共有設定が「リンクを知っている全員が閲覧可」か確認してください。"
        )
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return prepare_vr_dataframe(
                pd.read_csv(
                    BytesIO(content),
                    engine="python",
                    on_bad_lines="skip",
                    encoding=encoding,
                )
            )
        except Exception as e:
            last_error = e
    raise ValueError(f"CSVの読み込みに失敗しました: {last_error}")


def infer_device_id(file_info: CsvFileInfo, df: pd.DataFrame) -> str:
    """CSV内容の Player_ID を優先して端末IDを推定。"""
    if "Player_ID" in df.columns:
        values = (
            df["Player_ID"]
            .dropna()
            .astype(str)
            .str.strip()
        )
        values = values[(values != "") & (values.str.lower() != "nan") & (values != "None")]
        uniq = sorted(values.unique().tolist())
        if len(uniq) == 1:
            return str(uniq[0])
        if len(uniq) > 1:
            # 複数Playerを含むファイルは代表値として最多を使う
            mode = values.mode()
            if not mode.empty:
                return str(mode.iloc[0])

    name = file_info.name or ""
    patterns = [
        r"(?:player|vr|device|端末)[-_]?([A-Za-z0-9]+)",
        r"(?:^|[^A-Za-z0-9])([ABCD])(?:[^A-Za-z0-9]|$)",
        r"(?:^|[^0-9])([1-4])(?:[^0-9]|$)",
    ]
    for pat in patterns:
        m = re.search(pat, name, re.IGNORECASE)
        if m:
            return m.group(1).upper() if m.group(1).isalpha() else m.group(1)

    return "UNKNOWN"


def list_candidate_csvs(
    *,
    drive_url: str | None,
    api_key: str | None,
    prefer_local: bool,
    local_dir: Path | str | None,
) -> list[CsvFileInfo]:
    """毎回CSV一覧を再取得する。"""
    url = (drive_url or "").strip()

    if prefer_local and local_dir:
        files = list_local_csvs(local_dir)
        if files:
            logger.info("re-listed %s local csv files", len(files))
            return files

    if url and is_folder_url(url):
        folder_id = extract_drive_folder_id(url)
        if not folder_id:
            raise ValueError("Google DriveフォルダIDを抽出できませんでした。")
        files = list_drive_folder_csvs(folder_id, api_key=api_key)
        logger.info("re-listed %s drive folder csv files", len(files))
        return files

    if url:
        file_id = extract_drive_file_id(url)
        if not file_id:
            raise ValueError("Google DriveファイルIDを抽出できませんでした。")
        content, modified = download_drive_file(file_id)
        # 単一ファイルでも一覧として扱う（後で端末判定）
        info = CsvFileInfo(
            file_id=file_id,
            name=f"{file_id}.csv",
            modified_time=modified,
            source="drive",
        )
        # content は呼び出し側で再DLする（状態を持たない）
        logger.info("re-listed single drive file id=%s modified=%s", file_id, modified)
        return [info]

    if local_dir:
        files = list_local_csvs(local_dir)
        if files:
            return files

    raise FileNotFoundError(
        "CSV一覧を取得できませんでした。"
        "Google DriveのフォルダURLを指定するか、data/input にCSVを配置してください。"
    )


def _load_file_content(info: CsvFileInfo) -> tuple[bytes, str | None]:
    if info.source == "local" and info.path:
        return Path(info.path).read_bytes(), info.modified_time
    content, header_modified = download_drive_file(info.file_id)
    if not info.modified_time and header_modified:
        info.modified_time = header_modified
    return content, info.modified_time


def select_latest_csv_per_device(
    candidates: list[CsvFileInfo],
) -> tuple[dict[str, DeviceCsvSelection], str]:
    """全候補を読み、端末ごとに modifiedTime 最新の1件を選ぶ。"""
    if not candidates:
        raise FileNotFoundError("CSVファイルが見つかりませんでした。")

    # 端末ごとに候補を集める
    by_device_files: dict[str, list[tuple[CsvFileInfo, pd.DataFrame]]] = {}
    log_lines = ["[VR端末別 最新CSV選定]"]

    for info in candidates:
        content, _ = _load_file_content(info)
        df = read_csv_bytes(content)
        device_id = infer_device_id(info, df)
        by_device_files.setdefault(device_id, []).append((info, df))
        log_lines.append(
            f"  候補: name={info.name} fileId={info.file_id} "
            f"modifiedTime={info.modified_time or '-'} device={device_id} rows={len(df)}"
        )

    selections: dict[str, DeviceCsvSelection] = {}
    for device_id, items in by_device_files.items():
        # modifiedTime で最新ファイルを選択（ファイル名は使わない）
        best_info, best_df = max(items, key=lambda pair: pair[0].sort_key)
        sel = DeviceCsvSelection(
            device_id=device_id,
            file=best_info,
            df=best_df,
            record_count=len(best_df),
        )
        selections[device_id] = sel
        log_latest_csv_selection(best_info, [p[0] for p in items])
        log_lines.append(
            f"  --> 端末 {device_id} の最新: {best_info.name} | "
            f"fileId={best_info.file_id} | modifiedTime={best_info.modified_time or '-'} | "
            f"records={len(best_df)}"
        )

    selection_log = "\n".join(log_lines)
    for line in log_lines:
        logger.info(line)
    return selections, selection_log


def build_analysis_frame(
    selections: dict[str, DeviceCsvSelection],
    *,
    mode: str,
    selected_device: str | None,
) -> pd.DataFrame:
    if mode == "individual":
        if not selected_device:
            raise ValueError("個人モードではVR端末 / Player_ID を選択してください。")
        if selected_device not in selections:
            available = ", ".join(sorted(selections.keys())) or "(なし)"
            raise ValueError(
                f"選択された端末「{selected_device}」の最新CSVが見つかりません。"
                f"利用可能: {available}"
            )
        return selections[selected_device].df.copy()

    # 全員: 各端末の最新CSVを統合
    frames = [sel.df.copy() for _, sel in sorted(selections.items(), key=lambda x: str(x[0]))]
    if not frames:
        raise FileNotFoundError("統合対象のCSVがありません。")
    return pd.concat(frames, ignore_index=True)


def load_vr_csvs_for_mode(
    *,
    drive_url: str | None,
    api_key: str | None,
    mode: str,
    selected_device: str | None,
    prefer_local: bool = False,
    local_dir: Path | str | None = None,
) -> VrLoadResult:
    """
    mode=all: 各VR端末の最新CSVを1つずつ統合
    mode=individual: 選択端末の最新CSVのみ
    """
    from app.drive_latest import LOCAL_CSV_DIR

    candidates = list_candidate_csvs(
        drive_url=drive_url,
        api_key=api_key,
        prefer_local=prefer_local,
        local_dir=local_dir or LOCAL_CSV_DIR,
    )
    selections, selection_log = select_latest_csv_per_device(candidates)
    df = build_analysis_frame(selections, mode=mode, selected_device=selected_device)

    used = list(selections.values())
    if mode == "individual" and selected_device:
        used = [selections[selected_device]]

    result = VrLoadResult(
        mode=mode,
        selected_device=selected_device,
        df=df,
        selections=used if mode == "individual" else list(selections.values()),
        candidates=candidates,
        selection_log=selection_log,
        record_count=len(df),
    )

    logger.info(
        "[分析データ確定] mode=%s selected_device=%s files=%s record_count=%s",
        mode,
        selected_device,
        [(s.file.name, s.file.file_id, s.file.modified_time) for s in result.selections],
        result.record_count,
    )
    for s in result.selections:
        logger.info(
            "[使用CSV] device=%s name=%s fileId=%s modifiedTime=%s records=%s",
            s.device_id,
            s.file.name,
            s.file.file_id,
            s.file.modified_time,
            s.record_count,
        )
    return result
