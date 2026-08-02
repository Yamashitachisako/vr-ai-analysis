import re
import sys
import os
import logging
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

# 事象別カラー定義
EVENT_COLORS = {
    "誤嚥": "#e74c3c",
    "誤飲": "#c0392b",
    "転倒": "#e67e22",
    "転落": "#d35400",
    "噛みつき": "#9b59b6",
    "窒息": "#8e44ad",
    "アレルギー": "#f39c12",
    "None": "#bdc3c7",
}

def get_event_color(event_type):
    return EVENT_COLORS.get(str(event_type), "#3498db")

DEFAULT_DRIVE_CSV_URL = (
    "https://drive.google.com/file/d/10s13cnRpNdIpdR4Gaeez5sCaewonj1a9/view?usp=sharing"
)

def reset_load_state() -> None:
    """アップロード／Drive取得前に前回の選択・キャッシュをクリアする。"""
    st.session_state.df = None
    st.session_state.import_meta = None
    st.session_state.pdf_bytes = None
    st.session_state.pdf_error = None
    st.session_state.last_upload_key = None
    st.session_state.latest_csv_info = None
    logger.info("load state reset before fetching latest CSV")


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

def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """CSVカラム名をアプリ標準名に統一する。"""
    df = df.copy()
    df.columns = df.columns.str.strip()

    direct_rename = {
        "Elapsed_Time": "timestamp",
        "Event_Type": "event_type",
        "Player_ID": "player_id",
    }
    df = df.rename(columns={k: v for k, v in direct_rename.items() if k in df.columns})

    if "Target_Object" in df.columns:
        if "location" not in df.columns:
            df["location"] = df["Target_Object"]
        if "target_object" not in df.columns:
            df["target_object"] = df["Target_Object"]

    if "Data_Value" in df.columns:
        df["data_value"] = pd.to_numeric(df["Data_Value"], errors="coerce")
        if "reaction_time" not in df.columns:
            df["reaction_time"] = df["data_value"]

    if "WorldX" in df.columns and "gaze_x" not in df.columns:
        df["gaze_x"] = pd.to_numeric(df["WorldX"], errors="coerce")
    if "WorldY" in df.columns and "gaze_y" not in df.columns:
        df["gaze_y"] = pd.to_numeric(df["WorldY"], errors="coerce")

    if "reaction_time" in df.columns:
        df["reaction_time"] = pd.to_numeric(df["reaction_time"], errors="coerce")
    if "data_value" not in df.columns and "reaction_time" in df.columns:
        df["data_value"] = df["reaction_time"]

    return df

def load_csv_from_upload(uploaded_file) -> pd.DataFrame:
    return standardize_columns(read_csv_robust(uploaded_file.getvalue()))

def load_latest_csv_for_analysis(
    *,
    drive_url: str,
    api_key: str | None,
    prefer_local: bool = False,
) -> dict:
    """毎回一覧を再取得し、最新CSVのみを読み込む。"""
    from app.drive_latest import format_csv_selection_log, resolve_latest_csv_from_source

    info, content, candidates = resolve_latest_csv_from_source(
        drive_url=drive_url,
        api_key=api_key,
        prefer_local=prefer_local,
    )
    selection_log = format_csv_selection_log(info, candidates)
    logger.info("load_latest_csv_for_analysis selected=%s modified=%s", info.name, info.modified_time)
    raw = standardize_columns(read_csv_robust(content))
    return {
        "info": info,
        "content": content,
        "raw": raw,
        "candidates": candidates,
        "selection_log": selection_log,
    }

def load_csv_bytes_from_google_drive(url: str) -> tuple[bytes, str, str | None]:
    """互換用: 単一ファイルURLから取得。"""
    from app.drive_latest import download_drive_file, extract_drive_file_id

    file_id = extract_drive_file_id(url) or url.strip()
    content, modified = download_drive_file(file_id)
    return content, file_id, modified

