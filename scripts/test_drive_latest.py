# -*- coding: utf-8 -*-
"""Drive フォルダ一覧・サービスアカウント周りのテスト。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.drive_latest import (
    DEFAULT_DRIVE_FOLDER_ID,
    DEFAULT_DRIVE_FOLDER_URL,
    _parse_embedded_folder_html,
    list_drive_csvs_embedded,
    list_drive_folder_csvs,
)


def test_fixed_folder_constants():
    assert DEFAULT_DRIVE_FOLDER_ID == "1ClTITbRVQc_hiDDIF5lfEEEttJs5qTc9"
    assert DEFAULT_DRIVE_FOLDER_ID in DEFAULT_DRIVE_FOLDER_URL
    print("OK fixed folder constants")


def test_parse_sample_html():
    html = """
    <div id="entry-1owEb2MoDWtwWWSaoaz-x294qJFyEWHOw" tabindex="0" role="link">
      <a href="https://drive.google.com/file/d/1owEb2MoDWtwWWSaoaz-x294qJFyEWHOw/view?usp=drive_web">
        <div class="flip-entry-title">Log_LAPTOP-C6T7L3C5_20260509_165331.csv</div>
      </a>
    </div>
    """
    files = _parse_embedded_folder_html(html)
    assert len(files) == 1
    assert files[0].file_id == "1owEb2MoDWtwWWSaoaz-x294qJFyEWHOw"
    assert files[0].name.endswith(".csv")
    assert files[0].modified_time is not None
    print("OK parse sample html")


def test_service_account_preferred_over_html():
    from app.drive_latest import CsvFileInfo

    fake = [
        CsvFileInfo(
            file_id="abc",
            name="Log_x_20260701_120000.csv",
            modified_time="2026-07-01T12:00:00Z",
            source="drive",
        )
    ]
    with patch("app.drive_auth.get_service_account_email", return_value="sa@test.iam.gserviceaccount.com"), \
         patch("app.drive_latest.list_drive_csvs_service_account", return_value=fake) as sa_list, \
         patch("app.drive_latest.list_drive_csvs_embedded") as emb:
        files = list_drive_folder_csvs(DEFAULT_DRIVE_FOLDER_ID, api_key=None)
        assert files == fake
        sa_list.assert_called_once()
        emb.assert_not_called()
    print("OK service account preferred")


def test_live_folder_list_fallback():
    # SA が無い環境では embedded フォールバック
    files = list_drive_folder_csvs(DEFAULT_DRIVE_FOLDER_ID, api_key=None)
    assert len(files) >= 1, f"expected csvs, got {len(files)}"
    assert any(f.name.lower().endswith(".csv") for f in files)
    print(f"OK folder_csvs fallback/live: {len(files)} files, sample={files[0].name}")


def test_auth_module_without_secrets():
    from app.drive_auth import get_service_account_email, load_service_account_info

    # 通常のローカルでは未設定のはず
    info = load_service_account_info()
    email = get_service_account_email()
    assert (info is None and email is None) or (email and info)
    print(f"OK auth module (configured={bool(email)})")


if __name__ == "__main__":
    test_fixed_folder_constants()
    test_parse_sample_html()
    test_service_account_preferred_over_html()
    test_auth_module_without_secrets()
    test_live_folder_list_fallback()
    print("ALL TESTS PASSED")
