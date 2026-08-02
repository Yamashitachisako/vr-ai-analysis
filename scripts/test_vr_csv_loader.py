# -*- coding: utf-8 -*-
"""実CSVヘッダー・VR最大4台ごとの最新CSV選定テスト。"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.drive_latest import CsvFileInfo
from app.vr_csv_loader import (
    EXPECTED_VR_DEVICE_COUNT,
    infer_device_id,
    load_vr_csvs_for_mode,
    prepare_vr_dataframe,
    select_latest_csv_per_device,
    unique_player_ids,
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


def test_infer_device_keeps_player_id():
    info = CsvFileInfo(file_id="1", name="Log_Quest.csv", modified_time="2026-08-01T00:00:00Z")
    assert infer_device_id(info, pd.DataFrame({"Player_ID": ["ota", "ota"]})) == "ota"
    assert infer_device_id(info, pd.DataFrame({"Player_ID": ["Player"]})) == "Player"


def test_four_devices_all_and_individual():
    assert EXPECTED_VR_DEVICE_COUNT == 4
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        devices = ["ota", "Player", "player2", "Quest4"]
        for d in devices:
            pd.DataFrame([_row(d, 0)]).to_csv(tmp_path / f"{d}_old.csv", index=False)
        time.sleep(0.05)
        for d in devices:
            p = tmp_path / f"{d}_new.csv"
            pd.DataFrame([_row(d, 1), _row(d, 2)]).to_csv(p, index=False)
            os.utime(p, None)

        all_result = load_vr_csvs_for_mode(
            drive_url=None,
            api_key=None,
            mode="all",
            selected_device=None,
            prefer_local=True,
            local_dir=tmp_path,
        )
        assert len(all_result.player_ids) == 4
        assert len(all_result.selections) == 4
        assert set(all_result.player_ids) == set(devices)
        assert "Player_ID" in all_result.df.columns
        assert list(all_result.headers) == REAL_HEADERS
        # 全員は4台分の行が結合される（各2行）
        assert all_result.record_count == 8

        one = load_vr_csvs_for_mode(
            drive_url=None,
            api_key=None,
            mode="individual",
            selected_device="ota",
            prefer_local=True,
            local_dir=tmp_path,
        )
        assert len(one.selections) == 1
        assert one.selections[0].device_id == "ota"
        assert one.selections[0].file.name == "ota_new.csv"
        assert one.record_count == 2
        assert set(one.df["Player_ID"].astype(str)) == {"ota"}


def test_select_latest_per_device_prefers_newer():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        older = tmp_path / "ota_old.csv"
        newer = tmp_path / "ota_new.csv"
        pd.DataFrame([_row("ota", 0)]).to_csv(older, index=False)
        time.sleep(0.05)
        pd.DataFrame([_row("ota", 1), _row("ota", 2)]).to_csv(newer, index=False)
        os.utime(newer, None)

        from app.drive_latest import list_local_csvs

        sels, _ = select_latest_csv_per_device(list_local_csvs(tmp_path))
        assert set(sels.keys()) == {"ota"}
        assert sels["ota"].file.name == "ota_new.csv"
        assert sels["ota"].record_count == 2


if __name__ == "__main__":
    test_prepare_keeps_real_headers()
    test_reject_sample_schema()
    test_infer_device_keeps_player_id()
    test_select_latest_per_device_prefers_newer()
    test_four_devices_all_and_individual()
    print("OK: all vr_csv_loader tests passed")
