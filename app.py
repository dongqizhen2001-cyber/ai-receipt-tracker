import streamlit as st
import json
import re
from io import BytesIO
import pandas as pd
import plotly.express as px
import os
import sqlite3
import tempfile
import hashlib
from pathlib import Path
from datetime import datetime
from PIL import Image

DB_PATH = Path("finance.db")
SETTINGS_PATH = Path("app_settings.json")


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
    # 创建表格存储：日期、总额、支付方式、总热量、原始JSON
    c.execute('''CREATE TABLE IF NOT EXISTS records
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  date TEXT, 
                  amount REAL, 
                  method TEXT, 
                  calories INTEGER, 
                  raw_json TEXT)''')

    # 兼容旧库：新增上传日期字段，用于“今日统计”按上传当天计算
    cols = [row[1] for row in c.execute("PRAGMA table_info(records)").fetchall()]
    if "upload_date" not in cols:
        c.execute("ALTER TABLE records ADD COLUMN upload_date TEXT")
    conn.commit()
    conn.close()


def get_record_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]


def save_record(record_date, amount, method, calories, data):
    upload_date = datetime.today().strftime("%Y-%m-%d")
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO records (date, amount, method, calories, raw_json, upload_date) VALUES (?, ?, ?, ?, ?, ?)",
            (record_date, amount, method, calories, json.dumps(data, ensure_ascii=False), upload_date),
        )


def get_calorie_history():
    query = """
        SELECT COALESCE(date, upload_date) AS stat_date,
               SUM(COALESCE(calories, 0)) AS total_calories,
               COUNT(*) AS receipt_count
        FROM records
        GROUP BY COALESCE(date, upload_date)
        ORDER BY COALESCE(date, upload_date)
    """
    with get_conn() as conn:
        df = pd.read_sql_query(query, conn)
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
    with get_conn() as conn:
        df = pd.read_sql_query(query, conn)

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
    with get_conn() as conn:
        row = conn.execute(query, (today,)).fetchone()
    total_amount = float(row[0] or 0)
    total_calories = int(row[1] or 0)
    return total_amount, total_calories


def build_exercise_plan(calories_today, target_calories=2000):
    diff = calories_today - target_calories
    if diff <= 0:
        return {
            "status": "within",
            "message": f"今天摄入约 {calories_today} kcal，处于建议范围内。",
            "extra": 0,
        }

    extra = int(diff)
    return {
        "status": "over",
        "message": f"今天比建议值高出约 {extra} kcal，可通过运动做平衡。",
        "extra": extra,
        "plans": [
            {"name": "快走", "kcal_per_min": 5},
            {"name": "慢跑", "kcal_per_min": 10},
            {"name": "骑行", "kcal_per_min": 8},
            {"name": "跳绳", "kcal_per_min": 12},
        ],
    }


def render_health_section(current_total_calories=0):
    st.markdown("---")
    st.subheader("🏃 健康与运动建议")

    target_calories = st.slider(
        "每日目标摄入(kcal)",
        min_value=1200,
        max_value=3200,
        value=2000,
        step=50,
        help="可根据你的增肌/减脂目标自行调整",
    )

    history_df = get_calorie_history()
    if history_df.empty:
        st.info("暂无历史记录。先上传并分析一张小票，就会生成卡路里趋势和运动建议。")
        return

    today = pd.Timestamp(datetime.today().date())
    today_row = history_df[history_df["date"] == today]
    history_today_cal = int(today_row["total_calories"].iloc[0]) if not today_row.empty else 0
    today_amount, today_calories_db = get_today_receipt_totals()
    calories_today = max(history_today_cal, today_calories_db, int(current_total_calories or 0))

    avg_7d = float(history_df.tail(7)["total_calories"].mean()) if not history_df.empty else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📅 今日总摄入", f"{calories_today} kcal")
    col2.metric("📊 近7日平均", f"{avg_7d:.0f} kcal")
    col3.metric("🎯 每日目标", f"{target_calories} kcal")
    col4.metric("💵 今日总消费(按小票日期)", f"${today_amount:.2f}")

    chart_df = history_df.copy()
    chart_df["date_str"] = chart_df["date"].dt.strftime("%Y-%m-%d")
    fig = px.line(
        chart_df,
        x="date_str",
        y="total_calories",
        markers=True,
        title="每日卡路里摄入趋势",
        labels={"date_str": "日期", "total_calories": "总卡路里(kcal)"},
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
                    "运动": item["name"],
                    "预计消耗(kcal/分钟)": item["kcal_per_min"],
                    "建议时长(分钟)": mins,
                }
            )
        st.write("建议选择任一运动完成下列时长：")
        st.table(pd.DataFrame(plan_rows))
    else:
        st.success("今天维持轻量活动即可：如散步 20-30 分钟，帮助消化和恢复。")


