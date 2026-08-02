# -*- coding: utf-8 -*-
"""private_key 正規化・端末キー抽出の単体テスト。"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.drive_auth import _normalize_private_key
from app.drive_latest import CsvFileInfo
from app.vr_csv_loader import device_key_from_filename, infer_device_id
from app.vr_dashboard_charts import player_id_summary, player_value_chart_frame
import pandas as pd


def _pem_with_body(body: str, *, escaped: bool = True) -> str:
    if escaped:
        return (
            "-----BEGIN PRIVATE KEY-----\\n"
            + body
            + "\\n-----END PRIVATE KEY-----\\n"
        )
    return (
        "-----BEGIN PRIVATE KEY-----\n"
        + body
        + "\n-----END PRIVATE KEY-----\n"
    )


def test_normalize_escaped_newlines():
    body = base64.b64encode(b"\x01" * 48).decode("ascii")
    info = _normalize_private_key(
        {
            "type": "service_account",
            "client_email": "vr-ai-analysis-drive-reader@routinesupport.iam.gserviceaccount.com",
            "private_key": _pem_with_body(body, escaped=True),
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    key = info["private_key"]
    assert "\\n" not in key
    assert "\n" in key
    assert key.startswith("-----BEGIN PRIVATE KEY-----")
    assert "END PRIVATE KEY-----" in key
    print("OK normalize escaped newlines")


def test_normalize_strips_invalid_dot_symbol_46():
    """Invalid symbol 46 (= '.') を base64 本体から除去できること。"""
    raw = b"\x02" * 64
    body = base64.b64encode(raw).decode("ascii")
    # 途中に不正な '.' を混入
    tainted = body[:20] + "." + body[20:]
    info = _normalize_private_key(
        {
            "client_email": "a@b.com",
            "private_key": _pem_with_body(tainted, escaped=True),
        }
    )
    key = info["private_key"]
    assert "-----BEGIN PRIVATE KEY-----" in key
    # PEM ヘッダ以外に単独の不正 '.' が残っていないこと（末尾等は無視）
    body_only = key.split("-----BEGIN PRIVATE KEY-----", 1)[1]
    body_only = body_only.split("-----END PRIVATE KEY-----", 1)[0]
    assert "." not in body_only.replace("\n", "")
    # 復元後に元バイトへ戻せる
    cleaned = "".join(body_only.split())
    assert base64.b64decode(cleaned) == raw
    print("OK strip invalid dot (symbol 46)")


def test_normalize_quoted_key():
    body = base64.b64encode(b"abc123").decode("ascii")
    info = _normalize_private_key(
        {
            "client_email": "a@b.com",
            "private_key": '"' + _pem_with_body(body, escaped=True) + '"',
        }
    )
    assert info["private_key"].startswith("-----BEGIN")
    assert "\\n" not in info["private_key"]
    print("OK normalize quoted key")


def test_normalize_triple_quoted_newlines():
    """三重引用符相当（実改行入り）でも正規化できること。"""
    body = base64.b64encode(b"\x03" * 80).decode("ascii")
    wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    info = _normalize_private_key(
        {
            "client_email": "a@b.com",
            "private_key": (
                "-----BEGIN PRIVATE KEY-----\n"
                + wrapped
                + "\n-----END PRIVATE KEY-----\n"
            ),
        }
    )
    key = info["private_key"]
    assert "\\n" not in key
    assert key.startswith("-----BEGIN PRIVATE KEY-----\n")
    assert key.endswith("-----END PRIVATE KEY-----\n")
    print("OK normalize triple-quoted newlines")


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
    assert infer_device_id(info, df2) == "Quest 3S"
    print("OK infer keeps player id / filename fallback")


def test_player_summary_and_chart_includes_all():
    df = pd.DataFrame(
        {
            "Player_ID": ["ota", "ota", "Player", "Playerchi", "Playeruu"],
            "Reaction_Time_Micro": [1.0, 2.0, None, 0.0, None],
        }
    )
    summary = player_id_summary(df)
    assert set(summary["Player_ID"]) == {"ota", "Player", "Playerchi", "Playeruu"}
    chart = player_value_chart_frame(df, "Reaction_Time_Micro")
    assert set(chart["Player_ID"]) == {"ota", "Player", "Playerchi", "Playeruu"}
    assert "データなし" in set(chart["状態"])
    assert "数値あり" in set(chart["状態"])
    print("OK player summary/chart includes all players")


if __name__ == "__main__":
    test_normalize_escaped_newlines()
    test_normalize_strips_invalid_dot_symbol_46()
    test_normalize_quoted_key()
    test_normalize_triple_quoted_newlines()
    test_device_key_from_filename()
    test_infer_keeps_player_id()
    test_player_summary_and_chart_includes_all()
    print("ALL TESTS PASSED")
