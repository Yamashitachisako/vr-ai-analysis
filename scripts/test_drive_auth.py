# -*- coding: utf-8 -*-
"""private_key 正規化・端末キー抽出の単体テスト。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.drive_auth import _normalize_private_key
from app.drive_latest import CsvFileInfo
from app.vr_csv_loader import device_key_from_filename, infer_device_id
import pandas as pd


def test_normalize_escaped_newlines():
    info = _normalize_private_key(
        {
            "type": "service_account",
            "client_email": "vr-ai-analysis-drive-reader@routinesupport.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\\nABC\\nDEF\\n-----END PRIVATE KEY-----\\n",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    key = info["private_key"]
    assert "\\n" not in key
    assert "\n" in key
    assert key.startswith("-----BEGIN PRIVATE KEY-----")
    assert "END PRIVATE KEY-----" in key
    print("OK normalize escaped newlines")


def test_normalize_quoted_key():
    info = _normalize_private_key(
        {
            "client_email": "a@b.com",
            "private_key": '"-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----\\n"',
        }
    )
    assert info["private_key"].startswith("-----BEGIN")
    assert "\\n" not in info["private_key"]
    print("OK normalize quoted key")


def test_device_key_from_filename():
    assert device_key_from_filename("Log_Quest 3S_20260801_094820.csv") == "Quest 3S"
    assert device_key_from_filename("Log_LAPTOP-C6T7L3C5_20260509_165331.csv") == "LAPTOP-C6T7L3C5"
    assert device_key_from_filename("other.csv") is None
    print("OK device_key_from_filename")


def test_infer_keeps_player_id():
    info = CsvFileInfo(file_id="1", name="Log_Quest 3S_20260801_094820.csv")
    df = pd.DataFrame({"Player_ID": ["ota", "ota"]})
    assert infer_device_id(info, df) == "ota"
    df2 = pd.DataFrame({"Player_ID": ["Player", "Playerchi"]})
    # 複数ある場合はファイル名キー
    assert infer_device_id(info, df2) == "Quest 3S"
    print("OK infer keeps player id / filename fallback")


if __name__ == "__main__":
    test_normalize_escaped_newlines()
    test_normalize_quoted_key()
    test_device_key_from_filename()
    test_infer_keeps_player_id()
    print("ALL TESTS PASSED")
