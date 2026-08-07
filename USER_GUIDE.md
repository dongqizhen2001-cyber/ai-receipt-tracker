# Smart Expense and Health Tracker — User Guide / 使用說明

## English

### 1. System Overview
Smart Expense and Health Tracker is a Hong Kong–oriented expense and health tracking
system. You take a photo of a paper receipt, and the system automatically extracts
the date, total amount, payment method and food items, estimates the calories, and
records everything into a local database.

Technology stack: Python 3.11, Streamlit, OpenCV, PaddleOCR (Traditional Chinese),
Doubao LLM (Volcengine ARK API), SQLite, SHA-256 deduplication.

### 2. Requirements
- Windows / macOS / Linux
- Python 3.11.9 or later
- Internet connection (only for the Doubao LLM API)
- A Doubao API key (Volcengine ARK)

### 3. Installation
1. Install Python 3.11.9 and make sure `python` is available in the terminal.
2. Open a terminal in the project folder and create a virtual environment:
   ```
   python -m venv .venv
   ```
3. Activate it and install dependencies:
   - Windows: `.venv\Scripts\activate`
   - macOS/Linux: `source .venv/bin/activate`
   ```
   pip install -r requirements.txt
   ```
4. Set up the API key: put your Doubao key in `app_settings.json`, or enter it in
   the sidebar when the app starts.

### 4. Starting the System
- Windows: double-click `start_webapp.bat`, or run:
  ```
  streamlit run app.py
  ```
- The browser will open at `http://localhost:8501`.

### 5. How to Use
1. **Upload receipts**: drag one or more receipt photos (PNG / JPG / JPEG) into the
   upload area.
2. **Choose OCR language**: select Traditional Chinese for Hong Kong receipts
   (Simplified Chinese and English are also available).
3. **Adjust preprocessing (optional)**: enable/disable deskewing, contrast
   enhancement and auto-zoom; rotate the photo first if it is badly tilted.
4. **Low-confidence warning**: if the OCR confidence is too low, the system asks you
   to retake the photo; you can still choose to continue.
5. **Review the results**: check the editable table, correct the date, amount,
   payment method or items if needed.
6. **Save**: confirmed records are stored in the local SQLite database
   (`finance.db`).
7. **Dashboard**: view spending summaries, calorie intake, the exercise
   recommendation plan and the 2026 monthly spending calendar; export data as CSV.

### 6. Notes
- All data is stored locally on your machine.
- The same receipt uploaded twice in one session is detected by SHA-256 and not
  processed again (saves API cost).
- Keep your API key private; do not commit `app_settings.json` to a public
  repository.
- Faded or damaged receipts may not be readable; retake the photo or enter the
  amount manually.

---

## 繁體中文

### 1. 系統簡介
「智能消費與健康追蹤系統」是一款針對香港場景設計的記賬與健康管理工具。用戶只需
拍攝紙本小票，系統便會自動辨識日期、總金額、支付方式與食物項目，估算卡路里，並
把資料存入本機資料庫。

技術組成：Python 3.11、Streamlit、OpenCV、PaddleOCR（繁體中文）、豆包大模型
（火山方舟 API）、SQLite、SHA-256 去重。

### 2. 環境需求
- Windows / macOS / Linux
- Python 3.11.9 或以上
- 可上網（僅用於豆包大模型 API）
- 豆包 API Key（火山方舟）

### 3. 安裝步驟
1. 安裝 Python 3.11.9，並確認終端機可以執行 `python`。
2. 在專案資料夾開啟終端機，建立虛擬環境：
   ```
   python -m venv .venv
   ```
3. 啟用虛擬環境並安裝依賴：
   - Windows：`.venv\Scripts\activate`
   - macOS/Linux：`source .venv/bin/activate`
   ```
   pip install -r requirements.txt
   ```
4. 設定 API Key：把豆包 Key 填到 `app_settings.json`，或於程式啟動後在側邊欄輸入。

### 4. 啟動系統
- Windows：雙擊 `start_webapp.bat`，或執行：
  ```
  streamlit run app.py
  ```
- 瀏覽器會自動開啟 `http://localhost:8501`。

### 5. 使用方法
1. **上傳小票**：把一張或多張小票照片（PNG / JPG / JPEG）拖入上傳區域。
2. **選擇辨識語言**：香港小票請選「繁體中文」（亦可選簡體中文或英文）。
3. **調整預處理（可選）**：可開關去斜、對比度增強與自動放大；若小票傾斜嚴重，
   可先旋轉照片。
4. **低置信度提示**：當 OCR 置信度過低時，系統會建議重新拍攝；使用者亦可選擇
   繼續處理。
5. **核對結果**：在可編輯表格中檢查日期、金額、支付方式與項目，如有錯誤可直接修改。
6. **儲存**：確認後的記錄會寫入本機 SQLite 資料庫（`finance.db`）。
7. **檢視儀表板**：查看消費統計、卡路里攝取、運動建議與 2026 月度消費月曆，
   並可匯出 CSV。

### 6. 注意事項
- 所有資料均儲存在本機，不會上傳至伺服器（除豆包 API 的文字請求外）。
- 同一張小票在同一工作階段重複上傳，會被 SHA-256 偵測到並直接沿用結果，
   節省 API 費用。
- 請妥善保管 API Key，切勿把 `app_settings.json` 上傳到公開的程式庫。
- 褪色或破損的小票可能無法辨識，建議重新拍攝或手動輸入金額。
