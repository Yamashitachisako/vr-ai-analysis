import sys
import os
import logging
from pathlib import Path

import streamlit as st
import pandas as pd
from io import BytesIO

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ページ設定
st.set_page_config(
    page_title="VR保育研修 分析ダッシュボード",
    page_icon="🏥",
    layout="wide"
)

# 認証
VALID_USERNAME = "hyogo"
VALID_PASSWORD = "test"

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "df" not in st.session_state:
    st.session_state.df = None
if "pdf_bytes" not in st.session_state:
    st.session_state.pdf_bytes = None
if "pdf_error" not in st.session_state:
    st.session_state.pdf_error = None
if "import_meta" not in st.session_state:
    st.session_state.import_meta = None
if "vr_selection_sig" not in st.session_state:
    st.session_state.vr_selection_sig = None
if "force_csv_reload" not in st.session_state:
    st.session_state.force_csv_reload = False
if "available_devices" not in st.session_state:
    st.session_state.available_devices = []
if "available_players" not in st.session_state:
    st.session_state.available_players = []
if "had_successful_load" not in st.session_state:
    st.session_state.had_successful_load = False

if not st.session_state.authenticated:
    st.markdown("## ログイン")
    with st.form("login_form"):
        username = st.text_input("ユーザー名")
        password = st.text_input("パスワード", type="password")
        submitted = st.form_submit_button("ログイン")
        if submitted:
            if username == VALID_USERNAME and password == VALID_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("ユーザー名またはパスワードが違います")
    st.stop()

# カスタムCSS
st.markdown("""
<style>
    .main-title {
        font-size: 2rem;
        font-weight: bold;
        color: #1a1a2e;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        font-size: 1rem;
        color: #666;
        margin-bottom: 1.5rem;
    }
    .kpi-card {
        background: #f8f9fa;
        border-radius: 10px;
        padding: 1rem;
        text-align: center;
        border-left: 4px solid #4a90d9;
    }
    .kpi-value {
        font-size: 2rem;
        font-weight: bold;
        color: #1a1a2e;
    }
    .kpi-label {
        font-size: 0.85rem;
        color: #666;
    }
    .section-header {
        font-size: 1.3rem;
        font-weight: bold;
        color: #1a1a2e;
        border-bottom: 2px solid #4a90d9;
        padding-bottom: 0.3rem;
        margin: 1.5rem 0 1rem 0;
    }
    .st-key-load_from_drive_btn button[kind="primary"] {
        background-color: #0068C9 !important;
        color: #ffffff !important;
        border: 1px solid #0068C9 !important;
    }
    .st-key-load_from_drive_btn button[kind="primary"]:hover {
        background-color: #0056A6 !important;
        border-color: #0056A6 !important;
        color: #ffffff !important;
    }
    .st-key-generate_pdf_btn button[kind="primary"] {
        background-color: #28a745 !important;
        color: #ffffff !important;
        border: 1px solid #28a745 !important;
    }
    .st-key-generate_pdf_btn button[kind="primary"]:hover {
        background-color: #218838 !important;
        border-color: #218838 !important;
        color: #ffffff !important;
    }
</style>
""", unsafe_allow_html=True)

# 単体CSVファイルは使わない。親フォルダ内を毎回一覧取得する。
# （Cloud でも確実に動くよう、ここでは app を import せず定数を直書きする）
DEFAULT_DRIVE_FOLDER_ID = "1ClTITbRVQc_hiDDIF5lfEEEttJs5qTc9"
DEFAULT_DRIVE_CSV_URL = (
    f"https://drive.google.com/drive/folders/{DEFAULT_DRIVE_FOLDER_ID}"
)

def reset_load_state() -> None:
    """アップロード／Drive取得前に前回の選択・キャッシュをクリアする。"""
    st.session_state.df = None
    st.session_state.import_meta = None
    st.session_state.pdf_bytes = None
    st.session_state.pdf_error = None
    st.session_state.last_upload_key = None
    st.session_state.latest_csv_info = None
    st.session_state.vr_load_result = None
    logger.info("load state reset before fetching CSV (clear cache / previous analysis)")


