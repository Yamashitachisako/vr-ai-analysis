"""Google Drive 親フォルダから最新CSVを取得する。

- sample_session.csv / 仮データは使わない
- 列名はリネームしない（実CSVヘッダー1行目を正とする）
- Player_ID の値（ota / Player / player 等）も置き換えない
"""

from __future__ import annotations

import logging
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
    pick_latest_csv,
)

logger = logging.getLogger(__name__)

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

# サンプルCSVの列名（これを検出したら拒否）
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
    "Elapsed_Time",
    "Player_X",
    "Player_Y",
    "Player_Z",
    "Reaction_Time_Micro",
    "Reaction_Time_Mic",
    "Data_Value",
    "World_X",
    "World_Y",
    "World_Z",
    "Local_X",
    "Local_Y",
    "Local_Z",
    "WorldX",
    "WorldY",
    "WorldZ",
    "LocalX",
    "LocalY",
    "LocalZ",
)


@dataclass
class DeviceCsvSelection:
    """互換用。device_id には CSV の Player_ID 値をそのまま入れる。"""

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
    player_ids: list[str] = field(default_factory=list)


def prepare_vr_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """ヘッダーはリネームせず、空白除去と数値化のみ。"""
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]

    lower_map = {c.lower(): c for c in out.columns}
    sample_hits = [name for name in FORBIDDEN_SAMPLE_COLUMNS if name in lower_map]
    # サンプル形式（timestamp 等）だけで構成されている場合は拒否
    if len(sample_hits) >= 4 and "Elapsed_Time" not in out.columns and "Player_ID" not in out.columns:
        raise ValueError(
            "サンプルCSV形式（timestamp / player_id 等）を検出しました。"
            "sample_session.csv や仮データは使えません。"
            "Google Drive フォルダ内の実CSVを読み込んでください。"
        )

    for col in NUMERIC_COLUMNS:
        if col in out.columns:
            # Elapsed_Time が "00:00.00" 形式の場合は数値化に失敗してもよい
            if col == "Elapsed_Time":
                continue
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "Player_ID" in out.columns:
        # 値は大文字化・A/B置換せず、前後空白のみ除去
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


def format_usage_log(
    *,
    mode: str,
    selected_player: str | None,
    info: CsvFileInfo,
    df: pd.DataFrame,
    headers: list[str],
    player_ids: list[str],
) -> str:
    mode_label = "全員" if mode == "all" else "個人"
    lines = [
        "[使用CSVログ]",
        f"  選択モード: {mode_label}",
        f"  選択 Player_ID: {selected_player or '(全員)'}",
        f"  読み込んだCSVファイル名: {info.name}",
        f"  fileId: {info.file_id}",
        f"  modifiedTime: {info.modified_time or '-'}",
        f"  CSVヘッダー一覧: {headers}",
        f"  Player_ID のユニーク値一覧: {player_ids}",
        f"  表示に使ったレコード件数: {len(df)}",
        f"  folderId: {DEFAULT_DRIVE_FOLDER_ID}",
    ]
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
    folderId 内のCSV一覧を再取得し、modifiedTime 最新の1ファイルを読み込む。
    mode=individual のときは CSV の Player_ID 値で行フィルタする（値は置換しない）。
    """
    from app.drive_latest import LOCAL_CSV_DIR

    candidates = list_candidate_csvs(
        drive_url=drive_url,
        api_key=api_key,
        prefer_local=prefer_local,
        local_dir=local_dir or LOCAL_CSV_DIR,
    )
    latest = pick_latest_csv(candidates)
    log_latest_csv_selection(latest, candidates)

    content, _ = _load_file_content(latest)
    raw_df = read_csv_bytes(content)
    headers = list(raw_df.columns)
    all_players = unique_player_ids(raw_df)

    df = raw_df
    if mode == "individual":
        if not selected_device:
            raise ValueError("個人モードでは Player_ID（例: ota / Player）を選択してください。")
        if "Player_ID" not in df.columns:
            raise ValueError("CSVに Player_ID 列がありません。")
        # 完全一致（大文字小文字は CSV の値を正とする）。ユーザー入力との緩い一致も許容。
        mask = df["Player_ID"].astype(str) == selected_device
        if not mask.any():
            # 大文字小文字を無視した候補提示
            raise ValueError(
                f"Player_ID「{selected_device}」の行がありません。"
                f"CSV内の値: {all_players}"
            )
        df = df.loc[mask].copy()

    selection = DeviceCsvSelection(
        device_id=selected_device or ",".join(all_players) or "ALL",
        file=latest,
        df=df,
        record_count=len(df),
    )
    usage = format_usage_log(
        mode=mode,
        selected_player=selected_device,
        info=latest,
        df=df,
        headers=headers,
        player_ids=all_players,
    )
    logger.info(usage)

    return VrLoadResult(
        mode=mode,
        selected_device=selected_device,
        df=df,
        selections=[selection],
        candidates=candidates,
        selection_log=usage,
        record_count=len(df),
        headers=headers,
        player_ids=all_players,
    )


# 後方互換エイリアス
def select_latest_csv_per_device(candidates: list[CsvFileInfo], **kwargs):
    latest = pick_latest_csv(candidates)
    content, _ = _load_file_content(latest)
    df = read_csv_bytes(content)
    pid = infer_device_id(latest, df)
    sel = DeviceCsvSelection(device_id=pid, file=latest, df=df, record_count=len(df))
    return {pid: sel}, format_usage_log(
        mode="all",
        selected_player=None,
        info=latest,
        df=df,
        headers=list(df.columns),
        player_ids=unique_player_ids(df),
    )


def build_analysis_frame(
    selections: dict[str, DeviceCsvSelection],
    *,
    mode: str,
    selected_device: str | None,
) -> pd.DataFrame:
    if mode == "individual":
        if not selected_device or selected_device not in selections:
            raise ValueError(f"Player_ID「{selected_device}」のCSVがありません。")
        return selections[selected_device].df.copy()
    frames = [s.df.copy() for _, s in sorted(selections.items(), key=lambda x: str(x[0]))]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
