"""Google Drive / ローカルから最新CSVを動的に選択する。"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

LOCAL_CSV_DIR = Path(__file__).resolve().parent.parent / "data" / "input"


@dataclass
class CsvFileInfo:
    file_id: str
    name: str
    modified_time: str | None = None
    created_time: str | None = None
    source: str = "drive"  # drive | local | upload
    path: str | None = None

    @property
    def sort_key(self) -> datetime:
        """日時優先（modified → created）。無い場合は最小値。"""
        for value in (self.modified_time, self.created_time):
            dt = _parse_dt(value)
            if dt is not None:
                return dt
        return datetime.min.replace(tzinfo=timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # RFC 2822 (Last-Modified) or ISO 8601
        if "," in value and "GMT" in value:
            return datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        try:
            return pd_to_datetime(value)
        except Exception:
            return None


def pd_to_datetime(value: str) -> datetime:
    import pandas as pd

    ts = pd.to_datetime(value, utc=True)
    return ts.to_pydatetime()


def extract_drive_folder_id(url_or_id: str) -> str | None:
    text = (url_or_id or "").strip()
    if not text:
        return None
    match = re.search(r"/folders/([a-zA-Z0-9_-]+)", text)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", text)
    if match and "/file/" not in text:
        return match.group(1)
    # bare id that looks like folder (heuristic: not used if file/d present)
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", text) and "/file/" not in text:
        return text
    return None


def extract_drive_file_id(url_or_id: str) -> str | None:
    text = (url_or_id or "").strip()
    match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", text)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", text):
        return text
    return None


def is_folder_url(url: str) -> bool:
    return "/folders/" in (url or "") or bool(
        extract_drive_folder_id(url) and "/file/" not in (url or "")
    )


def format_csv_selection_log(
    selected: CsvFileInfo,
    candidates: list[CsvFileInfo] | None = None,
) -> str:
    """画面・ログ共通の選定結果メッセージ。"""
    lines = [
        "[最新CSV選定結果]",
        f"  選択ファイル名: {selected.name}",
        f"  更新日時(modifiedTime): {selected.modified_time or '(不明)'}",
        f"  作成日時(createdTime): {selected.created_time or '(不明)'}",
        f"  file_id: {selected.file_id}",
        f"  source: {selected.source}",
    ]
    if candidates is not None:
        lines.append(f"  候補件数: {len(candidates)}")
        ranked = sorted(candidates, key=lambda f: (f.sort_key, f.name), reverse=True)
        for i, f in enumerate(ranked[:10], start=1):
            mark = " <-- 選択" if f.file_id == selected.file_id and f.name == selected.name else ""
            lines.append(
                f"  [{i}] {f.name} | modified={f.modified_time or '-'} | created={f.created_time or '-'}{mark}"
            )
        if len(ranked) > 10:
            lines.append(f"  ... 他 {len(ranked) - 10} 件")
    return "\n".join(lines)


def log_latest_csv_selection(
    selected: CsvFileInfo,
    candidates: list[CsvFileInfo] | None = None,
) -> str:
    """選定結果をINFOログに出し、同じ文言を返す。"""
    message = format_csv_selection_log(selected, candidates)
    for line in message.splitlines():
        logger.info(line)
    return message


def pick_latest_csv(files: list[CsvFileInfo]) -> CsvFileInfo:
    """日時（modifiedTime優先）で降順ソートし、最新1件を返す。"""
    if not files:
        raise FileNotFoundError(
            "CSVファイルが見つかりませんでした。"
            "Google Driveフォルダの共有設定、または data/input を確認してください。"
        )
    ranked = sorted(files, key=lambda f: (f.sort_key, f.name), reverse=True)
    latest = ranked[0]
    log_latest_csv_selection(latest, files)
    return latest


def list_local_csvs(directory: Path | str = LOCAL_CSV_DIR) -> list[CsvFileInfo]:
    root = Path(directory)
    if not root.exists():
        return []
    files: list[CsvFileInfo] = []
    for path in root.glob("*.csv"):
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        created = datetime.fromtimestamp(getattr(stat, "st_ctime", stat.st_mtime), tz=timezone.utc).isoformat()
        files.append(
            CsvFileInfo(
                file_id=f"local:{path.name}",
                name=path.name,
                modified_time=modified,
                created_time=created,
                source="local",
                path=str(path),
            )
        )
    return files


def list_drive_csvs_api(folder_id: str, api_key: str) -> list[CsvFileInfo]:
    """Drive API v3 でフォルダ内CSVを一覧（modifiedTime降順）。"""
    query = (
        f"'{folder_id}' in parents and trashed=false and "
        f"(mimeType='text/csv' or mimeType='application/vnd.ms-excel' or name contains '.csv')"
    )
    params = {
        "q": query,
        "fields": "files(id,name,modifiedTime,createdTime,mimeType)",
        "orderBy": "modifiedTime desc",
        "pageSize": "100",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "key": api_key,
    }
    url = "https://www.googleapis.com/drive/v3/files?" + urlencode(params)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    files: list[CsvFileInfo] = []
    for item in payload.get("files", []):
        name = item.get("name") or ""
        if not name.lower().endswith(".csv"):
            # mimeType text/csv でも名前に.csvが無い場合は含める
            if item.get("mimeType") != "text/csv":
                continue
        files.append(
            CsvFileInfo(
                file_id=item["id"],
                name=name,
                modified_time=item.get("modifiedTime"),
                created_time=item.get("createdTime"),
                source="drive",
            )
        )
    logger.info("drive api listed %s csv files in folder %s", len(files), folder_id)
    return files


def list_drive_csvs_public_html(folder_id: str) -> list[CsvFileInfo]:
    """公開フォルダのHTMLからファイルIDを抽出（日時は取得できない場合あり）。"""
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    # /file/d/{id}/ と近くのファイル名を拾う
    file_ids = re.findall(r"/file/d/([a-zA-Z0-9_-]{10,})", html)
    # "name":"something.csv" patterns in embedded JSON
    named = re.findall(r'"([^\"]+\.csv)"\s*,\s*"([a-zA-Z0-9_-]{20,})"', html)
    named2 = re.findall(r'"id"\s*:\s*"([a-zA-Z0-9_-]{20,})"[^}]*?"name"\s*:\s*"([^\"]+\.csv)"', html)
    named3 = re.findall(r'"name"\s*:\s*"([^\"]+\.csv)"[^}]*?"id"\s*:\s*"([a-zA-Z0-9_-]{20,})"', html)

    by_id: dict[str, CsvFileInfo] = {}
    for fid in file_ids:
        by_id.setdefault(
            fid,
            CsvFileInfo(file_id=fid, name=f"{fid}.csv", source="drive"),
        )
    for name, fid in named + [(n, i) for i, n in named2] + named3:
        if not str(name).lower().endswith(".csv"):
            continue
        by_id[fid] = CsvFileInfo(file_id=fid, name=name, source="drive")

    # modifiedTime in page JSON
    for fid, info in list(by_id.items()):
        m = re.search(
            rf'{re.escape(fid)}[^\\{{}}]{{0,400}}?"modifiedTime"\s*:\s*"([^"]+)"',
            html,
        )
        if not m:
            m = re.search(
                rf'"modifiedTime"\s*:\s*"([^"]+)"[^\\{{}}]{{0,400}}?{re.escape(fid)}',
                html,
            )
        if m:
            info.modified_time = m.group(1)
        c = re.search(
            rf'{re.escape(fid)}[^\\{{}}]{{0,400}}?"createdTime"\s*:\s*"([^"]+)"',
            html,
        )
        if c:
            info.created_time = c.group(1)

    files = list(by_id.values())
    logger.info("drive html listed %s csv-like files in folder %s", len(files), folder_id)
    return files


def list_drive_folder_csvs(folder_id: str, api_key: str | None = None) -> list[CsvFileInfo]:
    errors: list[str] = []
    key = (api_key or os.environ.get("GOOGLE_API_KEY") or "").strip() or None
    if key:
        try:
            files = list_drive_csvs_api(folder_id, key)
            if files:
                return files
            errors.append("Drive API: CSVが0件でした。")
        except Exception as e:
            logger.warning("drive api list failed: %s", e)
            errors.append(f"Drive API: {e}")

    try:
        files = list_drive_csvs_public_html(folder_id)
        if files:
            return files
        errors.append("公開フォルダHTML: CSVが0件でした。")
    except Exception as e:
        logger.warning("drive html list failed: %s", e)
        errors.append(f"公開フォルダHTML: {e}")

    detail = " / ".join(errors) if errors else "不明なエラー"
    raise FileNotFoundError(
        "フォルダ内のCSV一覧を取得できませんでした。"
        f"（{detail}） "
        "共有を「リンクを知っている全員が閲覧可」にし、可能なら GOOGLE_API_KEY を設定してください。"
    )


def download_drive_file(file_id: str, timeout: int = 60) -> tuple[bytes, str | None]:
    """ファイルIDから毎回新規ダウンロード（キャッシュなし）。"""
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"
    request = Request(
        download_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urlopen(request, timeout=timeout) as resp:
        content = resp.read()
        modified = resp.headers.get("Last-Modified")
    if content[:15].lower().startswith(b"<!doctype") or content[:6].lower().startswith(b"<html"):
        # confirm token retry
        text = content.decode("utf-8", errors="ignore")
        confirm = re.search(r"confirm=([0-9A-Za-z_]+)", text)
        if confirm:
            download_url = (
                f"https://drive.google.com/uc?export=download&id={file_id}"
                f"&confirm={confirm.group(1)}"
            )
            request = Request(download_url, headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"})
            with urlopen(request, timeout=timeout) as resp2:
                content = resp2.read()
                modified = resp2.headers.get("Last-Modified")
    if content[:15].lower().startswith(b"<!doctype") or content[:6].lower().startswith(b"<html"):
        raise ValueError(
            "CSVではなくHTMLが返されました。共有設定が「リンクを知っている全員が閲覧可」か確認してください。"
        )
    return content, modified


def resolve_latest_csv_from_source(
    *,
    drive_url: str | None = None,
    api_key: str | None = None,
    local_dir: Path | str | None = LOCAL_CSV_DIR,
    prefer_local: bool = False,
) -> tuple[CsvFileInfo, bytes, list[CsvFileInfo]]:
    """
    ボタン押下ごとに一覧を再取得し、最新CSVだけを返す。
    戻り値: (file_info, content_bytes, candidates)
    """
    url = (drive_url or "").strip()
    candidates: list[CsvFileInfo] = []

    if prefer_local and local_dir:
        candidates = list_local_csvs(local_dir)
        if candidates:
            latest = pick_latest_csv(candidates)
            content = Path(latest.path).read_bytes()  # type: ignore[arg-type]
            return latest, content, candidates

    if url and is_folder_url(url):
        folder_id = extract_drive_folder_id(url)
        if not folder_id:
            raise ValueError("Google DriveフォルダIDをURLから抽出できませんでした。")
        candidates = list_drive_folder_csvs(folder_id, api_key=api_key)
        latest = pick_latest_csv(candidates)
        content, header_modified = download_drive_file(latest.file_id)
        if not latest.modified_time and header_modified:
            latest.modified_time = header_modified
            log_latest_csv_selection(latest, candidates)
        return latest, content, candidates

    if url:
        file_id = extract_drive_file_id(url)
        if not file_id:
            raise ValueError("Google DriveファイルIDをURLから抽出できませんでした。")
        content, header_modified = download_drive_file(file_id)
        info = CsvFileInfo(
            file_id=file_id,
            name=f"{file_id}.csv",
            modified_time=header_modified,
            source="drive",
        )
        candidates = [info]
        log_latest_csv_selection(info, candidates)
        return info, content, candidates

    if local_dir:
        candidates = list_local_csvs(local_dir)
        if candidates:
            latest = pick_latest_csv(candidates)
            content = Path(latest.path).read_bytes()  # type: ignore[arg-type]
            return latest, content, candidates

    raise FileNotFoundError(
        "最新CSVが見つかりませんでした。"
        "Google Driveのフォルダ/ファイルURLを指定するか、data/input にCSVを配置してください。"
    )


def pick_latest_uploaded(files: list[Any]) -> Any:
    """ブラウザアップロード複数ファイルから最新相当を選ぶ（ファイル名の日付優先）。"""
    if not files:
        raise FileNotFoundError("アップロードされたCSVがありません。")

    def score(f: Any) -> tuple:
        name = getattr(f, "name", "") or ""
        m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", name)
        if m:
            return (1, m.group(0), name)
        # 日付が無い場合は名前降順（弱いヒューリスティック）
        return (0, name)

    ranked = sorted(files, key=score, reverse=True)
    return ranked[0]
