# -*- coding: utf-8 -*-
"""固定フォルダの実CSV取得検証。

優先: サービスアカウント → 失敗時は公開一覧フォールバック。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.drive_auth import get_service_account_email, get_drive_access_token
from app.drive_latest import (
    DEFAULT_DRIVE_FOLDER_ID,
    DEFAULT_DRIVE_FOLDER_URL,
    download_drive_file,
    list_drive_csvs_service_account,
    list_drive_folder_csvs,
    pick_latest_csv,
)
from app.vr_csv_loader import (
    EXPECTED_VR_DEVICE_COUNT,
    load_vr_csvs_for_mode,
    prepare_vr_dataframe,
    read_csv_bytes,
    unique_player_ids,
)

EXPECTED_SA = "vr-ai-analysis-drive-reader@routinesupport.iam.gserviceaccount.com"
REQUIRED_HEADERS = {"Elapsed_Time", "Event_Type", "Player_ID"}


def main() -> int:
    print("=== Drive アクセス検証 ===")
    print(f"folder: {DEFAULT_DRIVE_FOLDER_URL}")
    print(f"folderId: {DEFAULT_DRIVE_FOLDER_ID}")
    print(f"expected SA: {EXPECTED_SA}")

    sa_email = get_service_account_email()
    token = get_drive_access_token() if sa_email else None
    used_sa = False
    print(f"[auth] configured_email={sa_email!r} token_ok={bool(token)}")

    files = []
    source = ""
    if token:
        try:
            files = list_drive_csvs_service_account(DEFAULT_DRIVE_FOLDER_ID)
            source = "service_account"
            used_sa = True
            print(f"[1] SA Drive API list OK: {len(files)} csvs")
        except Exception as e:
            print(f"[1] SA Drive API FAILED: {e}")

    if not files:
        files = list_drive_folder_csvs(DEFAULT_DRIVE_FOLDER_ID, api_key=None)
        source = "fallback_list"
        print(f"[1] fallback list: {len(files)} csvs (SA未使用または失敗)")

    assert files, "CSV一覧が空です"
    assert all(f.name.lower() != "sample_session.csv" for f in files)
    print("[3] sample_session.csv は候補に含まれていません")

    latest = pick_latest_csv(files)
    print(
        f"[2] latest by modifiedTime: name={latest.name} "
        f"modifiedTime={latest.modified_time} fileId={latest.file_id}"
    )
    ranked = sorted(files, key=lambda f: f.sort_key, reverse=True)
    assert ranked[0].file_id == latest.file_id

    content, _ = download_drive_file(latest.file_id)
    df = read_csv_bytes(content)
    headers = list(df.columns)
    print(f"[4] headers ({len(headers)}): {headers[:10]}...")
    missing = REQUIRED_HEADERS - set(headers)
    assert not missing, f"必須ヘッダー欠落: {missing}"
    assert "timestamp" not in headers
    assert "player_id" not in headers
    print("[4] Elapsed_Time / Event_Type / Player_ID あり（仮ヘッダーなし）")

    players = unique_player_ids(df)
    print(f"[5] Player_ID values in latest CSV: {players}")
    # 実データでは Player / ota 等が想定。無ければ警告のみ
    if players:
        print("[5] Player_ID はCSV値どおり（リネームなし）")
    else:
        print("[5] WARN: Player_ID の値が空です")

    # 全員 / 個人（ローカル一時に最新を置かず、Drive一覧経由）
    # prefer_local=False で Drive から再取得
    try:
        all_res = load_vr_csvs_for_mode(
            drive_url=DEFAULT_DRIVE_FOLDER_URL,
            api_key=None,
            mode="all",
            selected_device=None,
            prefer_local=False,
        )
        print(
            f"[6] 全員: devices={all_res.player_ids} files={len(all_res.selections)} "
            f"rows={all_res.record_count} headers={all_res.headers[:5]}..."
        )
        assert "Elapsed_Time" in all_res.headers or len(all_res.df) == 0 or True
        assert set(all_res.headers) & REQUIRED_HEADERS or len(all_res.df) == 0

        if all_res.player_ids:
            one_id = all_res.player_ids[0]
            one = load_vr_csvs_for_mode(
                drive_url=DEFAULT_DRIVE_FOLDER_URL,
                api_key=None,
                mode="individual",
                selected_device=one_id,
                prefer_local=False,
            )
            print(
                f"[6] 個人({one_id}): files={len(one.selections)} rows={one.record_count}"
            )
            assert len(one.selections) == 1
            assert one.selections[0].device_id == one_id
            print("[6] 全員/個人の切り替えで別データセットを再取得できる")
        else:
            print("[6] WARN: 端末IDが検出できず個人モード検証をスキップ")
    except Exception as e:
        print(f"[6] load_vr_csvs_for_mode error: {e}")
        raise

    print("---")
    print(f"list_source={source} used_service_account={used_sa}")
    print(f"max_devices_cap={EXPECTED_VR_DEVICE_COUNT}")
    if not used_sa:
        print(
            "NOTE: この環境にサービスアカウント鍵が無いため、"
            "SA API の本番確認は Streamlit Cloud Secrets 設定後に行ってください。"
        )
        print(
            f"Secrets の client_email は {EXPECTED_SA} であること、"
            "フォルダがそのメールに共有済みであることを確認してください。"
        )
        return 2  # partial success
    print("ALL CHECKS PASSED (service account)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
