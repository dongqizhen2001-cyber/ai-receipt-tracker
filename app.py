import os
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["FLAGS_enable_pir_in_executor"] = "0"
os.environ["FLAGS_use_onednn"] = "0"
import streamlit as st
import json
import re
from io import BytesIO
import pandas as pd
import plotly.express as px
import os
import time
import sqlite3
import tempfile
import hashlib
from pathlib import Path
from datetime import datetime
from PIL import Image
import calendar

class SpendCalendar(calendar.HTMLCalendar):
    def __init__(self, spends_dict):
        super().__init__(calendar.SUNDAY)
        self.spends_dict = spends_dict

    def formatday(self, day, weekday):
        if day == 0:
            return '<td style="background-color:#fafafa; border:1px solid #ddd;"></td>'
        amount = self.spends_dict.get(day, 0)
        daily_color = "#ffebee" if amount > 0 else "#fafafa"
        border_color = "#f44336" if amount > 0 else "#eee"
        text_color = "#d32f2f" if amount > 0 else "#ccc"
        amount_str = f"${amount:.2f}" if amount > 0 else "-"
        day_color = "#333" if amount > 0 else "#999"
        
        return f'<td style="background-color:{daily_color}; border:1px solid {border_color}; padding:14px; width:14%; text-align:center; vertical-align:top; border-radius:6px;">' \
               f'<div style="font-weight:bold; font-size:18px; color:{day_color};">{day}</div>' \
               f'<div style="color:{text_color}; font-size:16px; margin-top:6px; font-weight:bold;">{amount_str}</div>' \
               f'</td>'

    def formatweekheader(self):
        week_days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        cells = ''.join(f'<th style="padding:10px; background-color:#f0f2f6; border:1px solid #ddd; text-align:center; font-weight:bold; font-size:16px; color:#444;">{day}</th>' for day in week_days)
        return f'<tr>{cells}</tr>'

    def formatmonth(self, theyear, themonth, withyear=True):
        html = ['<table style="width:100%; border-collapse:collapse; margin-bottom:20px; font-family:sans-serif; box-shadow: 0 2px 5px rgba(0,0,0,0.05);">']
        html.append(self.formatweekheader())
        for week in self.monthdays2calendar(theyear, themonth):
            html.append('<tr>')
            for day, weekday in week:
                html.append(self.formatday(day, weekday))
            html.append('</tr>')
        html.append('</table>')
        return '\n'.join(html)

DB_PATH = Path("finance.db")
SETTINGS_PATH = Path("app_settings.json")
LOW_CONFIDENCE_THRESHOLD = 0.35  
LANG_OPTIONS = {
    "簡體中文": "ch",
    "繁體中文": "chinese_cht",
    "English": "en",
}
DEFAULT_LLM_MODELS = {
    "doubao": "doubao-pro-32k",
}
DOUBAO_ENDPOINT_ID = "ep-20260527114325-9bcwm"
def rotate_image_bytes(file_bytes, angle):
    """Rotate uploaded images before OCR when needed."""
    if angle == 0:
        return file_bytes
    img = Image.open(BytesIO(file_bytes))
    # PIL rotates counter-clockwise; use negative for clockwise and expand to avoid cropping.
    rotated_img = img.rotate(-angle, expand=True)
    buf = BytesIO()
    # Save as PNG for consistent OCR input.
    rotated_img.save(buf, format="PNG")
    return buf.getvalue()

def get_conn():
    return sqlite3.connect(DB_PATH)


def load_local_settings():
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_local_settings(settings):
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def get_saved_api_key(provider):
    settings = load_local_settings()
    return settings.get(f"{provider}_api_key", "")


def save_api_key(provider, api_key):
    settings = load_local_settings()
    settings[f"{provider}_api_key"] = api_key.strip()
    save_local_settings(settings)


def clear_saved_api_key(provider):
    settings = load_local_settings()
    settings.pop(f"{provider}_api_key", None)
    save_local_settings(settings)


def get_saved_doubao_base_url():
    settings = load_local_settings()
    return settings.get("doubao_base_url", "https://ark.cn-beijing.volces.com/api/v3")


def save_doubao_base_url(base_url):
    settings = load_local_settings()
    settings["doubao_base_url"] = base_url.strip()
    save_local_settings(settings)


# --- Database initialization ---
def init_db():
    conn = get_conn()
    c = conn.cursor()
    # Store: date, amount, payment method, calories, raw JSON
    c.execute('''CREATE TABLE IF NOT EXISTS records
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  date TEXT, 
                  amount REAL, 
                  method TEXT, 
                  calories INTEGER, 
                  raw_json TEXT)''')

    # Backward compatible: add upload date for daily stats.
    cols = [row[1] for row in c.execute("PRAGMA table_info(records)").fetchall()]
    if "upload_date" not in cols:
        c.execute("ALTER TABLE records ADD COLUMN upload_date TEXT")
    conn.commit()
    conn.close()


def get_record_count():
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    finally:
        conn.close()


def save_record(record_date, amount, method, calories, data):
    upload_date = datetime.today().strftime("%Y-%m-%d")
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO records (date, amount, method, calories, raw_json, upload_date) VALUES (?, ?, ?, ?, ?, ?)",
            (record_date, amount, method, calories, json.dumps(data, ensure_ascii=False), upload_date),
        )
        conn.commit()
    finally:
        conn.close()


