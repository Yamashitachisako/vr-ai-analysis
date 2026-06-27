from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase import pdfmetrics
from datetime import datetime
import os

pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))


def create_pdf_report(df, output_path="data/output/vr_analysis_report.pdf"):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    c = canvas.Canvas(output_path, pagesize=A4)
    width, height = A4
    y = height - 25 * mm

    font = "HeiseiKakuGo-W5"

    c.setFont(font, 18)
    c.drawString(25 * mm, y, "VR分析レポート")

    y -= 15 * mm
    c.setFont(font, 10)
    c.drawString(25 * mm, y, f"生成日時：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    y -= 15 * mm
    c.setFont(font, 13)
    c.drawString(25 * mm, y, "基本情報")

    y -= 8 * mm
    c.setFont(font, 10)
    c.drawString(25 * mm, y, f"行数：{len(df)}")

    y -= 6 * mm
    c.drawString(25 * mm, y, f"列数：{len(df.columns)}")

    if "reaction_time" in df.columns:
        y -= 12 * mm
        avg_reaction = df["reaction_time"].dropna().mean()

        c.setFont(font, 13)
        c.drawString(25 * mm, y, "反応時間分析")

        y -= 8 * mm
        c.setFont(font, 10)
        c.drawString(25 * mm, y, f"平均反応時間：{avg_reaction:.2f}")

    if "event_type" in df.columns and "reaction_time" in df.columns:
        y -= 12 * mm
        c.setFont(font, 13)
        c.drawString(25 * mm, y, "事象別 平均反応時間")

        event_summary = df.groupby("event_type")["reaction_time"].mean()

        y -= 8 * mm
        c.setFont(font, 10)

        for event_type, value in event_summary.items():
            c.drawString(25 * mm, y, f"{event_type}：{value:.2f}")
            y -= 6 * mm

    y -= 10 * mm
    c.setFont(font, 13)
    c.drawString(25 * mm, y, "コメント")

    y -= 8 * mm
    c.setFont(font, 10)
    c.drawString(25 * mm, y, "これはVR研修CSVデータから自動生成された試作版レポートです。")

    c.save()
    return output_path