def render_history_section():
    st.markdown("---")
    st.subheader("📚 历史消费数据")

    history_df = get_spending_history()
    if history_df.empty:
        st.info("当前还没有历史记录。先上传并处理一张小票，历史数据会自动出现在这里。")
        return

    min_date = history_df["receipt_date"].min().date()
    max_date = history_df["receipt_date"].max().date()

    col_filter1, col_filter2 = st.columns([1, 1])
    with col_filter1:
        date_range = st.date_input(
            "筛选日期范围",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
    with col_filter2:
        methods = sorted(history_df["method"].dropna().unique().tolist())
        selected_methods = st.multiselect(
            "筛选支付方式",
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
        st.warning("当前筛选条件下没有数据，请调整筛选条件。")
        return

    total_amount = float(filtered_df["amount"].sum())
    total_calories = int(filtered_df["calories"].sum())
    receipt_count = int(filtered_df.shape[0])
    avg_amount = total_amount / receipt_count if receipt_count else 0

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("🧾 消费笔数", f"{receipt_count}")
    col_m2.metric("💵 总消费", f"${total_amount:.2f}")
    col_m3.metric("🍱 总热量", f"{total_calories} kcal")
    col_m4.metric("📉 单笔均额", f"${avg_amount:.2f}")

    daily_df = (
        filtered_df.groupby(filtered_df["receipt_date"].dt.date, as_index=False)
        .agg(amount=("amount", "sum"), calories=("calories", "sum"), count=("id", "count"))
        .sort_values("receipt_date")
    )
    daily_df["date_str"] = pd.to_datetime(daily_df["receipt_date"]).dt.strftime("%Y-%m-%d")

    c1, c2 = st.columns(2)
    with c1:
        fig_amount = px.line(
            daily_df,
            x="date_str",
            y="amount",
            markers=True,
            title="每日消费金额趋势",
            labels={"date_str": "日期", "amount": "金额"},
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
            title="支付方式消费分布",
            labels={"method": "支付方式", "amount": "金额"},
        )
        st.plotly_chart(fig_method, width="stretch")

    table_df = filtered_df.copy().sort_values("receipt_date", ascending=False)
    table_df["upload_date"] = table_df["upload_date"].dt.strftime("%Y-%m-%d")
    table_df["receipt_date"] = table_df["receipt_date"].dt.strftime("%Y-%m-%d")
    table_df = table_df[["upload_date", "receipt_date", "amount", "method", "calories"]]
    table_df.columns = ["上传日期", "小票日期", "金额", "支付方式", "热量(kcal)"]

    st.write("历史明细")
    st.dataframe(table_df, width="stretch", hide_index=True)

    csv_data = table_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ 导出当前筛选结果 (CSV)",
        data=csv_data,
        file_name="spending_history.csv",
        mime="text/csv",
        use_container_width=True,
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


def process_receipt(file_bytes, api_key):
    import ocr_test

    os.environ["DEEPSEEK_API_KEY"] = api_key
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        tmp.write(file_bytes)
        temp_path = tmp.name

    try:
        ocr_engine = get_ocr_engine("ch")
        result = ocr_test.robust_ocr(ocr_engine, temp_path)
        records = ocr_test.normalize_result(result)
        clean_text = ocr_test.build_clean_text(records)
        raw_json = ocr_test.call_deepseek(clean_text, "deepseek-chat")
        data = parse_deepseek_json(raw_json)

        current_amount = data.get("total_amount", 0)
        try:
            current_amount_num = float(current_amount)
        except (TypeError, ValueError):
            current_amount_num = 0.0

        if current_amount_num <= 0:
            fallback_amount = _extract_amount_fallback(records, clean_text)
            if fallback_amount is not None:
                data["total_amount"] = round(fallback_amount, 2)

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

# 1. 网页配置：设置宽屏模式
st.set_page_config(page_title="Smart Expense Tracker", page_icon="🧾", layout="wide")

st.title("🧾 个人智能记账系统 (Final Year Project)")
st.markdown("---")

# 2. 侧边栏：API 配置与状态监控
with st.sidebar:
    st.header("⚙️ 系统设置")
    saved_api_key = st.session_state.get("saved_api_key", "")
    has_saved_key = bool(saved_api_key)

    use_saved_key = st.checkbox(
        "使用本机已保存的 API Key",
        value=has_saved_key,
        disabled=not has_saved_key,
        help="勾选后无需每次输入，系统会直接使用本机保存的 Key",
    )

    manual_api_key = st.text_input(
        "临时输入 API Key（可留空）",
        type="password",
        value="",
        help="如填写，将优先使用临时输入；不填写则使用本机已保存 Key",
    )

    auto_save_manual_key = st.checkbox(
        "将上方输入自动保存到本机",
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
            st.caption("已自动保存到本机。")

    api_key = manual_api_key.strip() if manual_api_key.strip() else (saved_api_key if use_saved_key else "")

    with st.expander("🔐 API Key 后台管理", expanded=False):
        st.caption("支持新增/更新/删除本机保存的 API Key")
        manage_key = st.text_input(
            "新增或更新 API Key",
            type="password",
            value="",
            key="manage_api_key_input",
        )
        col_key_1, col_key_2 = st.columns(2)
        if col_key_1.button("保存/更新 Key", use_container_width=True):
            if manage_key.strip():
                save_api_key(manage_key.strip())
                st.session_state["saved_api_key"] = manage_key.strip()
                st.success("已保存到本机。")
            else:
                st.warning("请输入要保存的 Key。")

        if col_key_2.button("删除已保存 Key", use_container_width=True):
            clear_saved_api_key()
            st.session_state["saved_api_key"] = ""
            st.success("已删除本机保存的 Key。")

        if st.session_state.get("saved_api_key", ""):
            st.info("当前状态：本机已有已保存 Key")
            st.text_area(
                "已保存 API Key（完整）",
                value=st.session_state.get("saved_api_key", ""),
                height=80,
                disabled=True,
            )
        else:
            st.info("当前状态：本机未保存 Key")
    st.divider()
    
    # 读取数据库，看看记了多少笔账
    count = get_record_count()
    
    st.success(f"🗄️ 数据库状态：已记录 **{count}** 笔消费")
    st.info("💡 提示：本系统采用 PaddleOCR 提取文字，DeepSeek-V3 进行语义与热量分析，SQLite 实现本地持久化存储。")

# 3. 主界面布局
st.subheader("📥 小票上传区")
st.caption("支持点击选择，也支持直接把图片拖拽到下方上传框。")
uploaded_files = st.file_uploader(
    "📸 拖拽小票到这里，或点击选择文件（支持一次多张）",
    type=["png", "jpg", "jpeg"],
    accept_multiple_files=True,
    help="可拖拽 PNG/JPG/JPEG 文件到上传框，松手后会自动开始识别。",
)

current_total_calories = 0

if uploaded_files:
    st.subheader("🤖 自动识别与入库")

    if not api_key:
        st.warning("⚠️ 请先在左侧栏输入 API Key，输入后会自动开始识别。")
    else:
        progress = st.progress(0, text="准备开始批量处理...")
        success_count = 0
        failed_count = 0
        reused_count = 0
        failed_uploads = {}

        for idx, uploaded_file in enumerate(uploaded_files, start=1):
            file_bytes = uploaded_file.getvalue()
            progress.progress(
                int((idx - 1) / len(uploaded_files) * 100),
                text=f"正在处理第 {idx}/{len(uploaded_files)} 张：{uploaded_file.name}",
            )

            with st.expander(f"第 {idx} 张：{uploaded_file.name}", expanded=(idx == 1)):
                st.caption("可选：识别前手动旋转，复杂角度小票更稳。")
                rotate_angle = st.selectbox(
                    "旋转角度（识别前）",
                    options=[0, 90, 180, 270],
                    index=0,
                    key=f"rotate_{idx}_{uploaded_file.name}",
                )
                rotated_bytes = rotate_image_bytes(file_bytes, int(rotate_angle))
                file_hash = hashlib.sha256(rotated_bytes).hexdigest()
                st.image(rotated_bytes, width="stretch")

                if file_hash in st.session_state["processed_receipts"]:
                    cached = st.session_state["processed_receipts"][file_hash]
                    data = cached["data"]
                    total_calories = int(cached.get("total_calories", 0))
                    reused_count += 1
                    st.info("这张小票已识别并入库，已为你复用结果。")
                else:
                    with st.spinner(f"正在处理：{uploaded_file.name}"):
                        try:
                            data = process_receipt(rotated_bytes, api_key)
                        except Exception as e:
                            failed_count += 1
                            failed_uploads[file_hash] = {
                                "name": uploaded_file.name,
                                "bytes": rotated_bytes,
                            }
                            st.error(f"❌ 处理出错：{e}")
                            continue

                    items_list = data.get("items", [])
                    total_calories = 0
                    if items_list:
                        temp_df = pd.DataFrame(items_list)
                        if "calories_estimate" in temp_df.columns:
                            temp_df["calories_estimate"] = pd.to_numeric(
                                temp_df["calories_estimate"], errors="coerce"
                            ).fillna(0)
                            total_calories = int(temp_df["calories_estimate"].sum())

                    record_date = data.get("date")
                    if not record_date or record_date == "未知日期":
                        record_date = datetime.today().strftime("%Y-%m-%d")

                    save_record(
                        record_date,
                        data.get("total_amount", 0),
                        data.get("payment_method", "未知"),
                        total_calories,
                        data,
                    )
                    st.session_state["processed_receipts"][file_hash] = {
                        "data": data,
                        "total_calories": total_calories,
                    }
                    success_count += 1
                    st.success("✅ 已自动识别并入库完成。")

                current_total_calories += total_calories

                m_col1, m_col2, m_col3 = st.columns(3)
                m_col1.metric("💰 总金额", f"${data.get('total_amount', 0)}")
                m_col2.metric("💳 支付方式", data.get("payment_method", "未知"))
                m_col3.metric("📅 消费日期", data.get("date", "未知日期"))

                items_list = data.get("items", [])
                if items_list:
                    df = pd.DataFrame(items_list)
                    if "calories_estimate" in df.columns:
                        df["calories_estimate"] = pd.to_numeric(df["calories_estimate"], errors="coerce").fillna(0)

                    st.write(f"📝 消费明细（✨ 本单预估总热量: **{total_calories} kcal**）：")
                    st.data_editor(df, width="stretch")

                    if "name" in df.columns and "qty" in df.columns:
                        df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(1)
                        fig = px.pie(df, names="name", values="qty", title="消费单品数量分布")
                        st.plotly_chart(fig, width="stretch")

                with st.expander("👀 查看原始 JSON 数据"):
                    st.json(data)

        progress.progress(100, text="批量处理完成")
        st.session_state["last_failed_uploads"] = failed_uploads

        s1, s2, s3 = st.columns(3)
        s1.metric("✅ 新增成功", success_count)
        s2.metric("♻️ 复用结果", reused_count)
        s3.metric("❌ 失败数量", failed_count)

        if failed_count > 0:
            st.warning("有部分图片处理失败，可点击下方按钮重试失败项。")
            if st.button("🔁 重试失败项", use_container_width=True):
                retry_failed = st.session_state.get("last_failed_uploads", {})
                if not retry_failed:
                    st.info("当前没有可重试的失败项。")
                else:
                    retry_progress = st.progress(0, text="准备重试失败项...")
                    retry_success = 0
                    still_failed = {}
                    retry_items = list(retry_failed.items())

                    for retry_idx, (retry_hash, item) in enumerate(retry_items, start=1):
                        retry_progress.progress(
                            int((retry_idx - 1) / len(retry_items) * 100),
                            text=f"重试中 {retry_idx}/{len(retry_items)}：{item['name']}",
                        )
                        try:
                            retry_data = process_receipt(item["bytes"], api_key)
                        except Exception as retry_err:
                            still_failed[retry_hash] = item
                            st.error(f"重试失败：{item['name']} -> {retry_err}")
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
                        retry_success += 1

                    retry_progress.progress(100, text="失败项重试完成")
                    st.session_state["last_failed_uploads"] = still_failed
                    st.success(f"重试完成，成功 {retry_success} 张，仍失败 {len(still_failed)} 张。")

render_health_section(current_total_calories)
render_history_section()