"""飆股雷達 v69：A1 首發突破 / A2 二次加碼 / B 跌深低接（依 2026-10-17「程式選股新架構：APP SCRIPT 雙 Workflow＋分時量矩陣預計算」最終版）。
價格與行情全部來自富邦 Neo API；法人買賣超用證交所＋櫃買官方資料（只當參考，不當篩選條件）。停損由你自己處理：不監控停損、不發停損通知。

【觸發方式】Google Apps Script 以 workflow_dispatch 呼叫兩條 workflow（GitHub 本身不設 cron）：
  18:00 後  baseline.yml → baseline.py ：盤後夜間運算，產出 initial_pools.json（隔日盤中唯一的靜態資料）＋更新 signal_history.json
  08:00~09:00 daily.yml → radar.py       ：盤前載入、暖機、盤中 15 秒掃描、13:35 收盤結算（執行檔即「單一合併檔案」）
  radar.py 一律用 Asia/Taipei 判斷時間：09:00 前啟動就等到開盤；09:05 後才啟動視為「延遲啟動」，先暖機 2 輪再開放訊號；
  13:25 後才啟動只做收盤結算。同一交易日 stock_states.json 已標 COMPLETED 時，重複觸發直接忽略。

【市場趨勢：只有 NORMAL / BEAR 兩態（上市用加權、上櫃用櫃買，各自獨立）】
  即時月線 MA20 =（前 19 個完整交易日指數收盤 + 盤中即時指數）/ 20；指數 ≥ MA20 ⇔ 指數 ≥ P（P = 前 19 日收盤平均，夜間算好存進 initial_pools.json）。
  NORMAL：A1 預估量 ≥ 5 日均量 × 1.5（搭配同期量比）、A2 開放、B 開放。
  BEAR  ：A1 預估量 ≥ 5 日均量 × 2.0（搭配同期量比）、A2 禁止、B 開放。
  切換防抖：NORMAL→BEAR 連續 2 輪低於即時月線；BEAR→NORMAL 站回即時月線並連續維持 5 分鐘；正式切換才 Telegram 通知一次。
  沒有 Hard Freeze，也沒有 DATA_ERROR_FREEZE。

【夜間（baseline.py）→ initial_pools.json】
  A1 候選池：5 日均量 ≥ 1,000 張 ＋ 昨收 ≤ max(昨日 MA5/10/20)；隔天盤中 A1 只掃描這批，不在盤中重新篩全市場。
  A2 追蹤池：過去 10 個交易日內「收盤確認成功」的 A1（盤中通知過但收盤沒站穩的不進池）。
  B 候選池：KD(9,3,3) 前日 K≤D、當日 K>D 且 K<40 ＋ 成交量 ≥ 前 20 日均量 × 2 ＋ 收盤 ≥ 最低價 × 1.02 ＋ 收盤位置 ≥ 60%。
  volume_time_matrix：每檔候選股、開盤後每一分鐘的「歷史同期累積量佔比」全部夜間算好；盤中只查表，不碰歷史分 K。
  initial_pools.json 一律「暫存檔 → fsync → os.replace」原子寫入；資料不完整時不覆寫前一版。

【盤中（radar.py）】09:05~13:24:45 每 15 秒一輪，連續 2 輪符合才通知；13:25 起停止所有新的個股訊號。
  A1：昨收 ≤ 昨日最高均線；現價同時站上盤中即時 MA5/10/20；漲幅 > 2%；現價 > 開盤價；現價 > 即時 VWAP；預估全日量 ≥ 1,000 張；
      同期量比 ≥ 1.5（BEAR 2.0；等價於預估全日量 ≥ 5 日均量 × 倍數）；預估全日成交額 ≥ 30 億。
  A2：僅追蹤池內；曾收盤跌破 MA5 但守住 MA10 與突破日低點，整理第 2~6 天；今日最低價 ≥ 突破日低點；現價 > 即時 MA5 且 > 即時 VWAP；
      預估全日量 ≥ 整理期平均量 × 1.2；預估全日成交額 ≥ 30 億；只在 NORMAL 發。
  B ：10:00 後；今日最低價 ≥ B 訊號日最低價；現價 > 即時 MA5 且 > 即時 VWAP；預估全日量 ≤ B 訊號日量 × 0.6；預估全日成交額 ≥ 2 億；NORMAL / BEAR 都發。
  通知優先序 A2 > A1 > B；同一檔同一天只發一種主訊號（ONE_SIGNAL_PER_STOCK）。A1 與 B 候選池可以重疊，沒有 B→A1 升級。
【13:35 收盤結算】用富邦最終快照（closePrice 且非試撮）；A1 收盤確認 → A1_CLOSE_CONFIRMED 進 A2 追蹤池；A2 收盤跌破 MA10 或突破日低點移出、滿 10 日移出；
  B 每過一個交易日 waiting_days+1，第 5 個交易日收盤仍未發動 → B_EXPIRED 移出（不發停損／出場通知）；寫 signal_history.json、pool_report.csv。
  18:00 後 baseline 會用官方日 K 核對當日 A1 收盤確認（close_provisional → official_reconciled）。
【檔案】initial_pools.json（夜間產出，盤中唯讀）、signal_history.json（追蹤清單，結算後更新）、stock_states.json（當日已通知名單＋趨勢狀態＋執行鎖）、pool_report.csv
【環境變數】TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS（憑證 fubon.p12；可用 FUBON_CERT_PATH 改路徑）、DEBUG_CODES=2330,2317（可選）
"""
import os
import re
import csv
import sys
import json
import time
import html
import asyncio
import pathlib
import threading
import functools
from datetime import datetime, timedelta, timezone, date as date_cls

import aiohttp
import requests

# =========================
# 基本設定
# =========================
TW_TZ = timezone(timedelta(hours=8), name="Asia/Taipei")
BASE_DIR = pathlib.Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "stock_states.json"
INITIAL_POOLS_FILE = BASE_DIR / "initial_pools.json"
POOL_FILE = BASE_DIR / "signal_history.json"
POOL_CSV_FILE = BASE_DIR / "pool_report.csv"
IC_CACHE_FILE = BASE_DIR / "ic_sector_cache.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
FUBON_CERT_PATH = os.environ.get("FUBON_CERT_PATH", "fubon.p12")
DEBUG_STOCK_CODES = {c.strip() for c in os.environ.get("DEBUG_CODES", "").split(",") if c.strip()}

MIN_VOL5_LOTS = 1000                # 盤後 A1 候選池初篩：5 日均量（張）；盤中 A1 另要求預估全日量 ≥ 此值

# ---- 防雜訊 ----
EVAL_START_MINUTE = 5              # 開盤後第 5 分鐘（09:05）才開始判斷
LATE_START_WARMUP_POLLS = 2        # 延遲啟動（09:05 之後才啟動）時，先暖機的輪數
# ---- 二次確認（Double-Confirmation）----
CONFIRM_MIN_GAP_SECONDS = 10        # 兩次符合至少相隔幾秒（避免同一份快照被重複計成兩次）
CONFIRM_MAX_GAP_SECONDS = 45        # 超過這麼久才再次符合，就不算「下一次掃描」，重新當成首次
# ---- 輪詢 ----
POLL_INTERVAL = 15                  # 秒；富邦快照上限 300 次/分，每輪只打 2 次（上市、上櫃）
FAIL_ALERT_AFTER = 3                # 連續失敗幾輪發 Telegram 警告並重新登入
FAIL_BACKOFF_MAX = 60
# ---- 時間 ----
MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE = 9, 0
CLOSE_CONFIRM_HOUR, CLOSE_CONFIRM_MINUTE = 13, 35
OPEN_TO_CLOSE_MINUTES = 271         # 09:00 ~ 13:30（含 13:30 收盤集合競價那一根）
SIGNAL_CUTOFF = (13, 24, 45)        # 13:24:45 後停止所有即時通知
SETTLE_DEADLINE = (13, 55)          # 收盤結算最晚等到 13:55 取得收盤價
SETTLE_RETRY_SEC = 30

# ---- 官方產業鏈（族群標籤）----
IC_CHAIN_URL = "https://ic.tpex.org.tw/company_chain.php"
IC_CACHE_DAYS = 30
IC_FETCH_BUDGET_SECONDS = 300
IC_MAX_CONCURRENCY = 4
IC_TIMEOUT_SECONDS = 15
IC_LABEL_TOP_N = 2
MA_WINDOW = 20
IC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Referer": "https://ic.tpex.org.tw/",
}
STOCK_CODE_RE = re.compile(r"^[1-9]\d{3}$")
EXCLUDE_NAME_KEYWORDS = ["ETF", "期", "權", "存託", "反1", "正2", "債", "基金", "受益", "指數"]
INDUSTRY_CODE_NAME = {
    "1": "水泥工業", "2": "食品工業", "3": "塑膠工業", "4": "紡織纖維",
    "5": "電機機械", "6": "電器電纜", "8": "玻璃陶瓷", "9": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "13": "電子工業",
    "14": "建材營造", "15": "航運業", "16": "觀光餐旅", "17": "金融保險",
    "18": "貿易百貨", "19": "綜合", "20": "其他", "21": "化學工業",
    "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業",
    "25": "電腦及週邊設備業", "26": "光電業", "27": "通信網路業",
    "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業",
    "34": "電子商務", "35": "綠能環保", "36": "數位雲端",
    "37": "運動休閒", "38": "居家生活",
}
UNRELIABLE_MARKETS = set()
ALERT_QUEUE = []

# =========================
# v69 參數（全部集中在這裡；要調規則只改這一段）
# =========================
RADAR_VERSION = "v69"
INITIAL_RULE = "v69"                # initial_pools.json 的規則版本；舊版檔案會被拒用
POOL_RULE = "q2"                    # signal_history.json 規則版本（q1 會自動升級）
POOL_RULES_OK = ("q1", "q2")
CLOSED_KEEP = 300
DAYS_KEEP = 90
ONE_SIGNAL_PER_STOCK = True         # 同一檔同一天只發一種主訊號（優先序 A2 > A1 > B）

# ---- 富邦日 K ----
LOOKBACK_CALENDAR_DAYS = 120
DAILY_FIELDS = "open,high,low,close,volume,turnover"
DAILY_CHUNK_DAYS = 330              # 富邦限制：單次查詢區間須小於 1 年
DAILY_SHARES_PER_LOT = 1000.0       # 整股日 K 的 volume 單位是「股」→ 除以 1000 = 張（會與快照自動校驗）
PREFILTER_RATIO = 0.4
PREFILTER_MIN_UNIVERSE = 150
FETCH_MIN_COVERAGE = 0.6
KD_PERIOD = 9
KD_MIN_DAYS = 30

# ---- A1（首發突破，純硬條件）----
A1_ENABLED = True
A1_CHG_MIN = 2.0                    # 漲幅 > 2%；不設上限
A1_VOL_RATIO = 1.5                  # NORMAL：同期量比 ≥ 1.5（等價於預估全日量 ≥ 5 日均量 × 1.5）
A1_VOL_RATIO_BEAR = 2.0             # BEAR：× 2.0
A1_MIN_EST_LOTS = 1000              # 預估全日成交量 ≥ 1,000 張
A_MIN_EST_TURNOVER = 3_000_000_000  # 預估全日成交金額 ≥ 30 億（A1、A2 共用）
VWAP_FILTER = True                  # 現價 > 即時 VWAP（硬條件）

# ---- A2（二次加碼）----
A2_ENABLED = True
A2_MIN_DAYS, A2_MAX_DAYS = 1, 6     # 拉回整理：連續「收盤 < MA5 且 ≥ MA10 且 ≥ 突破日低點」1～6 天
A2_VOL_RATIO = 1.2                  # 預估全日量 ≥ 整理期平均量 × 1.2
A2_REQUIRE_VWAP = True              # 現價 > 即時 VWAP
A_TRACK_DAYS = 10                   # A1 收盤確認後追蹤 10 個交易日

# ---- B（跌深低接）----
B_ENABLED = True
B_KD_LOW = 40.0                     # 前日 K ≤ 前日 D、當日 K > 當日 D、當日 K < 40
B_VOL_RATIO = 2.0                   # 當日成交量 ≥ 前 20 日均量 × 2
B_AVG_DAYS = 20
B_MIN_TURNOVER = 0                  # 爆量日最低成交額（元）；架構沒有這條，設 0 = 不篩
B_LOWCLOSE_PCT = 2.0                # 收盤 ≥ 當日最低價 × (1 + 2%)
B_CLOSE_LOC_MIN = 0.6               # 收盤位置 (收-低)/(高-低) ≥ 60%
B_WAIT_DAYS = 5                     # B 訊號後第 5 個交易日收盤仍未發動 → B_EXPIRED
B_ACTIVE_TRACK_DAYS = 5             # 發動（確認通知）後再追蹤幾個交易日（只管清單）
B_CONFIRM_START_MINUTE = 60         # 10:00 起才判斷
B_SHRINK_RATIO = 0.6                # 預估全日量 ≤ B 訊號日量 × 0.6
B_MIN_EST_TURNOVER = 200_000_000    # 預估全日成交金額 ≥ 2 億
B_REQUIRE_MA5 = True                # 現價 > 即時 MA5
B_REQUIRE_VWAP = True               # 現價 > 即時 VWAP（確認強度）
B_STATUS_MISS_DAYS = 3              # 連續幾天抓不到收盤資料 → 資料缺失移出

# ---- 法人（官方：證交所 T86、櫃買；只當參考，不是篩選條件）----
INST_DAYS = 5                       # 記錄近 5 個交易日
INST_RETRIES = 4                    # T 日資料還沒出來時，重試次數與間隔
INST_RETRY_WAIT = 180
INST_FALLBACK_FUBON = True          # 官方抓不到時，對 B 候選改用富邦 institutional_trades 補
TWSE_INST_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_INST_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
OFFICIAL_MIN_INTERVAL = 2.0
OFFICIAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}
TPEX_HEADERS = {**OFFICIAL_HEADERS, "Referer": "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/pricing.html",
                "Origin": "https://www.tpex.org.tw"}

# ---- 市場趨勢（NORMAL / BEAR）----
REGIME_ENTER_BEAR_ROUNDS = 2        # NORMAL → BEAR：連續 2 輪低於即時月線
REGIME_EXIT_HOLD_SEC = 300          # BEAR → NORMAL：站回即時月線並連續維持 5 分鐘

# ---- 分時量矩陣（夜間預計算；盤中只查表）----
R_DAYS = 20                         # 取樣近 20 個交易日（每分鐘佔比取中位數）
R_MIN_DAYS = 12                     # 至少要有 12 個完整日才算
R_BLEND = (0.7, 0.3)                # 個股 70% ＋ 全市場曲線 30%
R_FLOOR = 0.02                      # 該時間點累積佔比小於這個值就不推估（避免除以接近 0）
MATRIX_POINTS = 272                 # 索引 = 開盤後第幾分鐘（0=09:00 … 270=13:30）；271 = 收盤後（1.0）
BASELINE_CHUNK_DAYS = 20            # 1 分 K 一次請求涵蓋幾個交易日；前 3 檔驗證不足會自動改成 5 天一段
BASELINE_CALL_INTERVAL = 1.15       # 每次歷史行情呼叫的最小間隔（秒），富邦限速 60 次/分
BASELINE_MIN_COVERAGE = 0.6         # 成功算出矩陣的檔數 / 候選檔數 低於此值就視為失敗
BASELINE_MIN_CANDLES_PER_DAY = 30   # 某一天的分 K 少於這個根數，視為該日資料不完整
BASELINE_MAX_MINUTE = OPEN_TO_CLOSE_MINUTES - 1      # 270 = 13:30 那一根

STATE = {"date": "", "run": {}, "triggered": {}, "a2": {}, "b_confirm": {}, "signals": [], "regime_events": [], "regime": {}}
PENDING_A1, PENDING_A2, PENDING_BC = {}, {}, {}      # 二次確認待確認池（只存在記憶體）


# =========================
# 小工具
# =========================
def now_tw() -> datetime:
    return datetime.now(TW_TZ)


def _at(dt: datetime, hour: int, minute: int) -> datetime:
    return dt.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _market_open_today(dt: datetime) -> datetime:
    return _at(dt, MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE)


def _close_confirm_today(dt: datetime) -> datetime:
    return _at(dt, CLOSE_CONFIRM_HOUR, CLOSE_CONFIRM_MINUTE)


def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5


def _f(value):
    """轉成 float；None、空字串、無法轉換都回傳 None（0 和負數是有效值）。"""
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None


def minutes_since_open(t: datetime) -> float:
    return (t - _market_open_today(t)).total_seconds() / 60.0


def atomic_write_json(path: pathlib.Path, obj, indent=None) -> None:
    """原子寫入：先寫暫存檔、flush、fsync，再 os.replace 取代正式檔案（中途中斷也不會留下半份 JSON）。"""
    tmp = path.with_name(path.name + ".tmp")
    if indent is None:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(obj, ensure_ascii=False, indent=indent)
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


# =========================
# Telegram
# =========================
_SAFE_TAGS = ("b", "code")


def _safe_html(text: str) -> str:
    """只保留 <b>、<code> 兩種標籤，其他 < > & 一律跳脫（族群名稱裡的「>」也會被正確顯示）。"""
    for i, tag in enumerate(_SAFE_TAGS):
        text = text.replace(f"<{tag}>", f"\x00{i}O\x00").replace(f"</{tag}>", f"\x00{i}C\x00")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for i, tag in enumerate(_SAFE_TAGS):
        text = text.replace(f"\x00{i}O\x00", f"<{tag}>").replace(f"\x00{i}C\x00", f"</{tag}>")
    return text


