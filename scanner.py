import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

# ====== ตั้งค่าเกณฑ์การสแกน (ปรับแก้ตัวเลขได้ตามต้องการ) ======
MIN_CHANGE_PCT = 2.0      # บวกขั้นต่ำกี่ % ขึ้นไป (เช่น 2.0 = +2%)
MIN_VOL_RATIO = 1.5       # วอลุ่มวันนี้เป็นกี่เท่าของค่าเฉลี่ย 20 วัน (เช่น 1.5 เท่า)
SEND_EMAIL = True         # ตั้งเป็น False หากต้องการเทสต์รันดูผลใน Log ก่อนส่งเมลจริง

# ใส่วันหยุดตลาดหลักทรัพย์ฯ เพิ่มเติมได้ในรูปแบบ "YYYY-MM-DD"
THAI_HOLIDAYS = {
    "2026-01-01", "2026-01-02", "2026-03-03", "2026-04-06",
    "2026-04-13", "2026-04-14", "2026-04-15", "2026-05-01",
    "2026-05-04", "2026-06-01", "2026-06-03", "2026-07-28",
    "2026-08-12", "2026-10-13", "2026-10-23", "2026-12-07",
    "2026-12-31",
}


def is_thai_market_open(now_bkk: datetime) -> tuple[bool, str]:
    date_str = now_bkk.strftime("%Y-%m-%d")
    if now_bkk.weekday() >= 5:
        return False, "วันนี้เป็นวันหยุดสุดสัปดาห์ (เสาร์-อาทิตย์)"
    if date_str in THAI_HOLIDAYS:
        return False, f"วันนี้เป็นวันหยุดตลาดหลักทรัพย์ฯ ({date_str})"

    current_minutes = now_bkk.hour * 60 + now_bkk.minute
    morning_start = 10 * 60         # 10:00
    morning_end = 12 * 60 + 30      # 12:30
    afternoon_start = 14 * 60 + 30  # 14:30
    afternoon_end = 16 * 60 + 30    # 16:30

    in_morning = morning_start <= current_minutes <= morning_end
    in_afternoon = afternoon_start <= current_minutes <= afternoon_end

    if in_morning or in_afternoon:
        return True, "ตลาดหุ้นไทยเปิดทำการอยู่"
    return False, f"นอกเวลาเปิดตลาดหุ้นไทย (เวลาไทยปัจจุบัน {now_bkk.strftime('%H:%M')})"


def load_tickers(filepath: str = "tickers.txt") -> list[str]:
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]


def scan_stocks(tickers: list[str]) -> list[dict]:
    alerts = []
    for ticker in tickers:
        try:
            df = yf.download(ticker, period="35d", interval="1d", progress=False, auto_adjust=False)
            if df is None or len(df) < 22:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.dropna(subset=["Close", "Volume"])
            if len(df) < 22:
                continue

            prev_close = float(df["Close"].iloc[-2])
            latest_close = float(df["Close"].iloc[-1])
            latest_vol = float(df["Volume"].iloc[-1])
            avg_vol_20 = float(df["Volume"].iloc[-21:-1].mean())

            if prev_close <= 0 or avg_vol_20 <= 0:
                continue

            change_pct = ((latest_close - prev_close) / prev_close) * 100.0
            vol_ratio = latest_vol / avg_vol_20

            if change_pct >= MIN_CHANGE_PCT and vol_ratio >= MIN_VOL_RATIO:
                alerts.append({
                    "ticker": ticker,
                    "price": round(latest_close, 2),
                    "change_pct": round(change_pct, 2),
                    "vol_ratio": round(vol_ratio, 2),
                })
        except Exception as e:
            print(f"[WARN] ดึงข้อมูล {ticker} ไม่สำเร็จ: {e}")

    alerts.sort(key=lambda x: x["change_pct"], reverse=True)
    return alerts


def send_gmail_alert(alerts: list[dict], now_bkk: datetime) -> None:
    sender_email = os.environ.get("GMAIL_USER", "").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    receiver_email = os.environ.get("GMAIL_TO", sender_email).strip()

    if not sender_email or not app_password:
        print("[INFO] ยังไม่ได้ตั้งค่า GMAIL_USER หรือ GMAIL_APP_PASSWORD ข้ามการส่งอีเมล")
        return

    subject = f"[SET Scanner] พบหุ้นเข้าเกณฑ์ {len(alerts)} ตัว ({now_bkk.strftime('%H:%M')} น.)"
    lines = [
        f"เวลาไทย: {now_bkk.strftime('%Y-%m-%d %H:%M:%S')}",
        f"เกณฑ์สแกน: บวก >= {MIN_CHANGE_PCT}% และ วอลุ่ม >= {MIN_VOL_RATIO}x ของค่าเฉลี่ย 20 วัน",
        "-" * 45,
    ]
    for item in alerts:
        lines.append(
            f"• {item['ticker']} | ราคา: {item['price']} | "
            f"เปลี่ยนแปลง: +{item['change_pct']}% | วอลุ่ม: {item['vol_ratio']}x"
        )

    body = "\n".join(lines)

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = receiver_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender_email, app_password)
        server.send_message(msg)

    print("[SUCCESS] ส่งแจ้งเตือนเข้า Gmail เรียบร้อยแล้ว")


def main() -> None:
    now_bkk = datetime.now(ZoneInfo("Asia/Bangkok"))
    is_open, reason = is_thai_market_open(now_bkk)
    print(f"[TIME] {now_bkk.strftime('%Y-%m-%d %H:%M:%S')} (Asia/Bangkok) -> {reason}")

    force_run = os.environ.get("FORCE_RUN", "false").lower() == "true"
    if not is_open and not force_run:
        return

    tickers = load_tickers("tickers.txt")
    print(f"[INFO] จำนวนหุ้นในลิสต์: {len(tickers)} ตัว")
    if not tickers:
        return

    results = scan_stocks(tickers)
    if not results:
        print("[INFO] รอบนี้ยังไม่มีหุ้นตัวไหนผ่านเกณฑ์")
        return

    print(f"[ALERT] หุ้นที่ผ่านเกณฑ์ ({len(results)} ตัว):")
    for r in results:
        print(f"  - {r['ticker']}: ราคา {r['price']} (+{r['change_pct']}%), วอลุ่ม {r['vol_ratio']}x")

    if SEND_EMAIL:
        send_gmail_alert(results, now_bkk)


if __name__ == "__main__":
    main()
