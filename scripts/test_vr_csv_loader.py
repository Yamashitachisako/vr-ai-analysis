# -*- coding: utf-8 -*-
"""実CSVヘッダー・Player_ID据え置き・最新CSV読み込みのテスト。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.drive_latest import CsvFileInfo
from app.vr_csv_loader import (
    prepare_vr_dataframe,
    unique_player_ids,
    infer_device_id,
    load_vr_csvs_for_mode,
)

REAL_HEADERS = [
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


def _row(player: str, i: int = 0) -> dict:
    return {
        "Elapsed_Time": f"00:00.{i:02d}",
        "Event_Type": "PlayerPosition",
        "Player_ID": player,
        "Player_X": 0.1,
        "Player_Y": 0.9,
        "Player_Z": -10.0,
        "Target_Object": "",
        "Reaction_Time_Micro": 100.0 + i,
        "World_X": 1.0,
        "World_Y": 2.0,
        "World_Z": 3.0,
        "Local_X": 0.0,
        "Local_Y": 0.0,
        "Local_Z": 0.0,
    }


def test_prepare_keeps_real_headers():
    df = prepare_vr_dataframe(pd.DataFrame([_row("ota"), _row("Player")]))
    assert list(df.columns) == REAL_HEADERS
    assert "timestamp" not in df.columns
    assert "player_id" not in df.columns
    assert "gaze_x" not in df.columns
    assert unique_player_ids(df) == ["Player", "ota"] or set(unique_player_ids(df)) == {
        "Player",
        "ota",
    }


def test_reject_sample_schema():
    sample = pd.DataFrame(
        [
            {
                "timestamp": 1,
                "player_id": "A",
                "event_type": "x",
                "reaction_time": 1,
                "gaze_x": 0,
                "gaze_y": 0,
                "location": "y",
            }
        ]
    )
    try:
        prepare_vr_dataframe(sample)
        raise AssertionError("sample should be rejected")
    except ValueError as e:
        assert "サンプル" in str(e) or "実CSV" in str(e)


def test_player_id_column_required_for_individual_filter():
    info = CsvFileInfo(file_id="1", name="Log_Quest.csv", modified_time="2026-08-01T00:00:00Z")
    df = pd.DataFrame({"Player_ID": ["ota", "ota"]})
    assert infer_device_id(info, df) == "ota"
    df2 = pd.DataFrame({"Player_ID": ["Player"]})
    assert infer_device_id(info, df2) == "Player"


def test_load_latest_local_preserves_headers_and_player():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        older = tmp_path / "old.csv"
        newer = tmp_path / "new.csv"
        pd.DataFrame([_row("player")]).to_csv(older, index=False)
        pd.DataFrame([_row("ota"), _row("ota", 1)]).to_csv(newer, index=False)
        import time

        time.sleep(0.05)
        newer.write_text(newer.read_text(encoding="utf-8"), encoding="utf-8")

        # touch newer
        import os

        os.utime(newer, None)

        result = load_vr_csvs_for_mode(
            drive_url=None,
            api_key=None,
            mode="all",
            selected_device=None,
            prefer_local=True,
            local_dir=tmp_path,
        )
        assert "Player_ID" in result.df.columns
        assert "timestamp" not in result.df.columns
        assert list(result.headers) == REAL_HEADERS
        assert "ota" in result.player_ids

        one = load_vr_csvs_for_mode(
            drive_url=None,
            api_key=None,
            mode="individual",
            selected_device="ota",
            prefer_local=True,
            local_dir=tmp_path,
        )
        assert set(one.df["Player_ID"].astype(str)) == {"ota"}
        assert one.record_count >= 1


if __name__ == "__main__":
    test_prepare_keeps_real_headers()
    test_reject_sample_schema()
    test_player_id_column_required_for_individual_filter()
    test_load_latest_local_preserves_headers_and_player()
    print("OK: all vr_csv_loader tests passed")
