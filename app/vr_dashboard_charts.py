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


def player_id_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Player_ID ごとの件数と数値列の有効件数。"""
    if "Player_ID" not in df.columns or df.empty:
        return pd.DataFrame(columns=["Player_ID", "件数"])

    tmp = df.copy()
    tmp["Player_ID"] = tmp["Player_ID"].astype(str)
    summary = (
        tmp.groupby("Player_ID", dropna=False)
        .size()
        .reset_index(name="件数")
        .sort_values("Player_ID", key=lambda s: s.str.lower())
    )

    value_col = value_column(df)
    if value_col and value_col in tmp.columns:
        nums = pd.to_numeric(tmp[value_col], errors="coerce")
        tmp["_num"] = nums
        valid = (
            tmp.dropna(subset=["_num"])
            .groupby("Player_ID")
            .size()
            .rename(f"{value_col}有効件数")
        )
        summary = summary.merge(valid, on="Player_ID", how="left")
        summary[f"{value_col}有効件数"] = (
            summary[f"{value_col}有効件数"].fillna(0).astype(int)
        )
        summary["数値データ"] = summary[f"{value_col}有効件数"].apply(
            lambda n: "あり" if n > 0 else "データなし"
        )
    else:
        summary["数値データ"] = "列なし"
    return summary.reset_index(drop=True)


def player_value_chart_frame(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """全 Player_ID を残し、数値欠損は NaN のまま（グラフでデータなし表示用）。"""
    players = sorted(
        df["Player_ID"].dropna().astype(str).unique().tolist(),
        key=lambda x: x.lower(),
    )
    tmp = df.copy()
    tmp["Player_ID"] = tmp["Player_ID"].astype(str)
    tmp[value_col] = pd.to_numeric(tmp[value_col], errors="coerce")
    avg = tmp.groupby("Player_ID")[value_col].mean()
    out = pd.DataFrame({"Player_ID": players})
    out[value_col] = out["Player_ID"].map(avg)
    out["表示用"] = out[value_col].apply(
        lambda v: float(v) if pd.notna(v) else 0.0
    )
    out["状態"] = out[value_col].apply(
        lambda v: "数値あり" if pd.notna(v) else "データなし"
    )
    return out


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

    # --- Player_ID 件数サマリーを先に表示 ---
    st.markdown('<div class="section-header">👥 Player_ID 件数サマリー</div>', unsafe_allow_html=True)
    summary = player_id_summary(filtered)
    if not summary.empty:
        st.dataframe(summary, use_container_width=True, hide_index=True)
        logger.info("Player_ID summary: %s", summary.to_dict(orient="records"))
        st.caption(
            "先頭行が特定の Player_ID（例: ota）でも、上表で全員分の件数を確認できます。"
        )
        if value_col and f"{value_col}有効件数" in summary.columns:
            no_num = summary[summary[f"{value_col}有効件数"] == 0]["Player_ID"].tolist()
            if no_num:
                st.info(
                    f"{value_col} が空/None/非数値のためグラフ平均に乗らない Player_ID: "
                    + ", ".join(no_num)
                )
    else:
        st.caption("Player_ID 列がありません。")

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
    c_players = (
        filtered["Player_ID"].dropna().astype(str).nunique()
        if "Player_ID" in filtered.columns
        else 0
    )
    c3.metric("Player_ID種類", f"{c_players} 名")

    if value_col and value_col in filtered.columns:
        vals = pd.to_numeric(filtered[value_col], errors="coerce").dropna()
        label = value_col
        c4.metric(f"{label}平均", f"{vals.mean():.2f}" if not vals.empty else "N/A")
        c5.metric(f"{label}有効件数", f"{len(vals)} 件")
    else:
        c4.metric("数値列", "N/A")
        c5.metric("数値列", "N/A")

    try:
        st.markdown(
            f'<div class="section-header">📈 {value_col or "Value"} / Event_Type</div>',
            unsafe_allow_html=True,
        )
        a, b = st.columns(2)
        with a:
            if value_col and value_col in filtered.columns:
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
                else:
                    st.caption(f"{value_col} に有効数値がなく、折れ線はデータなしです。")
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
                else:
                    st.caption("Event_Type別平均: データなし")
    except Exception as e:
        st.warning(f"グラフエラー: {e}")
        logger.exception("chart error")

    try:
        c, d = st.columns(2)
        with c:
            st.markdown(
                f'<div class="section-header">👤 Player_ID別 {value_col or "件数"}</div>',
                unsafe_allow_html=True,
            )
            if "Player_ID" in filtered.columns:
                if value_col and value_col in filtered.columns:
                    chart_df = player_value_chart_frame(filtered, value_col)
                    fig = px.bar(
                        chart_df,
                        x="Player_ID",
                        y="表示用",
                        color="状態",
                        title=f"Player_ID別 {value_col}平均（全員表示）",
                        labels={
                            "Player_ID": "Player_ID",
                            "表示用": value_col,
                            "状態": "状態",
                        },
                        color_discrete_map={
                            "数値あり": "#4a90d9",
                            "データなし": "#bdc3c7",
                        },
                        hover_data={"状態": True, value_col: True, "表示用": False},
                    )
                    fig.update_layout(height=350)
                    st.plotly_chart(fig, use_container_width=True)
                    missing = chart_df[chart_df["状態"] == "データなし"]["Player_ID"].tolist()
                    if missing:
                        st.caption(
                            "データなし（灰）: "
                            + ", ".join(missing)
                            + f" ／ {value_col} が空・None・非数値です"
                        )
                else:
                    # 数値列が無い場合は件数で全員表示
                    counts = (
                        filtered.assign(Player_ID=filtered["Player_ID"].astype(str))
                        .groupby("Player_ID")
                        .size()
                        .reset_index(name="件数")
                        .sort_values("Player_ID", key=lambda s: s.str.lower())
                    )
                    fig = px.bar(
                        counts,
                        x="Player_ID",
                        y="件数",
                        color="Player_ID",
                        title="Player_ID別 件数（全員表示）",
                    )
                    fig.update_layout(height=350, showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("Player_ID 列がありません。")
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
                else:
                    st.caption("Target_Object × Event_Type: データなし")
    except Exception as e:
        st.warning(f"Player_ID / Target_Object グラフエラー: {e}")
        logger.exception("player chart error")

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
