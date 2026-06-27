import re
import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

def extract_google_drive_file_id(url: str) -> str:
    match = re.search(r"/file/d/([^/]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([^&]+)", url)
    if match:
        return match.group(1)
    return url.strip()

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

def load_csv_from_google_drive(url: str) -> pd.DataFrame:
    download_url = to_google_drive_download_url(url)
    request = Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=60) as response:
        content = response.read()
    return standardize_columns(read_csv_robust(content))

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

# CSV読み込み（ログイン後・メインエリア上部に常時表示）
st.markdown('<div class="section-header">📂 データ読み込み</div>', unsafe_allow_html=True)
uploaded_file = st.file_uploader("CSVをアップロード", type=["csv"])

st.markdown("**Google Driveから読み込む**")
drive_url = st.text_input("Google DriveのCSVリンク", value=DEFAULT_DRIVE_CSV_URL)
load_from_drive = st.button(
    "Google Driveから読み込む",
    type="primary",
    use_container_width=True,
    key="load_from_drive_btn",
)
st.markdown("---")

df = None
if uploaded_file is not None:
    try:
        st.session_state.df = load_csv_from_upload(uploaded_file)
        st.session_state.pdf_bytes = None
        st.session_state.pdf_error = None
    except Exception as e:
        st.error(f"CSVの読み込みエラー: {e}")
elif load_from_drive and drive_url.strip():
    try:
        with st.spinner("Google Driveから読み込み中..."):
            st.session_state.df = load_csv_from_google_drive(drive_url.strip())
        st.session_state.pdf_bytes = None
        st.session_state.pdf_error = None
        st.success("Google Driveから読み込みました。")
    except Exception as e:
        st.error(f"Google Driveの読み込みエラー: {e}")

df = st.session_state.df

if df is not None:

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
    st.info("👆 CSVファイルをアップロードするか、Google Driveのリンクから読み込んでください。")
    st.markdown("""
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