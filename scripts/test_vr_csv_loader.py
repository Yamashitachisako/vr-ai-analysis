# -*- coding: utf-8 -*-
"""VR端末ごとの最新CSV選定テスト（modifiedTime）。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.drive_latest import CsvFileInfo
from app.vr_csv_loader import (
    build_analysis_frame,
    infer_device_id,
    load_vr_csvs_for_mode,
    prepare_vr_dataframe,
    select_latest_csv_per_device,
)


HEADERS = [
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


def _write_csv(path: Path, player: str, rows: int = 2) -> None:
    df = pd.DataFrame(
        [
            {
                "Elapsed_Time": i,
                "Event_Type": "転倒",
                "Player_ID": player,
                "Target_Object": "床",
                "Data_Value": 1.5 + i,
                "WorldX": 0,
                "WorldY": 0,
                "WorldZ": 0,
                "LocalX": 0,
                "LocalY": 0,
                "LocalZ": 0,
            }
            for i in range(rows)
        ]
    )
    df.to_csv(path, index=False)


def test_prepare_keeps_headers():
    df = prepare_vr_dataframe(
        pd.DataFrame([{h: 1 if h != "Player_ID" else "A" for h in HEADERS}])
    )
    assert list(df.columns) == HEADERS
    assert "timestamp" not in df.columns
    assert "player_id" not in df.columns


def test_infer_device_from_player_id():
    info = CsvFileInfo(file_id="1", name="anything.csv", modified_time="2024-01-01T00:00:00Z")
    df = pd.DataFrame({"Player_ID": ["B", "B"]})
    assert infer_device_id(info, df) == "B"


def test_select_latest_per_device_by_modified_time():
    # 端末A: 古い / 新しい、端末B: 1件、端末C: 1件、端末D: 古いがファイル名は新しい見た目
    candidates = [
        CsvFileInfo("a_old", "vrA_2026.csv", "2020-01-01T00:00:00Z", source="local"),
        CsvFileInfo("a_new", "vrA_oldname.csv", "2026-06-01T12:00:00Z", source="local"),
        CsvFileInfo("b1", "device_B.csv", "2026-05-01T00:00:00Z", source="local"),
        CsvFileInfo("c1", "player-C-log.csv", "2026-04-01T00:00:00Z", source="local"),
        CsvFileInfo("d_old", "D_latest_looking.csv", "2019-01-01T00:00:00Z", source="local"),
        CsvFileInfo("d_new", "D_zzz.csv", "2026-07-01T00:00:00Z", source="local"),
    ]

    # 実ファイルとして読む必要があるため temp に書き出し path をセット
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        mapping = {
            "a_old": ("A", 1),
            "a_new": ("A", 3),
            "b1": ("B", 2),
            "c1": ("C", 2),
            "d_old": ("D", 1),
            "d_new": ("D", 4),
        }
        loaded = []
        for info in candidates:
            player, rows = mapping[info.file_id]
            path = tmp_path / f"{info.file_id}.csv"
            _write_csv(path, player, rows)
            info.source = "local"
            info.path = str(path)
            loaded.append(info)

        selections, log = select_latest_csv_per_device(loaded)
        assert set(selections.keys()) == {"A", "B", "C", "D"}
        assert selections["A"].file.file_id == "a_new"
        assert selections["A"].record_count == 3
        assert selections["D"].file.file_id == "d_new"
        assert "modifiedTime" in log

        all_df = build_analysis_frame(selections, mode="all", selected_device=None)
        assert len(all_df) == 3 + 2 + 2 + 4

        one = build_analysis_frame(selections, mode="individual", selected_device="B")
        assert len(one) == 2
        assert set(one["Player_ID"].astype(str)) == {"B"}
        assert list(all_df.columns)[:5] == HEADERS[:5]


def test_load_vr_csvs_local_all_and_individual():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for player, stamp in [("A", "2026-01-01"), ("B", "2026-02-01"), ("C", "2026-03-01"), ("D", "2026-04-01")]:
            path = tmp_path / f"session_{player}.csv"
            _write_csv(path, player, rows=2)
            # touch mtime ordering via write order; list_local uses file mtime
        result_all = load_vr_csvs_for_mode(
            drive_url=None,
            api_key=None,
            mode="all",
            selected_device=None,
            prefer_local=True,
            local_dir=tmp_path,
        )
        assert result_all.record_count == 8
        assert len(result_all.selections) == 4

        result_one = load_vr_csvs_for_mode(
            drive_url=None,
            api_key=None,
            mode="individual",
            selected_device="C",
            prefer_local=True,
            local_dir=tmp_path,
        )
        assert result_one.record_count == 2
        assert len(result_one.selections) == 1
        assert result_one.selections[0].device_id == "C"


if __name__ == "__main__":
    test_prepare_keeps_headers()
    test_infer_device_from_player_id()
    test_select_latest_per_device_by_modified_time()
    test_load_vr_csvs_local_all_and_individual()
    print("OK: all vr_csv_loader tests passed")
