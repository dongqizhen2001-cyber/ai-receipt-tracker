import os
import argparse
import json
from pathlib import Path
from datetime import datetime
import requests
import cv2
import numpy as np

# 某些 Windows + CPU 环境会在 oneDNN 路径触发未实现算子，先禁用以保证可运行。
os.environ.setdefault("FLAGS_use_mkldnn", "0")

from paddleocr import PaddleOCR

def parse_args():
    parser = argparse.ArgumentParser(description="OCR image and save results")
    parser.add_argument("--img", default="test.png", help="Image path")
    parser.add_argument(
        "--lang",
        default="ch",
        choices=["ch", "chinese_cht", "en"],
        help="OCR language, use chinese_cht to try Traditional Chinese",
    )
    parser.add_argument("--out-dir", default="output", help="Output directory")
    parser.add_argument(
        "--use-deepseek",
        action="store_true",
        help="Send cleaned OCR text to DeepSeek for structured extraction",
    )
    parser.add_argument(
        "--deepseek-model",
        default="deepseek-chat",
        help="DeepSeek model name",
    )
    return parser.parse_args()


def init_ocr(lang):
    print("正在加载 OCR 模型，请稍候...")
    try:
        return PaddleOCR(use_angle_cls=True, lang=lang), lang
    except Exception as exc:
        if lang == "chinese_cht":
            print(f"繁体模型初始化失败，自动回退到简中模型。原因: {exc}")
            return PaddleOCR(use_angle_cls=True, lang="ch"), "ch"
        raise


def normalize_result(result):
    records = []
    for res in result or []:
        if not isinstance(res, list):
            continue
        for line in res:
            if (
                isinstance(line, list)
                and len(line) > 1
                and isinstance(line[1], (list, tuple))
                and len(line[1]) > 0
            ):
                text = line[1][0]
                score = line[1][1] if len(line[1]) > 1 else None
                box = line[0] if len(line) > 0 else None
                if text:
                    records.append({"text": text, "score": score, "box": box})
    return records


def average_confidence(records):
    scores = [float(item.get("score") or 0) for item in (records or [])]
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _score_records(records):
    # Prefer results with more meaningful text and better confidence.
    if not records:
        return 0.0
    total_chars = sum(len(str(item.get("text", ""))) for item in records)
    total_conf = sum(float(item.get("score") or 0) for item in records)
    return total_chars + total_conf * 5


def _extract_receipt_region(image):
    """Try to crop the likely receipt paper region from a full-frame photo."""
    if image is None:
        return None

    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    # Receipt is usually bright and low-saturation compared with table/background.
    mask = cv2.inRange(hsv, (0, 0, 130), (180, 80, 255))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    best = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(best)
    if area < 0.04 * (h * w):
        return None

    x, y, cw, ch = cv2.boundingRect(best)
    pad_x = int(cw * 0.04)
    pad_y = int(ch * 0.04)
    x0 = max(0, x - pad_x)
    y0 = max(0, y - pad_y)
    x1 = min(w, x + cw + pad_x)
    y1 = min(h, y + ch + pad_y)
    return image[y0:y1, x0:x1]


def _order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _find_receipt_contour(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
    for cnt in contours:
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) == 4:
            return approx.reshape(4, 2)
    return None


def _warp_perspective(image, pts):
    rect = _order_points(pts)
    (tl, tr, br, bl) = rect

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_width = int(max(width_a, width_b))

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_height = int(max(height_a, height_b))

    if max_width <= 0 or max_height <= 0:
        return None

    dst = np.array(
        [
            [0, 0],
            [max_width - 1, 0],
            [max_width - 1, max_height - 1],
            [0, max_height - 1],
        ],
        dtype="float32",
    )
    m = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, m, (max_width, max_height))


def preprocess_image(image):
    if image is None:
        return None

    base = _extract_receipt_region(image) or image
    contour = _find_receipt_contour(base)
    warped = _warp_perspective(base, contour) if contour is not None else base
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def robust_ocr(ocr_engine, image_path):
    """Run OCR after perspective correction + denoise to improve robustness."""
    image = cv2.imread(str(image_path))
    if image is None:
        return ocr_engine.ocr(str(image_path), cls=True)

    processed = preprocess_image(image)
    if processed is None:
        return ocr_engine.ocr(str(image_path), cls=True)
    return ocr_engine.ocr(processed, cls=True)


def build_clean_text(records):
    lines = [str(item.get("text", "")).strip() for item in records]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def call_deepseek(clean_text, model):
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY 环境变量")
    current_year = datetime.now().year

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 🔴 核心升级：告诉 AI 提取日期并估算卡路里
    system_prompt = (
        "你是香港地区的智能财务与健康助手。请将用户提供的小票 OCR 文本提取为 JSON 格式。\n"
        "必须且只能包含以下字段：\n"
        f"1. date: 消费日期 (格式 YYYY-MM-DD，如果小票没有写年份请默认{current_year}年，如果没有日期请留空)。\n"
        "2. total_amount: 总金额 (纯数字)。【非常重要】：请提取本次消费的实际支付金额（通常标为合计、总计、Total 或单个商品的加总）。绝不能提取“余额”、“八达通余额”、“Remaining Value”或“卡号”。\n"
        "3. payment_method: 支付方式 (如 八达通, 现金, 信用卡等)。\n"
        "4. items: 数组，每个元素包含 name(商品名), qty(数量, 默认为1), price(单价, 纯数字), calories_estimate(根据商品名称估算的卡路里整数值，比如可乐150，意粉600。如果是非食品如胶袋则为0)。\n"
        "只输出合法 JSON，不要输出任何解释标记或 Markdown 符号。"
    )

    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": clean_text},
        ],
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def save_outputs(records, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / "ocr_result.txt"
    json_path = out_dir / "ocr_result.json"
    clean_txt_path = out_dir / "receipt_clean.txt"

    txt_content = "\n".join(item["text"] for item in records)
    clean_content = build_clean_text(records)

    txt_path.write_text(txt_content, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )
    clean_txt_path.write_text(clean_content, encoding="utf-8-sig")
    return txt_path, json_path, clean_txt_path, clean_content


def main():
    args = parse_args()
    img_path = Path(args.img)
    out_dir = Path(args.out_dir)

    if not img_path.exists():
        raise FileNotFoundError(f"图片不存在：{img_path}")

    ocr, used_lang = init_ocr(args.lang)
    result = ocr.ocr(str(img_path), cls=True)
    records = normalize_result(result)
    txt_path, json_path, clean_txt_path, clean_content = save_outputs(records, out_dir)

if __name__ == "__main__":
    main()