def get_google_api_key() -> str | None:
    key = ""
    try:
        key = st.secrets.get("GOOGLE_API_KEY", "")  # type: ignore[attr-defined]
    except Exception:
        key = ""
    if not key:
        key = os.environ.get("GOOGLE_API_KEY", "")
    key = (key or "").strip()
    return key or None


def extract_google_drive_file_id(url: str) -> str:
    from app.drive_latest import extract_drive_file_id

    fid = extract_drive_file_id(url)
    return fid or url.strip()

def to_google_drive_download_url(url: str) -> str:
    """共有リンクを pandas で読み込めるダウンロードURLに変換する。"""
    file_id = extract_google_drive_file_id(url)
    return f"https://drive.google.com/uc?export=download&id={file_id}"

def read_csv_robust(content: bytes) -> pd.DataFrame:
    """アップロード・Google Drive 共通の CSV 読み込み。"""
    if content[:15].lower().startswith(b"<!doctype") or content[:6].lower().startswith(b"<html"):
        raise ValueError(
            "CSVではなくHTMLが返されました。"
            "Google Driveの共有設定が「リンクを知っている全員が閲覧可」になっているか確認してください。"
        )

    last_error = None
    for encoding in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return pd.read_csv(
                BytesIO(content),
                engine="python",
                on_bad_lines="skip",
                encoding=encoding,
            )
        except UnicodeDecodeError as e:
            last_error = e
        except Exception as e:
            last_error = e

    raise ValueError(f"CSVの読み込みに失敗しました: {last_error}")

def prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """実CSVヘッダー名を維持したまま型を整える（リネームしない）。"""
    from app.vr_csv_loader import prepare_vr_dataframe

    return prepare_vr_dataframe(df)

def load_csv_from_upload(uploaded_file) -> pd.DataFrame:
    return prepare_dataframe(read_csv_robust(uploaded_file.getvalue()))

def format_vr_usage_log(
    *,
    mode: str,
    selected_device: str | None,
    selections: list,
    record_count: int,
    chart_record_count: int | None = None,
    headers: list | None = None,
    player_ids: list | None = None,
) -> str:
    mode_label = "全員" if mode == "all" else "個人"
    lines = [
        "[使用CSVログ]",
        f"  選択モード: {mode_label}",
        f"  選択 Player_ID: {selected_device or '(全員)'}",
        f"  表示に使ったレコード件数: {chart_record_count if chart_record_count is not None else record_count}",
    ]
    if headers is not None:
        lines.append(f"  CSVヘッダー一覧: {list(headers)}")
    if player_ids is not None:
        lines.append(f"  Player_ID のユニーク値一覧: {list(player_ids)}")
    for s in selections:
        lines.append(f"  読み込んだCSVファイル名: {s.file.name}")
        lines.append(f"  fileId: {s.file.file_id}")
        lines.append(f"  modifiedTime: {s.file.modified_time or '-'}")
        lines.append(f"  records: {s.record_count}")
    return "\n".join(lines)


def apply_import_policy_for_selections(
    selections: list,
    *,
    import_mode: str,
    start_date,
    end_date,
    date_column: str | None,
) -> dict:
    """端末ごとの最新CSVに取り込みポリシーを適用してから結合する。"""
    frames = []
    notes: list[str] = []
    total_rows = 0
    selected_rows = 0
    date_column_used = None
    result_mode = import_mode

    for sel in selections:
        result = apply_import_policy(
            sel.df,
            file_id=sel.file.file_id,
            file_name=sel.file.name,
            content=None,
            modified_time=sel.file.modified_time,
            mode=import_mode,
            start_date=start_date,
            end_date=end_date,
            date_column=date_column,
        )
        frames.append(result["df"])
        total_rows += int(result["total_rows"])
        selected_rows += int(result["selected_rows"])
        result_mode = result["mode"]
        if result.get("date_column_used"):
            date_column_used = result["date_column_used"]
        for n in result.get("notes") or []:
            notes.append(f"[{sel.device_id}/{sel.file.name}] {n}")

    if not frames:
        merged = pd.DataFrame()
    else:
        merged = pd.concat(frames, ignore_index=True)

    return {
        "df": merged,
        "mode": result_mode,
        "total_rows": total_rows,
        "selected_rows": selected_rows,
        "date_column_used": date_column_used,
        "notes": notes,
        "content_hash": None,
        "modified_time": None,
    }


