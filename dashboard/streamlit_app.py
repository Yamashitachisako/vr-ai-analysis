import re
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
from urllib.request import Request, urlopen

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

def load_csv_from_upload(uploaded_file) -> pd.DataFrame:
    return read_csv_robust(uploaded_file.getvalue())

def load_csv_from_google_drive(url: str) -> pd.DataFrame:
    download_url = to_google_drive_download_url(url)
    request = Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=60) as response:
        content = response.read()
    return read_csv_robust(content)

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
        df = load_csv_from_upload(uploaded_file)
    except Exception as e:
        st.error(f"CSVの読み込みエラー: {e}")
elif load_from_drive and drive_url.strip():
    try:
        with st.spinner("Google Driveから読み込み中..."):
            df = load_csv_from_google_drive(drive_url.strip())
        st.success("Google Driveから読み込みました。")
    except Exception as e:
        st.error(f"Google Driveの読み込みエラー: {e}")

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

    # ── グラフ行2：保育者別比較 ＋ 場所別ヒートマップ ──
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

    # ── PDFレポート生成 ──
    st.markdown('<div class="section-header">📄 PDFレポート生成</div>', unsafe_allow_html=True)
    if st.button("📥 PDFレポートを生成", type="primary"):
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib import colors
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            import datetime

            buf = BytesIO()
            doc = SimpleDocTemplate(buf, pagesize=A4)
            elements = []
            styles = getSampleStyleSheet()

            # タイトル
            title_style = ParagraphStyle("title", fontSize=16, spaceAfter=12, fontName="Helvetica-Bold")
            elements.append(Paragraph("VR Nursery Training Analysis Report", title_style))
            elements.append(Paragraph(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", styles["Normal"]))
            elements.append(Spacer(1, 12))

            # 基本情報テーブル
            summary_data = [
                ["Item", "Value"],
                ["Total Records", str(total_rows)],
                ["Total Events", str(len(event_rows))],
            ]
            if "reaction_time" in filtered_df.columns:
                avg_rt2 = filtered_df["reaction_time"].replace(0, pd.NA).dropna().mean()
                summary_data.append(["Avg Reaction Time", f"{avg_rt2:.2f} sec" if pd.notna(avg_rt2) else "N/A"])

            t = Table(summary_data, colWidths=[200, 200])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4a90d9")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f8")]),
            ]))
            elements.append(t)

            doc.build(elements)
            buf.seek(0)
            st.download_button(
                label="⬇️ PDFをダウンロード",
                data=buf,
                file_name=f"vr_analysis_report_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.pdf",
                mime="application/pdf"
            )
        except Exception as e:
            st.error(f"PDF生成エラー: {e}")

else:
    st.info("👆 CSVファイルをアップロードするか、Google Driveのリンクから読み込んでください。")
    st.markdown("""
    **対応カラム例：**
    | カラム名 | 内容 |
    |---|---|
    | `timestamp` | 記録時刻 |
    | `player_id` | 保育者ID（A/B/C/D） |
    | `event_type` | 事象タイプ（誤嚥・転倒など） |
    | `reaction_time` | 反応時間（秒） |
    | `gaze_x`, `gaze_y` | 視線座標 |
    | `location` | 場所（リビング・廊下など） |
    """)