def _send_telegram_chunk(message: str, retry: int = 3) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[模擬 Telegram]\n{message}", flush=True)
        return True
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(1, retry + 1):
        try:
            response = requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": _safe_html(message), "parse_mode": "HTML"},
                timeout=15,
            )
            if response.status_code == 200:
                return True
            print(f"Telegram HTTP={response.status_code}: {response.text[:200]}", flush=True)
        except Exception as exc:
            print(f"Telegram 第{attempt}次失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
        time.sleep(2 * attempt)
    return False


def send_telegram(message: str, retry: int = 3) -> bool:
    """超過 3800 字（Telegram 上限 4096）時，依段落拆成多則送出。"""
    limit = 3800
    if len(message) <= limit:
        return _send_telegram_chunk(message, retry)
    parts, buf = [], ""
    for block in message.split("\n\n"):
        if buf and len(buf) + len(block) + 2 > limit:
            parts.append(buf)
            buf = ""
        while len(block) > limit:
            cut = block.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            parts.append(block[:cut])
            block = block[cut:].lstrip("\n")
        buf = f"{buf}\n\n{block}" if buf else block
    if buf:
        parts.append(buf)
    ok = True
    for part in parts:
        ok = _send_telegram_chunk(part, retry) and ok
        time.sleep(0.5)
    return ok


def send_telegram_document(path: pathlib.Path, caption: str = "", retry: int = 3) -> bool:
    if not path.exists():
        print(f"找不到檔案: {path}", flush=True)
        return False
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[模擬 Telegram 傳檔] {path.name}｜{caption}", flush=True)
        return True
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    for attempt in range(1, retry + 1):
        try:
            with path.open("rb") as fh:
                response = requests.post(
                    url,
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": _safe_html(caption), "parse_mode": "HTML"},
                    files={"document": (path.name, fh, "text/csv")},
                    timeout=45,
                )
            if response.status_code == 200:
                return True
            print(f"Telegram 傳檔 HTTP={response.status_code}: {response.text[:200]}", flush=True)
        except Exception as exc:
            print(f"Telegram 傳檔第{attempt}次失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
        time.sleep(2 * attempt)
    return False


def enqueue_alert(message: str) -> None:
    ALERT_QUEUE.append(message)


ALERT_SEND_GAP = 1.0                 # 兩則個股通知之間間隔（秒），避免 Telegram 同一聊天室每秒限 1 則的限制


async def flush_alerts(loop) -> None:
    """一檔一則：佇列裡每則訊息獨立送出，不合併。"""
    if not ALERT_QUEUE:
        return
    batch, ALERT_QUEUE[:] = ALERT_QUEUE[:], []
    for msg in batch:
        await loop.run_in_executor(None, send_telegram, msg)
        await asyncio.sleep(ALERT_SEND_GAP)


# =========================
# 櫃買中心產業價值鏈（族群標籤）
# =========================
_IC_FOOTER_WORDS = ("使用條款", "隱私權", "網站地圖", "版權所有")


def parse_company_chain_html(page: str):
    """回傳 [(大類, 子類), ...]；None=頁面結構不符；[]=平台查無此公司。"""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html.unescape(text)
    if "查無公司資訊" in text:
        return []
    marker = text.find("所屬產業鏈如下")
    if marker < 0:
        return None
    chains = []
    for segment in text[marker:].split("►")[1:]:
        flat = " ".join(segment.split())
        if not flat or any(word in flat[:8] for word in _IC_FOOTER_WORDS):
            break
        for word in _IC_FOOTER_WORDS:
            cut = flat.find(word)
            if cut > 0:
                flat = flat[:cut].strip(" |")
        if ">" not in flat:
            continue
        top, sub = flat.split(">", 1)
        top, sub = top.strip(), sub.strip()
        if top and sub and len(top) <= 20 and len(sub) <= 80 and (top, sub) not in chains:
            chains.append((top, sub))
    return chains


def _load_ic_cache() -> dict:
    if not IC_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(IC_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_ic_cache(cache: dict) -> None:
    IC_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")


def _ic_cache_fresh(entry: dict, today: date_cls) -> bool:
    try:
        fetched = date_cls.fromisoformat(entry.get("fetched", ""))
    except ValueError:
        return False
    return (today - fetched).days < IC_CACHE_DAYS


async def _fetch_one_chain(session, semaphore, code):
    async with semaphore:
        try:
            async with session.get(
                IC_CHAIN_URL,
                params={"stk_code": code},
                timeout=aiohttp.ClientTimeout(total=IC_TIMEOUT_SECONDS),
            ) as response:
                if response.status != 200:
                    return code, None
                page = await response.text(errors="ignore")
        except Exception:
            return code, None
        await asyncio.sleep(0.3)
        return code, parse_company_chain_html(page)


async def refresh_ic_chains(codes) -> dict:
    today = now_tw().date()
    cache = _load_ic_cache()
    todo = [c for c in codes if not (c in cache and _ic_cache_fresh(cache[c], today))]
    print(f"[產業鏈] 快取 {len(codes) - len(todo)} 檔有效，需補查 {len(todo)} 檔（本次最多 {IC_FETCH_BUDGET_SECONDS} 秒）", flush=True)
    fetched_ok = not_listed = failed = 0
    layout_errors = 0
    if todo:
        started = time.time()
        semaphore = asyncio.Semaphore(IC_MAX_CONCURRENCY)
        connector = aiohttp.TCPConnector(limit=IC_MAX_CONCURRENCY)
        async with aiohttp.ClientSession(connector=connector, headers=IC_HEADERS) as session:
            batch_size = IC_MAX_CONCURRENCY * 10
            for i in range(0, len(todo), batch_size):
                if time.time() - started > IC_FETCH_BUDGET_SECONDS:
                    print(f"[產業鏈] 已達時間上限，剩餘 {len(todo) - i} 檔下次再補", flush=True)
                    break
                results = await asyncio.gather(
                    *[_fetch_one_chain(session, semaphore, c) for c in todo[i:i + batch_size]]
                )
                for code, chains in results:
                    if chains is None:
                        failed += 1
                        continue
                    cache[code] = {"fetched": today.isoformat(), "chains": [list(c) for c in chains]}
                    if chains:
                        fetched_ok += 1
                    else:
                        not_listed += 1
                first_batch = len(todo[:batch_size])
                if i == 0 and first_batch >= 10 and failed == first_batch:
                    layout_errors = failed
                    print("[產業鏈] 第一批全部查詢失敗，可能是網站無法連線或改版，本次停止補查", flush=True)
                    break
                _save_ic_cache(cache)
        _save_ic_cache(cache)
        print(f"[產業鏈] 補查完成：取得 {fetched_ok} 檔、平台未收錄 {not_listed} 檔、失敗 {failed} 檔，耗時 {time.time() - started:.1f}s", flush=True)
        if layout_errors:
            send_telegram("⚠️ <b>【飆股雷達】</b> 櫃買中心產業價值鏈平台查詢失敗，本次族群改用官方產業別顯示。")
    return {c: cache[c]["chains"] for c in codes if c in cache and cache[c].get("chains")}


def _chain_label(chains, top_n=IC_LABEL_TOP_N) -> str:
    return "、".join(f"{top}>{sub}" for top, sub in chains[:top_n])


def _is_excluded_name(name: str) -> bool:
    return any(key in name for key in EXCLUDE_NAME_KEYWORDS) or name.endswith(("特", "甲特", "乙特"))


def _trimmed_avg_vol(v5: list) -> float:
    """5 日均量，排除最大的一天（單日爆量不拉高基準）。"""
    if not v5:
        return 0.0
    if len(v5) < 5:
        return float(sum(v5)) / len(v5)
    trimmed = sorted(v5)[:-1]
    return float(sum(trimmed)) / len(trimmed)


def apply_ic_chains(stock_bases: dict, chain_map: dict) -> None:
    hit = 0
    for code, base in stock_bases.items():
        chains = chain_map.get(code)
        if chains:
            base["industry"] = _chain_label(chains)
            hit += 1
    print(f"[產業鏈] 採用櫃買產業鏈 {hit} 檔，其餘 {len(stock_bases) - hit} 檔使用官方產業別", flush=True)


# =========================
# 富邦 Neo API 行情
# =========================
try:  # 只有在 SDK 已安裝時才有；沒有也不影響本檔的語法檢查與離線測試
    from fubon_neo.fugle_marketdata.rest.base_rest import FugleAPIError  # type: ignore
except Exception:  # pragma: no cover
    FugleAPIError = Exception


class FubonFeed:
    """富邦行情連線。所有 SDK 呼叫都用同一把鎖序列化，避免多執行緒同時打 REST。"""

    def __init__(self):
        self.sdk = None
        self.stock = None
        self.lock = threading.RLock()
        self.index_syms = None
        self.index_names = []
        self._candle_fields_ok = True
        self._last_hist_call = 0.0
        self._adjusted_param_ok = True
        self._index_cache = {}
        self.daily_vol_div = DAILY_SHARES_PER_LOT

    # ---- 登入 ----
    def login(self) -> None:
        user_id = os.environ.get("FUBON_ID", "")
        api_key = os.environ.get("FUBON_API_KEY", "")
        cert_pass = os.environ.get("FUBON_CERT_PASS", "")
        missing = [n for n, v in (("FUBON_ID", user_id), ("FUBON_API_KEY", api_key), ("FUBON_CERT_PASS", cert_pass)) if not v]
        if missing:
            raise RuntimeError(f"缺少環境變數 {'、'.join(missing)}（請檢查 GitHub Secrets 名稱）")
        if not os.path.exists(FUBON_CERT_PATH):
            raise RuntimeError(f"找不到憑證檔 {FUBON_CERT_PATH}（請檢查 workflow 的「還原憑證」步驟）")
        from fubon_neo.sdk import FubonSDK  # type: ignore
        with self.lock:
            sdk = FubonSDK()
            res = sdk.apikey_login(user_id, api_key, FUBON_CERT_PATH, cert_pass)
            if not res.is_success:
                raise RuntimeError(f"富邦登入失敗：{res.message}")
            sdk.init_realtime()
            self.sdk = sdk
            self.stock = sdk.marketdata.rest_client.stock
            self.index_syms = None
        print("[富邦] 登入成功，行情連線建立完成", flush=True)

    # ---- 全市場快照 ----
    def _quotes(self, market: str):
        try:
            return self.stock.snapshot.quotes(market=market, type="COMMONSTOCK")
        except TypeError:                       # 舊版 SDK 沒有 type 參數
            return self.stock.snapshot.quotes(market=market)

    def snapshot_all(self) -> dict:
        """回傳 {"rows": {代號: 快照列}, "meta": {"tse": {date, time}, "otc": {...}}, "errors": [...]}。
        每一列另外加上 "_mkt"（"tse" / "otc"）。上市、上櫃都失敗才丟例外。"""
        rows, meta, errors = {}, {}, []
        with self.lock:
            for market in ("TSE", "OTC"):
                try:
                    r = self._quotes(market)
                except FugleAPIError as exc:
                    errors.append(f"{market} HTTP {getattr(exc, 'status_code', '?')}：{getattr(exc, 'response_text', exc)}")
                    continue
                except Exception as exc:
                    errors.append(f"{market} {type(exc).__name__}: {exc}")
                    continue
                key = market.lower()
                meta[key] = {"date": str(r.get("date", "")), "time": str(r.get("time", ""))}
                for item in r.get("data", []) or []:
                    code = str(item.get("symbol", ""))
                    if STOCK_CODE_RE.fullmatch(code):
                        item = dict(item)
                        item["_mkt"] = key
                        rows[code] = item
        if not rows:
            raise RuntimeError("；".join(errors) or "快照沒有任何資料")
        return {"rows": rows, "meta": meta, "errors": errors}

    # ---- 指數（加權、櫃買）----
    def index_symbols(self) -> dict:
        """用 intraday.tickers(type='INDEX') 的名稱找加權與櫃買指數代號（排除「報酬指數」）。結果快取；找不到的市場是 None（加權退回 IX0001）。
        指數清單會印在 log，第一次執行請核對。"""
        if self.index_syms is not None:
            return self.index_syms
        out, names = {"tse": None, "otc": None}, []
        try:
            listed = []                                       # (交易所, 代號, 名稱)
            for ex in ("TWSE", "TPEx"):
                try:
                    with self.lock:
                        r = self.stock.intraday.tickers(type="INDEX", exchange=ex)
                    for x in ((r.get("data") if isinstance(r, dict) else None) or []):
                        listed.append((ex, str(x.get("symbol")), str(x.get("name"))))
                except Exception as exc2:
                    print(f"[富邦] 查詢 {ex} 指數清單失敗：{type(exc2).__name__}: {exc2}", flush=True)
            names = [(s, n) for _, s, n in listed]
            for ex, sym, nm in listed:
                if "報酬" in nm:
                    continue
                if out["tse"] is None and ex == "TWSE" and "加權" in nm:
                    out["tse"] = sym
                if out["otc"] is None and ex == "TPEx" and ("櫃買" in nm or "櫃檯" in nm):
                    out["otc"] = sym
            print(f"[富邦] 櫃買(TPEx)指數清單：{[(s, n) for e, s, n in listed if e == 'TPEx']}", flush=True)
        except Exception as exc:
            print(f"[富邦] 查詢指數清單失敗：{type(exc).__name__}: {exc}", flush=True)
        if out["tse"] is None:
            out["tse"] = "IX0001"
        self.index_syms, self.index_names = out, names
        print(f"[富邦] 指數代號：加權 = {out['tse']}｜櫃買 = {out['otc']}（指數清單前 30 筆：{names[:30]}）", flush=True)
        return out

    def index_price(self, market: str):
        """即時指數（lastPrice）。失敗或沒有代號回傳 None。"""
        sym = self.index_symbols().get(market)
        if not sym:
            return None
        try:
            with self.lock:
                q = self.stock.intraday.quote(symbol=sym)
            return _f(q.get("lastPrice")) or _f(q.get("closePrice"))
        except Exception as exc:
            print(f"[富邦] 指數 {market} 查詢失敗：{type(exc).__name__}: {exc}", flush=True)
            return None

    def index_prices(self) -> dict:
        return {mk: self.index_price(mk) for mk in ("tse", "otc")}

    def quote_close(self, symbol: str):
        """單檔報價：收盤信號 isClose 為真（且不是試撮）才回傳 closePrice，否則 None。用於 13:35 結算時快照還沒給收盤價的個股（暫緩收盤延到 13:33）。"""
        try:
            with self.lock:
                q = self.stock.intraday.quote(symbol=symbol)
        except Exception as exc:
            print(f"[富邦] {symbol} 收盤價查詢失敗：{type(exc).__name__}: {exc}", flush=True)
            return None
        if not isinstance(q, dict) or not q.get("isClose") or q.get("isTrial"):
            return None
        return _f(q.get("closePrice"))

    def institutional(self, symbol: str, start: date_cls, end: date_cls):
        """富邦 ownership.institutional_trades（整理自證交所與櫃買日報）。回傳 {日期字串: (外資及陸資淨, 投信淨)}（股）；查詢失敗回傳 None。
        沒有法人進出的日子不會有那一筆（呼叫端當 0）。官方網站抓不到時才用（B 候選逐檔補）。"""
        try:
            self._hist_pace()
            with self.lock:
                res = self.stock.ownership.institutional_trades(**{"symbol": symbol, "from": start.isoformat(), "to": end.isoformat(), "sort": "asc"})
        except Exception as exc:
            print(f"[富邦] {symbol} 法人資料失敗：{type(exc).__name__}: {exc}", flush=True)
            return None
        out = {}
        for x in ((res.get("data") if isinstance(res, dict) else None) or []):
            out[str(x.get("date"))[:10]] = (float(((x.get("foreign") or {}).get("net")) or 0.0), float(((x.get("trust") or {}).get("net")) or 0.0))
        return out

    # ---- 日 K（歷史基底 / 大盤環境）----
    def _hist_pace(self) -> None:
        """歷史行情呼叫的最小間隔（富邦限速 60 次/分）。"""
        wait = BASELINE_CALL_INTERVAL - (time.time() - self._last_hist_call)
        if wait > 0:
            time.sleep(wait)
        self._last_hist_call = time.time()

    def _daily_chunk(self, symbol: str, start: date_cls, end: date_cls, fields: str, adjusted: bool = True):
        """單次日 K 查詢（區間須小於 1 年）。404（區間內沒有資料）回傳 []；429 等 61 秒重試；
        adjusted=True 的意思是「帶上 adjusted=false 參數」（個股要原始價格；舊版 SDK 不認得這個參數時自動退回不帶）。"""
        params = {"symbol": symbol, "from": start.isoformat(), "to": end.isoformat(), "timeframe": "D",
                  "sort": "asc", "fields": fields}
        if adjusted and self._adjusted_param_ok:
            params["adjusted"] = "false"
        for attempt in range(1, 4):
            self._hist_pace()
            try:
                with self.lock:
                    res = self.stock.historical.candles(**params)
                return res.get("data", []) if isinstance(res, dict) else []
            except FugleAPIError as exc:
                status = getattr(exc, "status_code", None)
                if status == 404:
                    return []
                if status == 429:
                    print(f"[富邦] {symbol} 日K 被限速（429），等待 61 秒（第 {attempt} 次）", flush=True)
                    time.sleep(61)
                    continue
                if status == 400 and "adjusted" in params:
                    print("[富邦] adjusted 參數被拒絕（SDK 版本較舊），改用預設（原始價格）", flush=True)
                    self._adjusted_param_ok = False
                    params.pop("adjusted")
                    continue
                raise
        raise RuntimeError(f"{symbol} 日K 連續 3 次失敗")

    def daily_candles(self, symbol: str, start: date_cls, end: date_cls) -> list:
        """個股日 K（原始價格、未還原權息），由舊到新：[{date, open, high, low, close, volume(張), value(元)}]。
        volume 原單位是股 → 除以 self.daily_vol_div 變成張；value = turnover（成交金額），缺值時用 收盤 × 股數 估。
        查詢區間 ≥ 1 年會自動分段。查無資料回傳 []。"""
        rows, s = {}, start
        while s <= end:
            e = min(end, s + timedelta(days=DAILY_CHUNK_DAYS - 1))
            for r in self._daily_chunk(symbol, s, e, DAILY_FIELDS):
                d = str(r.get("date"))[:10]
                o, h, l, c, v = (_f(r.get(k)) for k in ("open", "high", "low", "close", "volume"))
                if d and c and c > 0 and v is not None:
                    rows[d] = {"date": date_cls.fromisoformat(d), "open": o or c, "high": h or c, "low": l or c, "close": c,
                               "volume": v / self.daily_vol_div, "value": _f(r.get("turnover")) or c * v}
            s = e + timedelta(days=1)
        return [rows[k] for k in sorted(rows)]

    def index_closes(self, market: str, end: date_cls, calendar_days: int = 135) -> list:
        """指數日 K 收盤（富邦 historical.candles）。market = 'tse'（加權）/ 'otc'（櫃買）。回傳 [(日期, 收盤)] 由舊到新；失敗回傳 []。"""
        sym = self.index_symbols().get(market)
        if not sym:
            return []
        key = (sym, end.isoformat(), calendar_days)
        if key in self._index_cache:
            return self._index_cache[key]
        try:
            rows = self._daily_chunk(sym, end - timedelta(days=calendar_days), end, "close", adjusted=False)
        except Exception as exc:
            print(f"[大盤] {market} 指數日K查詢失敗：{type(exc).__name__}: {exc}", flush=True)
            return []
        out = sorted({(date_cls.fromisoformat(str(r["date"])[:10]), _f(r.get("close")))
                      for r in rows if r.get("date") and _f(r.get("close"))})
        if out:
            self._index_cache[key] = out
        return out

    def industry_map(self) -> dict:
        """{代號: 產業別名稱}。富邦 intraday.tickers 依產業別代碼逐一查詢（約 38 次）；某個產業別失敗就略過。"""
        out = {}
        for key, name in INDUSTRY_CODE_NAME.items():
            try:
                with self.lock:
                    res = self.stock.intraday.tickers(type="EQUITY", industry=key.zfill(2))
            except Exception as exc:
                print(f"[富邦] 產業別 {key} 查詢失敗：{type(exc).__name__}: {exc}", flush=True)
                continue
            for it in ((res.get("data") if isinstance(res, dict) else None) or []):
                sym = str(it.get("symbol", ""))
                if sym and sym not in out:
                    out[sym] = name
            time.sleep(0.25)
        return out

    # ---- 歷史 K 線（baseline.py 用）----
    def candles(self, symbol: str, start: str, end: str, timeframe: str = "1", fields="volume"):
        """回傳 K 線列表（時間由舊到新）；查無資料（404）回傳 []。429 會等 61 秒再試，最多 3 次。"""
        params = {"symbol": symbol, "from": start, "to": end, "timeframe": timeframe, "sort": "asc"}
        for attempt in range(1, 4):
            use_fields = bool(fields) and self._candle_fields_ok
            call = dict(params)
            if use_fields:
                call["fields"] = fields
            try:
                with self.lock:
                    res = self.stock.historical.candles(**call)
                return res.get("data", []) if isinstance(res, dict) else []
            except FugleAPIError as exc:
                status = getattr(exc, "status_code", None)
                if status == 404:
                    return []
                if status == 429:
                    print(f"[富邦] {symbol} 歷史行情被限速（429），等待 61 秒（第 {attempt} 次）", flush=True)
                    time.sleep(61)
                    continue
                if status == 400 and use_fields:
                    print(f"[富邦] fields={fields} 被拒絕（{getattr(exc, 'response_text', '')}），改用預設欄位", flush=True)
                    self._candle_fields_ok = False
                    continue
                raise
        raise RuntimeError(f"{symbol} 歷史行情連續 3 次失敗")


# =========================
# 顯示用小工具
# =========================
def _mmdd(day: str) -> str:
    return day[5:].replace("-", "/") if day else "-"


INDUSTRY_MAX_LEN = 18                # 族群文字超過這個長度就截斷加「...」


def _short(text: str, limit: int = INDUSTRY_MAX_LEN) -> str:
    text = text or "未分類"
    return text if len(text) <= limit else text[:limit] + "..."


def _p2(v) -> str:
    return "—" if v is None else f"{v:,.2f}"


def _pc(v, digits: int = 1) -> str:
    return "—" if v is None else f"{v:+.{digits}f}%"


def _md(date_str) -> str:
    return _mmdd(date_str) if date_str else "—"


# =========================
# 快照健康檢查（資料有沒有在更新）
# =========================
STALL_ALERT_POLLS = 8                # 全市場累積量連續 8 輪（約 2 分鐘）完全沒變，就發警告
STALL_CHECK_FROM_MINUTE = 3          # 開盤後第 3 分鐘起才檢查（開盤前累積量本來就不動）
STALL_CHECK_UNTIL_MINUTE = 265       # 13:25 後不檢查（尾盤只剩集合競價，量可能暫時不動）


def snapshot_lag_seconds(meta: dict, now: datetime):
    """快照時間欄位落後現在幾秒；欄位缺漏或格式不符回傳 None。"""
    best = None
    for m in meta.values():
        try:
            raw = str(m.get("time", "")).strip().zfill(6)[:6]
            d = str(m.get("date", "")).replace("-", "")
            dt = datetime.strptime(f"{d} {raw}", "%Y%m%d %H%M%S").replace(tzinfo=TW_TZ)
        except Exception:
            continue
        lag = (now - dt).total_seconds()
        best = lag if best is None else min(best, lag)
    return best


def fmt_updated(value) -> str:
    """富邦的 lastUpdated 是微秒時間戳，轉成台北時間 HH:MM:SS。"""
    try:
        return datetime.fromtimestamp(float(value) / 1_000_000, TW_TZ).strftime("%H:%M:%S")
    except Exception:
        return str(value) if value not in (None, "") else "-"


def snapshot_summary(snap: dict, now: datetime):
    """回傳 (全市場累積量張數, 這一輪 log 的快照摘要文字)。"""
    total = sum((_f(r.get("tradeVolume")) or 0.0) for r in snap["rows"].values())
    times = "/".join(f"{k.upper()} {snap['meta'][k].get('time', '?')}" for k in ("tse", "otc") if k in snap["meta"])
    lag = snapshot_lag_seconds(snap["meta"], now)
    lag_txt = "延遲 ?" if lag is None else f"延遲 {lag:.0f}s"
    return total, f"快照時間 {times}｜{lag_txt}｜全市場累積量 {total:,.0f} 張"


def sample_line(snap: dict, code: str = "2330") -> str:
    row = snap["rows"].get(code)
    if not row:
        return f"{code} 不在快照中"
    return (f"{code} {row.get('name', '')}｜現價 {row.get('lastPrice')}｜累積量 {row.get('tradeVolume')}"
            f"｜更新 {fmt_updated(row.get('lastUpdated'))}")


def snapshot_minutes(meta: dict, now: datetime) -> float:
    """這份快照對應「開盤後第幾分鐘」。快照時間欄位可信（落在現在前 3 分鐘內）就用它，否則用現在時間。"""
    t = now
    try:
        raw = str(meta.get("time", "")).strip().zfill(6)[:6]
        d = str(meta.get("date", "")).replace("-", "")
        dt = datetime.strptime(f"{d} {raw}", "%Y%m%d %H%M%S").replace(tzinfo=TW_TZ)
        if -60 <= (now - dt).total_seconds() <= 180:
            t = dt
    except Exception:
        pass
    return minutes_since_open(t)


def after_cutoff(t: datetime) -> bool:
    """13:24:45 之後（含）：停止所有即時通知。"""
    h, m, s = SIGNAL_CUTOFF
    return (t.hour, t.minute, t.second) >= (h, m, s)


# =========================
# 指標與判斷（盤中、收盤、夜間共用的純函式）
# =========================
def precompute_static(base: dict) -> dict:
    """昨日收盤後就固定的數字。ma*_cut = 今日收盤價「等於」該條均線的價位；prev_ma_max = 昨日 MA5/10/20 中最高者。"""
    base["ma5_cut"] = base["sum_4"] / 4
    base["ma10_cut"] = base["sum_9"] / 9
    base["ma20_cut"] = base["sum_19"] / 19
    base["prev_ma_max"] = max(base["true_ma5"], base["true_ma10"], base["true_ma20"])
    return base


def realtime_mas(base: dict, price: float):
    """含今日（盤中）價格的即時 MA5/10/20：前 4／9／19 個完整交易日收盤 + 現價。"""
    return ((base["sum_4"] + price) / 5, (base["sum_9"] + price) / 10, (base["sum_19"] + price) / 20)


def kdj_series(highs, lows, closes, n: int = KD_PERIOD):
    """逐日 KD（台灣慣用 9,3,3）：RSV = (收 - n 日低) / (n 日高 - n 日低) × 100；K = 2/3 前K + 1/3 RSV；D = 2/3 前D + 1/3 K；起始 50。回傳 [(K, D), ...]。"""
    k = d = 50.0
    out = []
    for i in range(len(closes)):
        lo = min(lows[max(0, i - n + 1):i + 1])
        hi = max(highs[max(0, i - n + 1):i + 1])
        rsv = 50.0 if hi == lo else (closes[i] - lo) / (hi - lo) * 100.0
        k = 2.0 / 3.0 * k + rsv / 3.0
        d = 2.0 / 3.0 * d + k / 3.0
        out.append((k, d))
    return out


def vwap_of(vol_lots, val_yuan):
    """當日成交均價 = 成交金額（元）÷ 成交股數（張 × 1000）。沒有成交回傳 None。"""
    if not vol_lots or vol_lots <= 0 or not val_yuan or val_yuan <= 0:
        return None
    return val_yuan / (vol_lots * 1000.0)


def chg_pct(base: dict, row: dict, price: float):
    """漲幅（%）。優先用富邦的 changePercent（已處理除權息參考價），沒有才用 change，最後才用昨收。"""
    cp = _f(row.get("changePercent"))
    if cp is not None:
        return cp
    ch = _f(row.get("change"))
    if ch is not None and price - ch > 0:
        return ch / (price - ch) * 100
    if base.get("yest_close"):
        return (price / base["yest_close"] - 1) * 100
    return None


# ---------- 分時量矩陣查表（夜間預計算；盤中只查表，不做任何歷史運算）----------
def matrix_ratio(table, x: float):
    """table = 夜間算好的該股累積佔比（索引 = 開盤後第幾分鐘）。x = 開盤後經過的分鐘數（可為小數）；同一分鐘內的 15 秒輪次共用該分鐘權重。
    該分鐘的值無效（缺值或 ≤ 0）時，往前找最近一個有效的時間點，絕不使用未來時間點。找不到回傳 None。"""
    if not table:
        return None
    i = int(x) if x >= 0 else -1
    if i < 0:
        return None
    i = min(i, len(table) - 1)
    while i >= 0:
        v = table[i]
        if v is not None and v > 0:
            return float(v)
        i -= 1
    return None


def estimate_full(vol_lots, val_yuan, table, x: float, avg5=None):
    """預估全日量與金額 = 當前累積 ÷ R_t（R_t = 矩陣查到的歷史同期累積佔比）。
    sync_ratio（歷史同期量比）= 當前累積量 ÷ (5 日均量 × R_t) ＝ 預估全日量 ÷ 5 日均量；R_t 小於 R_FLOOR 或查不到 → 回傳 None（不推估、不發訊號）。"""
    r = matrix_ratio(table, x)
    if r is None or r < R_FLOOR:
        return None
    vol, val = vol_lots or 0.0, val_yuan or 0.0
    sync = (vol / (avg5 * r)) if (avg5 and avg5 > 0) else None
    return {"R": r, "vol": vol, "val": val, "est_vol": vol / r, "est_val": val / r, "sync_ratio": sync}


# ---------- A1 ----------
A1_ORDER = ("chg", "red", "cross", "vwap", "vol", "turn")


def a1_stage(base: dict, price, open_, chg, mas, vwap, est, mode: str):
    """A1 純硬條件，依固定順序檢查；回傳第一個沒過的關卡（A1_ORDER 之一），全過回傳 None。"""
    if chg is None or chg <= A1_CHG_MIN:
        return "chg"
    if not open_ or price <= open_:
        return "red"
    if not (base["yest_close"] <= base["prev_ma_max"] and price > mas[0] and price > mas[1] and price > mas[2]):
        return "cross"
    if VWAP_FILTER and (vwap is None or price <= vwap):
        return "vwap"
    if est is None:
        return "vol"
    ratio = A1_VOL_RATIO_BEAR if mode == "BEAR" else A1_VOL_RATIO
    if est["sync_ratio"] is None or est["sync_ratio"] < ratio or est["est_vol"] < A1_MIN_EST_LOTS:
        return "vol"
    if est["est_val"] < A_MIN_EST_TURNOVER:
        return "turn"
    return None


# ---------- A2 ----------
def consolidation_run(dates, closes, vols_lots, after_date, floor_low=None):
    """拉回整理（截至最後一天）：連續「收盤 < MA5 且 ≥ MA10 且（有突破日低點時）≥ 突破日低點」的天數，且每一天都晚於 after_date（A1 突破日）。
    MA 用「含當天收盤」的真實均線。回傳 {"n": 天數, "avg_vol": 整理期平均量（張）}。"""
    n = len(closes)
    run, vs = 0, []
    i = n - 1
    while i >= 9 and dates[i] > after_date:
        ma5 = sum(closes[i - 4:i + 1]) / 5
        ma10 = sum(closes[i - 9:i + 1]) / 10
        if closes[i] < ma5 and closes[i] >= ma10 and (not floor_low or closes[i] >= floor_low):
            run += 1
            vs.append(vols_lots[i])
            i -= 1
        else:
            break
    return {"n": run, "avg_vol": (sum(vs) / len(vs)) if vs else None}


def a2_stage(a2: dict, floor_low, price, day_low, mas, vwap, est, mode: str):
    """A2：整理期 2～6 天；今日最低價 ≥ 突破日低點；現價 > 即時 MA5 且 > 即時 VWAP；預估全日量 ≥ 整理期平均量 × 1.2；預估全日金額 ≥ 30 億。只在 NORMAL 發。"""
    if mode != "NORMAL" or not a2 or not a2.get("n") or not a2.get("avg_vol"):
        return "none"
    if not (A2_MIN_DAYS <= a2["n"] <= A2_MAX_DAYS):
        return "days"
    if floor_low and (day_low is None or day_low < floor_low):
        return "floor"
    if price <= mas[0]:
        return "ma5"
    if A2_REQUIRE_VWAP and (vwap is None or price <= vwap):
        return "vwap"
    if est is None:
        return "vol"
    if est["est_vol"] < a2["avg_vol"] * A2_VOL_RATIO:
        return "vol"
    if est["est_val"] < A_MIN_EST_TURNOVER:
        return "turn"
    return None


# ---------- B ----------
def b_close_location(h, l, c) -> float:
    """收盤位置 = (收盤 - 最低) / (最高 - 最低)；最高 = 最低（一字）時視為 1.0，仍要通過其他條件。"""
    if h <= l:
        return 1.0
    return (c - l) / (h - l)


def b_scan_last(s: dict):
    """對「最後一天」（T 日）做 B 池夜間掃描；符合回傳細節 dict，否則 None。s = series_from_candles 的輸出（日 K，由舊到新，張）。
    條件：前日 K ≤ 前日 D、當日 K > 當日 D、當日 K < 40；成交量 ≥ 前 20 日均量 × 2；收盤 ≥ 最低價 × 1.02；收盤位置 ≥ 60%。"""
    n = len(s["c"])
    if n < max(KD_MIN_DAYS, B_AVG_DAYS + 2):
        return None
    o, h, l, c, v, val = s["o"][-1], s["h"][-1], s["l"][-1], s["c"][-1], s["v"][-1], s["val"][-1]
    if min(o, h, l, c) <= 0 or h < l:
        return None
    prior = s["v"][-(B_AVG_DAYS + 1):-1]
    avg20 = sum(prior) / len(prior)
    if avg20 <= 0 or v < avg20 * B_VOL_RATIO or val < B_MIN_TURNOVER:
        return None
    kd = kdj_series(s["h"], s["l"], s["c"])
    (kp, dp), (k, d) = kd[-2], kd[-1]
    if not (kp <= dp and k > d and k < B_KD_LOW):
        return None
    if c < l * (1 + B_LOWCLOSE_PCT / 100.0):
        return None
    loc = b_close_location(h, l, c)
    if loc < B_CLOSE_LOC_MIN:
        return None
    return {"burst_date": s["dates"][-1].isoformat(), "burst_low": l, "burst_high": h, "burst_open": o, "burst_close": c,
            "burst_vol": v, "burst_val": val, "burst_ratio": round(v / avg20, 2), "close_loc": round(loc, 3),
            "trigger": f"收盤位置 {loc * 100:.0f}%＋反彈 {(c / l - 1) * 100:.1f}%",
            "kd_k": round(k, 2), "kd_d": round(d, 2), "kd_k_prev": round(kp, 2), "kd_d_prev": round(dp, 2)}


def b_confirm_stage(day_low, est, burst_low, burst_vol, price, ma5, vwap):
    """B 隔日確認：今日最低價 ≥ B 訊號日最低價；預估全日量 ≤ B 訊號日量 × 0.6；現價 > 即時 MA5（可關）且 > 即時 VWAP（可關）；預估全日金額 ≥ 2 億。
    回傳沒過的關卡（'broken'/'est'/'volume'/'ma5'/'vwap'/'turn'）或 None。"""
    if day_low is None or day_low < burst_low:
        return "broken"
    if est is None:
        return "est"
    if est["est_vol"] > burst_vol * B_SHRINK_RATIO:
        return "volume"
    if B_REQUIRE_MA5 and not price > ma5:
        return "ma5"
    if B_REQUIRE_VWAP and (vwap is None or price <= vwap):
        return "vwap"
    if est["est_val"] < B_MIN_EST_TURNOVER:
        return "turn"
    return None


# =========================
# 市場趨勢（只有 NORMAL / BEAR；上市、上櫃各自獨立）
# =========================
def p_ma20(prior_closes):
    """即時月線穿越價 P = 前 19 日收盤平均。盤中即時 MA20 = (19 日收盤 + 即時指數) / 20；指數 ≥ MA20 ⇔ 指數 ≥ P。資料不足回傳 None。"""
    return sum(prior_closes[-19:]) / 19.0 if len(prior_closes) >= 19 else None


class IndexWatch:
    """單一指數的趨勢狀態機。NORMAL → BEAR：連續 REGIME_ENTER_BEAR_ROUNDS 輪低於即時月線；
    BEAR → NORMAL：站回即時月線（≥ P）並連續維持 REGIME_EXIT_HOLD_SEC 秒，期間任何一輪再跌回月線下方，計時歸零。讀不到指數的那一輪視為無法確認（計數歸零）。"""

    def __init__(self, name: str, p: float, state: str = "NORMAL", last_close: float = None):
        self.name, self.p, self.state, self.last_close = name, p, state, last_close
        self.below_rounds = 0
        self.above_since = None
        self.last_reading = None
        self.switches = 0

    def update(self, reading, now_sec: float):
        """每輪餵入即時指數與時間（秒）。回傳 'TO_BEAR'（本輪正式轉空頭）/ 'TO_NORMAL'（本輪正式恢復多頭）/ None。"""
        if reading is None:
            self.below_rounds = 0
            self.above_since = None
            return None
        self.last_reading = reading
        below = reading < self.p
        if self.state == "NORMAL":
            self.above_since = None
            if below:
                self.below_rounds += 1
                if self.below_rounds >= REGIME_ENTER_BEAR_ROUNDS:
                    self.state, self.below_rounds = "BEAR", 0
                    self.switches += 1
                    return "TO_BEAR"
            else:
                self.below_rounds = 0
            return None
        self.below_rounds = 0                       # BEAR
        if below:
            self.above_since = None
            return None
        if self.above_since is None:
            self.above_since = now_sec
        if now_sec - self.above_since >= REGIME_EXIT_HOLD_SEC:
            self.state, self.above_since = "NORMAL", None
            self.switches += 1
            return "TO_NORMAL"
        return None


class Regime:
    """market 'tse' / 'otc' → IndexWatch。個股用自己市場的指數；該市場沒有指數資料時退回加權；都沒有 → NORMAL（啟動訊息會警告）。"""

    def __init__(self, watches: dict):
        self.w = watches

    def mode(self, market: str) -> str:
        w = self.w.get(market) or self.w.get("tse") or self.w.get("otc")
        return w.state if w is not None else "NORMAL"


def build_regime(ctx: dict, saved: dict = None) -> Regime:
    """ctx = {market: {"name", "p", "last_close", "init_state"}}（夜間存在 initial_pools.json）。saved = 今天稍早存下的狀態（程式中途重啟時還原）。"""
    watches = {}
    for mk, c in (ctx or {}).items():
        p = c.get("p")
        if not p:
            continue
        state = ((saved or {}).get(mk) or {}).get("state") or c.get("init_state") or "NORMAL"
        watches[mk] = IndexWatch(c.get("name", mk), float(p), state if state in ("NORMAL", "BEAR") else "NORMAL", c.get("last_close"))
    return Regime(watches)


# =========================
# 股池狀態（signal_history.json，rule = q2）
#   a1_pending：今日盤中 A1 已通知、等收盤確認   a：A1_CLOSE_CONFIRMED（A2 追蹤池，10 個交易日）   b：B 池（Watching 等待 5 日 / Active 已發動）
# =========================
def new_pools() -> dict:
    return {"rule": POOL_RULE, "asof": "", "updated": "", "days": [], "a1_pending": {}, "a": {}, "b": {}, "closed": []}


def load_pools():
    """回傳 (pools, 說明)。檔案不存在 / 規則版本不認得 → 空股池 + 說明。q1 會自動升級成 q2。"""
    if not POOL_FILE.exists():
        return new_pools(), "找不到 signal_history.json，追蹤清單從空的開始"
    try:
        data = json.loads(POOL_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return new_pools(), f"signal_history.json 讀取失敗（{type(exc).__name__}），追蹤清單從空的開始"
    if data.get("rule") not in POOL_RULES_OK:
        return new_pools(), f"signal_history.json 是舊版格式（{data.get('rule')}），已忽略；追蹤清單從空的開始"
    pools = new_pools()
    pools.update({"asof": data.get("asof", ""), "updated": data.get("updated", ""), "days": list(data.get("days") or []),
                  "a1_pending": dict(data.get("a1_pending") or {}), "a": dict(data.get("a") or {}),
                  "b": dict(data.get("b") or {}), "closed": list(data.get("closed") or [])})
    upgraded = ""
    if data.get("rule") == "q1":
        for e in pools["a"].values():
            e.setdefault("breakout_low", None)
        upgraded = "（q1 → q2：舊追蹤標的視為已確認，沒有突破日低點，A2 的低點檢查略過）"
    return pools, (f"A2 追蹤池 {len(pools['a'])} 檔｜B 追蹤 {len(pools['b'])} 檔｜待收盤確認 A1 {len(pools['a1_pending'])} 檔"
                   f"（截至 {pools['asof'] or '—'}）{upgraded}")


def save_pools(pools: dict) -> None:
    pools["rule"] = POOL_RULE
    pools["days"] = sorted(set(pools.get("days") or []))[-DAYS_KEEP:]
    pools["closed"] = (pools.get("closed") or [])[-CLOSED_KEEP:]
    pools["updated"] = now_tw().isoformat(timespec="seconds")
    atomic_write_json(POOL_FILE, pools)


def days_since(pools: dict, date_str: str, *, inclusive: bool) -> int:
    """追蹤日曆裡，晚於（inclusive=False）或不早於（inclusive=True）date_str 的交易日數。"""
    days = pools.get("days") or []
    return len([d for d in days if (d >= date_str if inclusive else d > date_str)])


def b_window_offset(pools: dict, e: dict) -> int:
    """盤中：今天是 B 訊號日後第幾個交易日（今天還沒寫進 days，所以 +1）。"""
    return days_since(pools, e["burst_date"], inclusive=False) + 1


def close_record(pools: dict, pool: str, code: str, e: dict, day: str, reason: str, exit_price=None, extra=None) -> dict:
    """把移出寫進 closed 並回傳該筆紀錄。ret_pct 以通知價計（Watching 沒有 → None）。"""
    entry = e.get("entry_price")
    ret = ((exit_price / entry - 1) * 100.0) if (exit_price and entry) else None
    rec = {"pool": pool, "code": code, "name": e.get("name", code), "date": day, "reason": reason,
           "entry_date": e.get("entry_date") or e.get("burst_date") or "", "entry_price": entry,
           "exit_price": exit_price, "ret_pct": None if ret is None else round(ret, 2), "origin": e.get("origin", ""),
           "burst_date": e.get("burst_date", "")}
    if extra:
        rec.update(extra)
    pools.setdefault("closed", []).append(rec)
    return rec


# =========================
# 追蹤清單異動（通知發出的當下就寫進去）
# =========================
def pool_a1_pending_add(pools: dict, sig: dict, today_str: str, now: datetime) -> None:
    """A1 盤中通知發出 → 先放「待收盤確認」；收盤站穩三均線才會進 A2 追蹤池。"""
    pools.setdefault("a1_pending", {})[sig["code"]] = {
        "name": sig["name"], "industry": sig.get("industry", ""), "market": sig.get("market", ""),
        "notify_date": today_str, "notify_time": now.strftime("%H:%M:%S"), "notify_price": sig["price"]}


def pool_a2_mark(pools: dict, sig: dict, today_str: str) -> None:
    e = pools["a"].get(sig["code"])
    if e is not None:
        e.setdefault("a2_dates", []).append(today_str)


def pool_b_confirm(pools: dict, sig: dict, today_str: str, now: datetime) -> None:
    """B 確認發動：Watching → Active（之後再追蹤 B_ACTIVE_TRACK_DAYS 個交易日，只管清單）。"""
    e = pools["b"].get(sig["code"])
    if e is None:
        return
    e.update({"status": "Active", "entry_date": today_str, "entry_time": now.strftime("%H:%M:%S"), "entry_price": sig["price"],
              "track_days": B_ACTIVE_TRACK_DAYS})


# =========================
# 收盤結算（13:35 起；只用已產生的收盤價）
# =========================
def close_info_from_row(row):
    """快照列 → (收盤資料 dict 或 None, 原因)。收盤價必須已產生：closePrice 有值且 isTrial 不是真。13:25～13:30 的 lastPrice 含試撮，絕不使用。"""
    if row is None:
        return None, "nodata"
    trial = row.get("isTrial")
    if trial is True or str(trial).lower() == "true":
        return None, "trial"
    close = _f(row.get("closePrice"))
    if not close:
        return None, "noclose"
    return {"close": close, "open": _f(row.get("openPrice")), "high": _f(row.get("highPrice")) or close,
            "low": _f(row.get("lowPrice")) or close, "vol": _f(row.get("tradeVolume")) or 0.0, "val": _f(row.get("tradeValue")) or 0.0}, ""


def _a_row(pools: dict, code: str, e: dict, close: float, ma5: float, ma10: float, ma20: float, new_today: bool) -> dict:
    entry = e.get("entry_price")
    age = days_since(pools, e["entry_date"], inclusive=False)
    return {"code": code, "name": e.get("name", code), "industry": e.get("industry", ""), "status": e.get("status", "Active"),
            "origin": e.get("origin", "A1"), "entry_date": e["entry_date"], "entry_price": entry, "close": close,
            "ret_pct": ((close / entry - 1) * 100.0) if entry else None, "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "gap_ma10": (close / ma10 - 1) * 100.0, "breakout_low": e.get("breakout_low"), "age": age,
            "days_left": max(0, A_TRACK_DAYS - age), "a2_dates": e.get("a2_dates") or [], "new_today": new_today,
            "provisional": bool(e.get("close_provisional"))}


def a1_confirm_settle(pools: dict, bases: dict, closes: dict, today_str: str, skip=()):
    """A1 盤中通知 → 收盤確認：收盤價站上（含收盤價重算的）MA5/MA10/MA20 ＋ 收盤未跌破突破日低點 → A1_CLOSE_CONFIRMED，進 A2 追蹤池（突破日低點 = 當日最低價）。
    沒站穩 → 不進池（記錄在 closed）。今天沒取得收盤價的（skip）維持待確認。回傳 (確認的列表列, 確認的代號, 失敗紀錄)。"""
    rows, codes, failed = [], [], []
    for code, e in list(pools.get("a1_pending", {}).items()):
        if code in skip:
            continue
        like = {"name": e.get("name", code), "entry_date": e.get("notify_date"), "entry_price": e.get("notify_price"), "origin": "A1"}
        if e.get("notify_date") != today_str:                         # 前次沒結算到的：不能拿今天的收盤回推，逾期移出
            pools["a1_pending"].pop(code)
            failed.append(close_record(pools, "A1", code, like, today_str, "A1未結算逾期"))
            continue
        base, c = bases.get(code), closes.get(code)
        if not base or not c:
            pools["a1_pending"].pop(code)
            failed.append(close_record(pools, "A1", code, like, today_str, "A1收盤資料缺失"))
            continue
        close, low = c["close"], c["low"]
        ma5, ma10, ma20 = realtime_mas(base, close)
        ok = close > ma5 and close > ma10 and close > ma20 and close >= low
        pools["a1_pending"].pop(code)
        if not ok:
            failed.append(close_record(pools, "A1", code, like, today_str, "A1收盤未確認（沒站穩三均線）", close))
            continue
        new_e = {"name": e.get("name", code), "industry": base.get("industry") or e.get("industry", ""), "market": e.get("market", ""),
                 "entry_date": today_str, "entry_time": e.get("notify_time", ""), "entry_price": e.get("notify_price"),
                 "breakout_low": low, "status": "Active", "origin": "A1_CLOSE_CONFIRMED", "last_close": close, "last_date": today_str,
                 "miss_days": 0, "a2_dates": [], "close_src": "final_snapshot", "close_provisional": True, "src": "live"}
        pools["a"][code] = new_e
        codes.append(code)
        rows.append(_a_row(pools, code, new_e, close, ma5, ma10, ma20, True))
    return rows, codes, failed


def pool_a_close_update(pools: dict, bases: dict, closes: dict, today_str: str, skip=()):
    """A2 追蹤池收盤結算（只處理「今天以前」已確認的）。回傳 (列表列, 今日移出紀錄)。
    收盤跌破 MA10 或跌破突破日低點 → 移出並重置該週期；確認後滿 A_TRACK_DAYS 個交易日 → 追蹤期滿移出。"""
    rows, exits = [], []
    for code, e in list(pools["a"].items()):
        if code in skip:
            continue
        base, c = bases.get(code), closes.get(code)
        if not base or not c:
            e["miss_days"] = e.get("miss_days", 0) + 1
            if e["miss_days"] >= B_STATUS_MISS_DAYS:
                pools["a"].pop(code)
                exits.append(close_record(pools, "A", code, e, today_str, "資料缺失移出", e.get("last_close")))
            continue
        e["miss_days"] = 0
        close = c["close"]
        ma5, ma10, ma20 = realtime_mas(base, close)
        e["last_close"], e["last_date"] = close, today_str
        if base.get("industry"):
            e["industry"] = base["industry"]
        age = days_since(pools, e["entry_date"], inclusive=False)
        floor = e.get("breakout_low")
        if close < ma10:
            pools["a"].pop(code)
            exits.append(close_record(pools, "A", code, e, today_str, "收盤跌破MA10", close, {"ma10": round(ma10, 2), "age": age}))
            continue
        if floor and close < floor:
            pools["a"].pop(code)
            exits.append(close_record(pools, "A", code, e, today_str, "收盤跌破突破日低點", close, {"floor": floor, "age": age}))
            continue
        if age >= A_TRACK_DAYS:
            pools["a"].pop(code)
            exits.append(close_record(pools, "A", code, e, today_str, "追蹤期滿", close, {"age": age}))
            continue
        e["status"] = "Active" if close >= ma5 else "Pullback"
        rows.append(_a_row(pools, code, e, close, ma5, ma10, ma20, False))
    return rows, exits


def b_row_dict(pools: dict, e: dict, code: str, close: float, ma5: float, ma10: float, ma20: float, today_str: str) -> dict:
    off = days_since(pools, e["burst_date"], inclusive=False)
    entry = e.get("entry_price")
    age = days_since(pools, e["entry_date"], inclusive=False) if e.get("entry_date") else None
    watching = e.get("status") == "Watching"
    return {"code": code, "name": e.get("name", code), "industry": e.get("industry", ""), "status": e.get("status"),
            "burst_date": e["burst_date"], "burst_low": e["burst_low"], "burst_ratio": e.get("burst_ratio"), "trigger": e.get("trigger", ""),
            "kd_k": e.get("kd_k"), "kd_d": e.get("kd_d"), "close": close, "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "gap_low": (close / e["burst_low"] - 1) * 100.0, "entry_date": e.get("entry_date"), "entry_price": entry,
            "ret_pct": ((close / entry - 1) * 100.0) if entry else None, "age": age, "waiting_days": off,
            "days_left": (max(0, B_WAIT_DAYS - off) if watching else max(0, e.get("track_days", B_ACTIVE_TRACK_DAYS) - (age or 0))),
            "inst5": e.get("inst5") or [], "new_today": e["burst_date"] == today_str}


def pool_b_close_update(pools: dict, bases: dict, closes: dict, today_str: str, skip=()):
    """B 池收盤結算。每過一個交易日 waiting_days += 1。
    Watching：今日最低價跌破 B 訊號日最低價 → 失效；第 B_WAIT_DAYS 個交易日收盤仍未發動 → B_EXPIRED（NO_ACTIVATION_WITHIN_5_TRADING_DAYS）。
    Active：收盤跌破 B 訊號日最低價 → 防線失守移出；發動後滿 track_days 個交易日 → 到期移出。只管清單，不發停損 / 出場通知。"""
    rows, exits = [], []
    for code, e in list(pools["b"].items()):
        if code in skip:
            continue
        base, c = bases.get(code), closes.get(code)
        if not base or not c:
            e["miss_days"] = e.get("miss_days", 0) + 1
            if e["miss_days"] >= B_STATUS_MISS_DAYS:
                pools["b"].pop(code)
                exits.append(close_record(pools, "B", code, e, today_str, "資料缺失移出", e.get("last_close")))
            continue
        e["miss_days"] = 0
        close, low = c["close"], c["low"]
        ma5, ma10, ma20 = realtime_mas(base, close)
        e["last_close"], e["last_date"] = close, today_str
        if base.get("industry"):
            e["industry"] = base["industry"]
        off = days_since(pools, e["burst_date"], inclusive=False)
        e["waiting_days"] = off
        if e.get("status") == "Watching":
            if low < e["burst_low"]:
                pools["b"].pop(code)
                exits.append(close_record(pools, "B", code, e, today_str, "失效：跌破B低點防線", close))
                continue
            if off >= B_WAIT_DAYS:
                pools["b"].pop(code)
                exits.append(close_record(pools, "B", code, e, today_str, "B_EXPIRED:NO_ACTIVATION_WITHIN_5_TRADING_DAYS", close,
                                          {"status": "B_EXPIRED", "waiting_days": off}))
                continue
        else:
            age = days_since(pools, e["entry_date"], inclusive=False)
            if close < e["burst_low"]:
                pools["b"].pop(code)
                exits.append(close_record(pools, "B", code, e, today_str, "防線失守移出", close, {"age": age}))
                continue
            if age >= e.get("track_days", B_ACTIVE_TRACK_DAYS):
                pools["b"].pop(code)
                exits.append(close_record(pools, "B", code, e, today_str, "追蹤到期移出", close, {"age": age}))
                continue
        rows.append(b_row_dict(pools, e, code, close, ma5, ma10, ma20, today_str))
    return rows, exits


def settle_close(pools: dict, bases: dict, closes: dict, today_str: str, skip=()) -> dict:
    """收盤結算（純資料處理，不碰網路與 Telegram）。skip = 還沒取得收盤價的代號：不更新、不算缺資料，只在報表標註。
    順序：先處理「今天以前」已在追蹤池的 → 再確認今天盤中通知的 A1（確認後才進 A2 追蹤池）→ B 池。"""
    if today_str not in pools["days"]:
        pools["days"].append(today_str)
    a_rows, a_exits = pool_a_close_update(pools, bases, closes, today_str, skip)
    conf_rows, conf_codes, a1_failed = a1_confirm_settle(pools, bases, closes, today_str, skip)
    b_rows, b_exits = pool_b_close_update(pools, bases, closes, today_str, skip)
    unsettled = sorted(c for c in skip if c in pools["a"] or c in pools["b"] or c in pools["a1_pending"])
    pools["asof"] = today_str
    return {"a_rows": a_rows + conf_rows, "b_rows": b_rows, "a_exits": a_exits, "b_exits": b_exits,
            "a1_confirmed": conf_codes, "a1_failed": a1_failed, "unsettled": unsettled}


# =========================
# 分時量矩陣（夜間預先算好，寫進 initial_pools.json 的 volume_time_matrix；盤中只查表）
# =========================
def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return 0.0 if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def candle_minute(ts: str):
    """'2026-07-09T13:30:00.000+08:00' → ('2026-07-09', 270)；解析失敗回傳 None。"""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(TW_TZ)
    except Exception:
        return None
    return dt.strftime("%Y-%m-%d"), dt.hour * 60 + dt.minute - (MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MINUTE)


def day_curves(candles: list, days: list):
    """把 1 分 K 依日期整理成 {日期: 長度 MATRIX_POINTS 的累積量 list}：cum[m] = 開盤後第 m 分鐘『開始前』的累積量（張），cum[271] = 全日量。
    某天分 K 不足 BASELINE_MIN_CANDLES_PER_DAY 根就不放進結果。"""
    by_day = {d: [0.0] * (BASELINE_MAX_MINUTE + 1) for d in days}
    counts = {d: 0 for d in days}
    for c in candles:
        parsed = candle_minute(c.get("date"))
        vol = _f(c.get("volume"))
        if parsed is None or vol is None or parsed[0] not in by_day:
            continue
        day, minute = parsed
        if 0 <= minute <= BASELINE_MAX_MINUTE:
            by_day[day][minute] += vol
            counts[day] += 1
    out = {}
    for d in days:
        if counts[d] < BASELINE_MIN_CANDLES_PER_DAY:
            continue
        cum = [0.0] * (BASELINE_MAX_MINUTE + 2)
        for i in range(BASELINE_MAX_MINUTE + 1):
            cum[i + 1] = cum[i] + by_day[d][i]
        out[d] = cum
    return out


def build_entry(curves: dict, days: list):
    """每檔的分時累積佔比：近 R_DAYS 日「第 m 分鐘（取該分鐘開始與結束累積量的平均）累積量 ÷ 全日量」的中位數；至少 R_MIN_DAYS 個完整日才算。
    回傳 {"r": [MATRIX_POINTS 個佔比], "last": 基準日全日量（張）, "n": 完整日數}；不夠回傳 None。"""
    full = [d for d in days if d in curves and curves[d][-1] > 0]
    if len(full) < R_MIN_DAYS:
        return None
    r = []
    for m in range(MATRIX_POINTS):
        if m < MATRIX_POINTS - 1:
            r.append(_median([((curves[d][m] + curves[d][m + 1]) / 2.0) / curves[d][-1] for d in full]))
        else:
            r.append(1.0)
    for j in range(1, len(r)):                               # 單調不減
        r[j] = max(r[j], r[j - 1])
    r[-1] = 1.0
    last = curves[days[-1]][-1] if days[-1] in curves else None
    return {"r": r, "last": last, "n": len(full)}


def blend_r(entries: dict) -> None:
    """個股佔比與全市場佔比（各分鐘的中位數）以 R_BLEND（70:30）混合，就地修改。"""
    if not entries:
        return
    mkt = [_median([e["r"][j] for e in entries.values()]) for j in range(MATRIX_POINTS)]
    for e in entries.values():
        e["r"] = [round(R_BLEND[0] * a + R_BLEND[1] * b, 4) for a, b in zip(e["r"], mkt)]
        e["r"][-1] = 1.0


def trading_days(feed, asof: date_cls) -> list:
    """用 2330 的日 K 取得最近 R_DAYS 個交易日（含基準日）。"""
    start = (asof - timedelta(days=int(R_DAYS * 7 / 5) + 14)).isoformat()
    rows = feed.candles("2330", start, asof.isoformat(), timeframe="D", fields=None)
    days = sorted({str(r.get("date"))[:10] for r in rows if str(r.get("date"))[:10] <= asof.isoformat()})
    return days[-R_DAYS:]


def fetch_minute_curves(feed, code: str, days: list, chunk: int):
    """抓 1 分 K 並整理成 {日期: 累積量曲線}；days 依 chunk 分段請求（富邦單次回傳筆數上限沒有文件，所以可調）。"""
    candles = []
    for i in range(0, len(days), chunk):
        seg = days[i:i + chunk]
        feed._hist_pace()
        candles += feed.candles(code, seg[0], seg[-1], timeframe="1")
    return day_curves(candles, days), len(candles)


def check_against_snapshot(feed, entries: dict, asof: str) -> str:
    """分 K 加總（基準日全日量）對照富邦快照的 tradeVolume。"""
    import statistics
    try:
        snap = feed.snapshot_all()
    except Exception as exc:
        return f"快照對照略過（{type(exc).__name__}）"
    dates = {str(m.get("date", "")).replace("-", "") for m in snap["meta"].values()}
    if asof.replace("-", "") not in dates:
        return f"快照日期 {sorted(dates)} 不是基準日 {asof}，對照略過"
    ratios = []
    for code, e in entries.items():
        row = snap["rows"].get(code)
        tv = _f(row.get("tradeVolume")) if row else None
        if tv and tv > 0 and e.get("last"):
            ratios.append(e["last"] / tv)
    if not ratios:
        return "快照沒有可對照的資料"
    med = statistics.median(ratios)
    near = sum(1 for r in ratios if abs(r - 1) <= 0.02) / len(ratios)
    verdict = "✅ 一致" if abs(med - 1) <= 0.02 and near >= 0.8 else "⚠️ 不一致，請檢查單位或 13:30 收盤集合競價那一根"
    return f"分K加總 / 快照全日量：中位數 {med:.3f}，±2% 內 {near:.0%}（{len(ratios)} 檔）{verdict}"


def build_volume_matrix(codes, asof: date_cls, feed):
    """用富邦 1 分 K 為「候選股」預先算好分時累積佔比矩陣。成功回傳 (entries, days, 說明)；失敗（會發 Telegram）回傳 (None, None, 原因)。
    entries = {代號: {"r": [272 個佔比], "last": 基準日全日量, "n": 完整日數}}；寫檔由呼叫端（baseline.py）一次寫進 initial_pools.json。"""
    started = time.time()
    title = "【分時量矩陣】夜間預計算"
    universe = sorted(codes)
    try:
        days = trading_days(feed, asof)
    except Exception as exc:
        msg = f"查詢富邦交易日失敗：{type(exc).__name__}: {exc}"
        send_telegram(f"❌ <b>{title}</b> {msg}")
        return None, None, msg
    if len(days) < R_MIN_DAYS or days[-1] != asof.isoformat():
        msg = f"富邦日 K 的最後交易日是 {days[-1] if days else '無'}，與基準日 {asof} 不符（富邦歷史資料盤後 16:30 才更新，請稍後重跑）"
        send_telegram(f"❌ <b>{title}</b> {msg}")
        return None, None, msg
    print(f"[分時量矩陣] 基準日 {asof}，候選 {len(universe)} 檔，取樣 {len(days)} 日：{days[0]} ～ {days[-1]}", flush=True)
    chunk = BASELINE_CHUNK_DAYS
    entries, skipped, errors, probe = {}, [], 0, []
    for n, code in enumerate(universe, 1):
        try:
            curves, n_candles = fetch_minute_curves(feed, code, days, chunk)
        except Exception as exc:
            errors += 1
            skipped.append(code)
            print(f"  {code} 抓取失敗：{type(exc).__name__}: {exc}", flush=True)
            continue
        if n <= 3:
            print(f"  [診斷] {code}：分K {n_candles} 根（每段 {chunk} 日），完整日 {len(curves)}/{len(days)}", flush=True)
            probe.append(len(curves) / len(days))
            if n == 3 and chunk > 5 and sum(1 for p in probe if p < 0.7) >= 2:
                chunk = 5
                print("  [診斷] 一次請求多日只拿到部分日期 → 改成每 5 個交易日一段，重抓前 3 檔（耗時會變長）", flush=True)
                send_telegram(f"⚠️ <b>{title}</b> 富邦 1 分 K 一次請求 {BASELINE_CHUNK_DAYS} 個交易日回傳不完整，已自動改成每 5 日一段（耗時約 4 倍）。")
                entries.clear()
                skipped.clear()
                for c2 in universe[:3]:
                    cv, _ = fetch_minute_curves(feed, c2, days, chunk)
                    e2 = build_entry(cv, days)
                    if e2:
                        entries[c2] = e2
                    else:
                        skipped.append(c2)
                continue
        entry = build_entry(curves, days)
        if entry is None:
            skipped.append(code)
        else:
            entries[code] = entry
        if n % 50 == 0:
            print(f"  進度 {n}/{len(universe)}，成功 {len(entries)}，略過 {len(skipped)}，已用 {(time.time() - started) / 60:.1f} 分鐘", flush=True)
    coverage = len(entries) / len(universe) if universe else 0
    if coverage < BASELINE_MIN_COVERAGE:
        msg = f"成功率只有 {coverage:.0%}（{len(entries)}/{len(universe)}，抓取錯誤 {errors}）"
        send_telegram(f"❌ <b>{title}</b> {msg}，未更新 initial_pools.json。請看 Actions log 的診斷行。")
        return None, None, msg
    check_text = check_against_snapshot(feed, entries, asof.isoformat())
    print(f"[分時量矩陣] {check_text}", flush=True)
    blend_r(entries)
    minutes = (time.time() - started) / 60
    skipped_txt = f"｜略過 {len(skipped)} 檔（完整日不足 {R_MIN_DAYS} 天或抓取失敗）" if skipped else ""
    note = f"涵蓋 {len(entries)}/{len(universe)} 檔{skipped_txt}｜耗時 {minutes:.1f} 分鐘｜{check_text}"
    print(f"[分時量矩陣] 完成：{note}", flush=True)
    return entries, days, note


# =========================
# 歷史基底（富邦日 K）
# =========================
UNIVERSE_INFO = {"market": 0, "fetched": 0}      # 最近一次建立基底時：全市場一般股票檔數 / 實際去抓日 K 的檔數（baseline 的訊息用）


def fubon_universe(snap: dict, min_lots: float) -> dict:
    """由富邦全市場快照挑出要抓日 K 的股票：{代號: {name, market, snap_vol}}。"""
    out = {}
    for code, row in snap["rows"].items():
        name = str(row.get("name") or code)
        if _is_excluded_name(name):
            continue
        vol = _f(row.get("tradeVolume")) or 0.0
        if vol >= min_lots:
            out[code] = {"name": name, "market": row["_mkt"], "snap_vol": vol}
    return out


def pick_universe(snap: dict) -> dict:
    """預篩（快照全日量 ≥ 5 日均量門檻 × PREFILTER_RATIO）。預篩後太少（例如盤前快照量為 0）→ 改抓全部股票。"""
    uni = fubon_universe(snap, MIN_VOL5_LOTS * PREFILTER_RATIO)
    if len(uni) < PREFILTER_MIN_UNIVERSE:
        print(f"[歷史資料] 預篩只剩 {len(uni)} 檔（快照可能沒有成交量），改抓全部股票，耗時較長", flush=True)
        uni = fubon_universe(snap, 0.0)
    return uni


def series_from_candles(name: str, market: str, rows: list) -> dict:
    """富邦日 K（由舊到新）→ 一檔股票的序列：dates / o / h / l / c / v(張) / val(元)。"""
    return {"name": name, "market": market, "dates": [r["date"] for r in rows],
            "o": [r["open"] for r in rows], "h": [r["high"] for r in rows], "l": [r["low"] for r in rows],
            "c": [r["close"] for r in rows], "v": [r["volume"] for r in rows], "val": [r["value"] for r in rows]}


def fetch_series(feed, universe: dict, start: date_cls, end: date_cls):
    """逐檔向富邦抓日 K。回傳 ({代號: series}, 失敗檔數)。查無資料的略過（不算失敗）。"""
    out, fail, started, total = {}, 0, time.time(), len(universe)
    for i, (code, u) in enumerate(sorted(universe.items()), 1):
        try:
            rows = feed.daily_candles(code, start, end)
        except Exception as exc:
            fail += 1
            print(f"  {code} 日K抓取失敗：{type(exc).__name__}: {exc}", flush=True)
            continue
        if rows:
            out[code] = series_from_candles(u["name"], u["market"], rows)
        if i % 100 == 0 or i == total:
            print(f"  日K進度 {i}/{total}，成功 {len(out)}，失敗 {fail}，已用 {(time.time() - started) / 60:.1f} 分鐘", flush=True)
    return out, fail


def normalize_units(feed, series: dict, snap: dict, asof: date_cls) -> str:
    """日 K 最後一天全日量對照快照 tradeVolume（張），確認單位是「股→張」。比值約 0.001（API 其實回傳張）→ 自動校正。"""
    import statistics
    dates = {str(m.get("date", "")).replace("-", "") for m in snap["meta"].values()}
    if asof.strftime("%Y%m%d") not in dates:
        return f"單位校驗略過（快照日期 {sorted(dates)} 不是基準日 {asof}）"
    ratios = []
    for code, s in series.items():
        row = snap["rows"].get(code)
        tv = _f(row.get("tradeVolume")) if row else None
        if tv and tv > 0 and s["dates"] and s["dates"][-1] == asof:
            ratios.append(s["v"][-1] / tv)
    if len(ratios) < 20:
        return "單位校驗略過（可對照的股票太少）"
    med = statistics.median(ratios)
    if 0.0008 <= med <= 0.0012:
        for s in series.values():
            s["v"] = [x * 1000.0 for x in s["v"]]
        feed.daily_vol_div = 1.0
        return f"⚠️ 日K成交量單位其實是「張」（中位數比值 {med:.4f}），已自動校正"
    if abs(med - 1) <= 0.03:
        return f"日K成交量 / 快照全日量：中位數 {med:.3f}（{len(ratios)} 檔）✅ 單位一致"
    return f"⚠️ 日K成交量 / 快照全日量中位數 {med:.3f}（{len(ratios)} 檔），單位或 13:30 收盤集合競價可能有差異，請檢查"


def make_base(code: str, s: dict, industry: str = "", track=None):
    """由一檔股票的日 K 序列（由舊到新，截至基準日）算出歷史基底。不足 MA_WINDOW 天回傳 None。
    b_hit = B 池夜間掃描結果（最後一天符合才有）；a2 = A2 拉回整理標記（只在該股在 A2 追蹤池時才有，track = (A1 突破日, 突破日低點)）。"""
    if len(s["c"]) < MA_WINDOW:
        return None
    c20 = s["c"][-MA_WINDOW:]
    v5 = s["v"][-5:]
    avg5 = sum(v5) / len(v5)
    base = {
        "code": code, "name": s["name"], "market": s["market"], "industry": industry,
        "sum_4": float(sum(c20[-4:])), "sum_9": float(sum(c20[-9:])), "sum_19": float(sum(c20[-19:])),
        "true_ma5": sum(c20[-5:]) / 5, "true_ma10": sum(c20[-10:]) / 10, "true_ma20": sum(c20) / 20,
        "asof_date": s["dates"][-1], "yest_close": c20[-1], "yest_open": s["o"][-1], "yest_high": s["h"][-1], "yest_low": s["l"][-1],
        "yest_vol": v5[-1], "yest_val": s["val"][-1],
        "avg_5d_vol_simple": avg5, "avg_5d_vol": _trimmed_avg_vol(v5),
        "avg_turnover_5d": sum(c * v * 1000.0 for c, v in zip(s["c"][-5:], v5)) / len(v5),
        "liquid": avg5 >= MIN_VOL5_LOTS,
    }
    precompute_static(base)
    base["b_hit"] = b_scan_last(s) if (B_ENABLED and base["liquid"]) else None
    if track is not None:
        base["a2"] = consolidation_run(s["dates"], s["c"], s["v"], track[0], track[1])
    return base


def mark_unreliable(universe: dict, series: dict) -> None:
    """某個市場（上市 / 上櫃）的日 K 成功率太低 → 標記資料不完整，該市場的股票不發訊號。"""
    for mk, label in (("tse", "上市"), ("otc", "上櫃")):
        want = sum(1 for u in universe.values() if u["market"] == mk)
        got = sum(1 for s in series.values() if s["market"] == mk)
        if want >= 20 and got / want < FETCH_MIN_COVERAGE:
            UNRELIABLE_MARKETS.add(mk)
            msg = f"⚠️ <b>【飆股雷達】{label}日K資料不完整</b>\n富邦日K只抓到 {got}/{want} 檔，{label}個股的均線 / 均量可能有誤，本次不放進候選池。建議稍後重新執行。"
            print(msg.replace("<b>", "").replace("</b>", ""), flush=True)
            send_telegram(msg)


def tracked_from_pools(pools: dict) -> dict:
    """A2 追蹤池：{代號: (A1 突破日 date, 突破日低點)}，用來標記拉回整理。"""
    out = {}
    for code, e in (pools.get("a") or {}).items():
        try:
            out[code] = (date_cls.fromisoformat(str(e["entry_date"])), e.get("breakout_low"))
        except Exception:
            continue
    return out


def extra_codes_from_pools(pools: dict) -> set:
    """不論有沒有達標，追蹤清單裡的代號都要有日 K（收盤結算要用均線）。"""
    return set(pools.get("a") or {}) | set(pools.get("b") or {}) | set(pools.get("a1_pending") or {})


def load_all_stock_bases(feed, tracked: dict = None, extra=None) -> dict:
    """建立每檔股票的歷史基底。價格與成交量全部來自富邦：① snapshot.quotes 取清單並預篩 → ② historical.candles 日 K → ③ intraday.tickers 產業別。
    基準日 = 多數股票最後一根日 K 的日期（富邦盤後 16:30 前還沒更新當天日 K，基準日就是前一個交易日）。"""
    tracked = tracked or {}
    extra = set(extra or ())
    started = time.time()
    today = now_tw().date()
    snap = feed.snapshot_all()
    universe = pick_universe(snap)
    UNIVERSE_INFO.update(market=len(snap["rows"]), fetched=len(universe))
    for code in set(tracked) | extra:                        # 追蹤中的標的即使預篩被濾掉也要抓
        if code in snap["rows"] and code not in universe:
            row = snap["rows"][code]
            universe[code] = {"name": str(row.get("name") or code), "market": row["_mkt"], "snap_vol": _f(row.get("tradeVolume")) or 0.0}
    start = today - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    print(f"[歷史基底] 富邦全市場 {len(snap['rows']):,} 檔，預篩後抓日 K {len(universe):,} 檔"
          f"（{start} ～ {today}，約 {len(universe) * BASELINE_CALL_INTERVAL / 60:.0f} 分鐘）", flush=True)
    series, fail = fetch_series(feed, universe, start, today)
    if not universe or len(series) / len(universe) < FETCH_MIN_COVERAGE:
        print(f"[歷史基底] 日K成功率過低（{len(series)}/{len(universe)}，失敗 {fail}），放棄", flush=True)
        return {}
    last_dates = {}
    for s in series.values():
        last_dates[s["dates"][-1]] = last_dates.get(s["dates"][-1], 0) + 1
    asof = max(last_dates, key=lambda d: (last_dates[d], d))
    series = {c: s for c, s in series.items() if s["dates"][-1] == asof}
    print(f"[歷史基底] 基準日 {asof}；{normalize_units(feed, series, snap, asof)}", flush=True)
    mark_unreliable(universe, series)
    try:
        ind = feed.industry_map()
    except Exception as exc:
        print(f"[歷史基底] 產業別查詢失敗：{type(exc).__name__}: {exc}", flush=True)
        ind = {}
    bases = {}
    for code, s in series.items():
        b = make_base(code, s, ind.get(code, ""), tracked.get(code))
        if b:
            bases[code] = b
    tse = sum(1 for b in bases.values() if b["market"] == "tse")
    liquid = sum(1 for b in bases.values() if b["liquid"])
    n_b = sum(1 for b in bases.values() if b.get("b_hit"))
    n_a2 = sum(1 for b in bases.values() if (b.get("a2") or {}).get("n"))
    print(f"預載完成：{len(bases)} 檔（上市 {tse} / 上櫃 {len(bases) - tse}），5 日均量 ≥ {MIN_VOL5_LOTS:,} 張 {liquid} 檔｜"
          f"B 型態候選 {n_b} 檔、A2 拉回整理中 {n_a2} 檔｜日K失敗 {fail} 檔，耗時 {(time.time() - started) / 60:.1f} 分鐘", flush=True)
    return bases


# =========================
# 法人買賣超（官方：證交所 T86 + 櫃買；只當參考。官方抓不到時，B 候選改用富邦 institutional_trades 補）
# =========================
_inst_session = requests.Session()
_inst_last_ts = [0.0]
_inst_field_logged = set()


def _throttle_official() -> None:
    wait = OFFICIAL_MIN_INTERVAL - (time.time() - _inst_last_ts[0])
    if wait > 0:
        time.sleep(wait)
    _inst_last_ts[0] = time.time()


def _num(x) -> float:
    s = str(x).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _strip_paren(s: str) -> str:
    """去掉括號內文字：「外陸資買賣超股數(不含外資自營商)」→「外陸資買賣超股數」。"""
    return re.sub(r"[\(（][^\)）]*[\)）]", "", re.sub(r"<[^>]+>|\s+", "", str(s)))


def parse_inst_rows(fields: list, rows: list):
    """由欄位名稱找出「代號、外資、投信」（單位：股）。外資 = 欄名（去掉括號後）含「外」且不含「自營商」；投信 = 含「投信」。
    優先用「買賣超 / 淨買」欄；沒有就用「買進 − 賣出」自己算。找不到欄位 → 回傳 (None, 欄位清單)。回傳 ({代號: (外資淨, 投信淨)}, 欄位對應說明)。"""
    stripped = [_strip_paren(f) for f in fields]

    def find(pred):
        return next((i for i, f in enumerate(stripped) if pred(f)), None)

    ci = find(lambda f: "代號" in f)
    if ci is None and rows:
        try:
            if STOCK_CODE_RE.fullmatch(str(rows[0][0]).strip()):
                ci = 0
        except (IndexError, TypeError):
            pass
    f_net = find(lambda f: "外" in f and ("買賣超" in f or "淨買" in f) and "自營商" not in f)
    t_net = find(lambda f: "投信" in f and ("買賣超" in f or "淨買" in f))
    f_buy = find(lambda f: "外" in f and "買進" in f and "自營商" not in f)
    f_sell = find(lambda f: "外" in f and "賣出" in f and "自營商" not in f)
    t_buy = find(lambda f: "投信" in f and "買進" in f)
    t_sell = find(lambda f: "投信" in f and "賣出" in f)
    f_ok = f_net is not None or (f_buy is not None and f_sell is not None)
    t_ok = t_net is not None or (t_buy is not None and t_sell is not None)
    if ci is None or not f_ok or not t_ok:
        return None, fields
    out = {}
    for row in rows:
        try:
            code = str(row[ci]).strip()
            if not STOCK_CODE_RE.fullmatch(code):
                continue
            fv = _num(row[f_net]) if f_net is not None else _num(row[f_buy]) - _num(row[f_sell])
            tv = _num(row[t_net]) if t_net is not None else _num(row[t_buy]) - _num(row[t_sell])
            out[code] = (fv, tv)
        except (IndexError, TypeError):
            continue
    return out, f"外資 = {fields[f_net] if f_net is not None else '買進-賣出'}｜投信 = {fields[t_net] if t_net is not None else '買進-賣出'}"


def _inst_table(js):
    if not isinstance(js, dict):
        return None, None
    tbs = js.get("tables")
    if isinstance(tbs, list):
        for t in tbs:
            f, d = t.get("fields") or [], t.get("data") or []
            if any("代號" in str(x) for x in f) and d:
                return [str(x) for x in f], d
    f, d = js.get("fields"), js.get("data")
    if f and d:
        return [str(x) for x in f], d
    return None, None


def _inst_date_matches(js, day: date_cls):
    """回應裡有日期欄位時，必須等於要求的日期（避免拿到舊資料）。沒有日期欄位 → 無法驗證，視為相符。"""
    raw = str(js.get("date") or js.get("reportDate") or "").strip() if isinstance(js, dict) else ""
    if not raw:
        return True
    parts = re.findall(r"\d+", raw)
    try:
        if len(parts) >= 3:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        elif len(parts[0]) == 8:
            y, m, d = int(parts[0][:4]), int(parts[0][4:6]), int(parts[0][6:])
        else:
            return True
        y = y + 1911 if y < 1911 else y
        return date_cls(y, m, d) == day
    except (ValueError, IndexError):
        return True


def fetch_inst_market(mk: str, day: date_cls):
    """抓一個市場、一天的外資＋投信買賣超。回傳 ({代號: (外資淨股, 投信淨股)} 或 None, 說明)。"""
    _throttle_official()
    try:
        if mk == "tse":
            r = _inst_session.get(TWSE_INST_URL, params={"date": day.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"},
                                  headers=OFFICIAL_HEADERS, timeout=25)
        else:
            r = _inst_session.post(TPEX_INST_URL, data={"type": "Daily", "sect": "AL", "date": day.strftime("%Y/%m/%d"), "id": "", "response": "json"},
                                   headers=TPEX_HEADERS, timeout=25)
        js = r.json()
    except Exception as exc:
        return None, f"連線失敗 {type(exc).__name__}"
    if str(js.get("stat", "OK")).upper() not in ("OK",):
        return None, f"尚無資料（{str(js.get('stat'))[:30]}）"
    if not _inst_date_matches(js, day):
        return None, f"回應日期 {js.get('date') or js.get('reportDate')} 不是 {day}"
    fields, rows = _inst_table(js)
    if not fields:
        return None, "回應沒有資料表"
    table, picked = parse_inst_rows(fields, rows)
    if table is None:
        print(f"[法人] {mk} 欄位無法辨識：{fields}", flush=True)
        return None, "欄位無法辨識（請把 log 的欄位清單貼給我）"
    if mk not in _inst_field_logged:
        _inst_field_logged.add(mk)
        print(f"[法人] {mk} 欄位對應：{picked}（共 {len(table)} 檔）", flush=True)
    return table, ""


def fetch_inst_days(days: list, retries: int = None, wait: float = None):
    """days = [date, ...] 由舊到新。回傳 ({日期字串: {"tse": 表或 None, "otc": ...}}, [未取得說明])。最新一天沒資料時重試 retries 次（官方公布時間不固定）。"""
    retries = INST_RETRIES if retries is None else retries
    wait = INST_RETRY_WAIT if wait is None else wait
    out, notes = {}, []
    for idx, day in enumerate(days):
        latest = idx == len(days) - 1
        res = {}
        for mk in ("tse", "otc"):
            tbl, note = None, ""
            for attempt in range(1, (retries if latest else 1) + 1):
                tbl, note = fetch_inst_market(mk, day)
                if tbl is not None:
                    break
                if attempt < (retries if latest else 1):
                    print(f"[法人] {mk} {day} 尚未取得（{note}），{wait:.0f} 秒後重試（{attempt}/{retries}）", flush=True)
                    time.sleep(wait)
            res[mk] = tbl
            if tbl is None:
                notes.append(f"{'上市' if mk == 'tse' else '上櫃'} {day}：{note}")
        out[day.isoformat()] = res
    return out, notes


def attach_inst(bases: dict, inst_by_day: dict, days: list) -> None:
    """把近 INST_DAYS 日「外資＋投信」合計淨買賣超（張）寫進每檔基底：inst5 = [最舊…最新]（沒有資料的日子是 None）；inst_t = T 日。
    該市場當日有表、但這檔不在表中 = 當天沒有法人進出 = 0（不是缺資料）。"""
    keys = [d.isoformat() for d in days]
    for code, b in bases.items():
        nets = []
        for k in keys:
            tbl = (inst_by_day.get(k) or {}).get(b["market"])
            if tbl is None:
                nets.append(None)
            else:
                f, t = tbl.get(code, (0.0, 0.0))
                nets.append(round((f + t) / 1000.0, 1))
        b["inst5"] = nets
        b["inst_t"] = nets[-1] if nets else None


def fubon_inst_fill(feed, bases: dict, codes: list, day: date_cls) -> int:
    """官方抓不到的 B 候選，改用富邦 institutional_trades 補 T 日。回傳補到幾檔。"""
    n = 0
    for code in codes:
        b = bases.get(code)
        if b is None:
            continue
        d = feed.institutional(code, day - timedelta(days=INST_DAYS + 6), day)
        if d is None:
            continue
        f, t = d.get(day.isoformat(), (0.0, 0.0))                 # 該日沒有法人進出 = 0
        b["inst_t"], b["inst_src"] = round((f + t) / 1000.0, 1), "富邦"
        n += 1
    return n


# =========================
# initial_pools.json（夜間產出；隔日盤中唯一的靜態資料）
# =========================
def next_weekday(d: date_cls) -> date_cls:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _clean_base(b: dict) -> dict:
    """基底存檔前的整理：日期轉字串、浮點數四捨五入、拿掉盤中用不到的暫存欄位。"""
    out = {}
    for k, v in b.items():
        if k in ("b_hit", "a2"):
            continue
        if isinstance(v, date_cls):
            v = v.isoformat()
        elif isinstance(v, float):
            v = round(v, 4)
        elif isinstance(v, list):
            v = [round(x, 1) if isinstance(x, float) else x for x in v]
        out[k] = v
    return out


def index_regime_snapshot(feed, asof: date_cls) -> dict:
    """夜間保存最近 19 個完整交易日的指數收盤（上市=加權、上櫃=櫃買），隔日盤中算即時月線用。回傳 {市場: {...}}；抓不到的市場不放。"""
    out = {}
    try:
        syms = feed.index_symbols()
    except Exception as exc:
        print(f"[指數] 查詢代號失敗：{type(exc).__name__}: {exc}", flush=True)
        return out
    for mk, label in (("tse", "加權"), ("otc", "櫃買")):
        if not syms.get(mk):
            continue
        try:
            rows = feed.index_closes(mk, asof)
        except Exception as exc:
            print(f"[指數] {label} 日 K 失敗：{type(exc).__name__}: {exc}", flush=True)
            continue
        hist = [(d, v) for d, v in rows if d <= asof]
        if len(hist) < 19:
            print(f"[指數] {label} 歷史不足 19 日（{len(hist)}），略過", flush=True)
            continue
        closes = [float(v) for _, v in hist][-19:]
        p = sum(closes) / 19.0
        out[mk] = {"name": label, "symbol": syms[mk], "last_date": hist[-1][0].isoformat(), "stale": hist[-1][0] != asof,
                   "closes19": [round(v, 2) for v in closes], "p": round(p, 4), "last_close": closes[-1],
                   "init_state": "NORMAL" if closes[-1] >= p else "BEAR"}
    return out


def merge_b_candidates(pools: dict, bases: dict) -> dict:
    """把夜間掃到的 B 訊號合進 B 池（Watching）。已在池中、或同一 B 訊號日已移出過的不重複加入（重跑 baseline 也安全）。法人只當參考。"""
    done = {(r["code"], r.get("burst_date")) for r in pools.get("closed", []) if r.get("pool") == "B"}
    added = skipped = 0
    for code, base in sorted(bases.items()):
        h = base.get("b_hit")
        if not h or not base["liquid"] or base["market"] in UNRELIABLE_MARKETS:
            continue
        if code in pools["b"] or (code, h["burst_date"]) in done:
            skipped += 1
            continue
        e = {"name": base["name"], "industry": base.get("industry", ""), "market": base["market"], "status": "Watching",
             "waiting_days": 0, "miss_days": 0, "src": "night", "inst5": base.get("inst5") or [], "inst_t": base.get("inst_t")}
        e.update(h)
        pools["b"][code] = e
        added += 1
    return {"added": added, "skipped": skipped}


def settlement_bases(bases: dict) -> dict:
    """夜間補結算 / 核對用：日 K 基底的 sum_4/9/19 含基準日收盤；結算「基準日當天」要的是不含它的前 4/9/19 日收盤和，這裡換算。"""
    out = {}
    for code, b in bases.items():
        c = b["yest_close"]
        item = dict(b)
        item["sum_4"] = 5 * b["true_ma5"] - c
        item["sum_9"] = 10 * b["true_ma10"] - c
        item["sum_19"] = 20 * b["true_ma20"] - c
        out[code] = item
    return out


def official_closes(bases: dict) -> dict:
    """用官方（富邦）日 K 的基準日資料組成結算用的收盤資料。"""
    return {c: {"close": b["yest_close"], "open": b["yest_open"], "high": b["yest_high"], "low": b["yest_low"],
                "vol": b["yest_vol"], "val": b["yest_val"]} for c, b in bases.items()}


def reconcile_a1_confirm(pools: dict, bases: dict, asof_str: str) -> dict:
    """夜間用官方日 K 核對當日 13:35 的「暫定」A1 收盤確認：官方收盤沒站上 MA5/10/20 → 移出 A2 追蹤池；通過的標記 official_reconciled，突破日低點改用官方最低價。"""
    checked = removed = 0
    for code, e in list(pools["a"].items()):
        if e.get("entry_date") != asof_str:
            continue
        b = bases.get(code)
        if not b:
            continue
        checked += 1
        close = b["yest_close"]
        ok = close > b["true_ma5"] and close > b["true_ma10"] and close > b["true_ma20"] and close >= b["yest_low"]
        if not ok:
            pools["a"].pop(code)
            close_record(pools, "A", code, e, asof_str, "夜間核對：官方收盤未確認A1", close)
            removed += 1
            continue
        e["last_close"], e["breakout_low"] = close, b["yest_low"]
        e["close_provisional"], e["close_src"] = False, "official_reconciled"
    return {"checked": checked, "removed": removed}


def assemble_initial_pools(bases: dict, pools: dict, asof_iso: str, index_ctx: dict, inst_meta: dict, matrix: dict, matrix_days: list,
                           matrix_note: str = "", notes=None) -> dict:
    """組出 initial_pools.json：A1 候選池（5 日均量 ≥ 1,000 張 ＋ 昨收 ≤ 昨日最高均線）、A2 追蹤池（收盤確認的 A1）、B 候選池、
    分時量矩陣（候選股 × 開盤後每分鐘）、兩個市場的趨勢基準（前 19 日收盤）、法人參考資料。"""
    a1 = sorted(c for c, b in bases.items()
                if b["liquid"] and b["market"] not in UNRELIABLE_MARKETS and b["yest_close"] <= b["prev_ma_max"])
    a2_pool = {}
    for code, e in pools["a"].items():
        item = dict(e)
        item["a2"] = (bases.get(code) or {}).get("a2") or {"n": 0, "avg_vol": None}
        a2_pool[code] = item
    b_cand = {}
    for code, e in pools["b"].items():
        item = dict(e)
        base = bases.get(code)
        if base and base.get("inst5"):
            item["inst5"], item["inst_t"] = base["inst5"], base.get("inst_t")
        b_cand[code] = item
    codes = set(a1) | set(a2_pool) | set(b_cand)
    stock_bases = {c: _clean_base(bases[c]) for c in sorted(codes) if c in bases}
    vtm = {c: matrix[c]["r"] for c in sorted(codes) if matrix and c in matrix}
    meta = {"rule": INITIAL_RULE, "data_version": 1, "baseline_date": asof_iso, "trade_date": next_weekday(date_cls.fromisoformat(asof_iso)).isoformat(),
            "built_at": now_tw().isoformat(timespec="seconds"), "complete": True,
            "counts": {"a1": len(a1), "a2": len(a2_pool), "b": len(b_cand), "bases": len(stock_bases), "matrix": len(vtm)},
            "notes": list(notes or [])}
    return {
        "metadata": meta,
        "indicator_parameters": {
            "min_vol5_lots": MIN_VOL5_LOTS, "a1_chg_min": A1_CHG_MIN, "a1_vol_ratio": A1_VOL_RATIO, "a1_vol_ratio_bear": A1_VOL_RATIO_BEAR,
            "a1_min_est_lots": A1_MIN_EST_LOTS, "a_min_est_turnover": A_MIN_EST_TURNOVER, "a2_days": [A2_MIN_DAYS, A2_MAX_DAYS],
            "a2_vol_ratio": A2_VOL_RATIO, "a_track_days": A_TRACK_DAYS, "b_kd_low": B_KD_LOW, "b_vol_ratio": B_VOL_RATIO,
            "b_lowclose_pct": B_LOWCLOSE_PCT, "b_close_loc_min": B_CLOSE_LOC_MIN, "b_shrink_ratio": B_SHRINK_RATIO,
            "b_min_est_turnover": B_MIN_EST_TURNOVER, "b_wait_days": B_WAIT_DAYS, "regime_enter_bear_rounds": REGIME_ENTER_BEAR_ROUNDS,
            "regime_exit_hold_sec": REGIME_EXIT_HOLD_SEC, "r_blend": list(R_BLEND), "r_floor": R_FLOOR},
        "listed_market_regime": (index_ctx or {}).get("tse") or {},
        "otc_market_regime": (index_ctx or {}).get("otc") or {},
        "stock_bases": stock_bases,
        "A1_candidates": a1,
        "A2_tracking_pool": a2_pool,
        "B_candidates": b_cand,
        "volume_time_matrix": vtm,
        "volume_matrix_meta": {"unit": "minute_from_open", "points": MATRIX_POINTS, "days": list(matrix_days or []), "blend": list(R_BLEND),
                               "note": matrix_note},
        "institutional_data": inst_meta or {},
    }


def save_initial_pools(init: dict) -> None:
    atomic_write_json(INITIAL_POOLS_FILE, init)


def load_initial_pools():
    """回傳 (initial_pools 或 None, 說明)。規則版本不符 / 檔案不完整 → None。"""
    if not INITIAL_POOLS_FILE.exists():
        return None, "找不到 initial_pools.json（夜間 baseline 沒跑成功？）"
    try:
        data = json.loads(INITIAL_POOLS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"initial_pools.json 讀取失敗（{type(exc).__name__}）"
    meta = data.get("metadata") or {}
    if meta.get("rule") != INITIAL_RULE:
        return None, f"initial_pools.json 規則版本不符（{meta.get('rule')}，v69 需要 {INITIAL_RULE}），請先重跑 baseline"
    if not meta.get("complete") or not data.get("stock_bases") or not data.get("volume_time_matrix"):
        return None, "initial_pools.json 內容不完整（缺基底或分時量矩陣），請先重跑 baseline"
    c = meta.get("counts") or {}
    return data, (f"基準日 {meta.get('baseline_date')}｜A1 候選 {c.get('a1', 0)} 檔、A2 追蹤池 {c.get('a2', 0)} 檔、B 候選 {c.get('b', 0)} 檔｜"
                  f"矩陣 {c.get('matrix', 0)} 檔")


def sync_pools_from_initial(pools: dict, init: dict) -> dict:
    """盤前：把夜間快照（initial_pools.json）與追蹤清單（signal_history.json）對齊。
    A2 追蹤池：快照有、清單沒有的補進去；拉回整理標記（a2）一律以快照為準。B 候選：快照有、清單沒有且沒移出過的補進去（清單裡的狀態不覆蓋）。"""
    stats = {"a_added": 0, "b_added": 0}
    for code, e in (init.get("A2_tracking_pool") or {}).items():
        mine = pools["a"].get(code)
        if mine is None:
            pools["a"][code] = {k: v for k, v in e.items()}
            stats["a_added"] += 1
        else:
            mine["a2"] = e.get("a2")
    done = {(r["code"], r.get("burst_date")) for r in pools.get("closed", []) if r.get("pool") == "B"}
    for code, e in (init.get("B_candidates") or {}).items():
        if code in pools["b"] or (code, e.get("burst_date")) in done:
            continue
        pools["b"][code] = dict(e)
        stats["b_added"] += 1
    return stats


# =========================
# Telegram 訊息
# =========================
LIST_MAX = 15
EXIT_LIST_MAX = 15


def inst_line(inst5) -> list:
    """近 5 日外資＋投信合計淨買賣超（張，由舊到新；None = 該日資料沒取得）。只當參考。"""
    if not inst5:
        return []
    cells = ["—" if v is None else f"{v:+,.0f}" for v in inst5]
    return [f"▫️ <b>法人參考（外資＋投信，近 {len(inst5)} 日，張）：</b>" + "／".join(cells)]


def bear_line(sig) -> list:
    return [f"⚠️ <b>大盤空頭（BEAR）</b>：A1 同期量比門檻提高為 {A1_VOL_RATIO_BEAR:g} 倍"] if sig.get("mode") == "BEAR" else []


def build_a1_message(sig: dict, n_today: int, now: datetime) -> str:
    """🟢【A1：首發突破】一則訊息一檔股票。不附停損價（停損你自己處理）。盤中通知；收盤站穩三均線才會進 A2 追蹤池。"""
    t_now = now.strftime("%H:%M:%S")
    est = sig["est"]
    need = A1_VOL_RATIO_BEAR if sig.get("mode") == "BEAR" else A1_VOL_RATIO
    lines = [f"🟢 <b>【A1：首發突破】</b> <b>{sig['code']} {sig['name']}</b>"] + bear_line(sig) + [
        f"💡 <b>首發：</b>昨收 {sig['yest_close']:.2f} ≤ 昨日最高均線 {sig['prev_ma_max']:.2f}，今日現價同時站上盤中 MA5/10/20",
        "━━━━━━━━━━━━━━━",
        f"📈 <b>現價：{sig['price']:.2f} ({_pc(sig['chg'])})</b>｜開 {_p2(sig.get('open'))}",
        "",
        "📊 <b>【進場條件（全部成立）】</b>",
        f"▫️ <b>均線：</b>現價 {sig['price']:.2f} > 即時 MA5 {sig['ma5']:.2f}、MA10 {sig['ma10']:.2f}、MA20 {sig['ma20']:.2f} ✅",
        f"▫️ <b>VWAP：</b>{sig['vwap']:.2f}（現價高於 {_pc(sig['vwap_pct'])}）✅",
        f"▫️ <b>量能：</b>同期量比 <b>{est['sync_ratio']:.2f}x</b>（≥ {need:g}x）｜預估全日量 {est['est_vol']:,.0f} 張（5 日均量 {sig['avg5']:,.0f}，≥ {A1_MIN_EST_LOTS:,} 張）✅",
        f"▫️ <b>成交額：</b>已成交 {est['val'] / 1e8:.2f} 億｜預估全日 <b>{est['est_val'] / 1e8:.2f} 億</b>（≥ {A_MIN_EST_TURNOVER / 1e8:g} 億）✅",
    ]
    lines += inst_line(sig.get("inst5"))
    lines += ["", "🏷️ <b>【其他資訊】</b>", f"▫️ <b>族群：</b>{_short(sig.get('industry'))}",
              f"▫️ <b>確認：</b>{sig.get('first_time', '-')} ➔ {t_now}｜收盤站穩三均線才進 A2 追蹤池",
              f"⏱️ <b>{t_now} ｜ 今日第 {n_today} 則</b>"]
    return "\n".join(lines)


def build_a2_message(sig: dict, n_today: int, now: datetime) -> str:
    """🟡【A2：二次加碼】"""
    t_now = now.strftime("%H:%M:%S")
    est = sig["est"]
    chg_since = _pc((sig["price"] / sig["entry_price"] - 1) * 100.0) if sig.get("entry_price") else "—"
    lines = [f"🟡 <b>【A2：二次加碼】</b> <b>{sig['code']} {sig['name']}</b>",
             f"💡 <b>再發動：</b>A1 收盤確認後拉回整理 {sig['consol_days']} 天（收盤 < MA5、守住 MA10 與突破日低點），今日現價重新站上即時 MA5",
             "━━━━━━━━━━━━━━━",
             f"📈 <b>現價：{sig['price']:.2f} ({_pc(sig['chg'])})</b>｜即時 MA5 {sig['ma5']:.2f}｜MA10 {sig['ma10']:.2f}",
             f"▫️ <b>A1 突破：</b>{_md(sig.get('entry_date'))} @ {_p2(sig.get('entry_price'))}（至今 {chg_since}）｜突破日低點 {_p2(sig.get('breakout_low'))}",
             "", "📊 <b>【加碼條件（全部成立）】</b>",
             f"▫️ <b>整理期：</b>{sig['consol_days']} 天（{A2_MIN_DAYS}～{A2_MAX_DAYS} 天）｜平均量 {sig['consol_avg_vol']:,.0f} 張",
             f"▫️ <b>VWAP：</b>{_p2(sig.get('vwap'))}（現價高於 {_pc(sig.get('vwap_pct'))}）✅",
             f"▫️ <b>低點：</b>今日最低 {_p2(sig.get('low'))} ≥ 突破日低點 ✅",
             f"▫️ <b>量能：</b>預估全日量 {est['est_vol']:,.0f} 張 = 整理期平均量的 <b>{sig['est_ratio']:.2f}x</b>（≥ {A2_VOL_RATIO:g}x）✅",
             f"▫️ <b>成交額：</b>預估全日 <b>{est['est_val'] / 1e8:.2f} 億</b>（≥ {A_MIN_EST_TURNOVER / 1e8:g} 億）✅"]
    lines += inst_line(sig.get("inst5"))
    lines += ["", "🏷️ <b>【其他資訊】</b>", f"▫️ <b>族群：</b>{_short(sig.get('industry'))}",
              f"▫️ <b>確認：</b>{sig.get('first_time', '-')} ➔ {t_now}", f"⏱️ <b>{t_now} ｜ 今日第 {n_today} 則</b>"]
    return "\n".join(lines)


def build_bc_message(sig: dict, n_today: int, now: datetime) -> str:
    """🔵【B：跌深反彈確認】（NORMAL / BEAR 都會發）"""
    t_now = now.strftime("%H:%M:%S")
    est = sig["est"]
    k_txt = f"KD 低檔金叉（K {sig['kd_k']:.1f} > D {sig['kd_d']:.1f}）＋ " if sig.get("kd_k") is not None else ""
    lines = [f"🔵 <b>【B：跌深反彈確認】</b> <b>{sig['code']} {sig['name']}</b>"]
    if sig.get("mode") == "BEAR":
        lines.append("⚠️ <b>大盤空頭（BEAR）</b>：B 低接照常，請自行控管部位")
    lines += [f"💡 <b>B 訊號日 {_md(sig['burst_date'])}：</b>{k_txt}爆量 {sig['burst_ratio']:.1f}x 前 20 日均量 ＋ {sig.get('trigger', '')}",
              "━━━━━━━━━━━━━━━",
              f"📈 <b>現價：{sig['price']:.2f} ({_pc(sig['chg'])})</b>｜今日最低 {_p2(sig.get('low'))}｜即時 MA5 {sig['ma5']:.2f}",
              "", "📊 <b>【確認條件（全部成立）】</b>",
              f"▫️ <b>防線：</b>B 訊號日最低價 <code>{sig['burst_low']:.2f}</code>｜今日最低 ≥ 它（{_pc(sig['low_dist_pct'])}）✅",
              f"▫️ <b>量縮：</b>預估全日量 {est['est_vol']:,.0f} 張 = B 訊號日 {sig['burst_vol']:,.0f} 張的 <b>{sig['shrink_ratio']:.2f}x</b>（≤ {B_SHRINK_RATIO:g}x）✅"]
    if B_REQUIRE_MA5:
        lines.append(f"▫️ <b>站上 MA5：</b>現價 {sig['price']:.2f} > {sig['ma5']:.2f} ✅")
    if B_REQUIRE_VWAP:
        lines.append(f"▫️ <b>VWAP：</b>{_p2(sig.get('vwap'))}（現價高於 {_pc(sig.get('vwap_pct'))}）✅")
    lines += [f"▫️ <b>成交額：</b>預估全日 {est['est_val'] / 1e8:.2f} 億（≥ {B_MIN_EST_TURNOVER / 1e8:g} 億）✅",
              f"▫️ <b>等待期：</b>B 訊號日後第 {sig['offset']} 個交易日（最多 {B_WAIT_DAYS} 天）"]
    lines += inst_line(sig.get("inst5"))
    lines += ["", "🏷️ <b>【其他資訊】</b>", f"▫️ <b>族群：</b>{_short(sig.get('industry'))}",
              f"▫️ <b>確認：</b>{sig.get('first_time', '-')} ➔ {t_now}", f"⏱️ <b>{t_now} ｜ 今日第 {n_today} 則</b>"]
    return "\n".join(lines)


def build_regime_message(ev: str, w, now: datetime) -> str:
    """市場趨勢「正式切換」才發（防抖確認期間不發）；一個市場一則。"""
    t_now = now.strftime("%H:%M:%S")
    cur = f"{w.last_reading:,.2f}" if w.last_reading is not None else "—"
    if ev == "TO_BEAR":
        return (f"🔴 <b>【市場趨勢：空頭 BEAR】</b> {t_now}\n{w.name} 連續 {REGIME_ENTER_BEAR_ROUNDS} 輪低於盤中即時月線"
                f"（月線穿越價 {w.p:,.2f}，現 {cur}）\nA1 同期量比門檻提高為 {A1_VOL_RATIO_BEAR:g} 倍｜A2 禁止｜B 照常（{w.name} 對應的{'上市' if w.name == '加權' else '上櫃'}個股）")
    return (f"🟢 <b>【市場趨勢：多頭 NORMAL】</b> {t_now}\n{w.name} 站回盤中即時月線並連續維持 {REGIME_EXIT_HOLD_SEC // 60} 分鐘"
            f"（月線穿越價 {w.p:,.2f}，現 {cur}）\nA1 門檻回到 {A1_VOL_RATIO:g} 倍｜A2 恢復開放｜B 照常")


# ---------- 盤後 ----------
def build_close_summary(day_str: str, market_txt: str, regime, a_rows: list, b_rows: list, ev: dict, unsettled: list) -> str:
    lines = [f"📊 <b>【盤後總結】｜{day_str}</b>", market_txt]
    lines += regime_lines(regime, compact=True)
    n_pb = sum(1 for r in a_rows if r["status"] == "Pullback")
    lines += ["", f"🟢 <b>A2 追蹤池（A1 收盤確認後 {A_TRACK_DAYS} 日內）：{len(a_rows)} 檔</b>（Active {len(a_rows) - n_pb}｜Pullback {n_pb}）",
              f"   今日：A1 盤中通知 {ev['a1']}｜收盤確認 {ev['a1_ok']}｜未確認 {ev['a1_fail']}｜A2 加碼 {ev['a2']}｜移出 {ev['a_exit']}"]
    nw = sum(1 for r in b_rows if r["status"] == "Watching")
    lines += [f"🔵 <b>B 池：{len(b_rows)} 檔</b>（Watching {nw}｜Active {len(b_rows) - nw}）",
              f"   今日：確認發動 {ev['bc']}｜防線失守 / 失效 {ev['b_break']}｜B_EXPIRED（等滿 {B_WAIT_DAYS} 日沒發動）{ev['b_expire']}｜夜間新增 {ev['b_new']}"]
    if ev.get("regime_switches"):
        lines.append(f"🌡️ 今日市場趨勢正式切換 {ev['regime_switches']} 次")
    lines.append("ℹ️ 收盤價為富邦最終快照（暫定），18:00 後夜間運算會用官方日 K 核對 A1 收盤確認。")
    if unsettled:
        lines += ["", f"⚠️ 以下標的 {SETTLE_DEADLINE[0]}:{SETTLE_DEADLINE[1]:02d} 仍未取得收盤價（暫緩收盤或資料延遲），今日不結算：",
                  "、".join(unsettled)]
    return "\n".join(lines)


def build_a_exit_message(day_str: str, exits: list) -> str:
    lines = [f"🟢 <b>【A2 追蹤池移出】</b>｜{day_str}", f"{len(exits)} 檔今日收盤後移出（只管清單，不是交易指令）："]
    for ex in exits[:EXIT_LIST_MAX]:
        if ex["reason"] == "收盤跌破MA10":
            lines += ["", f"<b>{ex['code']} {ex['name']}</b>｜收 {_p2(ex['exit_price'])} < MA10 {_p2(ex.get('ma10'))}",
                      f"   A1 確認 {_md(ex['entry_date'])} @ {_p2(ex['entry_price'])} → {_pc(ex['ret_pct'])}｜追蹤 {ex.get('age', 0)} 日"]
        else:
            lines += ["", f"<b>{ex['code']} {ex['name']}</b>｜{ex['reason']}｜收 {_p2(ex.get('exit_price'))}"]
    if len(exits) > EXIT_LIST_MAX:
        lines += ["", f"…另有 {len(exits) - EXIT_LIST_MAX} 檔（完整名單見 pool_report.csv）"]
    return "\n".join(lines)


def build_b_close_message(day_str: str, exits: list) -> str:
    lines = [f"🔵 <b>【B 池移出】</b>｜{day_str}（只管清單，不發停損 / 出場通知）"]
    for ex in exits[:EXIT_LIST_MAX]:
        pnl = f"｜通知後 {_pc(ex['ret_pct'])}" if ex.get("ret_pct") is not None else ""
        lines.append(f"• <b>{ex['code']} {ex['name']}</b>｜{ex['reason']}｜收 {_p2(ex.get('exit_price'))}{pnl}")
    return "\n".join(lines)


def build_pool_a_list(rows: list) -> str:
    lines = [f"🟢 <b>【A2 追蹤池】{len(rows)} 檔</b>（A1 收盤確認後 {A_TRACK_DAYS} 日內；收盤跌破 MA10 或突破日低點移出）"]
    if not rows:
        return "\n".join(lines + ["目前沒有標的。"])
    for r in sorted(rows, key=lambda r: r["gap_ma10"])[:LIST_MAX]:
        mark = "📉 拉回整理" if r["status"] == "Pullback" else "✅"
        new = " 🆕" if r["new_today"] else ""
        prov = "（暫定）" if r.get("provisional") else ""
        a2 = f"｜A2 {'、'.join(_md(d) for d in r['a2_dates'])}" if r["a2_dates"] else ""
        lines += ["", f"<b>{r['code']} {r['name']}</b>{new}｜{mark}｜A1 {_md(r['entry_date'])} @ {_p2(r['entry_price'])}｜收 {_p2(r['close'])}（{_pc(r['ret_pct'])}）{prov}",
                  f"   MA10 {_p2(r['ma10'])}（距 {_pc(r['gap_ma10'])}）｜MA5 {_p2(r['ma5'])}｜突破日低點 {_p2(r.get('breakout_low'))}｜剩 {r['days_left']} 日{a2}"]
    if len(rows) > LIST_MAX:
        lines += ["", f"…另有 {len(rows) - LIST_MAX} 檔（完整名單見 pool_report.csv）"]
    return "\n".join(lines)


def build_pool_b_list(rows: list) -> str:
    nw = sum(1 for r in rows if r["status"] == "Watching")
    lines = [f"🔵 <b>【B 池】{len(rows)} 檔</b>（Watching {nw}｜Active {len(rows) - nw}）"]
    if not rows:
        return "\n".join(lines + ["目前沒有標的。"])
    for r in sorted(rows, key=lambda r: r["gap_low"])[:LIST_MAX]:
        new = " 🆕" if r["new_today"] else ""
        kd = f"KD {r['kd_k']:.1f}/{r['kd_d']:.1f}｜" if r.get("kd_k") is not None else ""
        if r["status"] == "Watching":
            lines += ["", f"<b>{r['code']} {r['name']}</b>{new}｜👀 等待 {r['waiting_days']}/{B_WAIT_DAYS} 天｜B 訊號日 {_md(r['burst_date'])} 最低 {_p2(r['burst_low'])}｜收 {_p2(r['close'])}（距 {_pc(r['gap_low'])}）",
                      f"   {kd}爆量 {r['burst_ratio']:.1f}x｜{r.get('trigger', '')}｜剩 {r['days_left']} 天"]
        else:
            lines += ["", f"<b>{r['code']} {r['name']}</b>｜✅ 已發動 {_md(r['entry_date'])} @ {_p2(r['entry_price'])}（{_pc(r['ret_pct'])}）｜收 {_p2(r['close'])}",
                      f"   B 防線 {_p2(r['burst_low'])}（距 {_pc(r['gap_low'])}）｜追蹤剩 {r['days_left']} 日"]
    if len(rows) > LIST_MAX:
        lines += ["", f"…另有 {len(rows) - LIST_MAX} 檔（完整名單見 pool_report.csv）"]
    return "\n".join(lines)


# ---------- 盤前 ----------
def regime_lines(regime, compact: bool = False) -> list:
    if regime is None or not regime.w:
        return ["🌡️ 市場趨勢：⚠️ initial_pools.json 沒有指數基準資料（前 19 日收盤），暫以 NORMAL 處理"]
    out = []
    for mk, w in regime.w.items():
        icon = "🔴 BEAR 空頭" if w.state == "BEAR" else "🟢 NORMAL 多頭"
        if compact:
            out.append(f"🌡️ {w.name}：{icon}｜正式切換 {w.switches} 次")
        else:
            lc = f"｜昨收 {w.last_close:,.0f}" if w.last_close else ""
            cur = f"｜現 {w.last_reading:,.0f}" if w.last_reading is not None else ""
            out.append(f"🌡️ {w.name}：<b>{icon}</b>｜即時月線穿越價 P {w.p:,.0f}{lc}{cur}")
    if "otc" not in regime.w and not compact:
        out.append("ℹ️ 沒有櫃買指數基準，上櫃個股暫用加權指數判斷")
    return out


def build_startup_message(init: dict, init_note: str, regime, pools: dict, pool_note: str, check_note: str, notes: list = None) -> str:
    meta = init["metadata"]
    c = meta.get("counts") or {}
    nw = sum(1 for e in pools["b"].values() if e.get("status") == "Watching")
    n_pb = sum(1 for e in pools["a"].values() if e.get("status") == "Pullback")
    n_a2 = sum(1 for e in (init.get("A2_tracking_pool") or {}).values()
               if A2_MIN_DAYS <= ((e.get("a2") or {}).get("n") or 0) <= A2_MAX_DAYS)
    sections = [
        [f"📡 <b>【飆股雷達 {RADAR_VERSION}】即時監控啟動（A1 / A2 / B）</b>",
         f"行情：富邦 Neo API 快照（每 {POLL_INTERVAL} 秒）｜歷史：夜間預算（盤中不抓歷史）｜法人：官方（僅供參考）",
         f"夜間資料：基準日 {meta.get('baseline_date')}（{meta.get('built_at', '')[:16]} 產出）",
         f"　　　　　A1 候選 {c.get('a1', 0)} 檔（5 日均量 ≥ {MIN_VOL5_LOTS:,} 張＋昨收 ≤ 昨日最高均線）｜分時量矩陣 {c.get('matrix', 0)} 檔",
         check_note],
        regime_lines(regime),
        [f"🟢 <b>A2 追蹤池：{len(pools['a'])} 檔</b>（Pullback {n_pb}｜今日可觸發：整理 {A2_MIN_DAYS}～{A2_MAX_DAYS} 天 {n_a2} 檔）",
         f"🔵 <b>B 池：{len(pools['b'])} 檔</b>（Watching {nw}｜Active {len(pools['b']) - nw}）"],
    ]
    if pool_note and ("舊版" in pool_note or "找不到" in pool_note or "失敗" in pool_note):
        sections.append([f"⚠️ {pool_note}"])
    sections.append(["📋 <b>規則摘要</b>",
                    f"🟢 A1：昨收 ≤ 昨日最高均線 → 今日同時站上即時 MA5/10/20 ＋ 漲幅 > {A1_CHG_MIN:g}% ＋ 紅 K ＋ 現價 > VWAP ＋ 同期量比 ≥ {A1_VOL_RATIO:g}（BEAR {A1_VOL_RATIO_BEAR:g}）＋ 預估量 ≥ {A1_MIN_EST_LOTS:,} 張 ＋ 預估金額 ≥ {A_MIN_EST_TURNOVER / 1e8:g} 億",
                    f"🟡 A2：A1 收盤確認後 {A_TRACK_DAYS} 日內，拉回整理 {A2_MIN_DAYS}～{A2_MAX_DAYS} 天 → 現價 > 即時 MA5 與 VWAP ＋ 預估量 ≥ 整理期均量 × {A2_VOL_RATIO:g} ＋ 預估金額 ≥ {A_MIN_EST_TURNOVER / 1e8:g} 億（BEAR 禁止）",
                    f"🔵 B：KD 低檔金叉＋爆量≥{B_VOL_RATIO:g}x＋收盤位置≥{B_CLOSE_LOC_MIN * 100:.0f}% → 10:00 起確認（最低未破＋量縮≤{B_SHRINK_RATIO:g}x＋站上 MA5/VWAP＋預估金額≥{B_MIN_EST_TURNOVER / 1e8:g} 億），等待最多 {B_WAIT_DAYS} 日",
                    f"趨勢：指數 ≥ 即時月線 = NORMAL；連續 {REGIME_ENTER_BEAR_ROUNDS} 輪低於 → BEAR；站回並維持 {REGIME_EXIT_HOLD_SEC // 60} 分鐘 → NORMAL（正式切換才通知）",
                    "判斷時段 09:05～13:24:45｜連續 2 輪符合才通知｜同一檔同一天只發一種主訊號｜停損由你自己處理"])
    if notes:
        sections.append(list(notes))
    return "\n\n".join("\n".join(s) for s in sections if s)


# ---------- CSV ----------
POOL_FIELDS = ["區別", "狀態", "代號", "名稱", "族群", "通知日/B訊號日", "通知價", "B防線/突破日低點", "今日收盤", "報酬(%)",
               "MA5", "MA10", "MA20", "距MA10/B防線(%)", "追蹤/等待天數", "剩餘天數", "A2通知日", "法人T日淨(張)", "備註"]


def build_pool_rows(a_rows: list, b_rows: list, exits: list, unsettled: list) -> list:
    rows = []
    for r in a_rows:
        rows.append({"區別": "A2追蹤池", "狀態": r["status"], "代號": r["code"], "名稱": r["name"], "族群": r["industry"], "通知日/B訊號日": r["entry_date"],
                     "通知價": r["entry_price"], "B防線/突破日低點": "" if r.get("breakout_low") is None else r["breakout_low"], "今日收盤": r["close"],
                     "報酬(%)": "" if r.get("ret_pct") is None else round(r["ret_pct"], 2),
                     "MA5": round(r["ma5"], 2), "MA10": round(r["ma10"], 2), "MA20": round(r["ma20"], 2), "距MA10/B防線(%)": round(r["gap_ma10"], 2),
                     "追蹤/等待天數": r["age"], "剩餘天數": r["days_left"], "A2通知日": "、".join(r["a2_dates"]), "法人T日淨(張)": "",
                     "備註": ("今日A1收盤確認" if r["new_today"] else "") + ("；收盤價暫定（待夜間核對）" if r.get("provisional") else "")})
    for r in b_rows:
        inst5 = r.get("inst5") or []
        t_net = inst5[-1] if inst5 and inst5[-1] is not None else ""
        rows.append({"區別": "B池", "狀態": r["status"], "代號": r["code"], "名稱": r["name"], "族群": r["industry"],
                     "通知日/B訊號日": r.get("entry_date") or r["burst_date"], "通知價": r.get("entry_price") or "", "B防線/突破日低點": r["burst_low"],
                     "今日收盤": r["close"], "報酬(%)": "" if r.get("ret_pct") is None else round(r["ret_pct"], 2),
                     "MA5": round(r["ma5"], 2), "MA10": round(r["ma10"], 2), "MA20": round(r["ma20"], 2), "距MA10/B防線(%)": round(r["gap_low"], 2),
                     "追蹤/等待天數": r["waiting_days"] if r["status"] == "Watching" else (r.get("age") or ""), "剩餘天數": r["days_left"], "A2通知日": "",
                     "法人T日淨(張)": t_net, "備註": ("今日新入池" if r["new_today"] else "") + f"；{r.get('trigger', '')}"})
    for ex in exits:
        rows.append({"區別": f"今日移出（{ex['pool']}）", "狀態": ex["reason"], "代號": ex["code"], "名稱": ex["name"], "通知日/B訊號日": ex.get("entry_date", ""),
                     "通知價": ex.get("entry_price") or "", "今日收盤": ex.get("exit_price") or "", "報酬(%)": "" if ex.get("ret_pct") is None else ex["ret_pct"],
                     "MA10": ex.get("ma10", ""), "追蹤/等待天數": ex.get("age", ex.get("waiting_days", ""))})
    for c in unsettled:
        rows.append({"區別": "未結算", "狀態": "未取得收盤價", "代號": c})
    return rows


def export_pool_csv(day_str: str, rows: list) -> pathlib.Path:
    n_a = sum(1 for r in rows if r["區別"] == "A2追蹤池")
    n_b = sum(1 for r in rows if r["區別"] == "B池")
    n_x = sum(1 for r in rows if r["區別"].startswith("今日移出"))
    title = f"追蹤清單明細：資料基準日 {day_str}（13:35 富邦最終快照，暫定）｜A2追蹤池 {n_a} 檔｜B池 {n_b} 檔｜今日移出 {n_x} 檔｜停損由使用者自行處理"
    with POOL_CSV_FILE.open("w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerow([title])
        writer = csv.DictWriter(fh, fieldnames=POOL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return POOL_CSV_FILE


def market_status_info(feed, today: date_cls) -> dict:
    """大盤 MA20 狀態（盤後摘要顯示用）。富邦加權指數日 K ＋ 即時指數。"""
    try:
        rows = feed.index_closes("tse", today)
        hist = [v for d, v in rows if d < today]
        live = feed.index_price("tse")
        if len(hist) >= 19 and live:
            ma20 = (sum(hist[-19:]) + live) / 20
            return {"ok": True, "close": live, "ma20": ma20, "above": live > ma20}
    except Exception as exc:
        print(f"[大盤] 狀態計算失敗：{type(exc).__name__}: {exc}", flush=True)
    return {"ok": False}


def market_line(info: dict) -> str:
    if not info.get("ok"):
        return "大盤：狀態無法取得（富邦加權指數抓取失敗）"
    if info["above"]:
        return f"大盤：加權指數 {info['close']:,.0f}｜位於 MA20（{info['ma20']:,.0f}）之上 ✅"
    return f"大盤：加權指數 {info['close']:,.0f}｜低於 MA20（{info['ma20']:,.0f}）⚠️"


# =========================
# 盤中判斷（只掃描夜間 initial_pools.json 裡的候選股；分時量權重直接查表）
# =========================
def evaluate_a1(base: dict, table, row: dict, x: float, mode: str, funnel: dict):
    """A1 首發突破（純硬條件）。符合回傳訊號 dict，否則 None。"""
    price = _f(row.get("lastPrice"))
    if not price or price <= 0 or base["avg_5d_vol_simple"] < MIN_VOL5_LOTS:
        return None
    open_ = _f(row.get("openPrice"))
    mas = realtime_mas(base, price)
    chg = chg_pct(base, row, price)
    vol, val = _f(row.get("tradeVolume")) or 0.0, _f(row.get("tradeValue")) or 0.0
    vwap = vwap_of(vol, val)
    est = estimate_full(vol, val, table, x, base["avg_5d_vol_simple"])
    stage = a1_stage(base, price, open_, chg, mas, vwap, est, mode)
    reached = A1_ORDER.index(stage) if stage else len(A1_ORDER)
    for s in A1_ORDER[:reached]:
        funnel[s] = funnel.get(s, 0) + 1
    if stage:
        return None
    return {"pool": "A1", "kind": "A1", "code": base["code"], "name": base["name"], "market": base["market"], "industry": base.get("industry", ""),
            "price": price, "open": open_, "chg": chg, "yest_close": base["yest_close"], "prev_ma_max": base["prev_ma_max"],
            "ma5": mas[0], "ma10": mas[1], "ma20": mas[2], "vwap": vwap, "vwap_pct": (price / vwap - 1) * 100.0,
            "vol": vol, "val": val, "est": est, "avg5": base["avg_5d_vol_simple"], "inst5": base.get("inst5"), "mode": mode}


def evaluate_a2(entry: dict, base: dict, table, row: dict, x: float, mode: str):
    """A2 二次加碼（僅 A2 追蹤池內）。符合回傳訊號 dict，否則 None。"""
    price = _f(row.get("lastPrice"))
    if not price or price <= 0:
        return None
    a2 = entry.get("a2")
    low = _f(row.get("lowPrice"))
    mas = realtime_mas(base, price)
    vol, val = _f(row.get("tradeVolume")) or 0.0, _f(row.get("tradeValue")) or 0.0
    vwap = vwap_of(vol, val)
    est = estimate_full(vol, val, table, x, base["avg_5d_vol_simple"])
    if a2_stage(a2, entry.get("breakout_low"), price, low, mas, vwap, est, mode) is not None:
        return None
    return {"pool": "A2", "kind": "A2", "code": base["code"], "name": base["name"], "market": base["market"], "industry": base.get("industry", ""),
            "price": price, "open": _f(row.get("openPrice")), "chg": chg_pct(base, row, price), "yest_close": base["yest_close"], "low": low,
            "ma5": mas[0], "ma10": mas[1], "ma20": mas[2], "vwap": vwap, "vwap_pct": (price / vwap - 1) * 100.0 if vwap else None,
            "vol": vol, "val": val, "est": est, "consol_days": a2["n"], "consol_avg_vol": a2["avg_vol"],
            "est_ratio": est["est_vol"] / a2["avg_vol"], "entry_date": entry.get("entry_date"), "entry_price": entry.get("entry_price"),
            "breakout_low": entry.get("breakout_low"), "inst5": base.get("inst5"), "mode": mode}


def evaluate_b_confirm(entry: dict, base: dict, table, row: dict, x: float, mode: str, offset: int):
    """B（Watching）確認。10:00 起才判斷。符合回傳訊號 dict，否則 None。"""
    if x < B_CONFIRM_START_MINUTE:
        return None
    price, low = _f(row.get("lastPrice")), _f(row.get("lowPrice"))
    if not price or price <= 0 or low is None:
        return None
    vol, val = _f(row.get("tradeVolume")) or 0.0, _f(row.get("tradeValue")) or 0.0
    est = estimate_full(vol, val, table, x, base["avg_5d_vol_simple"])
    mas = realtime_mas(base, price)
    vwap = vwap_of(vol, val)
    if b_confirm_stage(low, est, entry["burst_low"], entry["burst_vol"], price, mas[0], vwap) is not None:
        return None
    return {"pool": "B", "kind": "BC", "code": base["code"], "name": base["name"], "market": base["market"], "industry": base.get("industry", ""),
            "price": price, "chg": chg_pct(base, row, price), "open": _f(row.get("openPrice")), "low": low,
            "ma5": mas[0], "vwap": vwap, "vwap_pct": (price / vwap - 1) * 100.0 if vwap else None,
            "burst_low": entry["burst_low"], "burst_date": entry["burst_date"], "burst_vol": entry["burst_vol"],
            "burst_ratio": entry.get("burst_ratio") or 0.0, "trigger": entry.get("trigger", ""), "kd_k": entry.get("kd_k"), "kd_d": entry.get("kd_d"),
            "low_dist_pct": (low / entry["burst_low"] - 1) * 100.0, "vol": vol, "val": val, "est": est,
            "shrink_ratio": est["est_vol"] / entry["burst_vol"], "offset": offset,
            "inst5": entry.get("inst5") or base.get("inst5"), "mode": mode}


def apply_double_confirm(candidates: list, now: datetime, pending: dict) -> list:
    """二次確認：本輪符合的股票（candidates）與上一輪的待確認池（pending）比對。
    - 上一輪在池中、這一輪又符合，且間隔 CONFIRM_MIN_GAP～CONFIRM_MAX_GAP 秒 → 確認通過。
    - 首次符合（或間隔過久）→ 放進待確認池，等下一輪。
    - 上一輪在池中、這一輪沒再符合 → 移出（過濾單筆跳動的假訊號）。"""
    confirmed, new_pending = [], {}
    for sig in candidates:
        code = sig["code"]
        prev = pending.get(code)
        if prev is not None:
            gap = (now - prev["time"]).total_seconds()
            if gap < CONFIRM_MIN_GAP_SECONDS:             # 太快：同一份資料，不算第二次
                new_pending[code] = prev
                continue
            if gap <= CONFIRM_MAX_GAP_SECONDS:            # 下一次掃描再次符合 → 確認
                sig["first_time"] = prev["time"].strftime("%H:%M:%S")
                sig["first_price"] = prev["price"]
                confirmed.append(sig)
                continue
        new_pending[code] = {"time": now, "price": sig["price"]}
        print(f"[待確認] {code} {sig['name']}（{sig['kind']}，{sig['price']:.2f}）首次符合，等下一輪再確認", flush=True)
    done = {s["code"] for s in confirmed}
    for c in pending:
        if c not in new_pending and c not in done:
            print(f"[待確認] {c} 下一輪沒有再符合，移出待確認池（過濾假訊號）", flush=True)
    pending.clear()
    pending.update(new_pending)
    return confirmed


def debug_line(base: dict, table, row: dict, x: float, mode: str, pools: dict) -> str:
    price = _f(row.get("lastPrice"))
    if not price:
        return f"[診斷 {base['code']} {base['name']}] 無成交價"
    mas = realtime_mas(base, price)
    vol, val = _f(row.get("tradeVolume")) or 0.0, _f(row.get("tradeValue")) or 0.0
    est = estimate_full(vol, val, table, x, base["avg_5d_vol_simple"]) if table else None
    vw = vwap_of(vol, val)
    a_e, b_e = pools["a"].get(base["code"]), pools["b"].get(base["code"])
    return (f"[診斷 {base['code']} {base['name']}] 趨勢 {mode}｜價 {price}｜昨收 {base['yest_close']} 昨日最高均線 {base['prev_ma_max']:.2f}"
            f"｜即時 MA5/10/20 {mas[0]:.2f}/{mas[1]:.2f}/{mas[2]:.2f}｜VWAP {'無' if vw is None else round(vw, 2)}"
            f"｜R {'無' if not est else round(est['R'], 3)} 預估量 {'無' if not est else round(est['est_vol'])}（5日均量 {base['avg_5d_vol_simple']:.0f}）"
            f"同期量比 {'無' if not est or est['sync_ratio'] is None else round(est['sync_ratio'], 2)}"
            f"｜預估金額 {'無' if not est else round(est['est_val'] / 1e8, 1)} 億｜A2池 {'有' if a_e else '無'} {(a_e or {}).get('a2')}｜B {(b_e or {}).get('status', '無')}")


def process_poll(snap: dict, ctx: dict, regime, now: datetime, funnel: dict):
    """逐檔判斷，回傳 (A1, A2, B 確認) 三組「通過二次確認」的新訊號。只掃描：A1 候選池 ∪ A2 追蹤池 ∪ B 等待中。
    優先序 A2 > A1 > B；ONE_SIGNAL_PER_STOCK = True 時，同一檔同一輪只往下評一種。"""
    bases, matrix, a1_set, pools = ctx["bases"], ctx["matrix"], ctx["a1"], ctx["pools"]
    xs = {mkt: snapshot_minutes(meta, now) for mkt, meta in snap["meta"].items()}
    a1c, a2c, bcc = [], [], []
    universe = set(a1_set) | set(pools["a"]) | {c for c, e in pools["b"].items() if e.get("status") == "Watching"}
    for code in sorted(universe):
        row, base, table = snap["rows"].get(code), bases.get(code), matrix.get(code)
        if row is None or base is None or table is None:
            continue
        if not base["liquid"] or base["market"] in UNRELIABLE_MARKETS:
            continue
        mkt = row["_mkt"]
        mode = regime.mode(mkt) if regime is not None else "NORMAL"
        x = xs.get(mkt, minutes_since_open(now))
        if code in DEBUG_STOCK_CODES:
            print(debug_line(base, table, row, x, mode, pools), flush=True)
        if x < EVAL_START_MINUTE:
            continue
        done = False
        a_entry = pools["a"].get(code)
        if A2_ENABLED and a_entry and code not in STATE["a2"]:
            sig = evaluate_a2(a_entry, base, table, row, x, mode)
            if sig:
                a2c.append(sig)
                done = True
        if A1_ENABLED and code in a1_set and code not in STATE["triggered"] and not (done and ONE_SIGNAL_PER_STOCK):
            sig = evaluate_a1(base, table, row, x, mode, funnel)
            if sig:
                a1c.append(sig)
                done = True
        b_entry = pools["b"].get(code)
        if B_ENABLED and b_entry and b_entry.get("status") == "Watching" and code not in STATE["b_confirm"] and not (done and ONE_SIGNAL_PER_STOCK):
            off = b_window_offset(pools, b_entry)
            if 1 <= off <= B_WAIT_DAYS:
                sig = evaluate_b_confirm(b_entry, base, table, row, x, mode, off)
                if sig:
                    bcc.append(sig)
    funnel["pend"] = funnel.get("pend", 0) + len(a1c)
    a1 = apply_double_confirm(a1c, now, PENDING_A1)
    a2 = apply_double_confirm(a2c, now, PENDING_A2)
    bc = apply_double_confirm(bcc, now, PENDING_BC)
    funnel["conf"] = funnel.get("conf", 0) + len(a1)
    funnel["a2"] = funnel.get("a2", 0) + len(a2)
    funnel["bconf"] = funnel.get("bconf", 0) + len(bc)
    return a1, a2, bc


# =========================
# 狀態檔（stock_states.json：當日已通知的名單、市場趨勢狀態、執行鎖；重啟不重發）
# =========================
def load_states() -> None:
    STATE.update({"date": now_tw().strftime("%Y-%m-%d"), "run": {}, "triggered": {}, "a2": {}, "b_confirm": {}, "signals": [],
                  "regime_events": [], "regime": {}})
    if not STATE_FILE.exists():
        return
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if raw.get("_date") != STATE["date"]:
        return
    STATE["run"] = dict(raw.get("_run") or {})
    for key, name in (("triggered", "_triggered"), ("a2", "_a2"), ("b_confirm", "_b_confirm")):
        STATE[key] = dict(raw.get(name) or {})
    STATE["signals"] = list(raw.get("_signals") or [])
    STATE["regime_events"] = list(raw.get("_regime_events") or [])
    STATE["regime"] = dict(raw.get("_regime") or {})


def save_states() -> None:
    raw = {"_date": STATE["date"], "_run": STATE["run"], "_triggered": STATE["triggered"], "_a2": STATE["a2"], "_b_confirm": STATE["b_confirm"],
           "_signals": STATE["signals"], "_regime_events": STATE["regime_events"], "_regime": STATE["regime"]}
    atomic_write_json(STATE_FILE, raw)


def set_run_status(status: str) -> None:
    """執行鎖：RUNNING / COMPLETED。同一交易日已 COMPLETED，後續重複觸發（例如 Apps Script 重試）直接忽略。"""
    STATE["run"] = {"status": status, "run_id": os.environ.get("GITHUB_RUN_ID", "LOCAL"), "updated": now_tw().isoformat(timespec="seconds")}
    save_states()


def signal_row(sig: dict, now: datetime, label: str) -> dict:
    est = sig.get("est") or {}
    return {"時間": now.strftime("%H:%M:%S"), "首次符合時間": sig.get("first_time", ""), "類型": label, "代號": sig["code"], "名稱": sig["name"],
            "市場": sig.get("market", ""), "趨勢": sig.get("mode", ""), "現價": round(sig["price"], 2), "漲幅(%)": round(sig.get("chg") or 0.0, 2),
            "同期量比": None if est.get("sync_ratio") is None else round(est["sync_ratio"], 2),
            "預估成交額(億)": None if not est else round(est["est_val"] / 1e8, 2)}


# =========================
# 啟動檢查
# =========================
async def validate_baseline(feed, loop, today: date_cls, baseline_date: str):
    """基準日必須是「今天以前最近一個交易日」（用富邦 2330 日 K 判斷，會處理連假）；富邦查詢失敗時退而求其次：基準日在 5 天內。回傳 (是否通過, 說明)。"""
    expected = None
    try:
        days = await loop.run_in_executor(None, trading_days, feed, today - timedelta(days=1))
        expected = days[-1] if days else None
    except Exception as exc:
        print(f"[基準日] 查詢最近交易日失敗（{type(exc).__name__}: {exc}），改用日期差判斷", flush=True)
    if expected is not None:
        return expected == baseline_date, f"最近交易日 {expected}，initial_pools 基準日 {baseline_date}"
    try:
        gap = (today - date_cls.fromisoformat(baseline_date)).days
    except ValueError:
        return False, f"基準日格式錯誤：{baseline_date}"
    return gap <= 5, f"（富邦查詢失敗，以日期差判斷）基準日 {baseline_date} 距今 {gap} 天"


async def self_check(feed, loop):
    """盤前自檢：上市 / 上櫃全市場快照、加權 / 櫃買指數是否可用。回傳 (指數讀數 dict, 說明)。"""
    notes = []
    try:
        snap = await loop.run_in_executor(None, feed.snapshot_all)
        n_tse = sum(1 for r in snap["rows"].values() if r["_mkt"] == "tse")
        notes.append(f"行情自檢：上市快照 {n_tse:,} 檔、上櫃快照 {len(snap['rows']) - n_tse:,} 檔 ✅")
        if snap["errors"]:
            notes[-1] = f"⚠️ 行情自檢：部分市場失敗（{'；'.join(snap['errors'])}）"
    except Exception as exc:
        notes.append(f"⚠️ 行情自檢：快照失敗（{type(exc).__name__}: {exc}）")
    readings = {}
    try:
        readings = await loop.run_in_executor(None, feed.index_prices)
    except Exception as exc:
        print(f"[自檢] 指數查詢失敗：{type(exc).__name__}: {exc}", flush=True)
    notes.append("指數：" + "｜".join(f"{'加權' if mk == 'tse' else '櫃買'} {'讀取失敗' if v is None else f'{v:,.2f}'}" for mk, v in readings.items()))
    return readings, "\n".join(notes)


# =========================
# 收盤結算（13:35 起；只用已產生的收盤價）
# =========================
def close_events(pools: dict, res: dict, init: dict, regime) -> dict:
    base_date = ((init or {}).get("metadata") or {}).get("baseline_date")
    b_exp = sum(1 for e in res["b_exits"] if str(e["reason"]).startswith("B_EXPIRED"))
    return {"a1": len(STATE["triggered"]), "a1_ok": len(res["a1_confirmed"]), "a1_fail": len(res["a1_failed"]), "a2": len(STATE["a2"]),
            "bc": len(STATE["b_confirm"]), "a_exit": len(res["a_exits"]), "b_break": len(res["b_exits"]) - b_exp, "b_expire": b_exp,
            "b_new": sum(1 for e in ((init or {}).get("B_candidates") or {}).values() if e.get("burst_date") == base_date),
            "regime_switches": sum(w.switches for w in regime.w.values()) if regime else 0}


async def run_close(bases: dict, feed, loop, today: date_cls, pools: dict, regime=None, init: dict = None) -> bool:
    today_str = today.strftime("%Y-%m-%d")
    needed = set(pools["a"]) | set(pools["b"]) | set(pools.get("a1_pending") or {})
    deadline = datetime(today.year, today.month, today.day, SETTLE_DEADLINE[0], SETTLE_DEADLINE[1], tzinfo=TW_TZ)
    closes, reasons, first = {}, {}, True
    while True:
        snap = None
        for attempt in range(1, 5):
            try:
                snap = await loop.run_in_executor(None, feed.snapshot_all)
                break
            except Exception as exc:
                print(f"[收盤結算] 第 {attempt} 次抓快照失敗：{type(exc).__name__}: {exc}", flush=True)
                await asyncio.sleep(20)
        if snap is None:
            await loop.run_in_executor(None, send_telegram, "❌ <b>【飆股雷達】</b> 收盤結算失敗：抓不到最終快照，請手動確認。")
            return False
        if first:
            first = False
            dates = {str(m.get("date", "")).replace("-", "") for m in snap["meta"].values()}
            if today_str.replace("-", "") not in dates:
                print(f"[收盤結算] 快照日期 {dates} 不是今天 {today_str}，視為非交易日，不發盤後報告", flush=True)
                return False
            if not any("isTrial" in r for r in snap["rows"].values()):
                print("[收盤結算] ⚠️ 快照列沒有 isTrial 欄位：無法判斷是否為試撮，只靠 closePrice 是否有值（暫緩收盤個股可能誤用試撮價，請確認富邦回傳欄位）", flush=True)
            if pools.get("asof") == today_str:
                print(f"[收盤結算] 追蹤清單已在 {today_str} 結算過，不重複處理（避免追蹤天數被重複計算）", flush=True)
                return False
        for code in needed - set(closes):
            info, why = close_info_from_row(snap["rows"].get(code))
            if info:
                closes[code] = info
            else:
                reasons[code] = why
        pending = [c for c in needed if c not in closes and reasons.get(c) in ("trial", "noclose")]
        for c in list(pending):                                   # 快照還沒給收盤價 → 單檔報價看 isClose
            cp = await loop.run_in_executor(None, feed.quote_close, c)
            if cp:
                info, _ = close_info_from_row(dict(snap["rows"].get(c) or {}, isTrial=False, closePrice=cp))
                if info:
                    closes[c] = info
                    pending.remove(c)
        if not pending or now_tw() >= deadline:
            break
        print(f"[收盤結算] {len(pending)} 檔尚未取得收盤價（{', '.join(sorted(pending))}），{SETTLE_RETRY_SEC} 秒後重查", flush=True)
        await asyncio.sleep(SETTLE_RETRY_SEC)
    skip = {c for c in needed if c not in closes and reasons.get(c) in ("trial", "noclose")}
    res = settle_close(pools, bases, closes, today_str, skip)
    save_pools(pools)
    ev = close_events(pools, res, init, regime)
    print(f"[收盤結算] A2 追蹤池 {len(res['a_rows'])} 檔、B 池 {len(res['b_rows'])} 檔｜今日 {ev}｜未取得收盤價 {res['unsettled']}", flush=True)
    market_info = await loop.run_in_executor(None, market_status_info, feed, today)
    msgs = [build_close_summary(today_str, market_line(market_info), regime, res["a_rows"], res["b_rows"], ev, res["unsettled"])]
    if res["a1_failed"]:
        lines = [f"🟡 <b>【A1 收盤未確認】</b>｜{today_str}", "盤中通知過 A1，但收盤沒站穩三均線 / 沒有收盤資料，不進 A2 追蹤池："]
        for r in res["a1_failed"][:EXIT_LIST_MAX]:
            lines.append(f"• <b>{r['code']} {r['name']}</b>｜{r['reason']}｜收 {_p2(r.get('exit_price'))}")
        msgs.append("\n".join(lines))
    if res["a_exits"]:
        msgs.append(build_a_exit_message(today_str, res["a_exits"]))
    if res["b_exits"]:
        msgs.append(build_b_close_message(today_str, res["b_exits"]))
    msgs += [build_pool_a_list(res["a_rows"]), build_pool_b_list(res["b_rows"])]
    for i, m in enumerate(msgs):
        if i:
            await asyncio.sleep(1.0)
        await loop.run_in_executor(None, send_telegram, m)
    today_closed = [r for r in pools["closed"] if r["date"] == today_str]
    csv_path = export_pool_csv(today_str, build_pool_rows(res["a_rows"], res["b_rows"], today_closed, res["unsettled"]))
    await loop.run_in_executor(None, send_telegram_document, csv_path,
                               f"🧾 追蹤清單明細（資料基準日 {today_str}）：A2 追蹤池 {len(res['a_rows'])}｜B 池 {len(res['b_rows'])}｜今日移出 {len(today_closed)}")
    save_states()
    return True


# =========================
# 主程式
# =========================
async def main_async() -> None:
    print("=" * 55, flush=True)
    print(f"飆股雷達 {RADAR_VERSION}：A1 / A2 / B｜行情富邦 Neo API｜夜間預算 initial_pools.json｜盤中只查表", flush=True)
    print("=" * 55, flush=True)
    current = now_tw()
    print(f"[程序啟動] PID={os.getpid()}, GITHUB_RUN_ID={os.environ.get('GITHUB_RUN_ID', 'LOCAL')}, "
          f"EVENT={os.environ.get('GITHUB_EVENT_NAME', 'unknown')}, 時間={current.strftime('%Y-%m-%d %H:%M:%S')}（Asia/Taipei）", flush=True)
    if not _is_weekday(current):
        print("週末非交易日，結束", flush=True)
        return
    loop = asyncio.get_running_loop()
    today = current.date()
    today_str = today.strftime("%Y-%m-%d")
    load_states()
    if STATE["run"].get("status") == "COMPLETED":
        print(f"[執行鎖] {today_str} 已完成（run_id={STATE['run'].get('run_id')}），重複觸發直接忽略", flush=True)
        return
    set_run_status("RUNNING")
    after_close = current >= _close_confirm_today(current)
    feed = FubonFeed()
    try:
        await loop.run_in_executor(None, feed.login)
    except Exception as exc:
        send_telegram(f"❌ <b>【飆股雷達】</b> 富邦登入失敗，監控未啟動：{type(exc).__name__}: {exc}")
        raise
    pools, pool_note = load_pools()
    print(f"[追蹤清單] {pool_note}", flush=True)
    init, init_note = load_initial_pools()
    print(f"[夜間資料] {init_note}", flush=True)
    if init is None:
        send_telegram(f"❌ <b>【飆股雷達】</b> 無法載入夜間資料：{init_note}\n今天不發盤中訊號。請先確認 baseline workflow 已成功執行。")
        return
    baseline_date = init["metadata"]["baseline_date"]
    ok, why = await validate_baseline(feed, loop, today, baseline_date)
    print(f"[基準日] {why}｜{'通過' if ok else '不符'}", flush=True)
    if not ok:
        send_telegram(f"❌ <b>【飆股雷達】</b> 夜間資料的基準日不是最近交易日（{why}），今天不發盤中訊號。\n"
                      f"請確認昨晚 baseline workflow 已成功執行並 commit 了 initial_pools.json。")
        return
    bases, matrix, a1_set = init["stock_bases"], init["volume_time_matrix"], set(init["A1_candidates"])
    sync_stats = sync_pools_from_initial(pools, init)
    print(f"[對齊] 夜間快照 → 追蹤清單：補進 A2 追蹤池 {sync_stats['a_added']} 檔、B 池 {sync_stats['b_added']} 檔", flush=True)
    save_pools(pools)
    ctx_idx = {mk: v for mk, v in (("tse", init.get("listed_market_regime")), ("otc", init.get("otc_market_regime"))) if v and v.get("p")}
    if not ctx_idx:
        print("[趨勢] initial_pools.json 沒有指數基準，改向富邦補抓前 19 日收盤", flush=True)
        ctx_idx = await loop.run_in_executor(None, index_regime_snapshot, feed, date_cls.fromisoformat(baseline_date))
    regime = build_regime(ctx_idx, STATE.get("regime"))
    if after_close:
        print("已過收盤確認時間：直接執行收盤結算（通知紀錄取自 stock_states.json）", flush=True)
        done = await run_close(bases, feed, loop, today, pools, regime, init)
        if done:
            set_run_status("COMPLETED")
        return
    readings, check_note = await self_check(feed, loop)
    for mk, w in regime.w.items():
        if mk not in STATE["regime"] and readings.get(mk) is not None:                     # 今天第一次啟動：用目前讀數決定起始狀態
            w.state = "NORMAL" if readings[mk] >= w.p else "BEAR"
            w.last_reading = readings[mk]
    STATE["regime"] = {mk: {"state": w.state, "p": w.p} for mk, w in regime.w.items()}
    save_states()
    print(f"[趨勢] {[(mk, w.name, round(w.p, 2), w.state) for mk, w in regime.w.items()]}", flush=True)
    late = current >= _market_open_today(current) + timedelta(minutes=EVAL_START_MINUTE)
    notes = []
    if STATE["triggered"] or STATE["a2"] or STATE["b_confirm"]:
        notes.append(f"續跑：今日已通知 A1 {len(STATE['triggered'])}、A2 {len(STATE['a2'])}、B 確認 {len(STATE['b_confirm'])} 檔，不會重複通知")
    if any(c.get("stale") for c in ctx_idx.values()):
        notes.append("⚠️ 指數日 K 最後一天不是基準日（富邦日 K 盤後 16:30 才更新），月線穿越價可能不是最新")
    if late and not after_cutoff(current):
        notes.append(f"⏰ <b>雷達延遲啟動</b>：實際啟動 {current.strftime('%H:%M:%S')}，已過 09:05；先暖機 {LATE_START_WARMUP_POLLS} 輪再開放訊號，啟動前的訊號可能已錯過，請自行對照看盤軟體")
    if after_cutoff(current):
        notes.append(f"⏰ 啟動時間 {current.strftime('%H:%M:%S')} 已過 13:25：今天不再發新的個股訊號，只做 13:35 收盤結算")
    startup = build_startup_message(init, init_note, regime, pools, pool_note, check_note, notes)
    await loop.run_in_executor(None, send_telegram, startup)
    ctx = {"bases": bases, "matrix": matrix, "a1": a1_set, "pools": pools}
    fail_streak, fail_alerted, fail_started = 0, False, None
    polls = 0
    last_total, stall_polls, stall_alerted, stall_started = None, 0, False, None
    funnel = {"chg": 0, "red": 0, "cross": 0, "vwap": 0, "vol": 0, "turn": 0, "pend": 0, "conf": 0, "a2": 0, "bconf": 0}
    warmup_left = LATE_START_WARMUP_POLLS if (late and not after_cutoff(current)) else 0
    try:
        while True:
            current = now_tw()
            if current >= _close_confirm_today(current):
                break
            open_dt = _market_open_today(current)
            if current < open_dt:
                wait = int((open_dt - current).total_seconds()) + 1
                print(f"[{current.strftime('%H:%M:%S')}] 尚未開盤，等待 {wait}s...", flush=True)
                await asyncio.sleep(min(wait, 60))
                continue
            if after_cutoff(current):
                await asyncio.sleep(10)            # 13:24:45 後停止所有即時通知（13:25 起收盤集合競價），等到 13:35 做收盤結算
                continue
            started = time.time()
            try:
                snap = await loop.run_in_executor(None, feed.snapshot_all)
            except Exception as exc:
                fail_streak += 1
                fail_started = fail_started or current
                print(f"[{current.strftime('%H:%M:%S')}] 快照失敗（連續 {fail_streak} 輪）：{type(exc).__name__}: {exc}", flush=True)
                if fail_streak >= FAIL_ALERT_AFTER:
                    if not fail_alerted:
                        fail_alerted = True
                        await loop.run_in_executor(None, send_telegram,
                                                   f"⚠️ <b>【飆股雷達】富邦行情連續 {fail_streak} 輪抓取失敗</b>\n{type(exc).__name__}: {exc}\n"
                                                   f"程式會每 {FAIL_ALERT_AFTER} 輪重新登入並持續重試；這段期間不會有盤中訊號。")
                    if fail_streak % FAIL_ALERT_AFTER == 0:
                        try:
                            await loop.run_in_executor(None, feed.login)
                        except Exception as login_exc:
                            print(f"[富邦] 重新登入失敗：{type(login_exc).__name__}: {login_exc}", flush=True)
                await asyncio.sleep(min(FAIL_BACKOFF_MAX, 5 * 2 ** (fail_streak - 1)))
                continue
            if fail_streak:
                lasted = (current - fail_started).total_seconds() if fail_started else 0
                print(f"[富邦] 已恢復（連續失敗 {fail_streak} 輪，約 {lasted:.0f} 秒）", flush=True)
                if fail_alerted:
                    await loop.run_in_executor(None, send_telegram, f"✅ <b>【飆股雷達】富邦行情已恢復</b>\n中斷約 {lasted / 60:.1f} 分鐘，這段時間的訊號可能已錯過，請自行對照看盤軟體。")
                fail_streak, fail_alerted, fail_started = 0, False, None
            if snap["errors"]:
                print(f"[富邦] 部分市場失敗：{'；'.join(snap['errors'])}", flush=True)
            dates = {str(m.get("date", "")).replace("-", "") for m in snap["meta"].values() if m.get("date")}
            if dates and today_str.replace("-", "") not in dates and current >= open_dt + timedelta(minutes=3):
                print(f"快照日期 {dates} 不是今天 {today_str}，今日休市，結束", flush=True)
                await loop.run_in_executor(None, send_telegram, f"ℹ️ <b>【飆股雷達】</b> 快照日期不是今天（{today_str}），判定今日休市，監控結束。")
                set_run_status("COMPLETED")
                return
            total_vol, summary = snapshot_summary(snap, current)
            delta = 0.0 if last_total is None else total_vol - last_total
            x_now = minutes_since_open(current)
            in_window = STALL_CHECK_FROM_MINUTE <= x_now <= STALL_CHECK_UNTIL_MINUTE
            if last_total is not None and in_window and delta <= 0:
                stall_polls += 1
                stall_started = stall_started or current
            elif last_total is not None and delta > 0:
                if stall_alerted:
                    lasted = (current - stall_started).total_seconds() if stall_started else 0
                    await loop.run_in_executor(None, send_telegram, f"✅ <b>【飆股雷達】快照資料已恢復更新</b>\n凍結約 {lasted / 60:.1f} 分鐘，這段時間的訊號可能已錯過，請自行對照看盤軟體。")
                stall_polls, stall_alerted, stall_started = 0, False, None
            if stall_polls >= STALL_ALERT_POLLS and not stall_alerted:
                stall_alerted = True
                await loop.run_in_executor(None, send_telegram,
                                           f"⚠️ <b>【飆股雷達】快照資料疑似沒有更新</b>\n全市場累積量連續 {stall_polls} 輪（約 {stall_polls * POLL_INTERVAL / 60:.1f} 分鐘）完全沒變。\n"
                                           f"{summary}\n可能是行情服務異常或休市，請對照看盤軟體；這段期間的訊號不可信。")
            last_total = total_vol
            # ---- 市場趨勢：每輪更新兩個指數的即時月線狀態（防抖後「正式切換」才通知一次）----
            sent = 0
            readings = await loop.run_in_executor(None, feed.index_prices)
            for mk, w in regime.w.items():
                rd_ = readings.get(mk)
                if rd_ is not None and w.last_close and abs(rd_ / w.last_close - 1) > 0.25:
                    print(f"[趨勢] {w.name} 讀數 {rd_} 與昨收 {w.last_close} 差距過大（可能抓到報酬指數），忽略", flush=True)
                    rd_ = None
                ev = w.update(rd_, current.timestamp())
                if ev:
                    STATE["regime_events"].append({"time": current.strftime("%H:%M:%S"), "kind": ev, "market": mk, "reading": rd_, "p": w.p})
                    STATE["regime"][mk] = {"state": w.state, "p": w.p}
                    enqueue_alert(build_regime_message(ev, w, current))
                    print(f"[趨勢] {w.name} {ev}：現 {rd_}，月線穿越價 {w.p:.2f}", flush=True)
                    sent += 1
            if warmup_left > 0:
                warmup_left -= 1
                a1_sigs, a2_sigs, bc_sigs = [], [], []
                print(f"[暖機] 延遲啟動，剩 {warmup_left} 輪才開放訊號", flush=True)
            else:
                a1_sigs, a2_sigs, bc_sigs = await loop.run_in_executor(
                    None, functools.partial(process_poll, snap, ctx, regime, current, funnel))
            for sig in sorted(a1_sigs, key=lambda s: -s["est"]["val"]):
                STATE["triggered"][sig["code"]] = {"time": current.strftime("%H:%M:%S"), "price": sig["price"]}
                STATE["signals"].append(signal_row(sig, current, "A1"))
                pool_a1_pending_add(pools, sig, today_str, current)
                enqueue_alert(build_a1_message(sig, len(STATE["triggered"]), current))
                print(f"觸發 A1（二次確認通過｜{sig['mode']}）：{sig['code']} {sig['name']}（{sig['price']:.2f}，{sig['chg']:+.1f}%，同期量比 {sig['est']['sync_ratio']:.2f}x）", flush=True)
                sent += 1
            for sig in sorted(a2_sigs, key=lambda s: -s["est"]["val"]):
                STATE["a2"][sig["code"]] = {"time": current.strftime("%H:%M:%S"), "price": sig["price"]}
                STATE["signals"].append(signal_row(sig, current, "A2"))
                pool_a2_mark(pools, sig, today_str)
                enqueue_alert(build_a2_message(sig, len(STATE["a2"]), current))
                print(f"觸發 A2（二次確認通過）：{sig['code']} {sig['name']}（{sig['price']:.2f}，整理 {sig['consol_days']} 天）", flush=True)
                sent += 1
            for sig in sorted(bc_sigs, key=lambda s: -s["est"]["val"]):
                STATE["b_confirm"][sig["code"]] = {"time": current.strftime("%H:%M:%S"), "price": sig["price"]}
                STATE["signals"].append(signal_row(sig, current, "B確認"))
                pool_b_confirm(pools, sig, today_str, current)
                enqueue_alert(build_bc_message(sig, len(STATE["b_confirm"]), current))
                print(f"觸發 B 確認（二次確認通過｜{sig['mode']}）：{sig['code']} {sig['name']}（{sig['price']:.2f}，B 防線 {sig['burst_low']:.2f}，量縮 {sig['shrink_ratio']:.2f}x）", flush=True)
                sent += 1
            if sent:
                save_states()
                save_pools(pools)
            await flush_alerts(loop)
            polls += 1
            elapsed = time.time() - started
            print(f"[{current.strftime('%H:%M:%S')}] 第 {polls} 輪：快照 {len(snap['rows'])} 檔，新訊號/事件 {sent}，今日累計 {len(STATE['signals'])}，"
                  f"耗時 {elapsed:.2f}s｜{summary}（{delta:+,.0f}）", flush=True)
            if polls % 4 == 1:
                print(f"    樣本 {sample_line(snap)}｜趨勢 {[(w.name, w.state) for w in regime.w.values()]}", flush=True)
            if polls % 20 == 0:
                print(f"[漏斗/最近 20 輪累計人次] 漲幅>{A1_CHG_MIN:g}% {funnel['chg']} → 紅K {funnel['red']} → 同時站上三均線 {funnel['cross']} → VWAP {funnel['vwap']} "
                      f"→ 同期量比 {funnel['vol']} → 成交額 {funnel['turn']} → 待確認 {funnel['pend']} → A1 通過 {funnel['conf']}｜A2 {funnel['a2']}｜B 確認 {funnel['bconf']}", flush=True)
                funnel = {k: 0 for k in funnel}
            await asyncio.sleep(max(1.0, POLL_INTERVAL - elapsed))
    except asyncio.CancelledError:
        save_states()
        raise
    except Exception as exc:
        print(f"主迴圈發生未處理例外: {type(exc).__name__}: {repr(exc)}", flush=True)
        save_states()
        await loop.run_in_executor(None, send_telegram, f"⚠️ <b>【飆股雷達】</b> 監控異常中止：{type(exc).__name__}: {exc}")
        raise
    save_states()
    done = await run_close(bases, feed, loop, today, pools, regime, init)
    if done:
        set_run_status("COMPLETED")


if __name__ == "__main__":
    asyncio.run(main_async())
