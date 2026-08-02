"""実CSVヘッダー名でグラフ・KPIを描画（列名はリネームしない）。"""

from __future__ import annotations

import logging

import pandas as pd
import plotly.express as px
import streamlit as st

logger = logging.getLogger(__name__)

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


def value_column(df: pd.DataFrame) -> str | None:
    for col in ("Reaction_Time_Micro", "Reaction_Time_Mic", "Data_Value"):
        if col in df.columns:
            return col
    return None


def render_vr_dashboard(df: pd.DataFrame) -> pd.DataFrame:
    """フィルター後の DataFrame を返し、表・KPI・グラフを描画する。"""
    value_col = value_column(df)

    with st.sidebar:
        st.markdown("### 🔍 データフィルター")
        if "Player_ID" in df.columns:
            players = ["全員"] + sorted(
                df["Player_ID"].dropna().astype(str).unique().tolist(),
                key=lambda x: x.lower(),
            )
            selected_player = st.selectbox("Player_ID", players, key="filter_player_id")
        else:
            selected_player = "全員"

        if "Event_Type" in df.columns:
            events = ["全て"] + sorted(df["Event_Type"].dropna().astype(str).unique().tolist())
            selected_event = st.selectbox("Event_Type", events, key="filter_event_type")
        else:
            selected_event = "全て"

        if "Target_Object" in df.columns:
            targets = ["全て"] + sorted(
                df["Target_Object"].dropna().astype(str).unique().tolist()
            )
            selected_target = st.selectbox("Target_Object", targets, key="filter_target_object")
        else:
            selected_target = "全て"

    filtered = df.copy()
    if selected_player != "全員" and "Player_ID" in filtered.columns:
        filtered = filtered[filtered["Player_ID"].astype(str) == selected_player]
    if selected_event != "全て" and "Event_Type" in filtered.columns:
        filtered = filtered[filtered["Event_Type"].astype(str) == selected_event]
    if selected_target != "全て" and "Target_Object" in filtered.columns:
        filtered = filtered[filtered["Target_Object"].astype(str) == selected_target]

    st.markdown('<div class="section-header">📋 データプレビュー</div>', unsafe_allow_html=True)
    st.caption(f"列名（CSVヘッダー）: {list(filtered.columns)}")
    st.dataframe(filtered, use_container_width=True, height=250)

    st.markdown('<div class="section-header">📊 基本情報</div>', unsafe_allow_html=True)
    c1, c2, c3, c4, c5 = st.columns(5)
    total_rows = len(filtered)
    if "Event_Type" in filtered.columns:
        event_rows = filtered[
            filtered["Event_Type"].notna() & (filtered["Event_Type"].astype(str) != "None")
        ]
    else:
        event_rows = filtered

    c1.metric("総レコード数", f"{total_rows} 件")
    c2.metric("Event_Type件数", f"{len(event_rows)} 件")

    if value_col and value_col in filtered.columns:
        vals = pd.to_numeric(filtered[value_col], errors="coerce").dropna()
        label = value_col
        c3.metric(f"{label}平均", f"{vals.mean():.2f}" if not vals.empty else "N/A")
        c4.metric(f"{label}最大", f"{vals.max():.2f}" if not vals.empty else "N/A")
        c5.metric(f"{label}最小", f"{vals.min():.2f}" if not vals.empty else "N/A")
    else:
        c3.metric("数値列", "N/A")
        c4.metric("数値列", "N/A")
        c5.metric("数値列", "N/A")

    try:
        st.markdown(
            f'<div class="section-header">📈 {value_col or "Value"} / Event_Type</div>',
            unsafe_allow_html=True,
        )
        a, b = st.columns(2)
        with a:
            if value_col and "Elapsed_Time" in filtered.columns:
                plot_df = filtered.copy()
                plot_df[value_col] = pd.to_numeric(plot_df[value_col], errors="coerce")
                plot_df = plot_df.dropna(subset=[value_col])
                if not plot_df.empty:
                    fig = px.line(
                        plot_df.reset_index(drop=True).reset_index(),
                        x="index",
                        y=value_col,
                        color="Event_Type" if "Event_Type" in plot_df.columns else None,
                        color_discrete_map=EVENT_COLORS,
                        title=f"レコード順の {value_col}",
                        labels={"index": "index", value_col: value_col, "Event_Type": "Event_Type"},
                    )
                    fig.update_layout(height=350)
                    st.plotly_chart(fig, use_container_width=True)
        with b:
            if value_col and "Event_Type" in filtered.columns:
                tmp = filtered.copy()
                tmp[value_col] = pd.to_numeric(tmp[value_col], errors="coerce")
                avg = (
                    tmp.dropna(subset=[value_col])
                    .groupby("Event_Type")[value_col]
                    .mean()
                    .reset_index()
                )
                avg = avg[avg["Event_Type"].astype(str) != "None"]
                if not avg.empty:
                    fig = px.bar(
                        avg,
                        x="Event_Type",
                        y=value_col,
                        color="Event_Type",
                        color_discrete_map=EVENT_COLORS,
                        title=f"Event_Type別 {value_col}平均",
                        labels={"Event_Type": "Event_Type", value_col: value_col},
                    )
                    fig.update_layout(height=350, showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.warning(f"グラフエラー: {e}")
        logger.exception("chart error")

    try:
        c, d = st.columns(2)
        with c:
            st.markdown(
                f'<div class="section-header">👤 Player_ID別 {value_col or ""}</div>',
                unsafe_allow_html=True,
            )
            if value_col and "Player_ID" in filtered.columns:
                tmp = filtered.copy()
                tmp[value_col] = pd.to_numeric(tmp[value_col], errors="coerce")
                avg = (
                    tmp.dropna(subset=[value_col])
                    .groupby("Player_ID")[value_col]
                    .mean()
                    .reset_index()
                )
                if not avg.empty:
                    fig = px.bar(
                        avg,
                        x="Player_ID",
                        y=value_col,
                        color="Player_ID",
                        title=f"Player_ID別 {value_col}平均",
                        labels={"Player_ID": "Player_ID", value_col: value_col},
                    )
                    fig.update_layout(height=350, showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)
        with d:
            st.markdown(
                '<div class="section-header">📍 Target_Object × Event_Type</div>',
                unsafe_allow_html=True,
            )
            if "Target_Object" in filtered.columns and "Event_Type" in filtered.columns:
                loc = filtered[
                    filtered["Event_Type"].notna() & (filtered["Event_Type"].astype(str) != "None")
                ]
                counts = loc.groupby(["Target_Object", "Event_Type"]).size().reset_index(name="件数")
                if not counts.empty:
                    fig = px.bar(
                        counts,
                        x="Target_Object",
                        y="件数",
                        color="Event_Type",
                        color_discrete_map=EVENT_COLORS,
                        title="Target_Object別 Event_Type件数",
                        labels={
                            "Target_Object": "Target_Object",
                            "件数": "件数",
                            "Event_Type": "Event_Type",
                        },
                    )
                    fig.update_layout(height=350)
                    st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.warning(f"Player_ID / Target_Object グラフエラー: {e}")

    try:
        st.markdown(
            '<div class="section-header">📊 Event_Type / Target_Object 集計</div>',
            unsafe_allow_html=True,
        )
        e, f = st.columns(2)
        with e:
            if "Event_Type" in filtered.columns:
                counts = (
                    filtered[
                        filtered["Event_Type"].notna()
                        & (filtered["Event_Type"].astype(str) != "None")
                    ]
                    .groupby("Event_Type")
                    .size()
                    .reset_index(name="件数")
                )
                if not counts.empty:
                    fig = px.bar(
                        counts,
                        x="Event_Type",
                        y="件数",
                        color="Event_Type",
                        color_discrete_map=EVENT_COLORS,
                        title="Event_Type別 件数",
                        labels={"Event_Type": "Event_Type", "件数": "件数"},
                    )
                    fig.update_layout(height=350, showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)
        with f:
            if "Target_Object" in filtered.columns:
                counts = (
                    filtered[
                        filtered["Target_Object"].notna()
                        & (filtered["Target_Object"].astype(str) != "None")
                    ]
                    .groupby("Target_Object")
                    .size()
                    .reset_index(name="件数")
                )
                if not counts.empty:
                    fig = px.bar(
                        counts,
                        x="Target_Object",
                        y="件数",
                        color="Target_Object",
                        title="Target_Object別 件数",
                        labels={"Target_Object": "Target_Object", "件数": "件数"},
                    )
                    fig.update_layout(height=350, showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.warning(f"集計グラフエラー: {e}")

    return filtered
