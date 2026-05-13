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
        
        return f'<td style="background-color:{daily_color}; border:1px solid {border_color}; padding:10px; width:14%; text-align:center; vertical-align:top; border-radius:4px;">' \
               f'<div style="font-weight:bold; font-size:16px; color:{day_color};">{day}</div>' \
               f'<div style="color:{text_color}; font-size:15px; margin-top:4px; font-weight:bold;">{amount_str}</div>' \
               f'</td>'

    def formatweekheader(self):
        week_days = ['日', '一', '二', '三', '四', '五', '六']
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
LOW_CONFIDENCE_THRESHOLD = 0.6
LANG_OPTIONS = {
    "简体中文": "ch",
    "繁體中文": "chinese_cht",
    "English": "en",
}


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


def get_saved_api_key():
    settings = load_local_settings()
    return settings.get("deepseek_api_key", "")


def save_api_key(api_key):
    settings = load_local_settings()
    settings["deepseek_api_key"] = api_key.strip()
    save_local_settings(settings)


def clear_saved_api_key():
    settings = load_local_settings()
    settings.pop("deepseek_api_key", None)
    save_local_settings(settings)

# --- 数据库初始化逻辑（每次运行网页都会检查） ---
def init_db():
    conn = get_conn()
    c = conn.cursor()
    # 创建表格存储：日期、总额、支付方式、總熱量、原始JSON
    c.execute('''CREATE TABLE IF NOT EXISTS records
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  date TEXT, 
                  amount REAL, 
                  method TEXT, 
                  calories INTEGER, 
                  raw_json TEXT)''')

    # 兼容旧库：新增上傳日期字段，用于“今日统计”按上传当天计算
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
    df["method"] = df["method"].fillna("未知")
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
            "message": f"今天攝入約 {calories_today} kcal，處於建議範圍內。",
            "extra": 0,
        }

    extra = int(diff)
    return {
        "status": "over",
        "message": f"今天比建議值高出約 {extra} kcal，可透過運動做平衡。",
        "extra": extra,
        "plans": [
            {"name": "快走", "kcal_per_min": 5},
            {"name": "慢跑", "kcal_per_min": 10},
            {"name": "騎行", "kcal_per_min": 8},
            {"name": "跳繩", "kcal_per_min": 12},
        ],
    }