def load_vr_csv_bundle(
    *,
    drive_url: str,
    api_key: str | None,
    analysis_mode: str,
    selected_device: str | None,
    prefer_local: bool = False,
) -> dict:
    """毎回フォルダ一覧を再取得し、modifiedTime 最新CSVを読み込む。"""
    from app.vr_csv_loader import load_vr_csvs_for_mode

    result = load_vr_csvs_for_mode(
        drive_url=drive_url,
        api_key=api_key,
        mode=analysis_mode,
        selected_device=selected_device,
        prefer_local=prefer_local,
    )
    usage_log = result.selection_log or format_vr_usage_log(
        mode=result.mode,
        selected_device=result.selected_device,
        selections=result.selections,
        record_count=result.record_count,
        headers=result.headers,
        player_ids=result.player_ids,
    )
    logger.info(usage_log)
    return {
        "result": result,
        "usage_log": usage_log,
        "selection_log": usage_log,
    }

def load_csv_bytes_from_google_drive(url: str) -> tuple[bytes, str, str | None]:
    """互換用: 単体URLでも親フォルダから modifiedTime 最新CSVを取得。"""
    from app.drive_latest import resolve_latest_csv_from_source

    info, content, _ = resolve_latest_csv_from_source(
        drive_url=url,
        api_key=get_google_api_key(),
        prefer_local=False,
    )
    return content, info.file_id, info.modified_time

def load_csv_from_google_drive(url: str) -> pd.DataFrame:
    content, _, _ = load_csv_bytes_from_google_drive(url)
    return prepare_dataframe(read_csv_robust(content))

def apply_import_policy(
    raw_df: pd.DataFrame,
    *,
    file_id: str,
    file_name: str | None,
    content: bytes | None,
    modified_time: str | None,
    mode: str,
    start_date,
    end_date,
    date_column: str | None,
) -> dict:
    from app.incremental_loader import prepare_analysis_dataframe

    return prepare_analysis_dataframe(
        raw_df,
        file_id=file_id,
        file_name=file_name,
        content=content,
        modified_time=modified_time,
        mode=mode,
        start_date=start_date,
        end_date=end_date,
        date_column=date_column or None,
        mark_imported=(mode == "new_only"),
    )

def render_pdf_section(report_df: pd.DataFrame) -> None:
    """グラフ表示の下に必ず表示する PDF レポート生成セクション。"""
    st.markdown("---")
    st.markdown('<div class="section-header">📄 PDFレポート生成</div>', unsafe_allow_html=True)

    if st.button(
        "📥 PDFレポートを生成",
        type="primary",
        key="generate_pdf_btn",
        use_container_width=True,
    ):
        try:
            from app.pdf_builder import build_pdf_bytes

            with st.spinner("PDFを生成中..."):
                st.session_state.pdf_bytes = build_pdf_bytes(report_df)
                st.session_state.pdf_error = None
            st.success("PDFレポートを生成しました。下のボタンからダウンロードできます。")
        except ImportError:
            st.session_state.pdf_bytes = None
            st.session_state.pdf_error = (
                "reportlab がインストールされていません。"
                "requirements.txt に reportlab を追加して再デプロイしてください。"
            )
        except Exception as e:
            st.session_state.pdf_bytes = None
            st.session_state.pdf_error = f"{type(e).__name__}: {e}"

    if st.session_state.get("pdf_error"):
        st.error(f"PDF生成エラー: {st.session_state.pdf_error}")

    if st.session_state.get("pdf_bytes"):
        st.download_button(
            label="⬇️ PDFをダウンロード",
            data=st.session_state.pdf_bytes,
            file_name=f"vr_analysis_report_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
            use_container_width=True,
            key="download_pdf_btn",
        )

