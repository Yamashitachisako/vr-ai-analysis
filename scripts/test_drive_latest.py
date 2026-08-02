"""最新CSV選択ロジックのテスト。"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.drive_latest import CsvFileInfo, pick_latest_csv, pick_latest_uploaded, list_local_csvs


class DummyUpload:
    def __init__(self, name: str):
        self.name = name


def main() -> None:
    files = [
        CsvFileInfo("a", "old.csv", modified_time="2026-01-01T00:00:00Z"),
        CsvFileInfo("b", "new.csv", modified_time="2026-08-01T12:00:00Z"),
        CsvFileInfo("c", "mid.csv", modified_time="2026-06-01T00:00:00Z"),
    ]
    latest = pick_latest_csv(files)
    assert latest.file_id == "b", latest
    print("OK pick_latest by modifiedTime:", latest.name)

    files2 = [
        CsvFileInfo("x", "only.csv", created_time="2026-07-01T00:00:00Z"),
    ]
    assert pick_latest_csv(files2).file_id == "x"
    print("OK single file")

    try:
        pick_latest_csv([])
        raise AssertionError("should fail")
    except FileNotFoundError:
        print("OK empty list error")

    uploads = [DummyUpload("session_2026-01-01.csv"), DummyUpload("session_2026-08-01.csv")]
    picked = pick_latest_uploaded(uploads)
    assert picked.name == "session_2026-08-01.csv", picked.name
    print("OK upload pick by filename date:", picked.name)

    with tempfile.TemporaryDirectory() as tmp:
        p1 = Path(tmp) / "a.csv"
        p2 = Path(tmp) / "b.csv"
        p1.write_text("x\n1\n", encoding="utf-8")
        p2.write_text("x\n2\n", encoding="utf-8")
        # touch b as newer
        import os
        import time

        time.sleep(0.05)
        p2.write_text("x\n3\n", encoding="utf-8")
        local = list_local_csvs(tmp)
        latest_local = pick_latest_csv(local)
        assert latest_local.name == "b.csv", latest_local.name
        print("OK local mtime pick:", latest_local.name)

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
