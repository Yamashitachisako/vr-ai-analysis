from datetime import datetime
from io import BytesIO

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
FONT = "HeiseiKakuGo-W5"


def _value_series(df: pd.DataFrame) -> pd.Series:
    col = "data_value" if "data_value" in df.columns else "reaction_time"
    if col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce").dropna()


def _target_column(df: pd.DataFrame) -> str | None:
    if "target_object" in df.columns:
        return "target_object"
    if "location" in df.columns:
        return "location"
    return None


def build_analysis_comment(df: pd.DataFrame) -> str:
    total = len(df)
    parts = [f"総レコード数は {total} 件です。"]

    if "event_type" in df.columns:
        event_counts = (
            df[df["event_type"].notna() & (df["event_type"].astype(str) != "None")]["event_type"]
            .value_counts()
        )
        if not event_counts.empty:
            top_event = event_counts.index[0]
            parts.append(f"最も多い Event_Type は「{top_event}」（{event_counts.iloc[0]} 件）です。")

    target_col = _target_column(df)
    if target_col:
        target_counts = (
            df[df[target_col].notna() & (df[target_col].astype(str) != "None")][target_col]
            .value_counts()
        )
        if not target_counts.empty:
            top_target = target_counts.index[0]
            parts.append(f"最も多い Target_Object は「{top_target}」（{target_counts.iloc[0]} 件）です。")

    values = _value_series(df)
    if not values.empty:
        parts.append(
            f"Data_Value の平均は {values.mean():.2f}、"
            f"最小 {values.min():.2f}、最大 {values.max():.2f} です。"
        )

    parts.append("本レポートは VR 研修 CSV データから自動生成されました。")
    return " ".join(parts)


def _make_table(data: list[list[str]], col_widths: list[float]) -> Table:
    table = Table(data, colWidths=col_widths)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4a90d9")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, -1), FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f8")]),
            ]
        )
    )
    return table


def build_pdf_bytes(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Normal"], fontName=FONT, fontSize=16, spaceAfter=12)
    heading_style = ParagraphStyle("heading", parent=styles["Normal"], fontName=FONT, fontSize=12, spaceAfter=8)
    body_style = ParagraphStyle("body", parent=styles["Normal"], fontName=FONT, fontSize=10, spaceAfter=6)

    elements = [
        Paragraph("VR保育研修 分析レポート", title_style),
        Paragraph(f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}", body_style),
        Spacer(1, 8),
    ]

    elements.append(Paragraph("基本情報", heading_style))
    summary_data = [["項目", "値"], ["総レコード数", str(len(df))]]
    if "event_type" in df.columns:
        event_rows = df[df["event_type"].notna() & (df["event_type"].astype(str) != "None")]
        summary_data.append(["イベント件数", str(len(event_rows))])
    elements.append(_make_table(summary_data, [120, 280]))
    elements.append(Spacer(1, 12))

    if "event_type" in df.columns:
        elements.append(Paragraph("Event_Type 別件数", heading_style))
        event_count = (
            df[df["event_type"].notna() & (df["event_type"].astype(str) != "None")]
            .groupby("event_type")
            .size()
            .reset_index(name="件数")
        )
        event_table = [["Event_Type", "件数"]]
        for _, row in event_count.iterrows():
            event_table.append([str(row["event_type"]), str(row["件数"])])
        if len(event_table) == 1:
            event_table.append(["（データなし）", "0"])
        elements.append(_make_table(event_table, [200, 200]))
        elements.append(Spacer(1, 12))

    target_col = _target_column(df)
    if target_col:
        elements.append(Paragraph("Target_Object 別件数", heading_style))
        target_count = (
            df[df[target_col].notna() & (df[target_col].astype(str) != "None")]
            .groupby(target_col)
            .size()
            .reset_index(name="件数")
        )
        target_table = [["Target_Object", "件数"]]
        for _, row in target_count.iterrows():
            target_table.append([str(row[target_col]), str(row["件数"])])
        if len(target_table) == 1:
            target_table.append(["（データなし）", "0"])
        elements.append(_make_table(target_table, [200, 200]))
        elements.append(Spacer(1, 12))

    values = _value_series(df)
    if not values.empty:
        elements.append(Paragraph("Data_Value 基本統計", heading_style))
        stats_table = [
            ["統計量", "値"],
            ["件数", str(len(values))],
            ["平均", f"{values.mean():.2f}"],
            ["最小", f"{values.min():.2f}"],
            ["最大", f"{values.max():.2f}"],
            ["標準偏差", f"{values.std():.2f}"],
        ]
        elements.append(_make_table(stats_table, [200, 200]))
        elements.append(Spacer(1, 12))

    elements.append(Paragraph("分析コメント", heading_style))
    elements.append(Paragraph(build_analysis_comment(df), body_style))

    doc.build(elements)
    buf.seek(0)
    return buf.getvalue()


def create_pdf_report(df, output_path="data/output/vr_analysis_report.pdf"):
    """ローカル保存用（後方互換）。"""
    import os

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(build_pdf_bytes(df))
    return output_path