# ヘッダー
st.markdown('<div class="main-title">🏥 VR保育研修 分析ダッシュボード</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">VRセッションデータのアップロードと分析</div>', unsafe_allow_html=True)

# サイドバー
with st.sidebar:
    st.image("https://img.icons8.com/color/96/virtual-reality.png", width=80)
    st.title("設定・フィルター")
    st.markdown("---")
    st.markdown("### 👥 分析対象（CSV選択）")
    scope_label = st.radio(
        "全員 / 個人",
        options=["全員", "個人"],
        index=0,
        help="全員: Drive最新CSVの全行。個人: 同じCSV内の選択 Player_ID のみ（値はCSVどおり）。",
        key="analysis_scope_radio",
    )
    analysis_mode = "all" if scope_label == "全員" else "individual"
    selected_device = None
    if analysis_mode == "individual":
        player_options = sorted(
            set(st.session_state.available_players + st.session_state.available_devices),
            key=lambda x: str(x).lower(),
        )
        if player_options:
            selected_device = st.selectbox(
                "個人（Player_ID）",
                options=player_options,
                key="selected_device_box",
            )
        custom_device = st.text_input(
            "Player_ID を手入力（CSVの値どおり。例: ota / Player）",
            value="",
            placeholder="ota",
            key="custom_device_input",
        )
        if custom_device.strip():
            selected_device = custom_device.strip()
        if not selected_device:
            st.caption("※ 先に「全員」で取り込むか、Player_ID を手入力してください。")

    selection_sig = (analysis_mode, selected_device)
    prev_sig = st.session_state.get("vr_selection_sig")
    if prev_sig is not None and prev_sig != selection_sig:
        had_data = bool(st.session_state.get("had_successful_load"))
        logger.info(
            "CSV selection changed: %s -> %s ; clear cache%s",
            prev_sig,
            selection_sig,
            " and force reload" if had_data else "",
        )
        reset_load_state()
        st.session_state.had_successful_load = False
        # 前回読み込み済みなら、選択変更時に必ず再取得して再描画する
        if had_data:
            st.session_state.force_csv_reload = True
    st.session_state.vr_selection_sig = selection_sig

    st.markdown("---")
    st.markdown("### 📥 取り込み設定")
    import_mode_label = st.radio(
        "分析対象行",
        options=["新規追加分のみ", "期間指定", "全件（再分析）"],
        index=0,
        help="日付未指定時は新規追加分のみ。期間指定時はその期間のデータを抽出します。",
    )
    mode_map = {
        "新規追加分のみ": "new_only",
        "期間指定": "date_range",
        "全件（再分析）": "all",
    }
    import_mode = mode_map[import_mode_label]

    start_date = None
    end_date = None
    if import_mode == "date_range":
        c1, c2 = st.columns(2)
        start_date = c1.date_input("開始日")
        end_date = c2.date_input("終了日")
    else:
        st.caption("期間指定モードにすると開始日・終了日を選べます。")

    date_column_override = st.text_input(
        "日付カラム名（任意）",
        value="",
        placeholder="例: Session_Date",
        help="空欄の場合は候補カラムを自動検出します。Elapsed_Time（経過秒）は日付として使いません。",
    )

    st.markdown("---")
    if st.button("取り込み履歴をクリア", key="clear_history_btn"):
        try:
            from app.import_history import clear_history, get_connection

            clear_history(get_connection())
            st.success("取り込み履歴をクリアしました。")
        except Exception as e:
            st.error(f"履歴クリアエラー: {e}")

# CSV読み込み
st.markdown('<div class="section-header">📂 データ読み込み</div>', unsafe_allow_html=True)
st.caption(
    "ボタン押下のたびに Google Drive の CSV 一覧を再取得します。"
    "最新判定はファイル名ではなく **modifiedTime** です。"
    "「全員」= 各VR端末の最新CSVを統合 / 「個人」= 選択端末の最新CSVのみ。"
)