def load_csv_from_google_drive(url: str) -> pd.DataFrame:
    content, _, _ = load_csv_bytes_from_google_drive(url)
    return standardize_columns(read_csv_robust(content))

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
    st.markdown("### 📥 取り込み設定")
    import_mode_label = st.radio(
        "分析対象",
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
        placeholder="例: Session_Date / timestamp",
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

# CSV読み込み（ログイン後・メインエリア上部に常時表示）
st.markdown('<div class="section-header">📂 データ読み込み</div>', unsafe_allow_html=True)
st.caption(
    "「取り込む」ボタンを押すたびにCSV一覧を再取得し、"
    "**最終更新日時が最も新しいCSVだけ**を分析対象にします。"
)

uploaded_files = st.file_uploader(
    "CSVをアップロード（複数可・最新のみ使用）",
    type=["csv"],
    accept_multiple_files=True,
    key="csv_uploader",
)
load_from_upload = st.button(
    "アップロードした最新CSVを取り込む",
    use_container_width=True,
    key="load_from_upload_btn",
)

st.markdown("**Google Drive / ローカルから最新CSVを取り込む**")
drive_url = st.text_input(
    "Google DriveのフォルダまたはファイルURL",
    value=DEFAULT_DRIVE_CSV_URL,
    help="フォルダURLなら中の最新CSVを自動選択。ファイルURLならそのファイルを毎回新規取得します。",
)
api_key_input = st.text_input(
    "Google API Key（任意・フォルダ一覧用）",
    value="",
    type="password",
    help="フォルダ内のCSV一覧取得に使います。未設定でも公開フォルダの取得を試みます。",
)
prefer_local = st.checkbox("ローカル data/input の最新CSVを優先する", value=False)
load_from_drive = st.button(
    "最新CSVを取り込む",
    type="primary",
    use_container_width=True,
    key="load_from_drive_btn",
)
st.markdown("---")

if load_from_upload:
    try:
        reset_load_state()
        if not uploaded_files:
            raise FileNotFoundError("CSVが選択されていません。ファイルを選んでから再度ボタンを押してください。")
        from app.drive_latest import CsvFileInfo, format_csv_selection_log, pick_latest_uploaded

        latest_upload = pick_latest_uploaded(uploaded_files)
        content = latest_upload.getvalue()
        raw = load_csv_from_upload(latest_upload)
        selected_info = CsvFileInfo(
            file_id=f"upload:{latest_upload.name}",
            name=latest_upload.name,
            modified_time=None,
            source="upload",
        )
        candidate_infos = [
            CsvFileInfo(file_id=f"upload:{f.name}", name=f.name, source="upload")
            for f in uploaded_files
        ]
        selection_log = format_csv_selection_log(selected_info, candidate_infos)
        for line in selection_log.splitlines():
            logger.info(line)
        result = apply_import_policy(
            raw,
            file_id=f"upload:{latest_upload.name}",
            file_name=latest_upload.name,
            content=content,
            modified_time=None,
            mode=import_mode,
            start_date=start_date,
            end_date=end_date,
            date_column=date_column_override.strip() or None,
        )
        st.session_state.df = result["df"]
        st.session_state.import_meta = {
            **result,
            "source_name": latest_upload.name,
            "candidates": len(uploaded_files),
            "selection_log": selection_log,
        }
        st.session_state.latest_csv_info = {
            "name": latest_upload.name,
            "file_id": f"upload:{latest_upload.name}",
            "modified_time": None,
            "created_time": None,
            "source": "upload",
        }
        st.success(
            f"アップロード完了（最新のみ）: {latest_upload.name} ／ "
            f"候補{len(uploaded_files)}件中1件 ／ "
            f"全{result['total_rows']}行 → 分析対象 {result['selected_rows']}行"
        )
        st.info(
            f"選択された最新CSV: **{latest_upload.name}** ／ "
            f"更新日時: （ブラウザアップロードのためファイル名の日付で判定）"
        )
        with st.expander("最新CSV選定ログ", expanded=True):
            st.code(selection_log)
        for note in result.get("notes") or []:
            st.info(note)
    except Exception as e:
        st.error(f"CSVの読み込みエラー: {e}")
        logger.exception("upload load failed")

elif load_from_drive:
    try:
        reset_load_state()
        with st.spinner("最新CSVを検索・取得中..."):
            api_key = (api_key_input or "").strip() or get_google_api_key()
            loaded = load_latest_csv_for_analysis(
                drive_url=drive_url.strip(),
                api_key=api_key,
                prefer_local=prefer_local,
            )
            info = loaded["info"]
            content = loaded["content"]
            raw = loaded["raw"]
            selection_log = loaded.get("selection_log") or ""
            result = apply_import_policy(
                raw,
                file_id=info.file_id,
                file_name=info.name,
                content=content,
                modified_time=info.modified_time,
                mode=import_mode,
                start_date=start_date,
                end_date=end_date,
                date_column=date_column_override.strip() or None,
            )
        st.session_state.df = result["df"]
        st.session_state.import_meta = {
            **result,
            "source_name": info.name,
            "source": info.source,
            "file_id": info.file_id,
            "selection_log": selection_log,
        }
        st.session_state.latest_csv_info = {
            "name": info.name,
            "file_id": info.file_id,
            "modified_time": info.modified_time,
            "created_time": info.created_time,
            "source": info.source,
        }
        st.success(
            f"最新CSVを取り込みました: **{info.name}** ／ "
            f"全{result['total_rows']}行 → 分析対象 {result['selected_rows']}行"
            f"（モード: {result['mode']}）"
        )
        st.info(
            f"選択された最新CSV: **{info.name}** ／ "
            f"更新日時(modifiedTime): **{info.modified_time or '(不明)'}** ／ "
            f"作成日時: {info.created_time or '(不明)'}"
        )
        with st.expander("最新CSV選定ログ", expanded=True):
            st.code(selection_log or f"選択: {info.name} / modified={info.modified_time}")
        for note in result.get("notes") or []:
            st.info(note)
        if result["selected_rows"] == 0:
            st.warning(
                "分析対象が 0 件です。すでに取り込み済みか、期間に該当するデータがありません。"
                "「全件（再分析）」または期間指定を変更してください。"
            )
    except Exception as e:
        st.error(f"最新CSVの取得エラー: {e}")
        logger.exception("latest csv load failed")

df = st.session_state.df

if df is not None:
    meta = st.session_state.get("import_meta") or {}
    latest_info = st.session_state.get("latest_csv_info") or {}
    if meta or latest_info:
        name = latest_info.get("name") or meta.get("source_name") or "-"
        modified = latest_info.get("modified_time") or "(不明)"
        st.caption(
            f"使用中CSV: {name} / 更新日時: {modified} / 取り込みモード: {meta.get('mode')} / "
            f"元データ {meta.get('total_rows')} 行 → 表示中 {len(df)} 行"
            + (f" / 日付カラム: {meta.get('date_column_used')}" if meta.get("date_column_used") else "")
        )
        if meta.get("selection_log"):
            with st.expander("最新CSV選定ログ（確認用）", expanded=False):
                st.code(meta["selection_log"])

    # サイドバー：取り込み履歴
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

    # サイドバーフィルター
    with st.sidebar:
        st.markdown("### 🔍 データフィルター")

        # 保育者フィルター
        if "player_id" in df.columns:
            all_players = ["全員"] + sorted(df["player_id"].dropna().unique().tolist())
            selected_player = st.selectbox("保育者（player_id）", all_players)
        else:
            selected_player = "全員"

        # 事象フィルター
        if "event_type" in df.columns:
            all_events = ["全て"] + sorted(df["event_type"].dropna().unique().tolist())
            selected_event = st.selectbox("事象タイプ", all_events)
        else:
            selected_event = "全て"

        # 場所フィルター
        if "location" in df.columns:
            all_locations = ["全て"] + sorted(df["location"].dropna().unique().tolist())
            selected_location = st.selectbox("場所", all_locations)
        else:
            selected_location = "全て"

    # フィルター適用
    filtered_df = df.copy()
    if selected_player != "全員" and "player_id" in df.columns:
        filtered_df = filtered_df[filtered_df["player_id"] == selected_player]
    if selected_event != "全て" and "event_type" in df.columns:
        filtered_df = filtered_df[filtered_df["event_type"] == selected_event]
    if selected_location != "全て" and "location" in df.columns:
        filtered_df = filtered_df[filtered_df["location"] == selected_location]

    # ── データプレビュー ──
    st.markdown('<div class="section-header">📋 データプレビュー</div>', unsafe_allow_html=True)
    st.dataframe(filtered_df, use_container_width=True, height=250)

    # ── KPIカード ──
    st.markdown('<div class="section-header">📊 基本情報</div>', unsafe_allow_html=True)
    col1, col2, col3, col4, col5 = st.columns(5)

    total_rows = len(filtered_df)
    event_rows = filtered_df[filtered_df["event_type"].notna() & (filtered_df["event_type"] != "None")] if "event_type" in filtered_df.columns else filtered_df

    with col1:
        st.metric("総レコード数", f"{total_rows} 件")
    with col2:
        st.metric("総イベント数", f"{len(event_rows)} 件")
    with col3:
        if "reaction_time" in filtered_df.columns:
            avg_rt = filtered_df["reaction_time"].replace(0, pd.NA).dropna().mean()
            st.metric("平均反応時間", f"{avg_rt:.2f} 秒" if pd.notna(avg_rt) else "N/A")
        else:
            st.metric("平均反応時間", "N/A")
    with col4:
        if "reaction_time" in filtered_df.columns:
            max_rt = filtered_df["reaction_time"].replace(0, pd.NA).dropna().max()
            st.metric("最長反応時間", f"{max_rt:.2f} 秒" if pd.notna(max_rt) else "N/A")
        else:
            st.metric("最長反応時間", "N/A")
    with col5:
        if "reaction_time" in filtered_df.columns:
            min_rt = filtered_df["reaction_time"].replace(0, pd.NA).dropna().min()
            st.metric("最短反応時間", f"{min_rt:.2f} 秒" if pd.notna(min_rt) else "N/A")
        else:
            st.metric("最短反応時間", "N/A")

    # ── グラフ行1：反応時間推移 ＋ 事象別平均反応時間 ──
    try:
        st.markdown('<div class="section-header">📈 反応時間分析</div>', unsafe_allow_html=True)
        col_a, col_b = st.columns(2)

        with col_a:
            if "reaction_time" in filtered_df.columns:
                rt_df = filtered_df[filtered_df["reaction_time"] > 0].copy()
                if "event_type" in rt_df.columns:
                    rt_df["color"] = rt_df["event_type"].apply(get_event_color)
                    fig_line = px.line(
                        rt_df.reset_index(),
                        x="index",
                        y="reaction_time",
                        color="event_type",
                        color_discrete_map=EVENT_COLORS,
                        title="反応時間の推移（事象別）",
                        labels={"index": "レコード番号", "reaction_time": "反応時間（秒）", "event_type": "事象"}
                    )
                else:
                    fig_line = px.line(rt_df.reset_index(), x="index", y="reaction_time", title="反応時間の推移")
                fig_line.update_layout(height=350)
                st.plotly_chart(fig_line, use_container_width=True)

        with col_b:
            if "event_type" in filtered_df.columns and "reaction_time" in filtered_df.columns:
                event_avg = (
                    filtered_df[filtered_df["reaction_time"] > 0]
                    .groupby("event_type")["reaction_time"]
                    .mean()
                    .reset_index()
                    .rename(columns={"reaction_time": "平均反応時間"})
                )
                event_avg = event_avg[event_avg["event_type"] != "None"]
                event_avg["color"] = event_avg["event_type"].apply(get_event_color)
                fig_bar = px.bar(
                    event_avg,
                    x="event_type",
                    y="平均反応時間",
                    color="event_type",
                    color_discrete_map=EVENT_COLORS,
                    title="事象別 平均反応時間",
                    labels={"event_type": "事象", "平均反応時間": "平均反応時間（秒）"}
                )
                fig_bar.update_layout(height=350, showlegend=False)
                st.plotly_chart(fig_bar, use_container_width=True)
    except Exception as e:
        st.warning(f"反応時間分析グラフの表示エラー: {e}")

    # ── グラフ行2：保育者別比較 ＋ 場所別ヒートマップ ──
    try:
        col_c, col_d = st.columns(2)

        with col_c:
            st.markdown('<div class="section-header">👤 保育者別 反応時間比較</div>', unsafe_allow_html=True)
            if "player_id" in filtered_df.columns and "reaction_time" in filtered_df.columns:
                player_avg = (
                    filtered_df[filtered_df["reaction_time"] > 0]
                    .groupby("player_id")["reaction_time"]
                    .mean()
                    .reset_index()
                    .rename(columns={"reaction_time": "平均反応時間"})
                )
                fig_player = px.bar(
                    player_avg,
                    x="player_id",
                    y="平均反応時間",
                    color="player_id",
                    title="保育者別 平均反応時間",
                    labels={"player_id": "保育者ID", "平均反応時間": "平均反応時間（秒）"}
                )
                fig_player.update_layout(height=350, showlegend=False)
                st.plotly_chart(fig_player, use_container_width=True)

        with col_d:
            st.markdown('<div class="section-header">📍 場所別 危険イベント数</div>', unsafe_allow_html=True)
            if "location" in filtered_df.columns and "event_type" in filtered_df.columns:
                loc_df = filtered_df[
                    filtered_df["event_type"].notna() & (filtered_df["event_type"] != "None")
                ]
                loc_count = loc_df.groupby(["location", "event_type"]).size().reset_index(name="件数")
                fig_loc = px.bar(
                    loc_count,
                    x="location",
                    y="件数",
                    color="event_type",
                    color_discrete_map=EVENT_COLORS,
                    title="場所別 危険イベント発生数",
                    labels={"location": "場所", "件数": "件数", "event_type": "事象"}
                )
                fig_loc.update_layout(height=350)
                st.plotly_chart(fig_loc, use_container_width=True)
    except Exception as e:
        st.warning(f"保育者別・場所別グラフの表示エラー: {e}")

    # ── グラフ行3：Event_Type / Target_Object 別件数 ＋ Data_Value推移 ──
    try:
        st.markdown('<div class="section-header">📊 イベント集計</div>', unsafe_allow_html=True)
        col_e, col_f = st.columns(2)

        with col_e:
            if "event_type" in filtered_df.columns:
                event_count = (
                    filtered_df[filtered_df["event_type"].notna() & (filtered_df["event_type"].astype(str) != "None")]
                    .groupby("event_type")
                    .size()
                    .reset_index(name="件数")
                )
                if not event_count.empty:
                    fig_event_count = px.bar(
                        event_count,
                        x="event_type",
                        y="件数",
                        color="event_type",
                        color_discrete_map=EVENT_COLORS,
                        title="Event_Type別 件数",
                        labels={"event_type": "事象", "件数": "件数"},
                    )
                    fig_event_count.update_layout(height=350, showlegend=False)
                    st.plotly_chart(fig_event_count, use_container_width=True)

        with col_f:
            target_col = "target_object" if "target_object" in filtered_df.columns else "location"
            if target_col in filtered_df.columns:
                target_count = (
                    filtered_df[filtered_df[target_col].notna() & (filtered_df[target_col].astype(str) != "None")]
                    .groupby(target_col)
                    .size()
                    .reset_index(name="件数")
                )
                if not target_count.empty:
                    fig_target_count = px.bar(
                        target_count,
                        x=target_col,
                        y="件数",
                        color=target_col,
                        title="Target_Object別 件数",
                        labels={target_col: "対象オブジェクト", "件数": "件数"},
                    )
                    fig_target_count.update_layout(height=350, showlegend=False)
                    st.plotly_chart(fig_target_count, use_container_width=True)

        value_col = "data_value" if "data_value" in filtered_df.columns else "reaction_time"
        if value_col in filtered_df.columns:
            dv_df = filtered_df.copy()
            dv_df[value_col] = pd.to_numeric(dv_df[value_col], errors="coerce")
            dv_df = dv_df[dv_df[value_col].notna()]
            if not dv_df.empty:
                if "timestamp" in dv_df.columns:
                    x_col = "timestamp"
                    x_label = "経過時間"
                else:
                    dv_df = dv_df.reset_index()
                    x_col = "index"
                    x_label = "レコード番号"
                fig_data_value = px.line(
                    dv_df,
                    x=x_col,
                    y=value_col,
                    color="event_type" if "event_type" in dv_df.columns else None,
                    color_discrete_map=EVENT_COLORS,
                    title="Data_Valueの推移",
                    labels={x_col: x_label, value_col: "Data_Value", "event_type": "事象"},
                )
                fig_data_value.update_layout(height=350)
                st.plotly_chart(fig_data_value, use_container_width=True)
    except Exception as e:
        st.warning(f"イベント集計グラフの表示エラー: {e}")

    render_pdf_section(filtered_df)

else:
    st.info("👆 「最新CSVを取り込む」ボタンで、毎回最新のCSVだけを取得します。")
    st.markdown("""
    **取り込みルール：**
    - Google Drive **フォルダURL** → 中のCSVを更新日時で並べ、最新1件のみ
    - Google Drive **ファイルURL** → そのファイルを毎回新規ダウンロード
    - ローカル `data/input` → 更新日時が最新のCSV
    - ブラウザアップロード（複数） → ファイル名の日付が新しいもの優先

    **対応カラム例：**
    | 標準カラム名 | VR CSVカラム名 | 内容 |
    |---|---|---|
    | `timestamp` | `Elapsed_Time` | 記録時刻 / 経過時間 |
    | `player_id` | `Player_ID` | 保育者ID |
    | `event_type` | `Event_Type` | 事象タイプ |
    | `reaction_time` | `Data_Value` | 反応時間 / データ値 |
    | `location` | `Target_Object` | 対象オブジェクト |
    | `gaze_x`, `gaze_y` | `WorldX`, `WorldY` | 視線・位置座標 |
    """)