"""取り込み履歴・新規抽出・日付フィルタの簡易テスト。"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from app.import_history import clear_history, get_connection
from app.incremental_loader import detect_date_columns, filter_by_date_range, prepare_analysis_dataframe


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "test_history.db"
        conn = get_connection(db)

        df1 = pd.DataFrame(
            {
                "timestamp": [1.0, 2.0, 3.0],
                "player_id": ["A", "A", "B"],
                "event_type": ["誤嚥", "転倒", "誤嚥"],
                "location": ["リビング", "廊下", "キッチン"],
                "reaction_time": [1.1, 2.2, 3.3],
                "Session_Date": ["2026-07-01", "2026-07-02", "2026-07-10"],
            }
        )

        r1 = prepare_analysis_dataframe(
            df1,
            file_id="file-test",
            file_name="test.csv",
            content=b"abc",
            mode="new_only",
            db_path=db,
        )
        assert r1["selected_rows"] == 3, r1
        print("OK new_only first import:", r1["selected_rows"])

        r2 = prepare_analysis_dataframe(
            df1,
            file_id="file-test",
            content=b"abc",
            mode="new_only",
            db_path=db,
        )
        assert r2["selected_rows"] == 0, r2
        print("OK new_only second import (no dup):", r2["selected_rows"])

        df2 = pd.concat(
            [
                df1,
                pd.DataFrame(
                    {
                        "timestamp": [4.0],
                        "player_id": ["C"],
                        "event_type": ["転倒"],
                        "location": ["廊下"],
                        "reaction_time": [4.4],
                        "Session_Date": ["2026-07-15"],
                    }
                ),
            ],
            ignore_index=True,
        )
        r3 = prepare_analysis_dataframe(
            df2,
            file_id="file-test",
            content=b"abcd",
            mode="new_only",
            db_path=db,
        )
        assert r3["selected_rows"] == 1, r3
        print("OK incremental append:", r3["selected_rows"])

        cols = detect_date_columns(df1)
        assert "Session_Date" in cols, cols
        filtered, used = filter_by_date_range(
            df1,
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 5),
            date_column="Session_Date",
        )
        assert used == "Session_Date"
        assert len(filtered) == 2, len(filtered)
        print("OK date range filter:", len(filtered), "rows")

        r4 = prepare_analysis_dataframe(
            df2,
            file_id="file-test",
            mode="all",
            mark_imported=False,
            db_path=db,
        )
        assert r4["selected_rows"] == 4, r4
        print("OK all mode:", r4["selected_rows"])

        clear_history(conn)
        conn.close()
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