def get_calorie_history():
    query = """
        SELECT COALESCE(date, upload_date) AS stat_date,
               SUM(COALESCE(calories, 0)) AS total_calories,
               COUNT(*) AS receipt_count
        FROM records
        GROUP BY COALESCE(date, upload_date)
        ORDER BY COALESCE(date, upload_date)
    """
    conn = get_conn()
    try:
        df = pd.read_sql_query(query, conn)
    finally:
        conn.close()
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["stat_date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    df["total_calories"] = pd.to_numeric(df["total_calories"], errors="coerce").fillna(0).astype(int)
    df["receipt_count"] = pd.to_numeric(df["receipt_count"], errors="coerce").fillna(0).astype(int)
    return df


def get_spending_history():
    query = """
        SELECT id,
               date AS receipt_date,
               COALESCE(upload_date, date) AS upload_date,
               amount,
               method,
               calories
        FROM records
        ORDER BY COALESCE(date, upload_date) DESC, id DESC
    """
    conn = get_conn()
    try:
        df = pd.read_sql_query(query, conn)
    finally:
        conn.close()

    if df.empty:
        return df

    df["upload_date"] = pd.to_datetime(df["upload_date"], errors="coerce")
    df["receipt_date"] = pd.to_datetime(df["receipt_date"], errors="coerce")
    df = df.dropna(subset=["upload_date"]).copy()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["calories"] = pd.to_numeric(df["calories"], errors="coerce").fillna(0).astype(int)
    df["method"] = df["method"].fillna("Unknown")
    return df


def get_today_receipt_totals():
    today = datetime.today().strftime("%Y-%m-%d")
    query = """
        SELECT SUM(COALESCE(amount, 0)) AS total_amount,
               SUM(COALESCE(calories, 0)) AS total_calories
        FROM records
        WHERE COALESCE(date, upload_date) = ?
    """
    conn = get_conn()
    try:
        row = conn.execute(query, (today,)).fetchone()
    finally:
        conn.close()
    total_amount = float(row[0] or 0)
    total_calories = int(row[1] or 0)
    return total_amount, total_calories


def build_exercise_plan(calories_today, target_calories=2000):
    diff = calories_today - target_calories
    if diff <= 0:
        return {
            "status": "within",
            "message": f"預估攝取為 {calories_today} kcal，仍在建議範圍內。",
            "extra": 0,
            "plans": EXERCISE_OPTIONS,
        }

    extra = int(diff)
    return {
        "status": "over",
        "message": f"今天約超過目標 {extra} kcal，建議透過運動平衡。",
        "extra": extra,
        "plans": EXERCISE_OPTIONS,
    }


def build_exercise_table(calories_today, target_calories):
    plan = build_exercise_plan(calories_today, target_calories)
    rows = []

    if plan["status"] == "over":
        extra = int(plan["extra"])
        for item in plan["plans"]:
            mins = int((extra + item["kcal_per_min"] - 1) // item["kcal_per_min"])
            rows.append(
                {
                    "運動項目": item["name"],
                    "消耗（kcal/分鐘）": item["kcal_per_min"],
                    "建議分鐘": mins,
                    "建議原因": "平衡今日攝取",
                }
            )
    else:
        for item in plan["plans"]:
            rows.append(
                {
                    "運動項目": item["name"],
                    "消耗（kcal/分鐘）": item["kcal_per_min"],
                    "建議分鐘": 20 if item["name"] == "快走" else 15,
                    "建議原因": "維持輕量活動",
                }
            )

    return pd.DataFrame(rows)


CALORIE_KEYWORDS = [
    ("burger", 550),
    ("hamburger", 550),
    ("fries", 320),
    ("cola", 150),
    ("coke", 150),
    ("milk tea", 280),
    ("coffee", 5),
    ("latte", 180),
    ("pizza", 285),
    ("pasta", 600),
    ("noodles", 350),
    ("rice", 260),
    ("salad", 150),
    ("egg", 80),
    ("chicken", 240),
    ("beef", 300),
    ("pork", 280),
    ("fish", 200),
    ("tea", 0),
    ("soup", 120),
    ("sandwich", 320),
    ("toast", 120),
    ("sushi", 300),
    ("dumpling", 250),
    ("cake", 350),
    ("cookie", 80),
    ("ice cream", 200),
    ("漢堡", 550),
    ("薯條", 320),
    ("可樂", 150),
    ("奶茶", 280),
    ("咖啡", 5),
    ("披薩", 285),
    ("意粉", 600),
    ("麵", 350),
    ("飯", 260),
    ("沙律", 150),
    ("蛋", 80),
    ("雞", 240),
    ("牛", 300),
    ("豬", 280),
    ("魚", 200),
    ("湯", 120),
    ("三明治", 320),
    ("吐司", 120),
    ("壽司", 300),
    ("水餃", 250),
    ("蛋糕", 350),
    ("雪糕", 200),
]

EXERCISE_OPTIONS = [
    {"name": "快走", "kcal_per_min": 5},
    {"name": "慢跑", "kcal_per_min": 10},
    {"name": "騎腳踏車", "kcal_per_min": 8},
    {"name": "跳繩", "kcal_per_min": 12},
]


def enrich_calories(items):
    if not items:
        return items

    enriched = []
    for item in items:
        name = str(item.get("name", "") or "")
        name_lower = name.lower()
        try:
            qty = float(item.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 1
        qty = max(qty, 1)

        try:
            current = float(item.get("calories_estimate") or 0)
        except (TypeError, ValueError):
            current = 0

        if current <= 0:
            estimate = 0
            for keyword, kcal in CALORIE_KEYWORDS:
                if keyword in name_lower or keyword in name:
                    estimate = int(kcal * qty)
                    break
            item["calories_estimate"] = estimate

        enriched.append(item)
    return enriched


def render_health_section(current_total_calories=0):
    st.markdown("---")
    st.subheader("🏃 健康與運動建議")

    target_calories = st.slider(
        "每日熱量目標（kcal）",
        min_value=1200,
        max_value=3200,
        value=2000,
        step=50,
        help="可依增肌或減脂目標調整",
    )

    history_df = get_calorie_history()
    if history_df.empty:
        st.info("目前還沒有歷史資料，請先上傳收據以產生熱量趨勢與建議。")
        return

    today = pd.Timestamp(datetime.today().date())
    today_row = history_df[history_df["date"] == today]
    history_today_cal = int(today_row["total_calories"].iloc[0]) if not today_row.empty else 0
    today_amount, today_calories_db = get_today_receipt_totals()
    calories_today = max(history_today_cal, today_calories_db, int(current_total_calories or 0))

    if today_row.empty and calories_today == 0:
        st.info("今天尚未有收據，因此暫時沒有運動建議。")
        return

    avg_7d = float(history_df.tail(7)["total_calories"].mean()) if not history_df.empty else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📅 今日總攝取", f"{calories_today} kcal")
    col2.metric("📊 7 日平均", f"{avg_7d:.0f} kcal")
    col3.metric("🎯 每日目標", f"{target_calories} kcal")
    col4.metric("💵 今日花費（依收據日期）", f"")

    chart_df = history_df.copy()
    chart_df["date_str"] = chart_df["date"].dt.strftime("%Y-%m-%d")
    fig = px.line(
        chart_df,
        x="date_str",
        y="total_calories",
        markers=True,
        title="每日熱量攝取趨勢",
        labels={"date_str": "日期", "total_calories": "總熱量（kcal）"},
    )
    fig.add_hline(y=target_calories, line_dash="dash", line_color="orange")
    st.plotly_chart(fig, width="stretch")

    plan = build_exercise_plan(calories_today, target_calories)
    st.info(plan["message"])

    st.write("運動建議表")
    st.table(build_exercise_table(calories_today, target_calories))

    curve_df = history_df.copy()
    curve_df["date_str"] = curve_df["date"].dt.strftime("%Y-%m-%d")
    curve_df = (
        curve_df.groupby("date_str", as_index=False)
        .agg(total_calories=("total_calories", "sum"))
        .sort_values("date_str")
    )
    curve_fig = px.line(
        curve_df,
        x="date_str",
        y="total_calories",
        markers=True,
        title="卡路里曲線圖",
        labels={"date_str": "日期", "total_calories": "總熱量（kcal）"},
    )
    curve_fig.add_hline(y=target_calories, line_dash="dash", line_color="orange")
    st.plotly_chart(curve_fig, width="stretch")

    if plan["status"] == "over":
        st.write("可任選一種運動，並依建議時間進行。")
    else:
        st.success("今天進行輕量活動即可：散步 20–30 分鐘有助恢復。")


def render_history_section():
    st.markdown("---")
    st.subheader("📚 花費歷史")

    history_df = get_spending_history()
    if history_df.empty:
        st.info("目前還沒有歷史資料，上傳收據後就會在這裡顯示。")
        return

    min_date = history_df["receipt_date"].min().date()
    max_date = history_df["receipt_date"].max().date()

    col_filter1, col_filter2 = st.columns([1, 1])
    with col_filter1:
        date_range = st.date_input(
            "日期範圍",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
    with col_filter2:
        methods = sorted(history_df["method"].dropna().unique().tolist())
        selected_methods = st.multiselect(
            "付款方式",
            options=methods,
            default=methods,
        )

    filtered_df = history_df.copy()

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date = pd.Timestamp(date_range[0])
        end_date = pd.Timestamp(date_range[1])
        filtered_df = filtered_df[
            (filtered_df["receipt_date"] >= start_date) & (filtered_df["receipt_date"] <= end_date)
        ]

    if selected_methods:
        filtered_df = filtered_df[filtered_df["method"].isin(selected_methods)]
    else:
        filtered_df = filtered_df.iloc[0:0]

    if filtered_df.empty:
        st.warning("目前篩選條件下沒有資料，請調整篩選條件。")
        return

    total_amount = float(filtered_df["amount"].sum())
    total_calories = int(filtered_df["calories"].sum())
    receipt_count = int(filtered_df.shape[0])
    avg_amount = total_amount / receipt_count if receipt_count else 0

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("🧾 收據", f"{receipt_count}")
    col_m2.metric("💵 總花費", f"${total_amount:.2f}")
    col_m3.metric("🍱 總熱量", f"{total_calories} kcal")
    col_m4.metric("📉 每張平均", f"${avg_amount:.2f}")

    daily_df = (
        filtered_df.groupby(filtered_df["receipt_date"].dt.date, as_index=False)
        .agg(amount=("amount", "sum"), calories=("calories", "sum"), count=("id", "count"))
        .sort_values("receipt_date")
    )
    daily_df["date_str"] = pd.to_datetime(daily_df["receipt_date"]).dt.strftime("%Y-%m-%d")

    st.markdown("#### 📅 2026 每日花費月曆")
    daily_df["year_month"] = pd.to_datetime(daily_df["receipt_date"]).dt.to_period("M")
    
    all_months_2026 = pd.period_range(start="2026-01", end="2026-12", freq="M")
    
    tabs = st.tabs([ym.strftime("%b %Y") for ym in all_months_2026])
    for tab, ym in zip(tabs, all_months_2026):
        with tab:
            month_data = daily_df[daily_df["year_month"] == ym]
            if not month_data.empty:
                day_spends = month_data.groupby(pd.to_datetime(month_data["receipt_date"]).dt.day)["amount"].sum().to_dict()
            else:
                day_spends = {}
            cal = SpendCalendar(day_spends)
            html_cal = cal.formatmonth(ym.year, ym.month)
            st.markdown(html_cal, unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    st.markdown("#### 📌 選擇日期查看明細")
    default_day = history_df["receipt_date"].dropna().max().date()
    selected_day = st.date_input(
        "收據日期",
        value=default_day,
        min_value=min_date,
        max_value=max_date,
    )

    day_df = history_df[history_df["receipt_date"].dt.date == selected_day].copy()
    day_amount = float(day_df["amount"].sum()) if not day_df.empty else 0.0
    day_calories = int(day_df["calories"].sum()) if not day_df.empty else 0
    day_count = int(day_df.shape[0]) if not day_df.empty else 0

    tab_spend, tab_health = st.tabs(["📒 每日花費", "🏃 每日熱量建議"])

    with tab_spend:
        st.caption(f"這一天共 {day_count} 張收據")
        c_day_1, c_day_2, c_day_3 = st.columns(3)
        c_day_1.metric("💵 總花費", f"${day_amount:.2f}")
        c_day_2.metric("🍱 總熱量", f"{day_calories} kcal")
        c_day_3.metric("🧾 收據", f"{day_count}")

        if day_df.empty:
            st.info("這一天沒有收據。")
        else:
            day_table = day_df.copy().sort_values("receipt_date", ascending=False)
            day_table["upload_date"] = day_table["upload_date"].dt.strftime("%Y-%m-%d")
            day_table["receipt_date"] = day_table["receipt_date"].dt.strftime("%Y-%m-%d")
            day_table = day_table[["upload_date", "receipt_date", "amount", "method", "calories"]]
            day_table.columns = ["上傳日期", "收據日期", "金額", "付款方式", "熱量（kcal）"]
            st.dataframe(day_table, width="stretch", hide_index=True)

    with tab_health:
        target_calories = st.slider(
            "每日熱量目標（kcal）",
            min_value=1200,
            max_value=3200,
            value=2000,
            step=50,
            help="可依增肌或減脂目標調整",
        )
        if day_count == 0:
            st.info("這一天沒有收據，因此暫時沒有建議。")
        plan = build_exercise_plan(day_calories, target_calories)
        st.info(plan["message"])
        st.write("運動建議表")
        st.table(build_exercise_table(day_calories, target_calories))

        if day_count > 0:
            day_curve_df = history_df.copy()
            day_curve_df["date_str"] = day_curve_df["receipt_date"].dt.strftime("%Y-%m-%d")
            day_curve_df = (
                day_curve_df.groupby("date_str", as_index=False)
                .agg(total_calories=("calories", "sum"))
                .sort_values("date_str")
            )
            day_curve_fig = px.line(
                day_curve_df,
                x="date_str",
                y="total_calories",
                markers=True,
                title="卡路里曲線圖",
                labels={"date_str": "日期", "total_calories": "總熱量（kcal）"},
            )
            day_curve_fig.add_hline(y=target_calories, line_dash="dash", line_color="orange")
            st.plotly_chart(day_curve_fig, width="stretch")

        if plan["status"] == "over":
            st.write("可任選一種運動，並依建議時間進行。")
        else:
            st.success("今天進行輕量活動即可：散步 20–30 分鐘有助恢復。")

    c1, c2 = st.columns(2)
    with c1:
        fig_amount = px.line(
            daily_df,
            x="date_str",
            y="amount",
            markers=True,
            title="每日花費趨勢",
            labels={"date_str": "日期", "amount": "金額"},
        )
        st.plotly_chart(fig_amount, width="stretch")

    with c2:
        method_df = (
            filtered_df.groupby("method", as_index=False)
            .agg(amount=("amount", "sum"))
            .sort_values("amount", ascending=False)
        )
        fig_method = px.bar(
            method_df,
            x="method",
            y="amount",
            title="依付款方式統計花費",
            labels={"method": "付款方式", "amount": "金額"},
        )
        st.plotly_chart(fig_method, width="stretch")

    table_df = filtered_df.copy().sort_values("receipt_date", ascending=False)
    table_df["upload_date"] = table_df["upload_date"].dt.strftime("%Y-%m-%d")
    table_df["receipt_date"] = table_df["receipt_date"].dt.strftime("%Y-%m-%d")
    table_df = table_df[["upload_date", "receipt_date", "amount", "method", "calories"]]
    table_df.columns = ["上傳日期", "收據日期", "金額", "付款方式", "熱量（kcal）"]

    st.write("收據明細")
    st.dataframe(table_df, width="stretch", hide_index=True)

    csv_data = table_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ 匯出目前結果（CSV）",
        data=csv_data,
        file_name="spending_history.csv",
        mime="text/csv",
        width="stretch",
    )


def parse_deepseek_json(raw_json):
    cleaned = raw_json.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
    return json.loads(cleaned)


def _extract_amount_fallback(records, clean_text):
    """Fallback amount extraction with context to avoid balance/card numbers."""
    lines = [str(item.get("text", "")).strip() for item in records if str(item.get("text", "")).strip()]
    amounts = []
    
    for i, line in enumerate(lines):
        # Combine previous line to avoid OCR splitting balance and numbers.
        context = line.lower()
        if i > 0:
            context = lines[i-1].lower() + " " + context
            
        # Skip balance/card related lines.
        if any(x in context for x in ["card", "卡號", "机号", "機號", "balance", "餘額", "余额", "point", "積分"]):
            continue
            
        match = re.search(r"\$?(\d+\.\d{1,2})", line)
        if match:
            val = float(match.group(1))
            if 0 < val < 1000:
                amounts.append(val)
                
    # Prefer values near payment method keywords.
    for i, line in enumerate(lines):
        context = line.lower()
        if i > 0: context = lines[i-1].lower() + " " + context
        if any(x in context for x in ["八達通", "wechat", "alipay", "微信", "支付寶", "現金", "cash", "總計", "total", "实收"]):
            if not any(x in context for x in ["card", "卡號", "balance", "餘額", "余额"]):
                match = re.search(r"\$?(\d+\.\d{1,2})", line)
                if match:
                    val = float(match.group(1))
                    if 0 < val < 1000:
                        return round(val, 2)
                        
    if amounts:
        return round(min(amounts), 2)
    return None


@st.cache_resource(show_spinner=False)
def get_ocr_engine(lang="ch"):
    import ocr_test

    ocr_engine, _ = ocr_test.init_ocr(lang)
    return ocr_engine


def finalize_llm_from_clean_text(clean_text, records, api_key, model, base_url=None):
    import ocr_test

    os.environ["DOUBAO_API_KEY"] = api_key
    raw_json = ocr_test.call_doubao(clean_text, model, base_url=base_url, api_key=api_key)

    data = parse_deepseek_json(raw_json)

    current_amount = data.get("total_amount", 0)
    try:
        current_amount_num = float(current_amount)
    except (TypeError, ValueError):
        current_amount_num = 0.0

    fallback_amount = _extract_amount_fallback(records, clean_text)
    
    if fallback_amount is not None:
        delta_ratio = abs(current_amount_num - fallback_amount) / max(fallback_amount, 1)
        if current_amount_num <= 0 or current_amount_num > 10 * fallback_amount or delta_ratio > 0.2:
            print(
                f"[Amount fallback] AI amount ${current_amount_num} overridden to ${fallback_amount}."
            )
            data["total_amount"] = round(fallback_amount, 2)

    # Force payment method from text when possible to avoid hallucinations.
    text_lower = clean_text.lower()
    if "八達通" in text_lower or "octopus" in text_lower:
        data["payment_method"] = "Octopus"
    elif "wechat" in text_lower or "微信" in text_lower:
        data["payment_method"] = "WeChat Pay"
    elif "alipay" in text_lower or "支付寶" in text_lower:
        data["payment_method"] = "Alipay"
    elif "現金" in text_lower or "cash" in text_lower:
        data["payment_method"] = "Cash"

    return data


def process_receipt(
    file_bytes,
    api_key,
    model,
    base_url,
    lang,
    auto_deskew,
    auto_enhance,
    auto_zoom,
    allow_low_confidence=False,
):
    import ocr_test

    os.environ["DOUBAO_API_KEY"] = api_key
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        tmp.write(file_bytes)
        temp_path = tmp.name

    try:
        ocr_engine = get_ocr_engine(lang)
        result = ocr_test.robust_ocr(
            ocr_engine,
            temp_path,
            auto_deskew=auto_deskew,
            auto_enhance=auto_enhance,
            auto_zoom=auto_zoom,
        )
        records = ocr_test.normalize_result(result)
        clean_text = ocr_test.build_clean_text(records)
        avg_conf = ocr_test.average_confidence(records)

        if avg_conf < LOW_CONFIDENCE_THRESHOLD and not allow_low_confidence:
            return {
                "_low_confidence": True,
                "_confidence": avg_conf,
                "_clean_text": clean_text,
                "_records": records,
            }

        data = finalize_llm_from_clean_text(
            clean_text,
            records,
            api_key,
            model,
            base_url=base_url,
        )
        data["items"] = enrich_calories(data.get("items", []))
        data["_confidence"] = avg_conf
        return data
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

init_db() 

if "saved_api_keys" not in st.session_state:
    st.session_state["saved_api_keys"] = {
        "doubao": get_saved_api_key("doubao"),
    }
if "llm_models" not in st.session_state:
    st.session_state["llm_models"] = {
        "doubao": DEFAULT_LLM_MODELS.get("doubao", "doubao-pro-32k"),
    }
if "doubao_base_url" not in st.session_state:
    st.session_state["doubao_base_url"] = get_saved_doubao_base_url()
if "processed_receipts" not in st.session_state:
    st.session_state["processed_receipts"] = {}
if "last_failed_uploads" not in st.session_state:
    st.session_state["last_failed_uploads"] = {}
if "batch_records" not in st.session_state:
    st.session_state["batch_records"] = {}

st.set_page_config(page_title="Smart Expense Tracker", page_icon="🧾", layout="wide")

st.markdown('''
<style>
.stMarkdown p, .stText, body, [data-testid="stSidebar"] {
    font-size: 19px !important;
    font-weight: 500 !important;
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif !important;
}

[data-testid="stMetricValue"] {
    font-size: 40px !important;
    font-weight: 900 !important;
    color: #1E88E5 !important;
}
[data-testid="stMetricLabel"] {
    font-size: 22px !important;
    font-weight: 700 !important;
}

input, select {
    font-size: 18px !important;
    font-weight: 500 !important;
}

[data-testid="stDataFrame"], .col-header-text, .row-header-text {
    font-size: 18px !important;
    font-weight: 500 !important;
}

[data-testid="stExpander"] details summary {
    display: flex !important;
    align-items: center !important;
    font-size: 18px !important;
}
[data-testid="stExpander"] details summary p {
    font-size: 18px !important;
    margin: 0 !important;
    display: inline-block !important;
}

span.material-symbols-rounded {
    font-size: inherit !important;
}

@media (max-width: 768px) {
    [data-testid="column"] {
        flex: 1 1 100% !important;
        width: 100% !important;
    }
    .stButton > button {
        width: 100% !important;
        padding: 0.75rem 1rem !important;
        font-size: 18px !important;
    }
}
</style>
''', unsafe_allow_html=True)

st.markdown(
    """
    <link rel="manifest" href="/static/manifest.json" />
    <meta name="theme-color" content="#1e88e5" />
    <link rel="apple-touch-icon" href="/static/icon.svg" />
    """,
    unsafe_allow_html=True,
)

st.title("🧾 智能支出与健康追踪系统（畢業專題）")
st.markdown("---")

with st.sidebar:
    st.header("⚙️ 設定")
    provider = "doubao"
    saved_api_key = st.session_state.get("saved_api_keys", {}).get(provider, "")
    has_saved_key = bool(saved_api_key)

    if provider == "doubao":
        model_value = DOUBAO_ENDPOINT_ID
        st.text_input(
            "模型",
            value=model_value,
            disabled=True,
            help="固定為程式中的豆包 Endpoint ID",
        )
        st.session_state["llm_models"][provider] = model_value

    base_url_value = None
    if provider == "doubao":
        base_url_value = st.text_input(
            "豆包 API 基底網址",
            value=st.session_state.get("doubao_base_url", get_saved_doubao_base_url()),
            help="相容 Volcengine ARK 的基底網址，例如 https://ark.cn-beijing.volces.com/api/v3",
        ).strip()
        if base_url_value and base_url_value != st.session_state.get("doubao_base_url"):
            st.session_state["doubao_base_url"] = base_url_value
            save_doubao_base_url(base_url_value)

    use_saved_key = st.checkbox(
        "使用已儲存的 API 金鑰",
        value=has_saved_key,
        disabled=not has_saved_key,
        help="直接使用本機已儲存的金鑰，不必每次輸入",
    )

    manual_api_key = st.text_input(
        "暫時 API 金鑰（可選）",
        type="password",
        value="",
        help="若有輸入，會覆蓋已儲存的金鑰",
    )

    auto_save_manual_key = st.checkbox(
        "自動儲存上方金鑰",
        value=True,
        help="啟用後，上方金鑰會儲存在本機",
    )

    if auto_save_manual_key and manual_api_key.strip():
        incoming_key = manual_api_key.strip()
        if incoming_key != saved_api_key:
            save_api_key(provider, incoming_key)
            st.session_state["saved_api_keys"][provider] = incoming_key
            saved_api_key = incoming_key
            has_saved_key = True
            st.caption("已儲存在本機。")

    api_key = manual_api_key.strip() if manual_api_key.strip() else (saved_api_key if use_saved_key else "")

    with st.expander("🔐 API 金鑰管理", expanded=False):
        st.caption("新增、更新或刪除本機儲存的 API 金鑰")
        manage_key = st.text_input(
            "新增或更新 API 金鑰",
            type="password",
            value="",
            key="manage_api_key_input",
        )
        col_key_1, col_key_2 = st.columns(2)
        if col_key_1.button("儲存／更新", width="stretch"):
            if manage_key.strip():
                save_api_key(provider, manage_key.strip())
                st.session_state["saved_api_keys"][provider] = manage_key.strip()
                st.success("已儲存在本機。")
            else:
                st.warning("請輸入金鑰。")

        if col_key_2.button("刪除已儲存金鑰", width="stretch"):
            clear_saved_api_key(provider)
            st.session_state["saved_api_keys"][provider] = ""
            st.success("已刪除本機金鑰。")

        if st.session_state.get("saved_api_keys", {}).get(provider, ""):
            st.info("狀態：已找到儲存金鑰（豆包）")
            st.text_area(
                "已儲存的 API 金鑰（完整顯示）",
                value=st.session_state.get("saved_api_keys", {}).get(provider, ""),
                height=80,
                disabled=True,
            )
        else:
            st.info("狀態：沒有儲存金鑰（豆包）")
    st.divider()
    
    count = get_record_count()
    
    st.success(f"🗄️ 資料庫：**{count}** 筆記錄。")    
    with st.expander("⚠️ 危險區域 - 重設資料庫"):
        st.warning("這將刪除所有歷史記錄。")
        if st.button("🗑️ 刪除所有記錄", type="primary", width="stretch"):
            try:
                get_conn().close()
                if DB_PATH.exists():
                    os.remove(DB_PATH)
                st.session_state["processed_receipts"] = {}
                st.session_state["last_failed_uploads"] = {}
                st.success("✅ 已清空資料庫與快取。")
                st.rerun() 
            except Exception as e:
                st.error(f"重設失敗：{e}")
    st.info("💡 提示：OCR 使用 PaddleOCR，LLM 使用豆包，SQLite 用於本機儲存。")

    st.divider()
    st.subheader("🧩 OCR 語言")
    lang_label = st.selectbox(
        "選擇 OCR 語言",
        options=list(LANG_OPTIONS.keys()),
        index=0,
        help="繁體中文很適合香港收據",
    )
    ocr_lang = LANG_OPTIONS.get(lang_label, "ch")

llm_model = st.session_state.get("llm_models", {}).get(
    "doubao",
    DEFAULT_LLM_MODELS.get("doubao", ""),
)
llm_base_url = st.session_state.get("doubao_base_url")

st.subheader("📥 上傳收據")
st.caption("可拖放或選取多張圖片進行批次處理。")

receipt_items = []
uploaded_files = st.file_uploader(
    "📸 將收據拖到這裡，或點擊瀏覽（支援多選）",
    type=["png", "jpg", "jpeg"],
    accept_multiple_files=True,
    help="支援 PNG／JPG／JPEG 檔案",
)

if uploaded_files:
    receipt_items = [
        {"name": item.name, "bytes": item.getvalue()} for item in uploaded_files
    ]

current_total_calories = 0

if receipt_items:
    st.subheader("🤖 自動 OCR 與儲存")

    if not api_key:
        st.warning("⚠️ 請先在側欄輸入 API 金鑰再開始。")
    else:
        progress = st.progress(0, text="正在準備批次處理...")
        success_count = 0
        failed_count = 0
        reused_count = 0
        failed_uploads = {}

        for idx, item in enumerate(receipt_items, start=1):
            file_bytes = item["bytes"]
            file_name = item["name"]
            progress.progress(
                int((idx - 1) / len(receipt_items) * 100),
                text=f"正在處理 {idx}/{len(receipt_items)}：{file_name}",
            )

            with st.expander(f"收據 {idx}：{file_name}", expanded=(idx == 1)):
                st.caption("可選：在 OCR 前旋轉以獲得更佳結果。")
                auto_deskew = st.checkbox(
                    "自動校正傾斜（OCR 前）",
                    value=False,
                    key=f"deskew_{idx}_{file_name}",
                )
                auto_enhance = st.checkbox(
                    "低畫質增強（亮度／對比／銳利化）",
                    value=False,
                    key=f"enhance_{idx}_{file_name}",
                )
                auto_zoom = st.checkbox(
                    "自動放大（擴大收據區域）",
                    value=False,
                    key=f"zoom_{idx}_{file_name}",
                )
                display_zoom = st.slider(
                    "圖片顯示比例（%）",
                    min_value=30,
                    max_value=100,
                    value=70,
                    step=5,
                    key=f"display_zoom_{idx}_{file_name}",
                )
                rotate_angle = st.selectbox(
                    "OCR 前旋轉",
                    options=[0, 90, 180, 270],
                    index=0,
                    key=f"rotate_{idx}_{file_name}",
                )
                rotated_bytes = rotate_image_bytes(file_bytes, int(rotate_angle))
                hash_input = rotated_bytes + (
                    f"|deskew:{int(auto_deskew)}|enhance:{int(auto_enhance)}|zoom:{int(auto_zoom)}"
                ).encode("utf-8")
                file_hash = hashlib.sha256(hash_input).hexdigest()
                display_width = int(900 * (display_zoom / 100))
                st.image(rotated_bytes, width=display_width)

                cached = st.session_state["processed_receipts"].get(file_hash)
                if cached:
                    data = cached.get("data")
                    total_calories = int(cached.get("total_calories", 0))
                    saved = cached.get("saved", False)
                    reused_count += 1
                else:
                    with st.spinner(f"正在處理：{file_name}"):
                        try:
                            data = process_receipt(
                                rotated_bytes,
                                api_key,
                                llm_model,
                                llm_base_url,
                                ocr_lang,
                                auto_deskew,
                                auto_enhance,
                                auto_zoom,
                            )
                        except Exception as e:
                            failed_count += 1
                            failed_uploads[file_hash] = {
                                "name": file_name,
                                "bytes": rotated_bytes,
                                "auto_deskew": auto_deskew,
                                "auto_enhance": auto_enhance,
                                "auto_zoom": auto_zoom,
                            }
                            st.error(f"❌ 處理失敗：{e}")
                            time.sleep(2)
                            continue

                    st.session_state["processed_receipts"][file_hash] = {
                        "data": data,
                        "total_calories": 0,
                        "saved": False,
                    }
                    total_calories = 0
                    saved = False

                if data.get("_low_confidence"):
                    st.warning(
                        f"OCR 信心度偏低（{data.get('_confidence', 0):.2f}）。建議重新拍攝。"
                    )
                    if st.button(
                        "仍要繼續（可能會消耗更多 token）",
                        key=f"continue_low_{file_hash}",
                        width="stretch",
                    ):
                        with st.spinner("正在使用 LLM 解析..."):
                            try:
                                data = finalize_llm_from_clean_text(
                                    data.get("_clean_text", ""),
                                    data.get("_records", []),
                                    api_key,
                                    llm_model,
                                    base_url=llm_base_url,
                                )
                                data["items"] = enrich_calories(data.get("items", []))
                                st.session_state["processed_receipts"][file_hash]["data"] = data
                            except Exception as e:
                                st.error(f"❌ 處理失敗：{e}")
                                continue
                    else:
                        continue

                items_list = enrich_calories(data.get("items", []))
                data["items"] = items_list
                if items_list:
                    temp_df = pd.DataFrame(items_list)
                    if "calories_estimate" in temp_df.columns:
                        temp_df["calories_estimate"] = pd.to_numeric(
                            temp_df["calories_estimate"], errors="coerce"
                        ).fillna(0)
                        total_calories = int(temp_df["calories_estimate"].sum())
                st.markdown("### ✅ 已加入批次審核")
                if not saved:
                    default_date = data.get("date")
                    if not default_date or default_date in {"Unknown date", "未知日期"}:
                        default_date = datetime.today().strftime("%Y-%m-%d")
                    st.session_state["batch_records"][file_hash] = {
                        "file_name": file_name,
                        "date": default_date,
                        "amount": float(data.get("total_amount", 0) or 0),
                        "method": str(data.get("payment_method", "Unknown")),
                        "calories": int(total_calories or 0),
                        "data": data,
                        "saved": False,
                    }

                with st.expander("查看項目", expanded=False):
                    items_df = pd.DataFrame(items_list)
                    if items_df.empty:
                        st.info("沒有偵測到項目。")
                    else:
                        items_df.rename(
                            columns={
                                "name": "品項",
                                "qty": "數量",
                                "price": "單價",
                                "calories_estimate": "熱量（kcal）",
                            },
                            inplace=True,
                        )
                        st.dataframe(items_df, width="stretch", height=220)

                if st.session_state["processed_receipts"].get(file_hash, {}).get("saved"):
                    current_total_calories += total_calories

        progress.progress(100, text="批次處理完成")
        st.session_state["last_failed_uploads"] = failed_uploads

        pending_items = [
            {"id": key, **value}
            for key, value in st.session_state["batch_records"].items()
            if not value.get("saved")
        ]
        if pending_items:
            st.markdown("---")
            st.subheader("🧾 批次審核與儲存")
            batch_df = pd.DataFrame(
                [
                    {
                        "ID": item["id"],
                        "檔案": item["file_name"],
                        "日期": item["date"],
                        "金額": float(item["amount"]),
                        "付款方式": item["method"],
                        "熱量": int(item["calories"]),
                    }
                    for item in pending_items
                ]
            )

            edited_batch = st.data_editor(
                batch_df,
                width="stretch",
                hide_index=True,
                disabled=["ID", "檔案"],
                key="batch_editor",
            )

            if st.button("💾 儲存所有記錄", width="stretch"):
                for _, row in edited_batch.iterrows():
                    row_id = row.get("ID")
                    record = st.session_state["batch_records"].get(row_id)
                    if not record or record.get("saved"):
                        continue

                    data = record.get("data", {})
                    updated_data = {
                        **data,
                        "date": str(row.get("日期")),
                        "total_amount": round(float(row.get("金額") or 0), 2),
                        "payment_method": str(row.get("付款方式") or "Unknown"),
                        "items": data.get("items", []),
                    }

                    calories_total = int(row.get("熱量") or 0)
                    save_record(
                        updated_data["date"],
                        updated_data["total_amount"],
                        updated_data["payment_method"],
                        calories_total,
                        updated_data,
                    )

                    st.session_state["batch_records"][row_id]["saved"] = True
                    st.session_state["processed_receipts"][row_id] = {
                        "data": updated_data,
                        "total_calories": calories_total,
                        "saved": True,
                    }
                    success_count += 1

                st.success("✅ 批次儲存完成。")

        s1, s2, s3 = st.columns(3)
        pending_count = len(pending_items)
        saved_total = sum(
            1
            for record in st.session_state.get("processed_receipts", {}).values()
            if record.get("saved")
        )
        s1.metric("✅ 已儲存", saved_total)
        s2.metric("♻️ 已重複", reused_count)
        s3.metric("📝 待處理", pending_count)

        if failed_count > 0:
            st.warning("部分圖片處理失敗，可在下方重新嘗試。")
            if st.button("🔁 重試失敗項目", width="stretch"):
                retry_failed = st.session_state.get("last_failed_uploads", {})
                if not retry_failed:
                    st.info("沒有可重試的失敗項目。")
                else:
                    retry_progress = st.progress(0, text="正在準備重試...")
                    retry_success = 0
                    still_failed = {}
                    retry_items = list(retry_failed.items())

                    for retry_idx, (retry_hash, item) in enumerate(retry_items, start=1):
                        retry_progress.progress(
                            int((retry_idx - 1) / len(retry_items) * 100),
                            text=f"正在重試 {retry_idx}/{len(retry_items)}：{item['name']}",
                        )
                        try:
                            retry_data = process_receipt(
                                item["bytes"],
                                api_key,
                                llm_model,
                                llm_base_url,
                                ocr_lang,
                                item.get("auto_deskew", True),
                                item.get("auto_enhance", True),
                                item.get("auto_zoom", True),
                            )
                        except Exception as retry_err:
                            still_failed[retry_hash] = item
                            st.error(f"重試失敗：{item['name']} -> {retry_err}")
                            time.sleep(2)
                            continue

                        retry_items_list = retry_data.get("items", [])
                        retry_total_calories = 0
                        if retry_items_list:
                            retry_df = pd.DataFrame(retry_items_list)
                            if "calories_estimate" in retry_df.columns:
                                retry_df["calories_estimate"] = pd.to_numeric(
                                    retry_df["calories_estimate"], errors="coerce"
                                ).fillna(0)
                                retry_total_calories = int(retry_df["calories_estimate"].sum())

                        retry_date = retry_data.get("date")
                        if not retry_date or retry_date in {"Unknown date", "未知日期"}:
                            retry_date = datetime.today().strftime("%Y-%m-%d")

                        save_record(
                            retry_date,
                            retry_data.get("total_amount", 0),
                            retry_data.get("payment_method", "Unknown"),
                            retry_total_calories,
                            retry_data,
                        )
                        st.session_state["processed_receipts"][retry_hash] = {
                            "data": retry_data,
                            "total_calories": retry_total_calories,
                        }
                        
                        if retry_idx < len(retry_items):
                            time.sleep(2.5)
                        retry_success += 1

                    retry_progress.progress(100, text="重試完成")
                    st.session_state["last_failed_uploads"] = still_failed
                    st.success(f"重試完成：成功 {retry_success} 筆，失敗 {len(still_failed)} 筆。")

render_history_section()