def render_health_section(current_total_calories=0):
    st.markdown("---")
    st.subheader("🏃 健康與運動建議")

    target_calories = st.slider(
        "每日目標攝入(kcal)",
        min_value=1200,
        max_value=3200,
        value=2000,
        step=50,
        help="可根據你的增肌/減脂目標自行調整",
    )

    history_df = get_calorie_history()
    if history_df.empty:
        st.info("暫無歷史記錄。先上傳並分析一張小票，就會生成卡路里趨勢和運動建議。")
        return

    today = pd.Timestamp(datetime.today().date())
    today_row = history_df[history_df["date"] == today]
    history_today_cal = int(today_row["total_calories"].iloc[0]) if not today_row.empty else 0
    today_amount, today_calories_db = get_today_receipt_totals()
    calories_today = max(history_today_cal, today_calories_db, int(current_total_calories or 0))

    avg_7d = float(history_df.tail(7)["total_calories"].mean()) if not history_df.empty else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📅 今日總攝入", f"{calories_today} kcal")
    col2.metric("📊 近7日平均", f"{avg_7d:.0f} kcal")
    col3.metric("🎯 每日目標", f"{target_calories} kcal")
    col4.metric("💵 今日總消費(按小票日期)", f"${today_amount:.2f}")

    chart_df = history_df.copy()
    chart_df["date_str"] = chart_df["date"].dt.strftime("%Y-%m-%d")
    fig = px.line(
        chart_df,
        x="date_str",
        y="total_calories",
        markers=True,
        title="每日卡路里攝入趨勢",
        labels={"date_str": "日期", "total_calories": "總卡路里(kcal)"},
    )
    fig.add_hline(y=target_calories, line_dash="dash", line_color="orange")
    st.plotly_chart(fig, width="stretch")

    plan = build_exercise_plan(calories_today, target_calories)
    st.info(plan["message"])

    if plan["status"] == "over":
        extra = plan["extra"]
        plan_rows = []
        for item in plan["plans"]:
            mins = int((extra + item["kcal_per_min"] - 1) // item["kcal_per_min"])
            plan_rows.append(
                {
                    "運動": item["name"],
                    "預計消耗(kcal/分鐘)": item["kcal_per_min"],
                    "建議時長(分鐘)": mins,
                }
            )
        st.write("建議選擇任一運動完成下列時長：")
        st.table(pd.DataFrame(plan_rows))
    else:
        st.success("今天维持轻量活动即可：如散步 20-30 分鐘，帮助消化和恢复。")


def render_history_section():
    st.markdown("---")
    st.subheader("📚 歷史消費數據")

    history_df = get_spending_history()
    if history_df.empty:
        st.info("當前還沒有歷史記錄。先上傳並處理一張小票，歷史數據會自動出現在這裡。")
        return

    min_date = history_df["receipt_date"].min().date()
    max_date = history_df["receipt_date"].max().date()

    col_filter1, col_filter2 = st.columns([1, 1])
    with col_filter1:
        date_range = st.date_input(
            "篩選日期範圍",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
    with col_filter2:
        methods = sorted(history_df["method"].dropna().unique().tolist())
        selected_methods = st.multiselect(
            "篩選支付方式",
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
        st.warning("當前篩選條件下沒有數據，請調整篩選條件。")
        return

    total_amount = float(filtered_df["amount"].sum())
    total_calories = int(filtered_df["calories"].sum())
    receipt_count = int(filtered_df.shape[0])
    avg_amount = total_amount / receipt_count if receipt_count else 0

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("🧾 消費筆數", f"{receipt_count}")
    col_m2.metric("💵 總消費", f"${total_amount:.2f}")
    col_m3.metric("🍱 總熱量", f"{total_calories} kcal")
    col_m4.metric("📉 單筆均額", f"${avg_amount:.2f}")

    daily_df = (
        filtered_df.groupby(filtered_df["receipt_date"].dt.date, as_index=False)
        .agg(amount=("amount", "sum"), calories=("calories", "sum"), count=("id", "count"))
        .sort_values("receipt_date")
    )
    daily_df["date_str"] = pd.to_datetime(daily_df["receipt_date"]).dt.strftime("%Y-%m-%d")

    st.markdown("#### 📅 2026全年每日消費日曆")
    daily_df["year_month"] = pd.to_datetime(daily_df["receipt_date"]).dt.to_period("M")
    
    # 強制生成2026年1月到12月的所有月份
    all_months_2026 = pd.period_range(start="2026-01", end="2026-12", freq="M")
    
    tabs = st.tabs([ym.strftime("%Y年%m月") for ym in all_months_2026])
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

    c1, c2 = st.columns(2)
    with c1:
        fig_amount = px.line(
            daily_df,
            x="date_str",
            y="amount",
            markers=True,
            title="每日消费金額趋势",
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
            title="支付方式消費分布",
            labels={"method": "支付方式", "amount": "金額"},
        )
        st.plotly_chart(fig_method, width="stretch")

    table_df = filtered_df.copy().sort_values("receipt_date", ascending=False)
    table_df["upload_date"] = table_df["upload_date"].dt.strftime("%Y-%m-%d")
    table_df["receipt_date"] = table_df["receipt_date"].dt.strftime("%Y-%m-%d")
    table_df = table_df[["upload_date", "receipt_date", "amount", "method", "calories"]]
    table_df.columns = ["上傳日期", "小票日期", "金額", "支付方式", "熱量(kcal)"]

    st.write("歷史明細")
    st.dataframe(table_df, width="stretch", hide_index=True)

    csv_data = table_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ 導出當前篩選結果 (CSV)",
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
    # Prefer amount-like OCR lines (currency marker or decimal) over plain integers.
    def _to_float_list(text):
        vals = []
        for raw in re.findall(r"\d+(?:\.\d{1,2})?", text):
            try:
                n = float(raw)
            except ValueError:
                continue
            if 0 < n < 5000:
                vals.append(n)
        return vals

    texts = [str(item.get("text", "")) for item in (records or [])]
    currency_candidates = []
    decimal_candidates = []

    for line in texts:
        if any(keyword in line for keyword in ["余额", "餘額", "balance", "卡號", "卡号", "机号", "機號"]):
            continue
            
        numbers = _to_float_list(line)
        if not numbers:
            continue
        has_currency = ("$" in line) or ("HK" in line.upper()) or ("港币" in line)
        has_decimal = "." in line

        if has_currency:
            currency_candidates.extend(numbers)
        elif has_decimal:
            decimal_candidates.extend(numbers)

    if currency_candidates:
        return round(max(currency_candidates), 2)
    if decimal_candidates:
        return round(max(decimal_candidates), 2)

    # Last fallback: parse full text but only keep decimal-like numbers.
    text_candidates = []
    for raw in re.findall(r"\d+\.\d{1,2}", clean_text or ""):
        try:
            n = float(raw)
        except ValueError:
            continue
        if 0 < n < 5000:
            text_candidates.append(n)

    if text_candidates:
        return round(max(text_candidates), 2)
    return None


def rotate_image_bytes(file_bytes, angle):
    if angle == 0:
        return file_bytes
    image = Image.open(BytesIO(file_bytes)).convert("RGB")
    rotated = image.rotate(-angle, expand=True)
    buf = BytesIO()
    rotated.save(buf, format="PNG")
    return buf.getvalue()


@st.cache_resource(show_spinner=False)
def get_ocr_engine(lang="ch"):
    import ocr_test

    ocr_engine, _ = ocr_test.init_ocr(lang)
    return ocr_engine


def finalize_deepseek_from_clean_text(clean_text, records, api_key):
    import ocr_test

    os.environ["DEEPSEEK_API_KEY"] = api_key
    raw_json = ocr_test.call_deepseek(clean_text, "deepseek-chat")
    data = parse_deepseek_json(raw_json)

    current_amount = data.get("total_amount", 0)
    try:
        current_amount_num = float(current_amount)
    except (TypeError, ValueError):
        current_amount_num = 0.0

    # 總是運行兜底校驗：如果抓取的總金額是大於0的數字，再對比單項消費的總和，防呆防錯錯讀。
    fallback_amount = _extract_amount_fallback(records, clean_text)
    if fallback_amount is not None:
        # 如果深度校驗返回的金額和AI抓的不一樣，或者AI根本沒抓到(<=0):
        # 優先相信我們正則兜底校驗出的金額（這個函數已經排除了餘額/卡號）
        # 這個邏輯能修復 84.0/64.0 的幻覺誤差。
        if current_amount_num <= 0 or current_amount_num > 10 * fallback_amount:
            # 如果 AI 沒抓到，或者抓得離譜，才相信我們正則兜底校驗出的金額
            data["total_amount"] = round(fallback_amount, 2)

    return data


def process_receipt(file_bytes, api_key, lang, allow_low_confidence=False):
    import ocr_test

    os.environ["DEEPSEEK_API_KEY"] = api_key
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        tmp.write(file_bytes)
        temp_path = tmp.name

    try:
        ocr_engine = get_ocr_engine(lang)
        result = ocr_test.robust_ocr(ocr_engine, temp_path)
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

        data = finalize_deepseek_from_clean_text(clean_text, records, api_key)
        data["_confidence"] = avg_conf
        return data
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

init_db() 

if "saved_api_key" not in st.session_state:
    st.session_state["saved_api_key"] = get_saved_api_key()
if "processed_receipts" not in st.session_state:
    st.session_state["processed_receipts"] = {}
if "last_failed_uploads" not in st.session_state:
    st.session_state["last_failed_uploads"] = {}

# 1. 網頁配置：設置寬屏模式
st.set_page_config(page_title="Smart Expense Tracker", page_icon="🧾", layout="wide")

# ================================
# 注入自定义 CSS：放大字体与表格，提升繁体显示效果
# ================================
st.markdown('''
<style>
/* 1. 增大全局默认正文字体，避免污染系统级 UI 元素（如 svg/span 图标） */
.stMarkdown p, .stText, body, [data-testid="stSidebar"] {
    font-size: 18px !important;
    font-family: "Microsoft JhengHei", "PingFang TC", sans-serif !important;
}

/* 2. 针对 Metric (大数字核心数据) 的放大和加粗 */
[data-testid="stMetricValue"] {
    font-size: 38px !important;
    font-weight: 800 !important;
    color: #1E88E5 !important;
}
[data-testid="stMetricLabel"] {
    font-size: 22px !important;
    font-weight: 600 !important;
}

/* 3. 稳妥地放大下拉框、输入框 */
input, select {
    font-size: 18px !important;
}

/* 4. Streamlit 数据表格专属放大 */
[data-testid="stDataFrame"], .col-header-text, .row-header-text {
    font-size: 18px !important;
}

/* 5. 专门针对侧边栏 Expander（手风琴面板）做安全优化，保持单行并在同一水平线 */
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

/* 避免影响 Material Icon 的默认样式导致出现 "_arrow_right" 等报错文字 */
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

@media (min-width: 769px) {
    [data-testid="stCameraInput"] {
        display: none !important;
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


st.title("🧾 個人智能記帳系統 (Final Year Project)")
st.markdown("---")

# 2. 侧边栏：API 配置与状态监控
with st.sidebar:
    st.header("⚙️ 系統設置")
    saved_api_key = st.session_state.get("saved_api_key", "")
    has_saved_key = bool(saved_api_key)

    use_saved_key = st.checkbox(
        "使用本機已保存的 API Key",
        value=has_saved_key,
        disabled=not has_saved_key,
        help="勾选后无需每次输入，系统会直接使用本机保存的 Key",
    )

    manual_api_key = st.text_input(
        "臨時輸入 API Key（可留空）",
        type="password",
        value="",
        help="如填写，将优先使用臨時輸入；不填写则使用本机已保存 Key",
    )

    auto_save_manual_key = st.checkbox(
        "将上方输入自動保存到本機",
        value=True,
        help="开启后，在上方输入新 Key 会自动更新本机保存值",
    )

    if auto_save_manual_key and manual_api_key.strip():
        incoming_key = manual_api_key.strip()
        if incoming_key != saved_api_key:
            save_api_key(incoming_key)
            st.session_state["saved_api_key"] = incoming_key
            saved_api_key = incoming_key
            has_saved_key = True
            st.caption("已自動保存到本機。")

    api_key = manual_api_key.strip() if manual_api_key.strip() else (saved_api_key if use_saved_key else "")

    with st.expander("🔐 API Key 後台管理", expanded=False):
        st.caption("支援新增/更新/刪除本機保存的 API Key")
        manage_key = st.text_input(
            "新增或更新 API Key",
            type="password",
            value="",
            key="manage_api_key_input",
        )
        col_key_1, col_key_2 = st.columns(2)
        if col_key_1.button("保存/更新 Key", width="stretch"):
            if manage_key.strip():
                save_api_key(manage_key.strip())
                st.session_state["saved_api_key"] = manage_key.strip()
                st.success("已保存到本机。")
            else:
                st.warning("請輸入要保存的 Key。")

        if col_key_2.button("刪除已保存 Key", width="stretch"):
            clear_saved_api_key()
            st.session_state["saved_api_key"] = ""
            st.success("已刪除本機保存的 Key。")

        if st.session_state.get("saved_api_key", ""):
            st.info("當前狀態：本機已有已保存 Key")
            st.text_area(
                "已保存 API Key（完整）",
                value=st.session_state.get("saved_api_key", ""),
                height=80,
                disabled=True,
            )
        else:
            st.info("當前狀態：本機未保存 Key")
    st.divider()
    
    # 读取数据库，看看记了多少笔账
    count = get_record_count()
    
    st.success(f"🗄️ 資料庫狀態：已記錄 **{count}** 筆消費")    
    with st.expander("⚠️ 危險操作區 - 重置資料庫"):
        st.warning("點擊下方按鈕將會刪除所有歷史消費數據。")
        if st.button("🗑️ 清空所有歷史數據", type="primary", width="stretch"):
            try:
                get_conn().close()  # Close any active connection
                if DB_PATH.exists():
                    os.remove(DB_PATH)
                st.session_state["processed_receipts"] = {}
                st.session_state["last_failed_uploads"] = {}
                st.success("✅ 資料庫與緩存已成功清空！")
                st.rerun()  # Refresh app
            except Exception as e:
                st.error(f"清空失敗: {e}")
    st.info("💡 提示：本系統採用 PaddleOCR 提取文字，DeepSeek-V3 進行語義與熱量分析，SQLite 實現本地持久化存儲。")

    st.divider()
    st.subheader("🧩 識別語言")
    lang_label = st.selectbox(
        "選擇 OCR 語言",
        options=list(LANG_OPTIONS.keys()),
        index=0,
        help="香港繁體小票建議選繁體，英文小票選 English。",
    )
    ocr_lang = LANG_OPTIONS.get(lang_label, "ch")

# 3. 主界面布局
st.subheader("📥 小票上傳區")
st.caption("可拍照即時識別，也支援上傳多張圖片批量處理。")
input_mode = st.radio(
    "選擇輸入方式",
    options=["📷 相機拍照", "🖼️ 上傳圖片"],
    horizontal=True,
)

receipt_items = []
if input_mode == "📷 相機拍照":
    camera_image = st.camera_input("拍照後立即識別")
    if camera_image is not None:
        receipt_items = [
            {
                "name": "camera_capture.png",
                "bytes": camera_image.getvalue(),
            }
        ]
else:
    st.caption("支援點擊選擇，也支援直接把圖片拖拽到下方上傳框。")
    uploaded_files = st.file_uploader(
        "📸 拖拽小票到這裡，或點擊選擇文件（支援一次多張）",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        help="可拖拽 PNG/JPG/JPEG 文件到上傳框，鬆手後會自動開始識別。",
    )
    if uploaded_files:
        receipt_items = [
            {"name": item.name, "bytes": item.getvalue()} for item in uploaded_files
        ]

current_total_calories = 0

if receipt_items:
    st.subheader("🤖 自動識別與入庫")

    if not api_key:
        st.warning("⚠️ 請先在左側欄輸入 API Key，輸入後會自動開始識別。")
    else:
        progress = st.progress(0, text="準備開始批量處理...")
        success_count = 0
        failed_count = 0
        reused_count = 0
        failed_uploads = {}

        for idx, item in enumerate(receipt_items, start=1):
            file_bytes = item["bytes"]
            file_name = item["name"]
            progress.progress(
                int((idx - 1) / len(receipt_items) * 100),
                text=f"正在處理第 {idx}/{len(receipt_items)} 张：{file_name}",
            )

            with st.expander(f"第 {idx} 张：{file_name}", expanded=(idx == 1)):
                st.caption("可選：識別前手動旋轉，複雜角度小票更穩。")
                rotate_angle = st.selectbox(
                    "旋轉角度（識別前）",
                    options=[0, 90, 180, 270],
                    index=0,
                    key=f"rotate_{idx}_{file_name}",
                )
                rotated_bytes = rotate_image_bytes(file_bytes, int(rotate_angle))
                file_hash = hashlib.sha256(rotated_bytes).hexdigest()
                st.image(rotated_bytes, width="stretch")

                cached = st.session_state["processed_receipts"].get(file_hash)
                if cached:
                    data = cached.get("data")
                    total_calories = int(cached.get("total_calories", 0))
                    saved = cached.get("saved", False)
                    reused_count += 1
                else:
                    with st.spinner(f"正在處理：{file_name}"):
                        try:
                            data = process_receipt(rotated_bytes, api_key, ocr_lang)
                        except Exception as e:
                            failed_count += 1
                            failed_uploads[file_hash] = {
                                "name": file_name,
                                "bytes": rotated_bytes,
                            }
                            st.error(f"❌ 處理出錯：{e}")
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
                        f"圖片可能較模糊，平均置信度 {data.get('_confidence', 0):.2f}，建議重新拍攝。"
                    )
                    if st.button(
                        "仍然繼續識別（可能消耗更多 Token）",
                        key=f"continue_low_{file_hash}",
                        width="stretch",
                    ):
                        with st.spinner("正在進行語義解析..."):
                            try:
                                data = finalize_deepseek_from_clean_text(
                                    data.get("_clean_text", ""),
                                    data.get("_records", []),
                                    api_key,
                                )
                                st.session_state["processed_receipts"][file_hash]["data"] = data
                            except Exception as e:
                                st.error(f"❌ 處理出錯：{e}")
                                continue
                    else:
                        continue

                items_list = data.get("items", [])
                if items_list:
                    temp_df = pd.DataFrame(items_list)
                    if "calories_estimate" in temp_df.columns:
                        temp_df["calories_estimate"] = pd.to_numeric(
                            temp_df["calories_estimate"], errors="coerce"
                        ).fillna(0)
                        total_calories = int(temp_df["calories_estimate"].sum())

                st.markdown("### ✅ 請確認並微調")
                form_key = f"confirm_form_{file_hash}"
                with st.form(key=form_key, clear_on_submit=False):
                    default_date = data.get("date")
                    if not default_date or default_date == "未知日期":
                        default_date = datetime.today().strftime("%Y-%m-%d")
                    parsed_date = pd.to_datetime(default_date, errors="coerce")
                    if pd.isna(parsed_date):
                        parsed_date = pd.to_datetime(datetime.today().strftime("%Y-%m-%d"))

                    date_value = st.date_input(
                        "消費日期",
                        value=parsed_date.date(),
                        key=f"date_{file_hash}",
                    )
                    amount_value = st.number_input(
                        "總金額",
                        min_value=0.0,
                        value=float(data.get("total_amount", 0) or 0),
                        step=0.01,
                        format="%.2f",
                        key=f"amount_{file_hash}",
                    )
                    method_value = st.text_input(
                        "支付方式",
                        value=str(data.get("payment_method", "未知")),
                        key=f"method_{file_hash}",
                    )

                    items_df = pd.DataFrame(items_list)
                    if items_df.empty:
                        items_df = pd.DataFrame(
                            columns=["name", "qty", "price", "calories_estimate"]
                        )
                    items_df.rename(
                        columns={
                            "name": "商品名稱",
                            "qty": "數量",
                            "price": "單價",
                            "calories_estimate": "熱量估算(kcal)",
                        },
                        inplace=True,
                    )
                    edited_df = st.data_editor(
                        items_df,
                        width="stretch",
                        height=260,
                        num_rows="dynamic",
                        key=f"items_editor_{file_hash}",
                    )

                    submitted = st.form_submit_button("✅ 確認入庫", width="stretch")

                if submitted:
                    edited_df = edited_df.rename(
                        columns={
                            "商品名稱": "name",
                            "數量": "qty",
                            "單價": "price",
                            "熱量估算(kcal)": "calories_estimate",
                        }
                    )
                    edited_items = edited_df.to_dict(orient="records")
                    calories_total = 0
                    if "calories_estimate" in edited_df.columns:
                        calories_total = int(
                            pd.to_numeric(
                                edited_df["calories_estimate"], errors="coerce"
                            ).fillna(0).sum()
                        )

                    updated_data = {
                        **data,
                        "date": date_value.strftime("%Y-%m-%d"),
                        "total_amount": round(float(amount_value), 2),
                        "payment_method": method_value.strip() or "未知",
                        "items": edited_items,
                    }

                    save_record(
                        updated_data["date"],
                        updated_data["total_amount"],
                        updated_data["payment_method"],
                        calories_total,
                        updated_data,
                    )

                    st.session_state["processed_receipts"][file_hash] = {
                        "data": updated_data,
                        "total_calories": calories_total,
                        "saved": True,
                    }
                    success_count += 1
                    total_calories = calories_total
                    data = updated_data
                    items_list = edited_items
                    st.success("✅ 已確認入庫完成。")

                if st.session_state["processed_receipts"].get(file_hash, {}).get("saved"):
                    current_total_calories += total_calories

                    m_col1, m_col2, m_col3 = st.columns(3)
                    m_col1.metric("💰 總金額", f"${data.get('total_amount', 0)}")
                    m_col2.metric("💳 支付方式", data.get("payment_method", "未知"))
                    m_col3.metric("📅 消費日期", data.get("date", "未知日期"))

                    if items_list:
                        df = pd.DataFrame(items_list)
                        if "calories_estimate" in df.columns:
                            df["calories_estimate"] = pd.to_numeric(
                                df["calories_estimate"], errors="coerce"
                            ).fillna(0)

                        df.rename(
                            columns={
                                "name": "商品名稱",
                                "qty": "數量",
                                "price": "單價",
                                "calories_estimate": "熱量估算(kcal)",
                            },
                            inplace=True,
                        )

                        st.write(f"📝 消費明細（✨ 本單預估總熱量: **{total_calories} kcal**）：")
                        st.dataframe(df, width="stretch", height=(len(df) + 1) * 45)

                        if "商品名稱" in df.columns and "數量" in df.columns:
                            df["數量"] = pd.to_numeric(df["數量"], errors="coerce").fillna(1)
                            fig = px.pie(df, names="商品名稱", values="數量", title="消費單品數量分布")
                            st.plotly_chart(fig, width="stretch")

                    with st.expander("👀 查看原始 JSON 數據"):
                        st.json(data)

        progress.progress(100, text="批量處理完成")
        st.session_state["last_failed_uploads"] = failed_uploads

        s1, s2, s3 = st.columns(3)
        s1.metric("✅ 新增成功", success_count)
        s2.metric("♻️ 復用結果", reused_count)
        s3.metric("❌ 失敗數量", failed_count)

        if failed_count > 0:
            st.warning("有部分圖片處理失敗，可點擊下方按鈕重試失敗項。")
            if st.button("🔁 重試失敗項", width="stretch"):
                retry_failed = st.session_state.get("last_failed_uploads", {})
                if not retry_failed:
                    st.info("當前沒有可重試的失敗項。")
                else:
                    retry_progress = st.progress(0, text="准备重試失敗項...")
                    retry_success = 0
                    still_failed = {}
                    retry_items = list(retry_failed.items())

                    for retry_idx, (retry_hash, item) in enumerate(retry_items, start=1):
                        retry_progress.progress(
                            int((retry_idx - 1) / len(retry_items) * 100),
                            text=f"重試中 {retry_idx}/{len(retry_items)}：{item['name']}",
                        )
                        try:
                            retry_data = process_receipt(item["bytes"], api_key)
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
                        if not retry_date or retry_date == "未知日期":
                            retry_date = datetime.today().strftime("%Y-%m-%d")

                        save_record(
                            retry_date,
                            retry_data.get("total_amount", 0),
                            retry_data.get("payment_method", "未知"),
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

                    retry_progress.progress(100, text="失敗項重試完成")
                    st.session_state["last_failed_uploads"] = still_failed
                    st.success(f"重試完成，成功 {retry_success} 张，仍失敗 {len(still_failed)} 张。")

render_health_section(current_total_calories)
render_history_section()
