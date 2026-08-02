# -*- coding: utf-8 -*-
"""Drive フォルダ一覧の回帰テスト。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.drive_latest import (
    DEFAULT_DRIVE_FOLDER_ID,
    _parse_embedded_folder_html,
    list_drive_csvs_embedded,
    list_drive_folder_csvs,
)


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


def test_live_folder_list():
    files = list_drive_csvs_embedded(DEFAULT_DRIVE_FOLDER_ID)
    assert len(files) >= 1, f"expected csvs, got {len(files)}"
    assert any(f.name.lower().endswith(".csv") for f in files)
    print(f"OK embedded live list: {len(files)} files, sample={files[0].name}")

    all_files = list_drive_folder_csvs(DEFAULT_DRIVE_FOLDER_ID, api_key=None)
    assert len(all_files) >= 1
    print(f"OK folder_csvs: {len(all_files)}")


if __name__ == "__main__":
    test_parse_sample_html()
    test_live_folder_list()
    print("ALL TESTS PASSED")
