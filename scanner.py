import os
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import yfinance as yf

# รายชื่อหุ้นไทย 50 ตัว (ปรับให้ทันสมัยและไม่ติดหุ้นที่ถูกลิสต์ออก)
FALLBACK_SET50_TICKERS = [
    "ADVANC.BK", "AOT.BK", "AWC.BK", "BANPU.BK", "BBL.BK",
    "BDMS.BK", "BEM.BK", "BGRIM.BK", "BH.BK", "BJC.BK",
    "BTS.BK", "CBG.BK", "CENTEL.BK", "COM7.BK", "CPALL.BK",
    "CPF.BK", "CPN.BK", "CRC.BK", "DELTA.BK", "EA.BK",
    "EGCO.BK", "GLOBAL.BK", "GPSC.BK", "GULF.BK", "HMPRO.BK",
    "IVL.BK", "KBANK.BK", "KCE.BK", "KKP.BK", "KTB.BK",
    "KTC.BK", "LH.BK", "MINT.BK", "MTC.BK", "OR.BK",
    "OSP.BK", "PTT.BK", "PTTEP.BK", "PTTGC.BK", "RATCH.BK",
    "SAWAD.BK", "SCB.BK", "SCC.BK", "SCGP.BK", "SIRI.BK",
    "TISCO.BK", "TLI.BK", "TOP.BK", "TRUE.BK", "TTB.BK"
]

def get_thai_time():
    tz_bkk = timezone(timedelta(hours=7))
    return datetime.now(tz_bkk)

def is_thai_market_day(now_bkk):
    # 0=จันทร์ ... 4=ศุกร์, 5=เสาร์, 6=อาทิตย์
    return now_bkk.weekday() < 5

def fetch_stock_summary(tickers, batch_size=50):
    results = []
    # แบ่งสแกนเป็นชุดละ 50 ตัว
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        print(f"[INFO] กำลังดึงข้อมูลชุดที่ {i // batch_size + 1} ({len(batch)} ตัว)...")
        data = yf.download(batch, period="5d", interval="1d", group_by="ticker", progress=False)

        for ticker in batch:
            try:
                df = data[ticker] if len(batch) > 1 else data
                df = df.dropna(subset=["Close", "Open"])
                if len(df) < 1:
                    continue

                latest = df.iloc[-1]
                open_price = float(latest["Open"])
                close_price = float(latest["Close"])

                # เทียบกับราคาปิดเมื่อวาน (ถ้ามี) หรือเทียบกับราคาเปิดวันนี้
                if len(df) >= 2:
                    prev_close = float(df.iloc[-2]["Close"])
                else:
                    prev_close = open_price

                diff_from_open = close_price - open_price
                pct_from_open = (diff_from_open / open_price * 100) if open_price > 0 else 0.0

                diff_from_prev = close_price - prev_close
                pct_from_prev = (diff_from_prev / prev_close * 100) if prev_close > 0 else 0.0

                symbol_clean = ticker.replace(".BK", "")
                results.append({
                    "symbol": symbol_clean,
                    "open": open_price,
                    "current": close_price,
                    "diff_open": diff_from_open,
                    "pct_open": pct_from_open,
                    "pct_prev": pct_from_prev,
                })
            except Exception as e:
                print(f"[WARN] ข้าม {ticker}: {e}")
    return results

def send_gmail_report(subject, body_text):
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    gmail_to = os.environ.get("GMAIL_TO") or gmail_user

    if not gmail_user or not gmail_pass:
        print("[ERROR] ไม่พบค่า GMAIL_USER หรือ GMAIL_APP_PASSWORD ใน GitHub Secrets")
        return

    msg = MIMEMultipart()
    msg["From"] = gmail_user
    msg["To"] = gmail_to
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, gmail_to, msg.as_string())
        print(f"[SUCCESS] ส่งอีเมลรายงานไปยัง {gmail_to} เรียบร้อยแล้ว!")
    except Exception as e:
        print(f"[ERROR] ส่งอีเมลไม่สำเร็จ: {e}")

def main():
    now_bkk = get_thai_time()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "manual")
    time_str = now_bkk.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[TIME] {time_str} (Asia/Bangkok) | Trigger: {event_name}")

    # ถ้าเป็นการรันอัตโนมัติตามเวลา ให้ข้ามวันเสาร์-อาทิตย์
    if event_name == "schedule" and not is_thai_market_day(now_bkk):
        print("[INFO] วันเสาร์-อาทิตย์ ตลาดหุ้นไทยปิดทำการ ข้ามการส่งอีเมล")
        return

    # ระบุรอบเวลา (รอบเช้า หรือ รอบเย็น)
    if now_bkk.hour < 13:
        session_label = "รอบเช้า (ตลาดเปิด)"
    else:
        session_label = "รอบเย็น (สรุป/ใกล้ปิดตลาด)"

    print(f"[AUTO] กำลังสแกนหุ้นทั้งหมด {len(FALLBACK_SET50_TICKERS)} ตัว ({session_label})")
    stocks = fetch_stock_summary(FALLBACK_SET50_TICKERS, batch_size=50)

    if not stocks:
        print("[WARN] ไม่สามารถดึงข้อมูลราคาหุ้นได้ในรอบนี้")
        return

    # สร้างข้อความรายงานสรุปครบทั้ง 50 ตัว
    lines = [
        f"รายงานราคาหุ้นไทย ({session_label})",
        f"เวลาสแกน: {time_str} (เวลาไทย)",
        f"จำนวนหุ้นที่ดึงข้อมูลได้: {len(stocks)} ตัว",
        "=" * 48,
        "ชื่อหุ้น | ราคาเปิดเช้า | ราคาล่าสุด/ปิด | เทียบเปิด | เทียบเมื่อวาน",
        "-" * 48,
    ]

    for s in stocks:
        sign_open = "+" if s["diff_open"] >= 0 else ""
        sign_prev = "+" if s["pct_prev"] >= 0 else ""
        line = (
            f"{s['symbol']:<7} | "
            f"เปิด: {s['open']:>7.2f} | "
            f"ล่าสุด: {s['current']:>7.2f} | "
            f"({sign_open}{s['pct_open']:.2f}%) | "
            f"วันก่อน: {sign_prev}{s['pct_prev']:.2f}%"
        )
        lines.append(line)

    lines.append("=" * 48)
    report_body = "\n".join(lines)

    # พิมพ์ลง Log บน GitHub ด้วย
    print(report_body)

    # ส่งเข้า Gmail ทุกครั้งที่รันเสร็จ
    subject = f"[SET50] รายงานราคาหุ้น 50 ตัว - {session_label} ({now_bkk.strftime('%d/%m/%Y')})"
    send_gmail_report(subject, report_body)

if __name__ == "__main__":
    main()