uploaded_files = st.file_uploader(
    "CSVをアップロード（複数可・端末ごとに最新を使用）",
    type=["csv"],
    accept_multiple_files=True,
    key="csv_uploader",
)
load_from_upload = st.button(
    "アップロードCSVを取り込む",
    use_container_width=True,
    key="load_from_upload_btn",
)

st.markdown("**Google Drive / ローカルからCSVを取り込む**")
drive_url = st.text_input(
    "Google DriveのフォルダURL",
    value=DEFAULT_DRIVE_CSV_URL,
    help=(
        "常に folderId=1ClTITbRVQc_hiDDIF5lfEEEttJs5qTc9 内のCSV一覧を再取得し、"
        "modifiedTimeが最新のものを使います。単体ファイルURLは使いません。"
    ),
)
st.caption(
    "固定参照フォルダ: `1ClTITbRVQc_hiDDIF5lfEEEttJs5qTc9` ／ "
    "単体 fileId の固定読み込みはしません"
)
st.caption(
    "推奨: Streamlit Cloud の Secrets に `GOOGLE_API_KEY` を設定すると、"
    "Drive の modifiedTime で正確に最新CSVを判定できます。"
)
api_key_input = st.text_input(
    "Google API Key（任意・フォルダ一覧用）",
    value="",
    type="password",
    help="フォルダ内のCSV一覧取得に使います。未設定でも公開フォルダの取得を試みます。",
)
prefer_local = st.checkbox(
    "ローカル data/input のCSVを優先する（非推奨・sample_session.csvは除外）",
    value=False,
)
load_from_drive = st.button(
    "最新CSVを取り込む",
    type="primary",
    use_container_width=True,
    key="load_from_drive_btn",
)
st.markdown("---")

do_drive_load = load_from_drive or st.session_state.get("force_csv_reload", False)

if load_from_upload:
    try:
        reset_load_state()
        if not uploaded_files:
            raise FileNotFoundError("CSVが選択されていません。ファイルを選んでから再度ボタンを押してください。")
        from app.drive_latest import CsvFileInfo, pick_latest_uploaded
        from app.vr_csv_loader import DeviceCsvSelection, prepare_vr_dataframe, unique_player_ids

        latest_upload = pick_latest_uploaded(uploaded_files)
        if latest_upload.name.lower() == "sample_session.csv":
            raise ValueError("sample_session.csv は使用できません。Google Driveの実CSVを使ってください。")
        raw = prepare_vr_dataframe(read_csv_robust(latest_upload.getvalue()))
        headers = list(raw.columns)
        players = unique_player_ids(raw)
        st.session_state.available_players = players
        st.session_state.available_devices = players

        df_use = raw
        if analysis_mode == "individual":
            if not selected_device:
                raise ValueError("個人モードでは Player_ID を指定してください。")
            if "Player_ID" not in df_use.columns:
                raise ValueError("CSVに Player_ID 列がありません。")
            mask = df_use["Player_ID"].astype(str) == selected_device
            if not mask.any():
                raise ValueError(f"Player_ID「{selected_device}」がありません。値: {players}")
            df_use = df_use.loc[mask].copy()

        info = CsvFileInfo(
            file_id=f"upload:{latest_upload.name}",
            name=latest_upload.name,
            modified_time=None,
            source="upload",
        )
        used = [
            DeviceCsvSelection(
                device_id=selected_device or ",".join(players) or "ALL",
                file=info,
                df=df_use,
                record_count=len(df_use),
            )
        ]
        result = apply_import_policy_for_selections(
            used,
            import_mode=import_mode,
            start_date=start_date,
            end_date=end_date,
            date_column=date_column_override.strip() or None,
        )
        headers = list(result["df"].columns)
        player_ids = unique_player_ids(result["df"]) if len(result["df"]) else players
        usage_log = format_vr_usage_log(
            mode=analysis_mode,
            selected_device=selected_device,
            selections=used,
            record_count=result["selected_rows"],
            headers=headers,
            player_ids=player_ids,
        )
        logger.info(usage_log)
        st.session_state.df = result["df"]
        st.session_state.had_successful_load = True
        st.session_state.import_meta = {
            **result,
            "source_name": latest_upload.name,
            "candidates": len(uploaded_files),
            "selection_log": usage_log,
            "usage_log": usage_log,
            "analysis_mode": analysis_mode,
            "selected_device": selected_device,
            "headers": headers,
            "player_ids": player_ids,
            "files": [
                {
                    "device": used[0].device_id,
                    "name": info.name,
                    "file_id": info.file_id,
                    "modified_time": None,
                    "records": len(result["df"]),
                }
            ],
        }
        st.session_state.latest_csv_info = {
            "name": latest_upload.name,
            "file_id": info.file_id,
            "modified_time": None,
            "source": "upload",
        }
        st.success(
            f"アップロード完了（モード: {scope_label}）／ "
            f"{latest_upload.name} ／ "
            f"全{result['total_rows']}行 → 分析対象 {result['selected_rows']}行"
        )
        with st.expander("使用CSVログ", expanded=True):
            st.code(usage_log)
        for note in result.get("notes") or []:
            st.info(note)
    except Exception as e:
        st.error(f"CSVの読み込みエラー: {e}")
        logger.exception("upload load failed")

