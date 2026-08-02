"""VR端末（最大4台）ごとの最新CSV取得・統合。

- sample_session.csv / 仮データは使わない
- 列名はリネームしない（実CSVヘッダー1行目を正とする）
- Player_ID の値も置き換えない（端末IDとしてそのまま使う）
- 全員: 各端末の最新CSVを1つずつ統合（最大4件）
- 個人: 選択した1端末の最新CSVのみ
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import pandas as pd

from app.drive_latest import (
    DEFAULT_DRIVE_FOLDER_ID,
    CsvFileInfo,
    download_drive_file,
    extract_drive_file_id,
    extract_drive_folder_id,
    is_folder_url,
    list_drive_folder_csvs,
    list_local_csvs,
    log_latest_csv_selection,
)

logger = logging.getLogger(__name__)

# VRは4台＝個人も最大4人分
EXPECTED_VR_DEVICE_COUNT = 4

# 実CSVで確認したヘッダー（参考。実際はファイル1行目を正とする）
VR_CSV_HEADERS = [
    "Elapsed_Time",
    "Event_Type",
    "Player_ID",
    "Player_X",
    "Player_Y",
    "Player_Z",
    "Target_Object",
    "Reaction_Time_Micro",
    "World_X",
    "World_Y",
    "World_Z",
    "Local_X",
    "Local_Y",
    "Local_Z",
]

FORBIDDEN_SAMPLE_COLUMNS = {
    "timestamp",
    "player_id",
    "event_type",
    "reaction_time",
    "gaze_x",
    "gaze_y",
    "location",
}

NUMERIC_COLUMNS = (
    "Player_X",
    "Player_Y",
    "Player_Z",
    "Reaction_Time_Micro",
    "Reaction_Time_Mic",
    "World_X",
    "World_Y",
    "World_Z",
    "Local_X",
    "Local_Y",
    "Local_Z",
)


@dataclass
class DeviceCsvSelection:
    """1台分の最新CSV。device_id は CSV の Player_ID（またはファイル名推定）。"""

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
    headers: list[str] = field(default_factory=list)
    player_ids: list[str] = field(default_factory=list)  # 検出した端末ID（最大4）


def prepare_vr_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """ヘッダーはリネームせず、空白除去と数値化のみ。"""
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]

    lower_map = {c.lower(): c for c in out.columns}
    sample_hits = [name for name in FORBIDDEN_SAMPLE_COLUMNS if name in lower_map]
    has_real = (
        "Elapsed_Time" in out.columns
        or "Player_ID" in out.columns
        or "Event_Type" in out.columns
    )
    if sample_hits and not has_real:
        raise ValueError(
            "サンプルCSV形式（timestamp / player_id 等）を検出しました。"
            "仮データは使えません。Google Drive フォルダ内の実CSV"
            "（Elapsed_Time / Event_Type / Player_ID 等）を読み込んでください。"
        )
    if not has_real:
        raise ValueError(
            "実CSVヘッダー（Elapsed_Time / Event_Type / Player_ID）が見つかりません。"
            "Google Drive 上の VR ログ CSV を使用してください。"
        )

    for col in NUMERIC_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "Player_ID" in out.columns:
        out["Player_ID"] = out["Player_ID"].astype(str).str.strip()
        out.loc[out["Player_ID"].str.lower().isin(["nan", "none", ""]), "Player_ID"] = pd.NA

    return out


def unique_player_ids(df: pd.DataFrame) -> list[str]:
    if "Player_ID" not in df.columns:
        return []
    values = df["Player_ID"].dropna().astype(str).str.strip()
    values = values[(values != "") & (values.str.lower() != "nan")]
    return sorted(values.unique().tolist(), key=lambda x: x.lower())


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
        except ValueError:
            raise
        except Exception as e:
            last_error = e
    raise ValueError(f"CSVの読み込みに失敗しました: {last_error}")


def infer_device_id(file_info: CsvFileInfo, df: pd.DataFrame) -> str:
    """CSV の Player_ID 値をそのまま返す（A/B 等へ置換しない）。"""
    players = unique_player_ids(df)
    if len(players) == 1:
        return players[0]
    if len(players) > 1:
        mode = df["Player_ID"].dropna().astype(str).str.strip().mode()
        if not mode.empty:
            return str(mode.iloc[0]).strip()
    return "UNKNOWN"






def resolve_drive_folder_id(drive_url: str | None) -> str:
    """常に親フォルダ DEFAULT_DRIVE_FOLDER_ID を返す。"""
    url = (drive_url or "").strip()
    if url and is_folder_url(url):
        parsed = extract_drive_folder_id(url)
        if parsed and parsed != DEFAULT_DRIVE_FOLDER_ID:
            logger.warning(
                "folder URL folderId=%s は無視し、固定の folderId=%s を使います。",
                parsed,
                DEFAULT_DRIVE_FOLDER_ID,
            )
    elif url:
        file_id = extract_drive_file_id(url)
        logger.warning(
            "file URL/fileId=%s は固定読み込みしません。folderId=%s を使います。",
            file_id or url,
            DEFAULT_DRIVE_FOLDER_ID,
        )
    return DEFAULT_DRIVE_FOLDER_ID


def list_candidate_csvs(
    *,
    drive_url: str | None,
    api_key: str | None,
    prefer_local: bool,
    local_dir: Path | str | None,
) -> list[CsvFileInfo]:
    """毎回フォルダ内CSV一覧を再取得する。sample_session は除外。"""
    if prefer_local and local_dir:
        files = [
            f
            for f in list_local_csvs(local_dir)
            if f.name.lower() != "sample_session.csv"
        ]
        if files:
            logger.info("re-listed %s local csv files (sample_session excluded)", len(files))
            return files
        raise FileNotFoundError(
            "ローカルに実CSVがありません（sample_session.csv は使用しません）。"
            "Google Drive フォルダから取得してください。"
        )

    folder_id = resolve_drive_folder_id(drive_url)
    files = list_drive_folder_csvs(folder_id, api_key=api_key)
    files = [f for f in files if f.name.lower() != "sample_session.csv"]
    ranked = sorted(files, key=lambda f: f.sort_key, reverse=True)
    logger.info(
        "re-listed %s drive folder csv files from folderId=%s "
        "(latest modifiedTime=%s name=%s fileId=%s)",
        len(files),
        folder_id,
        ranked[0].modified_time if ranked else None,
        ranked[0].name if ranked else None,
        ranked[0].file_id if ranked else None,
    )
    if not files:
        raise FileNotFoundError(
            f"フォルダ（folderId={folder_id}）内にCSVが見つかりませんでした。"
        )
    return files


def _load_file_content(info: CsvFileInfo) -> tuple[bytes, str | None]:
    if info.source == "local" and info.path:
        return Path(info.path).read_bytes(), info.modified_time
    content, header_modified = download_drive_file(info.file_id)
    if not info.modified_time and header_modified:
        info.modified_time = header_modified
    return content, info.modified_time


def select_latest_csv_per_device(
    candidates: list[CsvFileInfo],
    *,
    max_downloads: int = 40,
    max_devices: int = EXPECTED_VR_DEVICE_COUNT,
) -> tuple[dict[str, DeviceCsvSelection], str]:
    """候補を modifiedTime 新しい順に読み、端末ごとに最新1件を選ぶ（最大4台）。"""
    if not candidates:
        raise FileNotFoundError("CSVファイルが見つかりませんでした。")

    ranked = sorted(candidates, key=lambda f: (f.sort_key, f.name), reverse=True)
    log_lines = [
        "[VR端末別 最新CSV選定]",
        f"  想定端末数={max_devices} / 候補総数={len(ranked)} / "
        f"読み込み上限={max_downloads}（modifiedTime新しい順）",
    ]

    selections: dict[str, DeviceCsvSelection] = {}
    inspected = 0
    for info in ranked:
        if len(selections) >= max_devices:
            break
        if inspected >= max_downloads and selections:
            break
        inspected += 1
        try:
            content, _ = _load_file_content(info)
            df = read_csv_bytes(content)
        except Exception as e:
            log_lines.append(f"  skip name={info.name} fileId={info.file_id} error={e}")
            logger.warning("skip csv name=%s error=%s", info.name, e)
            continue

        device_id = infer_device_id(info, df)
        log_lines.append(
            f"  検査: name={info.name} fileId={info.file_id} "
            f"modifiedTime={info.modified_time or '-'} device={device_id} rows={len(df)}"
        )
        if device_id in selections:
            # すでに新しい順で選済みのため、後続は古い
            continue

        selections[device_id] = DeviceCsvSelection(
            device_id=device_id,
            file=info,
            df=df,
            record_count=len(df),
        )
        log_latest_csv_selection(info, [info])
        log_lines.append(
            f"  --> 端末 {device_id} の最新: {info.name} | "
            f"fileId={info.file_id} | modifiedTime={info.modified_time or '-'} | "
            f"records={len(df)}"
        )

    if not selections:
        raise FileNotFoundError("端末ごとの最新CSVを特定できませんでした。")

    if len(selections) > max_devices:
        # 念のため truncate（通常はループで止まる）
        keep = sorted(selections.items(), key=lambda x: x[1].file.sort_key, reverse=True)[
            :max_devices
        ]
        selections = dict(keep)

    log_lines.append(
        f"  確定端末数={len(selections)} / 端末一覧={sorted(selections.keys(), key=str.lower)}"
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
            raise ValueError(
                "個人モードでは VR端末（最大4台のうち1つ）を選択してください。"
            )
        if selected_device not in selections:
            available = ", ".join(sorted(selections.keys(), key=str.lower)) or "(なし)"
            raise ValueError(
                f"選択された端末「{selected_device}」の最新CSVが見つかりません。"
                f"利用可能（最大{EXPECTED_VR_DEVICE_COUNT}台）: {available}"
            )
        return selections[selected_device].df.copy()

    frames = [sel.df.copy() for _, sel in sorted(selections.items(), key=lambda x: str(x[0]).lower())]
    if not frames:
        raise FileNotFoundError("統合対象のCSVがありません。")
    return pd.concat(frames, ignore_index=True)


def format_usage_log(
    *,
    mode: str,
    selected_device: str | None,
    selections: list[DeviceCsvSelection],
    headers: list[str],
    player_ids: list[str],
    record_count: int,
) -> str:
    mode_label = "全員（最大4台の最新CSVを統合）" if mode == "all" else "個人（1台の最新CSV）"
    lines = [
        "[使用CSVログ]",
        f"  選択モード: {mode_label}",
        f"  選択端末/Player_ID: {selected_device or '(全員・最大4台)'}",
        f"  使用ファイル数: {len(selections)} / 想定最大 {EXPECTED_VR_DEVICE_COUNT}",
        f"  検出端末一覧: {player_ids}",
        f"  CSVヘッダー一覧: {headers}",
        f"  表示に使ったレコード件数: {record_count}",
        f"  folderId: {DEFAULT_DRIVE_FOLDER_ID}",
    ]
    for s in selections:
        lines.append(
            f"  - device={s.device_id} name={s.file.name} "
            f"fileId={s.file.file_id} modifiedTime={s.file.modified_time or '-'} "
            f"records={s.record_count}"
        )
    return "\n".join(lines)


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
    mode=all: 各VR端末の最新CSVを1つずつ統合（最大4台）
    mode=individual: 選択端末の最新CSVのみ
    """
    from app.drive_latest import LOCAL_CSV_DIR

    candidates = list_candidate_csvs(
        drive_url=drive_url,
        api_key=api_key,
        prefer_local=prefer_local,
        local_dir=local_dir or LOCAL_CSV_DIR,
    )
    selections_map, selection_log = select_latest_csv_per_device(candidates)
    device_ids = sorted(selections_map.keys(), key=str.lower)

    df = build_analysis_frame(
        selections_map,
        mode=mode,
        selected_device=selected_device,
    )

    if mode == "individual" and selected_device:
        used = [selections_map[selected_device]]
    else:
        used = [selections_map[k] for k in device_ids]

    headers = list(df.columns)
    usage = format_usage_log(
        mode=mode,
        selected_device=selected_device,
        selections=used,
        headers=headers,
        player_ids=device_ids,
        record_count=len(df),
    )
    logger.info(usage)

    return VrLoadResult(
        mode=mode,
        selected_device=selected_device,
        df=df,
        selections=used,
        candidates=candidates,
        selection_log=selection_log + "\n\n" + usage,
        record_count=len(df),
        headers=headers,
        player_ids=device_ids,
    )
