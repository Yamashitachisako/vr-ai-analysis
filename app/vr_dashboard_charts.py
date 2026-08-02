"""実CSVヘッダー名（Elapsed_Time / Event_Type 等）でグラフ・KPIを描画。"""

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


def get_event_color(event_type) -> str:
    return EVENT_COLORS.get(str(event_type), "#3498db")


def render_vr_dashboard(df: pd.DataFrame) -> pd.DataFrame:
    """フィルター後の DataFrame を返し、表・KPI・グラフを描画する。"""
    with st.sidebar:
        st.markdown("### 🔍 データフィルター")
        if "Player_ID" in df.columns:
            players = ["全員"] + sorted(df["Player_ID"].dropna().astype(str).unique().tolist())
            selected_player = st.selectbox("Player_ID", players, key="filter_player_id")
        else:
            selected_player = "全員"

        if "Event_Type" in df.columns:
            events = ["全て"] + sorted(df["Event_Type"].dropna().astype(str).unique().tolist())
            selected_event = st.selectbox("Event_Type", events, key="filter_event_type")
        else:
            selected_event = "全て"

        if "Target_Object" in df.columns:
            targets = ["全て"] + sorted(df["Target_Object"].dropna().astype(str).unique().tolist())
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

    if "Data_Value" in filtered.columns:
        vals = filtered["Data_Value"].replace(0, pd.NA).dropna()
        c3.metric("Data_Value平均", f"{vals.mean():.2f}" if not vals.empty else "N/A")
        c4.metric("Data_Value最大", f"{vals.max():.2f}" if not vals.empty else "N/A")
        c5.metric("Data_Value最小", f"{vals.min():.2f}" if not vals.empty else "N/A")
    else:
        c3.metric("Data_Value平均", "N/A")
        c4.metric("Data_Value最大", "N/A")
        c5.metric("Data_Value最小", "N/A")

    try:
        st.markdown('<div class="section-header">📈 Data_Value / Event_Type</div>', unsafe_allow_html=True)
        a, b = st.columns(2)
        with a:
            if "Data_Value" in filtered.columns and "Elapsed_Time" in filtered.columns:
                plot_df = filtered.dropna(subset=["Data_Value", "Elapsed_Time"]).copy()
                if not plot_df.empty:
                    fig = px.line(
                        plot_df,
                        x="Elapsed_Time",
                        y="Data_Value",
                        color="Event_Type" if "Event_Type" in plot_df.columns else None,
                        color_discrete_map=EVENT_COLORS,
                        title="Elapsed_Time に対する Data_Value",
                        labels={
                            "Elapsed_Time": "Elapsed_Time",
                            "Data_Value": "Data_Value",
                            "Event_Type": "Event_Type",
                        },
                    )
                    fig.update_layout(height=350)
                    st.plotly_chart(fig, use_container_width=True)
        with b:
            if "Event_Type" in filtered.columns and "Data_Value" in filtered.columns:
                avg = (
                    filtered.dropna(subset=["Data_Value"])
                    .groupby("Event_Type")["Data_Value"]
                    .mean()
                    .reset_index()
                )
                avg = avg[avg["Event_Type"].astype(str) != "None"]
                if not avg.empty:
                    fig = px.bar(
                        avg,
                        x="Event_Type",
                        y="Data_Value",
                        color="Event_Type",
                        color_discrete_map=EVENT_COLORS,
                        title="Event_Type別 Data_Value平均",
                        labels={"Event_Type": "Event_Type", "Data_Value": "Data_Value"},
                    )
                    fig.update_layout(height=350, showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.warning(f"Data_Value / Event_Type グラフエラー: {e}")
        logger.exception("chart error")

    try:
        c, d = st.columns(2)
        with c:
            st.markdown('<div class="section-header">👤 Player_ID別 Data_Value</div>', unsafe_allow_html=True)
            if "Player_ID" in filtered.columns and "Data_Value" in filtered.columns:
                avg = (
                    filtered.dropna(subset=["Data_Value"])
                    .groupby("Player_ID")["Data_Value"]
                    .mean()
                    .reset_index()
                )
                if not avg.empty:
                    fig = px.bar(
                        avg,
                        x="Player_ID",
                        y="Data_Value",
                        color="Player_ID",
                        title="Player_ID別 Data_Value平均",
                        labels={"Player_ID": "Player_ID", "Data_Value": "Data_Value"},
                    )
                    fig.update_layout(height=350, showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)
        with d:
            st.markdown('<div class="section-header">📍 Target_Object × Event_Type</div>', unsafe_allow_html=True)
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
        st.markdown('<div class="section-header">📊 Event_Type / Target_Object 集計</div>', unsafe_allow_html=True)
        e, f = st.columns(2)
        with e:
            if "Event_Type" in filtered.columns:
                counts = (
                    filtered[
                        filtered["Event_Type"].notna() & (filtered["Event_Type"].astype(str) != "None")
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

        if "Data_Value" in filtered.columns:
            dv = filtered.copy()
            dv["Data_Value"] = pd.to_numeric(dv["Data_Value"], errors="coerce")
            dv = dv[dv["Data_Value"].notna()]
            if not dv.empty:
                x_col = "Elapsed_Time" if "Elapsed_Time" in dv.columns else None
                if x_col is None:
                    dv = dv.reset_index()
                    x_col = "index"
                fig = px.line(
                    dv,
                    x=x_col,
                    y="Data_Value",
                    color="Event_Type" if "Event_Type" in dv.columns else None,
                    color_discrete_map=EVENT_COLORS,
                    title="Data_Valueの推移",
                    labels={
                        x_col: "Elapsed_Time" if x_col == "Elapsed_Time" else "index",
                        "Data_Value": "Data_Value",
                        "Event_Type": "Event_Type",
                    },
                )
                fig.update_layout(height=350)
                st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.warning(f"集計グラフエラー: {e}")

    return filtered