elif do_drive_load:
    try:
        st.session_state.force_csv_reload = False
        reset_load_state()
        with st.spinner("CSV一覧を再取得し、端末ごとの最新ファイルを選定中..."):
            api_key = (api_key_input or "").strip() or get_google_api_key()
            loaded = load_vr_csv_bundle(
                drive_url=drive_url.strip(),
                api_key=api_key,
                analysis_mode=analysis_mode,
                selected_device=selected_device,
                prefer_local=prefer_local,
            )
            vr_result = loaded["result"]
            selection_log = loaded.get("selection_log") or ""
            usage_log = loaded.get("usage_log") or vr_result.selection_log or ""
            players = list(vr_result.player_ids or [])
            st.session_state.available_players = sorted(
                set(players) | set(st.session_state.available_players),
                key=lambda x: str(x).lower(),
            )
            st.session_state.available_devices = list(st.session_state.available_players)
            result = apply_import_policy_for_selections(
                vr_result.selections,
                import_mode=import_mode,
                start_date=start_date,
                end_date=end_date,
                date_column=date_column_override.strip() or None,
            )
            # import 後もヘッダーを維持
            headers = list(result["df"].columns) if result["df"] is not None else list(vr_result.headers)
            from app.vr_csv_loader import unique_player_ids

            player_ids = unique_player_ids(result["df"]) if len(result["df"]) else players
            usage_log = format_vr_usage_log(
                mode=analysis_mode,
                selected_device=selected_device,
                selections=vr_result.selections,
                record_count=result["selected_rows"],
                headers=headers,
                player_ids=player_ids or players,
            )
            logger.info(usage_log)
            for line in (selection_log or "").splitlines():
                logger.info(line)
        st.session_state.df = result["df"]
        st.session_state.had_successful_load = True
        st.session_state.import_meta = {
            **result,
            "source_name": ", ".join(s.file.name for s in vr_result.selections),
            "selection_log": usage_log,
            "usage_log": usage_log,
            "analysis_mode": analysis_mode,
            "selected_device": selected_device,
            "headers": headers,
            "player_ids": player_ids or players,
            "files": [
                {
                    "device": s.device_id,
                    "name": s.file.name,
                    "file_id": s.file.file_id,
                    "modified_time": s.file.modified_time,
                    "records": s.record_count,
                }
                for s in vr_result.selections
            ],
        }
        first = vr_result.selections[0] if vr_result.selections else None
        st.session_state.latest_csv_info = {
            "name": st.session_state.import_meta["source_name"],
            "file_id": ",".join(s.file.file_id for s in vr_result.selections),
            "modified_time": ", ".join(s.file.modified_time or "-" for s in vr_result.selections),
            "source": first.file.source if first else "drive",
        }
        st.success(
            f"取り込み完了（モード: {scope_label}"
            + (f" / 端末: {selected_device}" if selected_device else "")
            + f"）／ 使用ファイル {len(vr_result.selections)} 件／ "
            f"全{result['total_rows']}行 → 分析対象 {result['selected_rows']}行"
        )
        with st.expander("使用CSV・選定ログ", expanded=True):
            st.code((selection_log or "") + "\n\n" + usage_log)
        for note in result.get("notes") or []:
            st.info(note)
        if result["selected_rows"] == 0:
            st.warning(
                "分析対象が 0 件です。すでに取り込み済みか、期間に該当するデータがありません。"
                "「全件（再分析）」または期間指定を変更してください。"
            )
    except Exception as e:
        st.session_state.force_csv_reload = False
        st.error(f"CSVの取得エラー: {e}")
        logger.exception("vr csv load failed")

df = st.session_state.df

if df is not None:
    meta = st.session_state.get("import_meta") or {}
    latest_info = st.session_state.get("latest_csv_info") or {}
    if meta or latest_info:
        name = latest_info.get("name") or meta.get("source_name") or "-"
        modified = latest_info.get("modified_time") or "(不明)"
        mode_disp = "全員" if meta.get("analysis_mode") == "all" else "個人"
        st.caption(
            f"使用中CSV: {name} / modifiedTime: {modified} / 選択モード: {mode_disp}"
            + (f"({meta.get('selected_device')})" if meta.get("selected_device") else "")
            + f" / 取り込み: {meta.get('mode')} / "
            f"元データ {meta.get('total_rows')} 行 → 表示中 {len(df)} 行"
            + (f" / 日付カラム: {meta.get('date_column_used')}" if meta.get("date_column_used") else "")
        )
        if meta.get("usage_log") or meta.get("selection_log"):
            with st.expander("使用CSVログ（確認用）", expanded=False):
                st.code(meta.get("usage_log") or meta.get("selection_log"))

    with st.sidebar:
        st.markdown("### 🗂 取り込み履歴")
        try:
            from app.import_history import get_connection, list_import_runs

            runs = list_import_runs(get_connection(), limit=10)
            if runs:
                for run in runs:
                    st.caption(
                        f"{run['created_at'][:19]} | {run['mode']} | "
                        f"{run['rows_new']}/{run['rows_loaded']}行"
                    )
            else:
                st.caption("履歴はまだありません。")
        except Exception as e:
            st.caption(f"履歴表示エラー: {e}")

    from app.vr_dashboard_charts import render_vr_dashboard

    filtered_df = render_vr_dashboard(df)
    logger.info(
        "[グラフ再描画] analysis_mode=%s selected_device=%s chart_records=%s columns=%s",
        meta.get("analysis_mode"),
        meta.get("selected_device"),
        len(filtered_df),
        list(filtered_df.columns),
    )
    if meta.get("files"):
        for fmeta in meta["files"]:
            logger.info(
                "[グラフ使用CSV] device=%s name=%s fileId=%s modifiedTime=%s",
                fmeta.get("device"),
                fmeta.get("name"),
                fmeta.get("file_id"),
                fmeta.get("modified_time"),
            )

    render_pdf_section(filtered_df)

else:
    st.info(
        "👆 「最新CSVを取り込む」で、毎回 Drive 一覧を再取得し、"
        "端末ごとの最新CSV（modifiedTime）から分析します。"
        "全員／個人を切り替えると、古いデータは破棄して再取得します。"
    )
    st.markdown("""
    **取り込みルール：**
    - **全員** → Driveフォルダ内の modifiedTime 最新CSVを1件読み込み（全行）
    - **個人** → 同じ最新CSVのうち、選択した Player_ID（ota / Player 等）の行のみ
    - sample_session.csv や timestamp/player_id などの仮項目は使わない
    - 列名は実CSVヘッダーどおり
      （`Elapsed_Time`, `Event_Type`, `Player_ID`, `Player_X`, `Player_Y`, `Player_Z`,
      `Target_Object`, `Reaction_Time_Micro`, `World_X`, `World_Y`, `World_Z`,
      `Local_X`, `Local_Y`, `Local_Z`）
    """)
sample_session.csv や仮データを完全にやめて、Google Drive上の実CSVヘッダー Elapsed_Time / Event_Type / Player_ID などをそのまま使ってください。