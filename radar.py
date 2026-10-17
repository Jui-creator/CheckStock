"""飆股雷達 v67：雙股池（右側突破 × 左側低接）—— 價格與行情資料「全部」來自富邦 Neo API。
不再有 A/B/C 型態，改成兩條互相獨立的策略線，各自一個股池：

🟢 股池 A｜強勢波段池（右側順勢・MA10 移動停利）
  進場（盤中每 15 秒掃描，全部成立 → 二次確認 → 通知【A池：強勢突破買進訊號】）
    ① 現價 > 近 20 日最高價（不含今日）
    ② 均線多頭排列 MA5 > MA10 > MA20（含今日現價的即時均線）
    ③ 現價 > 當日成交均價 VWAP（成交金額 / 成交股數）
    ④ 累積量 ≥ 5 日「同期」均量 × 1.5（同期量曲線由 baseline.py 每晚用富邦 1 分 K 產生，排除單日最大量）
    ⑤ 外盤成交比率 ≥ 55%（富邦 intraday.quote 的內外盤累計量；只查通過 ①～④ 的候選股）
    ＋ 保留的底線：5 日均量 ≥ 1,000 張、預估成交額 > 30 億、漲幅 ≤ 9.9%（鎖漲停買不到）
  持股與出場：收盤價穩在 MA10 之上續抱；13:35 收盤「收盤跌破 MA10」→ 移出股池並通知【A池：MA10 跌破停利通知】（盤中不砍）。
  狀態：Active（收盤 ≥ MA5）／ Pullback（收盤 < MA5 但 ≥ MA10）／ 移出。

🔵 股池 B｜跌深反彈池（左側逆勢・低接防守，允許空頭或盤整，不要求多頭排列）
  三部曲：
    ① 入池（13:35 收盤掃描）：KD(9,3,3) 在低檔（K < 40）發生黃金交叉，且當天「低點爆量」（成交量 ≥ 20 日均量 × 2），
       並伴隨長下影線（下影線 > 實體的一半，且 ≥ 振幅 30%、振幅 ≥ 2%）或帶量長紅 K（實體 ≥ 3%）。
       → 加入股池 B（Watching），以爆量日「最低價」為絕對防線。
    ② 隔天確認（盤中，爆量日後 1～2 個交易日內，10:00 起）：未跌破爆量低點，且預估全日量 ≤ 爆量日量 × 0.6（浮額沉澱）
       → 二次確認 → 通知【B池：跌深反彈確認切入訊號】（狀態轉 Active，視為切入）。
    ③ 停損：已切入（Active）的標的，盤中跌破爆量低點且「帶量」（累積量 ≥ 5 日同期均量）→ 通知【B池：左側防線失守停損通知】；
       收盤跌破爆量低點則一律移出（不管有沒有帶量）。
  Watching 的標的若盤中最低價跌破爆量低點、或確認窗到期 → 靜默移出（盤後報告列出，不發停損通知，因為還沒切入）。
  Active 的其他出口：收盤站上 MA20（反彈達標）、持有滿 10 個交易日、或後來觸發 A 池突破（轉入 A 池）。

【資料來源：全部是富邦 Neo API】
  盤中：snapshot.quotes（全市場快照：現價 / 開高低 / 累積量 / 成交金額）＋ intraday.quote（外盤比、加權指數）
  盤後：snapshot.quotes 的最終資料做收盤結算
  歷史：historical.candles 日 K（原始價格 adjusted=false；volume 股→張；turnover 成交金額）→ 歷史基底、回測、大盤環境（加權指數日 K）
        historical.candles 1 分 K → 同期量曲線（baseline.py）
  產業別：intraday.tickers（依產業別代碼）。唯一的非富邦資料是「櫃買產業價值鏈」族群標籤（非價格，抓不到就退回富邦產業別）。
  已不再使用 TWSE / TPEx 官方網站的任何價格資料。

【每日流程】
  盤前（08:xx）：載入歷史基底與兩個股池 → 評估大盤環境（偏多 / 盤整 / 偏空，決定今日偏重 A 或 B）→ 發啟動訊息。
  盤中（09:05～13:30，每 15 秒一輪）：掃 A 進場；掃 B 確認；盯 B 停損。每個訊號都要「連續兩輪都符合」才通知，同一檔當天同一類只通知一次。
  收盤（13:35）：更新兩個股池（A：MA10 停利；B：停損 / 到期 / 達標）→ 掃描 B 新入池 → Telegram 盤後報告 + pool_report.csv。
  初始化 / 回測：python radar.py --backfill [--days 120]（富邦日 K，以同一套規則逐日重播，重建股池並輸出回測績效）。

【檔案】
  signal_history.json   兩個股池的狀態（rule = p1）。舊版（h2）強勢股池格式不相容：偵測到會忽略並提示，請跑一次 history_backfill 初始化。
  stock_states.json     當日已通知的名單（A 進場 / B 確認 / B 停損 / 被外盤比擋下），重啟不會重發；換日自動重置
  stock_bases.json      歷史基底快取（baseline.py 每晚產生：昨收、MA 合計、近 20 日高、20 日均量、KD 起算值…）
  volume_baseline.json  同期量基準（baseline.py 每晚產生）
  pool_report.csv       盤後雙股池明細（每檔一列）；backtest_trades.csv 回測逐筆交易
  breakout_watch.json   v66 起不再使用（A4 觀察池已取消），可刪除
【環境變數】TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS（憑證 fubon.p12 由 workflow 還原；可用 FUBON_CERT_PATH 改路徑）
            DEBUG_CODES=2330,2317（可選；盤中每輪印出這幾檔的判斷數字）
"""
import os

import re

import csv

import sys

import json

import time

import html

import bisect

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

BASELINE_FILE = BASE_DIR / "volume_baseline.json"

BASES_FILE = BASE_DIR / "stock_bases.json"

IC_CACHE_FILE = BASE_DIR / "ic_sector_cache.json"

BASELINE_RULE = "v50"               # baseline.py 與 radar.py 必須一致

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

FUBON_CERT_PATH = os.environ.get("FUBON_CERT_PATH", "fubon.p12")

DEBUG_STOCK_CODES = {c.strip() for c in os.environ.get("DEBUG_CODES", "").split(",") if c.strip()}

MIN_VOL5_LOTS = 1000                # C1：5 日均量（張）

# ---- 防雜訊（早盤集合競價量一次進來、基準量太小時，同期比較會失真）----
EVAL_START_MINUTE = 5              # 開盤後第 5 分鐘（09:05）才開始判斷，避開集合競價搓合量釋放的混亂期

MIN_BASELINE_CUM = 50               # 同時間 5 日均量低於 50 張就不判斷

SCALE_CAP = 15.0                    # 預估成交額的放大倍數上限（全日量 / 同時間量）

# ---- 二次確認（Double-Confirmation）----
CONFIRM_MIN_GAP_SECONDS = 10        # 兩次符合至少相隔幾秒（避免同一份快照被重複計成兩次）

CONFIRM_MAX_GAP_SECONDS = 45        # 超過這麼久才再次符合，就不算「下一次掃描」，重新當成首次

# ---- 輪詢 ----
POLL_INTERVAL = 15                  # 秒；富邦快照上限 300 次/分，每輪只打 2 次（上市、上櫃）

FAIL_ALERT_AFTER = 3                # 連續失敗幾輪發 Telegram 警告並重新登入

FAIL_BACKOFF_MAX = 60

# ---- 時間 ----
MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE = 9, 0

SIGNAL_END_MINUTES = 13 * 60 + 30   # 13:30 以後不再發盤中訊號

CLOSE_CONFIRM_HOUR, CLOSE_CONFIRM_MINUTE = 13, 35

OPEN_TO_CLOSE_MINUTES = 271         # 09:00 ~ 13:30（含 13:30 收盤集合競價那一根）

# 同期量曲線的取樣點：開盤後經過的分鐘數 m，值 = 第 m 分鐘開始前的累積量（張）
# 0~15 每分鐘一點（開盤量集中）、之後每 5 分鐘一點、最後 271 = 全日量；點與點之間線性內插
MARKS = sorted(set(list(range(0, 16)) + list(range(20, 271, 5)) + [271]))

# ---- 官方歷史資料 ----
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

HIGH_FALLBACK = {"rows": 0}         # 舊欄位（v67 起日 K 一定有最高價，恆為 0，保留是為了 stock_bases.json 格式相容）

ALERT_QUEUE = []

# =========================
# v67 策略參數（全部集中在這裡）
# =========================
RADAR_VERSION = "v67"               # 顯示在啟動訊息與 log；改版時只要改這一行
BASES_VERSION = 3                   # stock_bases.json 的欄位版本；舊快取（沒有近 20 日高 / KD / 20 日均量，或來自官方資料）會被拒用
POOL_FILE = BASE_DIR / "signal_history.json"
POOL_RULE = "p1"
POOL_CSV_FILE = BASE_DIR / "pool_report.csv"
BACKTEST_CSV_FILE = BASE_DIR / "backtest_trades.csv"
CLOSED_KEEP = 300                   # 股池檔案裡保留最近幾筆出場紀錄
DAYS_KEEP = 90                      # 股池檔案裡保留最近幾個交易日的日期清單

# ---- 富邦歷史日 K（歷史基底 / 回測）----
LOOKBACK_CALENDAR_DAYS = 120        # 歷史基底：往前抓幾個日曆天的日 K（約 80 個交易日，夠算 20 日高 / 20 日均量 / KD 暖機）
DAILY_FIELDS = "open,high,low,close,volume,turnover"
DAILY_CHUNK_DAYS = 330              # 富邦限制：單次查詢區間須小於 1 年，超過就自動分段
DAILY_SHARES_PER_LOT = 1000.0       # 整股日 K 的 volume 單位是「股」，除以 1000 = 張（啟動時會與快照自動校驗）
PREFILTER_RATIO = 0.4               # 預篩：快照全日量 ≥ 5 日均量門檻 × 此比例，才去抓日 K（省 API 呼叫，約少抓一半）
PREFILTER_MIN_UNIVERSE = 150        # 預篩後少於這個檔數（例如盤前快照量為 0）→ 改抓全部股票
FETCH_MIN_COVERAGE = 0.6            # 日 K 成功抓到的檔數 / 候選檔數 低於此值 → 視為失敗（市場別也用同一標準標記資料不完整）
MAX_BACKTEST_DAYS = 250

# ---- 股池 A：右側突破 ----
A_ENABLED = True
A_HIGH_DAYS = 20                    # 突破「近 N 日最高價」
A_VOL_RATIO = 1.5                   # 累積量 ≥ 5 日同期均量 × 1.5
A_OUTER_MIN = 0.55                  # 外盤成交比率下限
A_MAX_CHG_PCT = 9.9                 # 漲幅上限（鎖漲停買不到，不發訊號）
A_MIN_EST_TURNOVER = 3_000_000_000  # 預估成交額下限（元）；流動性底線
VWAP_FILTER = True                  # 現價必須 > 當日成交均價
VWAP_MIN_MARGIN_PCT = 0.0           # 現價須高於 VWAP 至少幾 %
FLOW_FILTER = True                  # 外盤比過濾（查不到資料時放行並註明，不會把訊號全擋掉）
FLOW_MIN_LOTS = 200                 # 內外盤合計低於這個張數 → 樣本太少，放行並註明

# ---- 股池 B：左側低接 ----
B_ENABLED = True
KD_PERIOD = 9
KD_MIN_DAYS = 30                    # KD 要有至少這麼多天的歷史才可信
B_KD_LOW = 40.0                     # 低檔：K < 40
B_VOL_RATIO = 2.0                   # 低點爆量：成交量 ≥ 20 日均量（月均量）× 2
B_AVG_DAYS = 20
B_SHADOW_BODY_RATIO = 0.5           # 長下影線：下影線 > 實體 × 0.5
B_SHADOW_RANGE_MIN = 0.30           # 且下影線 ≥ 振幅的 30%（避免十字星「實體≈0」自動過關）
B_MIN_RANGE_PCT = 2.0               # 且振幅 ≥ 2%
B_LONG_RED_PCT = 3.0                # 帶量長紅 K：收紅且實體 ≥ 3%
B_MIN_TURNOVER = 100_000_000        # 爆量日成交額下限（元）
B_CONFIRM_DAYS = 2                  # 爆量日後最多幾個交易日內確認
B_CONFIRM_START_MINUTE = 60         # 確認從開盤後第 60 分鐘（10:00）起判斷：預估全日量在盤初誤差太大
B_SHRINK_RATIO = 0.6                # 量縮：預估全日量 ≤ 爆量日量 × 0.6
B_STOP_VOL_RATIO = 1.0              # 「帶量」跌破：累積量 ≥ 5 日同期均量 × 1.0
B_MAX_HOLD_DAYS = 10                # 切入後最長持有交易日

# ---- 回測 ----
BACKTEST_DAYS = 120                 # 預設回測交易日數
BACKTEST_WARMUP_DAYS = 40           # 額外抓的暖機天數（20 日高、20 日均量、KD）
ROUND_TRIP_COST_PCT = 0.585         # 來回交易成本（手續費 0.1425%×2 + 證交稅 0.3%），回測的「扣成本」報酬用
A_ORDER = ("chg", "brk", "align", "vwap", "vol", "turn")   # A 條件的檢查順序（漏斗用）

PENDING = {}                        # 二次確認待確認池（只存在記憶體）：A 突破
PENDING_BC = {}                     # B 確認
PENDING_BS = {}                     # B 停損
STATE = {"date": "", "triggered": {}, "signals": [], "b_confirm": {}, "b_stop": {}, "flow_block": {}, "regime": {}}


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

def _norm(text) -> str:
    text = re.sub(r"</?br\s*/?>", "", str(text), flags=re.I)
    text = re.sub(r"\s", "", text)
    return text.replace("（", "(").replace("）", ")")

def curve_at(curve, x: float) -> float:
    """同期量曲線在「開盤後第 x 分鐘」的累積量（x 可為小數）。curve 與 MARKS 一一對應。"""
    if x <= MARKS[0]:
        return curve[0]
    if x >= MARKS[-1]:
        return curve[-1]
    i = bisect.bisect_right(MARKS, x) - 1
    m1, m2 = MARKS[i], MARKS[i + 1]
    return curve[i] + (curve[i + 1] - curve[i]) * (x - m1) / (m2 - m1)

def minutes_since_open(t: datetime) -> float:
    return (t - _market_open_today(t)).total_seconds() / 60.0

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
        self.taiex_symbol = None
        self._candle_fields_ok = True
        self._flow_block_until = 0.0
        self.flow_stats = {"ok": 0, "fail": 0, "blocked": 0, "nofield": 0}
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
            self.taiex_symbol = None
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

    # ---- 加權指數 ----
    def _find_taiex_symbol(self):
        try:
            idx = self.stock.intraday.tickers(type="INDEX")
            rows = idx.get("data", []) if isinstance(idx, dict) else []
            for x in rows:
                if str(x.get("name", "")) == "發行量加權股價指數":
                    return x.get("symbol")
        except Exception as exc:
            print(f"[富邦] 查詢指數清單失敗：{type(exc).__name__}: {exc}", flush=True)
        return "IX0001"

    def taiex_price(self):
        """加權股價指數（不是報酬指數）。失敗回傳 None。"""
        try:
            with self.lock:
                if self.taiex_symbol is None:
                    self.taiex_symbol = self._find_taiex_symbol()
                q = self.stock.intraday.quote(symbol=self.taiex_symbol)
            return _f(q.get("closePrice")) or _f(q.get("lastPrice"))
        except Exception as exc:
            print(f"[富邦] 加權指數查詢失敗：{type(exc).__name__}: {exc}", flush=True)
            return None

    # ---- 內外盤（外盤成交比率用）----
    def quote_flow(self, symbol: str):
        """intraday.quote 的內外盤累計量（張）。回傳 {"bid": 內盤, "ask": 外盤, "avg": 成交均價} 或 None。
        None = 查詢失敗 / 欄位不存在 / 暫停中（呼叫端當「未知」放行，不擋訊號）。遇到 429 會暫停查詢 30 秒。"""
        if time.time() < self._flow_block_until:
            self.flow_stats["blocked"] += 1
            return None
        try:
            with self.lock:
                q = self.stock.intraday.quote(symbol=symbol)
        except FugleAPIError as exc:
            self.flow_stats["fail"] += 1
            if getattr(exc, "status_code", None) == 429:
                self._flow_block_until = time.time() + 30
                print("[外盤比] 被限速（429），30 秒內略過外盤過濾", flush=True)
            return None
        except Exception as exc:
            self.flow_stats["fail"] += 1
            print(f"[外盤比] {symbol} 查詢失敗：{type(exc).__name__}: {exc}", flush=True)
            return None
        total = q.get("total") if isinstance(q, dict) else None
        bid = _f(total.get("tradeVolumeAtBid")) if isinstance(total, dict) else None
        ask = _f(total.get("tradeVolumeAtAsk")) if isinstance(total, dict) else None
        if bid is None or ask is None:
            self.flow_stats["nofield"] += 1
            return None
        self.flow_stats["ok"] += 1
        return {"bid": bid, "ask": ask, "avg": _f(q.get("avgPrice"))}

    def probe_flow(self, symbol: str = "2330"):
        """啟動時探測 intraday.quote 有沒有內外盤欄位。回傳 (是否可用, 說明)。"""
        try:
            with self.lock:
                q = self.stock.intraday.quote(symbol=symbol)
        except Exception as exc:
            return False, f"查詢失敗（{type(exc).__name__}）"
        total = q.get("total") if isinstance(q, dict) else None
        if isinstance(total, dict) and "tradeVolumeAtBid" in total and "tradeVolumeAtAsk" in total:
            return True, "可用"
        return False, "回傳資料沒有內外盤欄位"

    # ---- 日 K（歷史基底 / 回測 / 大盤環境）----
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

    def index_closes(self, end: date_cls, calendar_days: int = 135) -> list:
        """加權股價指數日 K 收盤（富邦 historical.candles 的指數日 K）。回傳 [(日期, 收盤)] 由舊到新；失敗回傳 []。"""
        key = (end.isoformat(), calendar_days)
        if key in self._index_cache:
            return self._index_cache[key]
        try:
            with self.lock:
                if self.taiex_symbol is None:
                    self.taiex_symbol = self._find_taiex_symbol()
            rows = self._daily_chunk(self.taiex_symbol, end - timedelta(days=calendar_days), end, "close", adjusted=False)
        except Exception as exc:
            print(f"[大盤] 加權指數日K查詢失敗：{type(exc).__name__}: {exc}", flush=True)
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
# 同期量基準（baseline.py 每晚產生）
# =========================
def load_baseline(stock_bases: dict):
    """回傳 ({代號: {"avg": [...], "yest": [...]}}, 說明文字)。基準日對不上就整份不用（避免用到舊資料）。"""
    if not BASELINE_FILE.exists():
        return {}, "⚠️ 找不到 volume_baseline.json：盤中 量能條件無法判斷，本次不發盤中訊號（請先執行 baseline 工作流程）"
    try:
        data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"⚠️ volume_baseline.json 讀取失敗（{type(exc).__name__}），本次不發盤中訊號"
    if data.get("rule") != BASELINE_RULE or data.get("marks") != MARKS:
        return {}, f"⚠️ 同期量基準的規則版本不符（{data.get('rule')}），本次不發盤中訊號，請重跑 baseline"
    ref = max(b["asof_date"] for b in stock_bases.values()).isoformat()
    if str(data.get("asof")) != ref:
        return {}, f"⚠️ 同期量基準日 {data.get('asof')} 與官方資料基準日 {ref} 不同，本次不發盤中訊號，請重跑 baseline"
    out = {}
    for code, item in (data.get("stocks") or {}).items():
        base = stock_bases.get(code)
        if base is None or not base["liquid"]:
            continue
        if len(item.get("avg", [])) == len(MARKS) and len(item.get("yest", [])) == len(MARKS):
            out[code] = {"avg": item["avg"], "yest": item["yest"]}
    liquid = sum(1 for b in stock_bases.values() if b["liquid"])
    return out, f"同期量基準：基準日 {ref}，涵蓋 {len(out)}/{liquid} 檔"

# =========================
# 歷史基底快取（baseline.py 每晚存，雷達開盤直接讀）
# =========================
def save_bases_cache(bases: dict) -> None:
    """把 5 日均量達標的股票基底存成 stock_bases.json（日期轉成字串）。"""
    asof = max(b["asof_date"] for b in bases.values()).isoformat()
    stocks = {}
    for code, b in bases.items():
        item = dict(b)
        item["asof_date"] = b["asof_date"].isoformat()
        stocks[code] = item
    payload = {"rule": BASELINE_RULE, "bases_ver": BASES_VERSION, "asof": asof, "generated_at": now_tw().isoformat(timespec="seconds"),
               "high_fallback": HIGH_FALLBACK["rows"], "stocks": stocks}
    tmp = BASES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(BASES_FILE)
    print(f"[基底快取] 已存 {len(stocks)} 檔，基準日 {asof}，{BASES_FILE.stat().st_size / 1024:.0f} KB", flush=True)

def load_bases_cache(feed, today: date_cls):
    """回傳 (bases 或 None, 說明)。基準日必須等於「今天以前最近一個交易日」，否則不用（避免用到過期資料）。
    最近交易日用富邦 2330 日 K 判斷（會處理連假）；富邦查詢失敗時，退而求其次要求基準日在 5 天內。"""
    if not BASES_FILE.exists():
        return None, "找不到 stock_bases.json"
    try:
        data = json.loads(BASES_FILE.read_text(encoding="utf-8"))
        if data.get("rule") != BASELINE_RULE:
            return None, f"基底快取規則版本不符（{data.get('rule')}）"
        asof = date_cls.fromisoformat(str(data["asof"]))
        stocks = data["stocks"]
        if int(data.get("bases_ver", 0) or 0) < BASES_VERSION:
            return None, f"基底快取版本過舊（v67 起改用富邦日 K，並新增近 {A_HIGH_DAYS} 日高 / KD / {B_AVG_DAYS} 日均量），需要重跑 baseline"
    except Exception as exc:
        return None, f"基底快取讀取失敗（{type(exc).__name__}）"
    expected = None
    try:
        days = trading_days(feed, today - timedelta(days=1))
        expected = days[-1] if days else None
    except Exception as exc:
        print(f"[基底快取] 查詢最近交易日失敗（{type(exc).__name__}: {exc}），改用日期差判斷", flush=True)
    if expected is not None:
        if expected != asof.isoformat():
            return None, f"快取基準日 {asof} 不是最近交易日 {expected}"
    elif (today - asof).days > 5:
        return None, f"快取基準日 {asof} 距今超過 5 天"
    bases = {}
    for code, item in stocks.items():
        b = dict(item)
        try:
            b["asof_date"] = date_cls.fromisoformat(str(b["asof_date"]))
        except Exception:
            continue
        try:
            precompute_static(b)                 # 用目前參數重算靜態布林值（舊快取沒有這些欄位也沒關係）
        except Exception:
            continue
        bases[code] = b
    if not bases:
        return None, "基底快取是空的"
    HIGH_FALLBACK["rows"] = int(data.get("high_fallback", 0) or 0)
    return bases, f"夜間基底快取（基準日 {asof}，{len(bases)} 檔）"

# =========================
# 同期量基準的建置（baseline.py 與雷達的「自動補算」共用）
# =========================
BASELINE_CALL_INTERVAL = 1.15        # 每次歷史行情呼叫的最小間隔（秒），富邦限速 60 次/分

BASELINE_MIN_COVERAGE = 0.6          # 成功算出基準的檔數 / 候選檔數 低於此值就視為失敗，不寫檔

BASELINE_MIN_CANDLES_PER_DAY = 30    # 某一天的分 K 少於這個根數，視為該日資料不完整

BASELINE_DAYS = 5

BASELINE_MAX_MINUTE = OPEN_TO_CLOSE_MINUTES - 1      # 270 = 13:30 那一根

def candle_minute(ts: str):
    """'2026-07-09T13:30:00.000+08:00' → ('2026-07-09', 270)；解析失敗回傳 None。"""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(TW_TZ)
    except Exception:
        return None
    return dt.strftime("%Y-%m-%d"), dt.hour * 60 + dt.minute - (MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MINUTE)

def day_curves(candles: list, days: list):
    """把分 K 依日期整理成 {日期: 取樣點上的累積量 list}。某天分 K 不足就不放進結果。"""
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
        cum = [0.0] * (BASELINE_MAX_MINUTE + 2)     # cum[m] = 第 m 分鐘開始前的累積量；cum[271] = 全日量
        for i in range(BASELINE_MAX_MINUTE + 1):
            cum[i + 1] = cum[i] + by_day[d][i]
        out[d] = [cum[m] for m in MARKS]
    return out

def build_entry(curves: dict, days: list):
    """5 天都齊才算。avg = 每個取樣點去掉最大的一天後平均；yest = 最後一天的曲線。"""
    if len(curves) < BASELINE_DAYS or any(d not in curves for d in days):
        return None
    avg = []
    for j in range(len(MARKS)):
        vals = sorted(curves[d][j] for d in days)
        avg.append(round(sum(vals[:-1]) / (len(vals) - 1), 1))
    yest = [round(v, 1) for v in curves[days[-1]]]
    return {"avg": avg, "yest": yest}

def trading_days(feed, asof: date_cls) -> list:
    """用 2330 的日 K 取得最近 5 個交易日（含基準日）。"""
    start = (asof - timedelta(days=21)).isoformat()
    rows = feed.candles("2330", start, asof.isoformat(), timeframe="D", fields=None)
    days = sorted({str(r.get("date"))[:10] for r in rows if str(r.get("date"))[:10] <= asof.isoformat()})
    return days[-BASELINE_DAYS:]

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
        if tv and tv > 0:
            ratios.append(e["yest"][-1] / tv)
    if not ratios:
        return "快照沒有可對照的資料"
    med = statistics.median(ratios)
    near = sum(1 for r in ratios if abs(r - 1) <= 0.02) / len(ratios)
    verdict = "✅ 一致" if abs(med - 1) <= 0.02 and near >= 0.8 else "⚠️ 不一致，請檢查單位或 13:30 收盤集合競價那一根"
    return f"分K加總 / 快照全日量：中位數 {med:.3f}，±2% 內 {near:.0%}（{len(ratios)} 檔）{verdict}"

def build_baseline(stock_bases: dict, feed, source: str = "盤後計算") -> bool:
    """用富邦 1 分 K 建立 volume_baseline.json。成功回傳 True（會寫檔並發 Telegram），失敗回傳 False（會發 Telegram）。
    這是同步、耗時（約 15～20 分鐘）的函式；在雷達裡用 run_in_executor 於背景執行。"""
    started = time.time()
    title = f"【同期量基準】{source}"
    asof = max(b["asof_date"] for b in stock_bases.values())
    universe = sorted(c for c, b in stock_bases.items() if b["liquid"])
    print(f"[基準] 基準日 {asof}，候選 {len(universe)} 檔（5 日均量 ≥ {MIN_VOL5_LOTS:,} 張）", flush=True)
    try:
        days = trading_days(feed, asof)
    except Exception as exc:
        send_telegram(f"❌ <b>{title}</b> 查詢富邦交易日失敗：{type(exc).__name__}: {exc}")
        return False
    if len(days) < BASELINE_DAYS or days[-1] != asof.isoformat():
        send_telegram(f"❌ <b>{title}</b> 富邦日 K 的最後交易日是 {days[-1] if days else '無'}，與官方基準日 {asof} 不符"
                      f"（富邦歷史資料盤後 16:30 才更新，請稍後重跑）。")
        return False
    print(f"[基準] 基準日期：{', '.join(days)}", flush=True)

    entries, skipped, errors = {}, [], 0
    last_call = 0.0
    for n, code in enumerate(universe, 1):
        wait = BASELINE_CALL_INTERVAL - (time.time() - last_call)
        if wait > 0:
            time.sleep(wait)
        last_call = time.time()
        try:
            candles = feed.candles(code, days[0], days[-1], timeframe="1")
        except Exception as exc:
            errors += 1
            skipped.append(code)
            print(f"  {code} 抓取失敗：{type(exc).__name__}: {exc}", flush=True)
            continue
        curves = day_curves(candles, days)
        entry = build_entry(curves, days)
        if n <= 3:
            print(f"  [診斷] {code}：分K {len(candles)} 根，有資料的日期 {sorted(curves)}，"
                  f"基準日全日量 {curves[days[-1]][-1] if days[-1] in curves else '無'}", flush=True)
        if entry is None:
            skipped.append(code)
        else:
            entries[code] = entry
        if n % 50 == 0:
            print(f"  進度 {n}/{len(universe)}，成功 {len(entries)}，略過 {len(skipped)}，已用 {(time.time() - started) / 60:.1f} 分鐘", flush=True)

    coverage = len(entries) / len(universe) if universe else 0
    if coverage < BASELINE_MIN_COVERAGE:
        send_telegram(f"❌ <b>{title}</b> 成功率只有 {coverage:.0%}（{len(entries)}/{len(universe)}，抓取錯誤 {errors}），"
                      f"未更新 volume_baseline.json。請看 Actions log 的診斷行。")
        return False

    check_text = check_against_snapshot(feed, entries, asof.isoformat())
    print(f"[基準] {check_text}", flush=True)
    payload = {
        "rule": BASELINE_RULE,
        "asof": asof.isoformat(),
        "days": days,
        "marks": MARKS,
        "generated_at": now_tw().isoformat(timespec="seconds"),
        "stocks": entries,
    }
    tmp = BASELINE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(BASELINE_FILE)

    minutes = (time.time() - started) / 60
    skipped_txt = f"｜略過 {len(skipped)} 檔（分K不足 5 天或抓取失敗）" if skipped else ""
    send_telegram(
        f"✅ <b>{title}</b> 完成\n基準日 {asof}｜取樣日期 {days[0]} ~ {days[-1]}\n"
        f"涵蓋 {len(entries)}/{len(universe)} 檔{skipped_txt}｜耗時 {minutes:.1f} 分鐘\n{check_text}")
    print(f"[基準] 完成：{len(entries)}/{len(universe)} 檔，耗時 {minutes:.1f} 分鐘，檔案 {BASELINE_FILE.stat().st_size / 1024:.0f} KB", flush=True)
    return True

# =========================
# 指標與判斷（盤中、收盤、回測共用的純函式）
# =========================
def realtime_mas(base: dict, price: float):
    """含今日價格的 MA5/10/20。"""
    return ((base["sum_4"] + price) / 5, (base["sum_9"] + price) / 10, (base["sum_19"] + price) / 20)


def precompute_static(base: dict) -> dict:
    """盤前靜態預算：只用「昨日收盤後就固定」的數字。
    ma5_cut / ma10_cut / ma20_cut = 今日收盤價「等於」該條均線的價位（收盤價低於它 = 收盤跌破該均線）。"""
    base["ma5_cut"] = base["sum_4"] / 4
    base["ma10_cut"] = base["sum_9"] / 9
    base["ma20_cut"] = base["sum_19"] / 19
    return base


def precompute_summary(bases: dict) -> str:
    liquid = [b for b in bases.values() if b.get("liquid")]
    h = sum(1 for b in liquid if b.get("high20"))
    k = sum(1 for b in liquid if b.get("kd_k") is not None)
    v = sum(1 for b in liquid if b.get("avg_20d_vol"))
    return f"盤前預算（{len(liquid)} 檔達標）：近 {A_HIGH_DAYS} 日高 {h} 檔｜KD {k} 檔｜{B_AVG_DAYS} 日均量 {v} 檔"


def kdj_series(highs, lows, closes, n: int = KD_PERIOD):
    """逐日 KD（台灣慣用 9,3,3）：RSV = (收盤 - n 日最低) / (n 日最高 - n 日最低) × 100；
    K = 2/3 × 前一日 K + 1/3 × RSV；D = 2/3 × 前一日 D + 1/3 × K；起始 K = D = 50。回傳 [(K, D), ...]。"""
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


def kd_next(k_prev: float, d_prev: float, hh8: float, ll8: float, high: float, low: float, close: float):
    """今日 KD。hh8 / ll8 = 前 8 日最高 / 最低（含今日 = 9 日區間）。與 kdj_series 的最後一筆完全一致。"""
    hi = max(hh8, high, close)
    lo = min(ll8, low, close)
    rsv = 50.0 if hi == lo else (close - lo) / (hi - lo) * 100.0
    k = 2.0 / 3.0 * k_prev + rsv / 3.0
    d = 2.0 / 3.0 * d_prev + k / 3.0
    return k, d


def candle_shape(o: float, h: float, l: float, c: float) -> dict:
    """K 棒形狀：實體、下影線、振幅。"""
    rng = h - l
    body = abs(c - o)
    lower = min(o, c) - l
    return {"range": rng, "body": body, "lower": lower, "red": c > o,
            "range_pct": (rng / c * 100.0) if c else 0.0,
            "body_pct": (body / o * 100.0) if o else 0.0,
            "lower_vs_range": (lower / rng) if rng > 0 else 0.0}


def b_burst_check(o, h, l, c, vol_lots, value, avg20_lots, k_prev, d_prev, hh8, ll8):
    """股池 B 入池判斷（收盤後、日 K 級別）。符合回傳細節 dict，否則 None。
    ① KD 低檔黃金交叉：昨日 K ≤ D、今日 K > D，且今日 K < B_KD_LOW
    ② 低點爆量：成交量 ≥ 20 日均量 × B_VOL_RATIO（且爆量日成交額 ≥ B_MIN_TURNOVER）
    ③ 長下影線（下影線 > 實體 × 0.5，且 ≥ 振幅 30%、振幅 ≥ 2%）或 帶量長紅 K（收紅、實體 ≥ 3%）"""
    if not (o and h and l and c) or min(o, h, l, c) <= 0 or h < l:
        return None
    if not avg20_lots or avg20_lots <= 0 or not vol_lots or vol_lots <= 0:
        return None
    if k_prev is None or d_prev is None or hh8 is None or ll8 is None:
        return None
    ratio = vol_lots / avg20_lots
    if ratio < B_VOL_RATIO:
        return None
    if value is not None and value < B_MIN_TURNOVER:
        return None
    k, d = kd_next(k_prev, d_prev, hh8, ll8, h, l, c)
    if not (k_prev <= d_prev and k > d and k < B_KD_LOW):
        return None
    s = candle_shape(o, h, l, c)
    long_shadow = (s["lower"] > s["body"] * B_SHADOW_BODY_RATIO and s["lower_vs_range"] >= B_SHADOW_RANGE_MIN
                   and s["range_pct"] >= B_MIN_RANGE_PCT)
    long_red = s["red"] and s["body_pct"] >= B_LONG_RED_PCT
    if not (long_shadow or long_red):
        return None
    trigger = "長下影線＋帶量長紅" if (long_shadow and long_red) else ("長下影線" if long_shadow else "帶量長紅 K")
    return {"k": k, "d": d, "k_prev": k_prev, "d_prev": d_prev, "ratio": ratio, "trigger": trigger,
            "lower_pct_range": s["lower_vs_range"] * 100.0, "range_pct": s["range_pct"], "body_pct": s["body_pct"]}


def a_stage(price, high20, ma5, ma10, ma20, vwap, ratio_avg, chg, turnover):
    """股池 A 進場條件，依固定順序檢查；回傳第一個沒過的關卡名稱（A_ORDER 之一），全過回傳 None。
    chg 漲幅上限｜brk 突破近 20 日高｜align 均線多頭排列｜vwap 現價 > VWAP｜vol 量能 ≥ 5 日同期 1.5x｜turn 預估成交額。"""
    if chg is not None and chg > A_MAX_CHG_PCT:
        return "chg"
    if not high20 or price <= high20:
        return "brk"
    if not (ma5 > ma10 > ma20):
        return "align"
    if VWAP_FILTER and vwap is not None and price <= vwap * (1 + VWAP_MIN_MARGIN_PCT / 100.0):
        return "vwap"
    if ratio_avg is None or ratio_avg < A_VOL_RATIO:
        return "vol"
    if turnover is None or turnover <= A_MIN_EST_TURNOVER:
        return "turn"
    return None


def vwap_of(vol_lots, val_yuan):
    """當日成交均價 = 成交金額（元）/ 成交股數（張 × 1000）。沒有成交量回傳 None。"""
    if not vol_lots or vol_lots <= 0 or not val_yuan or val_yuan <= 0:
        return None
    return val_yuan / (vol_lots * 1000.0)


def b_confirm_stage(day_low, est_full_vol, burst_low, burst_vol):
    """股池 B 隔天確認：未跌破爆量低點、預估全日量 ≤ 爆量日量 × 0.6。回傳沒過的關卡（"broken" / "volume"）或 None。"""
    if day_low is None or day_low < burst_low:
        return "broken"
    if est_full_vol is None or est_full_vol > burst_vol * B_SHRINK_RATIO:
        return "volume"
    return None


def b_stop_hit(price, burst_low, vol, avg_now) -> bool:
    """已切入的 B 池標的：現價跌破爆量低點，且帶量（累積量 ≥ 5 日同期均量 × B_STOP_VOL_RATIO）。"""
    if not price or price >= burst_low:
        return False
    return bool(avg_now and avg_now > 0 and vol is not None and vol >= avg_now * B_STOP_VOL_RATIO)


def judge_flow(flow):
    """外盤比判斷。回傳 (狀態, 外盤比)：ok = 達標；low = 低於門檻（擋下）；thin = 樣本太少（放行）；unknown = 查不到（放行）。"""
    if not flow:
        return "unknown", None
    bid, ask = flow.get("bid"), flow.get("ask")
    if bid is None or ask is None:
        return "unknown", None
    total = bid + ask
    if total < FLOW_MIN_LOTS:
        return "thin", (ask / total if total > 0 else None)
    ratio = ask / total
    return ("ok" if ratio >= A_OUTER_MIN else "low"), ratio


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


def market_regime(closes) -> dict:
    """大盤環境（用富邦加權指數日 K 收盤）。closes = [(日期, 收盤), ...] 由舊到新。
    多頭：收盤 > MA20 且 MA20 比 5 日前高；空頭：收盤 < MA20 且 MA20 比 5 日前低；其餘盤整。"""
    vals = [v for _, v in closes]
    if len(vals) < 26:
        return {"ok": False}
    last = vals[-1]
    ma20 = sum(vals[-20:]) / 20
    ma20_prev = sum(vals[-25:-5]) / 20
    ma60 = sum(vals[-60:]) / 60 if len(vals) >= 60 else None
    up = ma20 > ma20_prev
    if last > ma20 and up:
        regime = "bull"
    elif last < ma20 and not up:
        regime = "bear"
    else:
        regime = "range"
    return {"ok": True, "regime": regime, "close": last, "ma20": ma20, "ma60": ma60, "ma20_up": up,
            "date": closes[-1][0].isoformat() if hasattr(closes[-1][0], "isoformat") else str(closes[-1][0])}


REGIME_TEXT = {
    "bull": ("🟢", "多頭", "今日偏重 🟢 股池 A（右側追強）；股池 B 只當觀察"),
    "range": ("🟡", "盤整", "A、B 並重；右側突破容易假突破，請嚴選"),
    "bear": ("🔴", "空頭", "今日偏重 🔵 股池 B（防禦 / 低接）；A 訊號請降低部位"),
}


# =========================
# 股池狀態（signal_history.json，rule = p1）
# =========================
def new_pools() -> dict:
    return {"rule": POOL_RULE, "asof": "", "updated": "", "days": [], "a": {}, "b": {}, "closed": []}


def load_pools():
    """回傳 (pools, 說明)。檔案不存在 / 規則版本不符（舊版強勢股池 h2）→ 空股池 + 說明。"""
    if not POOL_FILE.exists():
        return new_pools(), "找不到 signal_history.json，股池從空的開始"
    try:
        data = json.loads(POOL_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return new_pools(), f"signal_history.json 讀取失敗（{type(exc).__name__}），股池從空的開始"
    if data.get("rule") != POOL_RULE:
        return new_pools(), (f"signal_history.json 是舊版格式（{data.get('rule')}），已忽略；"
                             f"請手動執行 history_backfill 初始化雙股池")
    pools = new_pools()
    pools.update({"asof": data.get("asof", ""), "updated": data.get("updated", ""),
                  "days": list(data.get("days") or []), "a": dict(data.get("a") or {}),
                  "b": dict(data.get("b") or {}), "closed": list(data.get("closed") or [])})
    return pools, f"股池 A {len(pools['a'])} 檔｜股池 B {len(pools['b'])} 檔（截至 {pools['asof'] or '—'}）"


def save_pools(pools: dict) -> None:
    pools["rule"] = POOL_RULE
    pools["days"] = sorted(set(pools.get("days") or []))[-DAYS_KEEP:]
    pools["closed"] = (pools.get("closed") or [])[-CLOSED_KEEP:]
    pools["updated"] = now_tw().isoformat(timespec="seconds")
    tmp = POOL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(pools, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(POOL_FILE)


def days_since(pools: dict, date_str: str, *, inclusive: bool) -> int:
    """股池記錄的交易日裡，晚於（inclusive=False）或不早於（inclusive=True）date_str 的天數。"""
    days = pools.get("days") or []
    return len([d for d in days if (d >= date_str if inclusive else d > date_str)])


def b_window_offset(pools: dict, e: dict) -> int:
    """盤中：今天是爆量日後第幾個交易日（今天還沒寫進 days，所以 +1）。"""
    return days_since(pools, e["burst_date"], inclusive=False) + 1


def close_record(pools: dict, pool: str, code: str, e: dict, day: str, reason: str, exit_price=None, extra=None) -> dict:
    """把出場寫進 closed 並回傳該筆紀錄。ret_pct 以進場價計（Watching 沒進場 → None）。"""
    entry = e.get("entry_price")
    ret = ((exit_price / entry - 1) * 100.0) if (exit_price and entry) else None
    rec = {"pool": pool, "code": code, "name": e.get("name", code), "date": day, "reason": reason,
           "entry_date": e.get("entry_date") or e.get("burst_date") or "", "entry_price": entry,
           "exit_price": exit_price, "ret_pct": None if ret is None else round(ret, 2),
           "hold_days": e.get("hold_days")}
    if extra:
        rec.update(extra)
    pools.setdefault("closed", []).append(rec)
    return rec


# =========================
# 強勢股池（signal_history.json）：收盤確認通過 → 入池；收盤跌破 MA10 或突破日低點 → 移出
# =========================
def _mmdd(day: str) -> str:
    return day[5:].replace("-", "/") if day else "-"


# =========================
# 通知文字與 CSV
# =========================
def _mkt_name(mkt: str) -> str:
    return "上市" if mkt == "tse" else "上櫃"


INDUSTRY_MAX_LEN = 18                # 族群文字超過這個長度就截斷加「...」


def _short(text: str, limit: int = INDUSTRY_MAX_LEN) -> str:
    text = text or "未分類"
    return text if len(text) <= limit else text[:limit] + "..."


def _parse_chains(text: str) -> list:
    """把族群文字（"大類>子類、大類>子類"，子類本身也可能含「、」）拆回 [[大類, 子類], ...]。"""
    chains = []
    for piece in str(text or "").split("、"):
        piece = piece.strip()
        if not piece:
            continue
        if ">" in piece:
            top, sub = piece.split(">", 1)
            chains.append([top.strip(), sub.strip()])
        elif chains:
            chains[-1][1] += "、" + piece           # 沒有「>」的片段屬於上一個子類名稱
        else:
            chains.append([piece, ""])
    return chains


def industry_compact(text: str, limit: int = 24) -> str:
    """族群縮短成一行：只有一條產業鏈 → 「大類 > 子類」；同一大類多條 → 只顯示大類；不同大類 → 「大類 / 大類」。"""
    chains = _parse_chains(text)
    if not chains:
        return "未分類"
    tops = list(dict.fromkeys(c[0] for c in chains))
    if len(tops) == 1:
        label = f"{tops[0]} > {chains[0][1]}" if (len(chains) == 1 and chains[0][1]) else tops[0]
    else:
        label = " / ".join(tops[:2])
    return label if len(label) <= limit else label[:limit] + "..."


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


# =========================
# 富邦歷史資料層：日 K → 歷史基底 / 回測（取代原本的 TWSE / TPEx 官方網站資料）
# =========================
UNIVERSE_INFO = {"market": 0, "fetched": 0}      # 最近一次建立基底時：全市場一般股票檔數 / 實際去抓日 K 的檔數（baseline 的 Telegram 訊息用）


def fubon_universe(snap: dict, min_lots: float) -> dict:
    """由富邦全市場快照挑出要抓日 K 的股票：{代號: {name, market, snap_vol}}。
    快照 type=COMMONSTOCK 已排除 ETF / 權證；再用代號格式與名稱關鍵字排除特別股、存託憑證等。
    min_lots：快照全日量（張）低於這個值的不抓（預篩，省 API 呼叫）。"""
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
    """把日 K 的「最後一天全日量」對照快照 tradeVolume（張），確認單位是「股→張」。
    比值落在 0.001 附近（API 其實回傳張）→ 自動乘 1000 校正；其他不一致只警告。快照日期不是 asof 就略過。"""
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


def momentum_base_fields(highs: list, lows: list, closes: list, vols_lots: list, kd_ok: bool = True) -> dict:
    """由「由舊到新」的日資料（截至昨日）算出 v67 需要的基底欄位。資料不足的欄位不會出現（雷達用 .get，缺欄位就略過對應條件）。
    high20 = 近 N 日最高價；avg_20d_vol = 20 日均量（張）；sum_8 = 近 8 日收盤合計（明日 MA10 停利價用）；
    kd_k / kd_d = 昨日收盤後的 KD；hh8 / ll8 = 近 8 日最高 / 最低（今日 9 日區間 = 這 8 日 + 今日）。"""
    out = {}
    if len(highs) >= A_HIGH_DAYS:
        out["high20"] = round(max(highs[-A_HIGH_DAYS:]), 4)
    if len(vols_lots) >= B_AVG_DAYS:
        out["avg_20d_vol"] = round(sum(vols_lots[-B_AVG_DAYS:]) / B_AVG_DAYS, 2)
    if len(closes) >= 8:
        out["sum_8"] = float(sum(closes[-8:]))
    if kd_ok and len(closes) >= KD_MIN_DAYS:
        k, d = kdj_series(highs, lows, closes)[-1]
        out.update({"kd_k": round(k, 2), "kd_d": round(d, 2),
                    "hh8": round(max(highs[-(KD_PERIOD - 1):]), 4), "ll8": round(min(lows[-(KD_PERIOD - 1):]), 4)})
    return out


def make_base(code: str, s: dict, industry: str = ""):
    """由一檔股票的日 K 序列（由舊到新，截至基準日）算出歷史基底。不足 MA_WINDOW 天回傳 None。"""
    if len(s["c"]) < MA_WINDOW:
        return None
    c20 = s["c"][-MA_WINDOW:]
    v5 = s["v"][-5:]
    avg5 = sum(v5) / len(v5)
    turnover_5d = sum(c * v * 1000.0 for c, v in zip(s["c"][-5:], v5)) / len(v5)
    base = {
        "code": code, "name": s["name"], "market": s["market"], "industry": industry,
        "sum_3": float(sum(c20[-3:])),      # 昨日起往前 3 日收盤合計（算「明日 MA5 臨界價」用）
        "sum_4": float(sum(c20[-4:])),      # 昨日起往前 4 日收盤合計（今日 MA5 = (sum_4 + 現價) / 5）
        "sum_9": float(sum(c20[-9:])),
        "sum_19": float(sum(c20[-19:])),
        "true_ma5": sum(c20[-5:]) / 5,      # 昨日的 MA5/10/20
        "true_ma10": sum(c20[-10:]) / 10,
        "true_ma20": sum(c20) / 20,
        "asof_date": s["dates"][-1],
        "yest_close": c20[-1],
        "yest_high": s["h"][-1],
        "yest_vol": v5[-1],                 # 昨日全日量（張）
        "avg_5d_vol_simple": avg5,
        "avg_5d_vol": _trimmed_avg_vol(v5),
        "avg_turnover_5d": turnover_5d,
        "liquid": avg5 >= MIN_VOL5_LOTS,
    }
    precompute_static(base)
    base.update(momentum_base_fields(s["h"], s["l"], s["c"], s["v"], True))
    return base


def mark_unreliable(universe: dict, series: dict) -> None:
    """某個市場（上市 / 上櫃）的日 K 成功率太低 → 標記資料不完整，該市場的股票不發訊號。"""
    for mk, label in (("tse", "上市"), ("otc", "上櫃")):
        want = sum(1 for u in universe.values() if u["market"] == mk)
        got = sum(1 for s in series.values() if s["market"] == mk)
        if want >= 20 and got / want < FETCH_MIN_COVERAGE:
            UNRELIABLE_MARKETS.add(mk)
            msg = f"⚠️ <b>【飆股雷達】{label}日K資料不完整</b>\n富邦日K只抓到 {got}/{want} 檔，{label}個股的均線 / 均量可能有誤，本次不發 {label} 的通知。建議稍後重新執行。"
            print(msg.replace("<b>", "").replace("</b>", ""), flush=True)
            send_telegram(msg)


def load_all_stock_bases(feed) -> dict:
    """建立每檔股票的歷史基底（昨收、昨高、均線合計、均量、近 20 日高、KD…）。價格與成交量全部來自富邦：
    ① snapshot.quotes 取得全市場清單並預篩 → ② historical.candles 日 K → ③ intraday.tickers 產業別。
    基準日 = 多數股票最後一根日 K 的日期（盤後 16:30 前富邦還沒更新當天日 K，基準日就是前一個交易日）。"""
    started = time.time()
    today = now_tw().date()
    snap = feed.snapshot_all()
    universe = pick_universe(snap)
    UNIVERSE_INFO.update(market=len(snap["rows"]), fetched=len(universe))
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
    series = {c: s for c, s in series.items() if s["dates"][-1] == asof}        # 最後一天不是基準日（停牌 / 資料落後）的不用
    print(f"[歷史基底] 基準日 {asof}；{normalize_units(feed, series, snap, asof)}", flush=True)
    mark_unreliable(universe, series)
    try:
        ind = feed.industry_map()
    except Exception as exc:
        print(f"[歷史基底] 產業別查詢失敗：{type(exc).__name__}: {exc}", flush=True)
        ind = {}
    bases = {}
    for code, s in series.items():
        b = make_base(code, s, ind.get(code, ""))
        if b:
            bases[code] = b
    tse = sum(1 for b in bases.values() if b["market"] == "tse")
    liquid = sum(1 for b in bases.values() if b["liquid"])
    n_h20 = sum(1 for b in bases.values() if b.get("high20"))
    n_kd = sum(1 for b in bases.values() if b.get("kd_k") is not None)
    print(f"預載完成：{len(bases)} 檔（上市 {tse} / 上櫃 {len(bases) - tse}），5 日均量 ≥ {MIN_VOL5_LOTS:,} 張 {liquid} 檔｜"
          f"近 {A_HIGH_DAYS} 日高 {n_h20} 檔、KD {n_kd} 檔｜日K失敗 {fail} 檔，耗時 {(time.time() - started) / 60:.1f} 分鐘", flush=True)
    return bases


def fetch_backtest_series(feed, days: int):
    """回測 / 初始化用：全市場日 K（暖機 + 評估期）。回傳 (series, all_days, 失敗檔數, universe, 單位校驗文字)。
    all_days = 至少 30% 股票有資料的日期（排除偶發缺漏）。"""
    today = now_tw().date()
    snap = feed.snapshot_all()
    universe = pick_universe(snap)
    need = days + BACKTEST_WARMUP_DAYS
    start = today - timedelta(days=int(need * 7 / 5) + 14)
    print(f"[初始化] 目標 {days} 個交易日 + {BACKTEST_WARMUP_DAYS} 日暖機；富邦日 K {len(universe):,} 檔（{start} ～ {today}）", flush=True)
    series, fail = fetch_series(feed, universe, start, today)
    if not series:
        return {}, [], fail, universe, ""
    counts = {}
    for s in series.values():
        for d in s["dates"]:
            counts[d] = counts.get(d, 0) + 1
    all_days = sorted(d for d, n in counts.items() if n >= 0.3 * len(series))
    unit_note = normalize_units(feed, series, snap, all_days[-1]) if all_days else ""
    return series, all_days, fail, universe, unit_note


async def _prepare_stock_bases(feed, loop):
    """快取不可用時的後備：直接向富邦抓日 K 建立基底（約 10～15 分鐘），再補產業鏈標籤。"""
    stock_bases = await loop.run_in_executor(None, load_all_stock_bases, feed)
    if not stock_bases:
        return {}
    try:
        chain_map = await refresh_ic_chains(sorted(c for c, b in stock_bases.items() if b["liquid"]))
    except Exception as exc:
        print(f"[產業鏈] 查詢流程例外，改用富邦產業別: {type(exc).__name__}: {repr(exc)}", flush=True)
        chain_map = {}
    apply_ic_chains(stock_bases, chain_map)
    return stock_bases


# =========================
# 盤中判斷
# =========================
def evaluate_a(base: dict, bl: dict, row: dict, x: float, funnel: dict):
    """股池 A 進場判斷。符合回傳訊號 dict，否則 None。funnel 累計各關卡通過的檔數（除錯用）。"""
    price = _f(row.get("lastPrice"))
    if not price or price <= 0 or not base.get("high20"):
        return None
    mas = realtime_mas(base, price)
    chg = chg_pct(base, row, price)
    vol = _f(row.get("tradeVolume")) or 0.0
    val = _f(row.get("tradeValue")) or 0.0
    vwap = vwap_of(vol, val)
    avg_now = curve_at(bl["avg"], x)
    ratio = (vol / avg_now) if avg_now >= MIN_BASELINE_CUM else 0.0
    scale = min(SCALE_CAP, bl["avg"][-1] / avg_now) if avg_now > 0 else 0.0
    est_turnover = val * scale
    if base["avg_5d_vol_simple"] < MIN_VOL5_LOTS:
        return None
    stage = a_stage(price, base["high20"], mas[0], mas[1], mas[2], vwap, ratio, chg, est_turnover)
    reached = A_ORDER.index(stage) if stage else len(A_ORDER)
    for s in A_ORDER[:reached]:
        funnel[s] = funnel.get(s, 0) + 1
    if stage:
        return None
    return {
        "pool": "A", "code": base["code"], "name": base["name"], "market": base["market"], "industry": base["industry"],
        "kind": "A", "price": price, "open": _f(row.get("openPrice")), "chg": chg if chg is not None else 0.0,
        "yest_close": base["yest_close"], "yest_high": base["yest_high"], "high20": base["high20"],
        "break_pct": (price / base["high20"] - 1) * 100.0,
        "ma5": mas[0], "ma10": mas[1], "ma20": mas[2], "ma10_cut": base["ma10_cut"],
        "vwap": vwap, "vwap_pct": ((price / vwap - 1) * 100.0) if vwap else None,
        "vol": vol, "avg_now": avg_now, "ratio_avg": ratio, "val": val, "est_turnover": est_turnover, "flow": None,
    }


def evaluate_b_confirm(entry: dict, base: dict, bl: dict, row: dict, x: float):
    """股池 B（Watching）隔天確認。符合回傳訊號 dict，否則 None。10:00 起才判斷（預估全日量在盤初誤差大）。"""
    if x < B_CONFIRM_START_MINUTE:
        return None
    price = _f(row.get("lastPrice"))
    low = _f(row.get("lowPrice"))
    if not price or price <= 0 or low is None:
        return None
    vol = _f(row.get("tradeVolume")) or 0.0
    avg_now = curve_at(bl["avg"], x)
    if avg_now < MIN_BASELINE_CUM:
        return None
    scale = min(SCALE_CAP, bl["avg"][-1] / avg_now)
    est_full = vol * scale
    if b_confirm_stage(low, est_full, entry["burst_low"], entry["burst_vol"]):
        return None
    val = _f(row.get("tradeValue")) or 0.0
    chg = chg_pct(base, row, price)
    return {
        "pool": "B", "kind": "BC", "code": base["code"], "name": base["name"], "market": base["market"],
        "industry": base["industry"], "price": price, "chg": chg if chg is not None else 0.0,
        "open": _f(row.get("openPrice")), "high": _f(row.get("highPrice")), "low": low,
        "burst_low": entry["burst_low"], "burst_date": entry["burst_date"], "burst_vol": entry["burst_vol"],
        "burst_ratio": entry.get("burst_ratio"), "trigger": entry.get("trigger", ""),
        "dist_pct": (price / entry["burst_low"] - 1) * 100.0, "low_dist_pct": (low / entry["burst_low"] - 1) * 100.0,
        "vol": vol, "est_full_vol": est_full, "shrink_ratio": est_full / entry["burst_vol"],
        "ratio_avg": vol / avg_now, "val": val,
        "kd_k": entry.get("kd_k"), "kd_d": entry.get("kd_d"),
        "offset": entry.get("_offset", 1),
    }


def evaluate_b_stop(entry: dict, base: dict, bl: dict, row: dict, x: float):
    """股池 B（Active）停損：跌破爆量低點且帶量。符合回傳訊號 dict，否則 None。"""
    price = _f(row.get("lastPrice"))
    if not price or price <= 0:
        return None
    vol = _f(row.get("tradeVolume")) or 0.0
    avg_now = curve_at(bl["avg"], x) if bl else 0.0
    if avg_now < MIN_BASELINE_CUM or not b_stop_hit(price, entry["burst_low"], vol, avg_now):
        return None
    entry_price = entry.get("entry_price")
    return {
        "pool": "B", "kind": "BS", "code": base["code"], "name": base["name"], "price": price,
        "chg": chg_pct(base, row, price) or 0.0, "burst_low": entry["burst_low"], "burst_date": entry["burst_date"],
        "entry_price": entry_price, "entry_date": entry.get("entry_date"),
        "ret_pct": ((price / entry_price - 1) * 100.0) if entry_price else None,
        "below_pct": (price / entry["burst_low"] - 1) * 100.0, "vol": vol, "ratio_avg": vol / avg_now,
    }


def apply_double_confirm(candidates: list, now: datetime, pending: dict = None) -> list:
    """二次確認：本輪符合條件的股票（candidates）與上一輪的待確認池比對。
    - 上一輪在池中、這一輪又符合，且間隔 CONFIRM_MIN_GAP～CONFIRM_MAX_GAP 秒 → 確認通過，回傳。
    - 首次符合（或間隔過久）→ 放進待確認池，等下一輪。
    - 上一輪在池中、這一輪沒再符合 → 移出（過濾點火後撤單 / 單筆跳動的假訊號）。"""
    pend = PENDING if pending is None else pending
    confirmed, new_pending = [], {}
    for sig in candidates:
        code = sig["code"]
        prev = pend.get(code)
        if prev is not None:
            gap = (now - prev["time"]).total_seconds()
            if gap < CONFIRM_MIN_GAP_SECONDS:            # 太快：同一份資料，不算第二次，維持原本的觀察紀錄
                new_pending[code] = prev
                continue
            if gap <= CONFIRM_MAX_GAP_SECONDS:           # 下一次掃描再次符合 → 確認
                sig["first_time"] = prev["time"].strftime("%H:%M:%S")
                sig["first_price"] = prev["price"]
                confirmed.append(sig)
                continue
        new_pending[code] = {"time": now, "price": sig["price"], "kind": sig.get("kind", "")}
        print(f"[待確認] {code} {sig['name']}（{sig.get('kind', '')}，{sig['price']:.2f}）首次符合，等下一輪再確認", flush=True)
    dropped = [c for c in pend if c not in new_pending and c not in {s['code'] for s in confirmed}]
    for c in dropped:
        print(f"[待確認] {c} 下一輪沒有再符合，移出待確認池（過濾假訊號）", flush=True)
    pend.clear()
    pend.update(new_pending)
    return confirmed


def process_poll(snap: dict, bases: dict, baselines: dict, pools: dict, now: datetime, funnel: dict, flow_fn=None):
    """逐檔判斷，回傳 (A 進場訊號, B 確認訊號, B 停損訊號)，都是「通過二次確認」的新訊號。
    flow_fn(code) → {"bid", "ask"} 或 None：給了才做外盤比過濾（會逐檔查 API，請在執行緒裡跑）。"""
    xs = {mkt: snapshot_minutes(meta, now) for mkt, meta in snap["meta"].items()}
    a_cands, bc_cands, bs_cands = [], [], []
    for code, row in snap["rows"].items():
        base = bases.get(code)
        if base is None or not base["liquid"] or base["market"] in UNRELIABLE_MARKETS:
            continue
        bl = baselines.get(code)
        x = xs.get(row["_mkt"], minutes_since_open(now))
        if code in DEBUG_STOCK_CODES:
            print(debug_line(base, bl, row, x, pools), flush=True)
        if bl is None:
            continue
        b_entry = pools["b"].get(code)
        # ---- B：確認 / 停損 ----
        if B_ENABLED and b_entry:
            if b_entry.get("status") == "Watching" and code not in STATE["b_confirm"]:
                off = b_window_offset(pools, b_entry)
                if 1 <= off <= B_CONFIRM_DAYS:
                    sig = evaluate_b_confirm(dict(b_entry, _offset=off), base, bl, row, x)
                    if sig:
                        bc_cands.append(sig)
            elif b_entry.get("status") == "Active" and code not in STATE["b_stop"]:
                sig = evaluate_b_stop(b_entry, base, bl, row, x)
                if sig:
                    bs_cands.append(sig)
        # ---- A：進場（已在股池 A 或今天已通知過的不再判斷）----
        if not A_ENABLED or code in pools["a"] or code in STATE["triggered"] or x < EVAL_START_MINUTE:
            continue
        funnel["total"] = funnel.get("total", 0) + 1
        sig = evaluate_a(base, bl, row, x, funnel)
        if not sig:
            continue
        if FLOW_FILTER and flow_fn is not None:                  # 籌碼確認：外盤比（只查通過 ①～④ 的候選股）
            fl = flow_fn(code)
            status, ratio = judge_flow(fl)
            sig["flow"] = {"status": status, "ratio": ratio, "bid": (fl or {}).get("bid"), "ask": (fl or {}).get("ask")}
            if status == "low":
                funnel["flow_fail"] = funnel.get("flow_fail", 0) + 1
                STATE.setdefault("flow_block", {})[code] = {"time": now.strftime("%H:%M:%S"), "ratio": ratio}
                print(f"[外盤比] {code} {sig['name']} 外盤比 {ratio * 100:.1f}% < {A_OUTER_MIN * 100:g}%，擋下", flush=True)
                continue
            STATE.get("flow_block", {}).pop(code, None)           # 後來外盤比轉強 → 解除封鎖
            funnel["flow"] = funnel.get("flow", 0) + 1
        a_cands.append(sig)
    funnel["pend"] = funnel.get("pend", 0) + len(a_cands)
    a_conf = apply_double_confirm(a_cands, now, PENDING)
    bc_conf = apply_double_confirm(bc_cands, now, PENDING_BC)
    bs_conf = apply_double_confirm(bs_cands, now, PENDING_BS)
    funnel["conf"] = funnel.get("conf", 0) + len(a_conf)
    funnel["bconf"] = funnel.get("bconf", 0) + len(bc_conf)
    funnel["bstop"] = funnel.get("bstop", 0) + len(bs_conf)
    return a_conf, bc_conf, bs_conf


def debug_line(base: dict, bl, row: dict, x: float, pools: dict) -> str:
    price = _f(row.get("lastPrice"))
    if not price:
        return f"[診斷 {base['code']} {base['name']}] 無成交價"
    mas = realtime_mas(base, price)
    vol = _f(row.get("tradeVolume")) or 0.0
    val = _f(row.get("tradeValue")) or 0.0
    vw = vwap_of(vol, val)
    avg_now = curve_at(bl["avg"], x) if bl else 0.0
    pa, pb = pools["a"].get(base["code"]), pools["b"].get(base["code"])
    return (f"[診斷 {base['code']} {base['name']}] 價 {price}｜20日高 {base.get('high20')}｜突破 {bool(base.get('high20') and price > base['high20'])}"
            f"｜MA5/10/20 {mas[0]:.2f}/{mas[1]:.2f}/{mas[2]:.2f}｜多頭排列 {mas[0] > mas[1] > mas[2]}"
            f"｜VWAP {'無' if vw is None else round(vw, 2)}｜量 {vol:,.0f}／5日同期 {avg_now:,.0f}（{(vol / avg_now) if avg_now else 0:.2f}x）"
            f"｜股池 A {pa['status'] if pa else '無'}｜股池 B {pb['status'] if pb else '無'}")


# =========================
# 股池異動（進場 / 確認 / 轉入）
# =========================
def pool_a_enter(pools: dict, sig: dict, today_str: str, now: datetime) -> None:
    """A 進場通知發出的當下就寫進股池 A（盤中即時存檔；收盤再統一更新）。若這檔原本在股池 B → 轉入 A 池。"""
    code = sig["code"]
    if code in pools["b"]:
        e = pools["b"].pop(code)
        close_record(pools, "B", code, e, today_str, "轉入A池", sig["price"])
    pools["a"][code] = {
        "name": sig["name"], "industry": sig.get("industry", ""), "entry_date": today_str,
        "entry_time": now.strftime("%H:%M:%S"), "entry_price": sig["price"], "break_level": sig["high20"],
        "high_since": sig["price"], "status": "Active", "last_close": None, "last_date": None, "hold_days": 0,
        "miss_days": 0, "src": "live",
    }


def pool_b_confirm(pools: dict, sig: dict, today_str: str, now: datetime) -> None:
    """B 確認切入：Watching → Active（視為切入）。"""
    e = pools["b"].get(sig["code"])
    if not e:
        return
    e.update({"status": "Active", "entry_date": today_str, "entry_time": now.strftime("%H:%M:%S"),
              "entry_price": sig["price"], "hold_days": 0})


# =========================
# 收盤更新與掃描
# =========================
def close_info_from_row(row: dict):
    close = _f(row.get("closePrice")) or _f(row.get("lastPrice"))
    if not close:
        return None
    return {"close": close, "open": _f(row.get("openPrice")), "high": _f(row.get("highPrice")) or close,
            "low": _f(row.get("lowPrice")) or close, "vol": _f(row.get("tradeVolume")) or 0.0,
            "val": _f(row.get("tradeValue")) or 0.0}


def next_ma10_cut(base: dict, close: float):
    """明日收盤價「等於」明日 MA10 的價位（明日收盤跌破它 = 明日收盤跌破 MA10）= (今日收盤 + 前 8 日收盤合計) / 9。"""
    s8 = base.get("sum_8")
    return (close + s8) / 9.0 if s8 else None


def pool_a_close_update(pools: dict, bases: dict, closes: dict, today_str: str):
    """收盤更新股池 A。回傳 (列表列, 今日出場紀錄)。收盤跌破 MA10 → 移出（A池：MA10 跌破停利）。
    股池裡的股票若連續 3 天抓不到資料 → 資料缺失移出。"""
    rows, exits = [], []
    for code, e in list(pools["a"].items()):
        base, c = bases.get(code), closes.get(code)
        if not base or not c:
            e["miss_days"] = e.get("miss_days", 0) + 1
            if e["miss_days"] >= 3:
                pools["a"].pop(code)
                exits.append(close_record(pools, "A", code, e, today_str, "資料缺失移出", e.get("last_close")))
            continue
        e["miss_days"] = 0
        close = c["close"]
        ma5, ma10, ma20 = realtime_mas(base, close)
        e["high_since"] = max(e.get("high_since") or 0.0, c["high"] or close)
        e["hold_days"] = days_since(pools, e["entry_date"], inclusive=True)
        e["last_close"], e["last_date"] = close, today_str
        if base.get("industry"):
            e["industry"] = base["industry"]
        entry = e["entry_price"]
        if close < ma10:
            pools["a"].pop(code)
            ex = close_record(pools, "A", code, e, today_str, "MA10停利", close,
                              {"ma10": round(ma10, 2), "mfe_pct": round((e["high_since"] / entry - 1) * 100.0, 2)})
            exits.append(ex)
            continue
        e["status"] = "Active" if close >= ma5 else "Pullback"
        rows.append({
            "code": code, "name": e.get("name", code), "industry": e.get("industry", ""), "status": e["status"],
            "entry_date": e["entry_date"], "entry_price": entry, "close": close,
            "ret_pct": (close / entry - 1) * 100.0, "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "gap_ma10": (close / ma10 - 1) * 100.0, "cut_today": base["ma10_cut"], "cut_next": next_ma10_cut(base, close),
            "hold_days": e["hold_days"], "break_level": e.get("break_level"),
            "holds_break": (close >= e["break_level"]) if e.get("break_level") else None,
            "high_since": e["high_since"], "mfe_pct": (e["high_since"] / entry - 1) * 100.0,
            "new_today": e["entry_date"] == today_str,
        })
    return rows, exits


def b_row_dict(pools: dict, e: dict, code: str, close: float, ma5: float, ma10: float, ma20: float, today_str: str) -> dict:
    """股池 B 單檔的盤後列表列（新入池與既有標的共用）。"""
    off = days_since(pools, e["burst_date"], inclusive=False)
    entry = e.get("entry_price")
    return {
        "code": code, "name": e.get("name", code), "industry": e.get("industry", ""), "status": e.get("status"),
        "burst_date": e["burst_date"], "burst_low": e["burst_low"], "burst_close": e.get("burst_close"),
        "burst_ratio": e.get("burst_ratio"), "burst_vol": e.get("burst_vol"), "trigger": e.get("trigger", ""),
        "kd_k": e.get("kd_k"), "kd_d": e.get("kd_d"), "close": close, "ma5": ma5, "ma10": ma10, "ma20": ma20,
        "gap_low": (close / e["burst_low"] - 1) * 100.0, "entry_date": e.get("entry_date"), "entry_price": entry,
        "ret_pct": ((close / entry - 1) * 100.0) if entry else None, "hold_days": e.get("hold_days", 0),
        "days_left": max(0, B_CONFIRM_DAYS - off) if e.get("status") == "Watching" else None,
        "new_today": e["burst_date"] == today_str,
    }


def pool_b_close_update(pools: dict, bases: dict, closes: dict, today_str: str):
    """收盤更新股池 B。回傳 (列表列, 今日出場 / 失效紀錄)。
    Watching：盤中最低價跌破爆量低點 → 失效；確認窗到期 → 失效。
    Active：收盤跌破爆量低點 → 停損；收盤站上 MA20 → 反彈達標；持有滿 B_MAX_HOLD_DAYS → 到期。"""
    rows, exits = [], []
    for code, e in list(pools["b"].items()):
        base, c = bases.get(code), closes.get(code)
        if not base or not c:
            e["miss_days"] = e.get("miss_days", 0) + 1
            if e["miss_days"] >= 3:
                pools["b"].pop(code)
                exits.append(close_record(pools, "B", code, e, today_str, "資料缺失移出", e.get("last_close")))
            continue
        e["miss_days"] = 0
        close, low = c["close"], c["low"]
        ma5, ma10, ma20 = realtime_mas(base, close)
        e["last_close"], e["last_date"] = close, today_str
        if base.get("industry"):
            e["industry"] = base["industry"]
        off = days_since(pools, e["burst_date"], inclusive=False)          # 今天已在 days 裡
        if e.get("status") == "Watching":
            if low < e["burst_low"]:
                pools["b"].pop(code)
                exits.append(close_record(pools, "B", code, e, today_str, "失效：跌破爆量低點", close))
                continue
            if off >= B_CONFIRM_DAYS:
                pools["b"].pop(code)
                exits.append(close_record(pools, "B", code, e, today_str, "失效：確認窗到期", close))
                continue
        else:
            e["hold_days"] = days_since(pools, e["entry_date"], inclusive=True)
            notified = code in STATE["b_stop"]
            if close < e["burst_low"]:
                pools["b"].pop(code)
                exits.append(close_record(pools, "B", code, e, today_str, "停損", close,
                                          {"intraday_notified": notified}))
                continue
            if close >= ma20:
                pools["b"].pop(code)
                exits.append(close_record(pools, "B", code, e, today_str, "反彈達標", close))
                continue
            if e["hold_days"] >= B_MAX_HOLD_DAYS:
                pools["b"].pop(code)
                exits.append(close_record(pools, "B", code, e, today_str, "持有期滿", close))
                continue
        rows.append(b_row_dict(pools, e, code, close, ma5, ma10, ma20, today_str))
    return rows, exits


def pool_b_scan(pools: dict, bases: dict, closes: dict, today_str: str) -> list:
    """收盤掃描：KD 低檔黃金交叉 + 低點爆量 + 長下影線 / 帶量長紅 K → 加入股池 B（Watching）。回傳新入池的代號。"""
    new = []
    for code, base in bases.items():
        if code in pools["a"] or code in pools["b"] or not base.get("liquid") or base["market"] in UNRELIABLE_MARKETS:
            continue
        c = closes.get(code)
        if not c or not c.get("open"):
            continue
        hit = b_burst_check(c["open"], c["high"], c["low"], c["close"], c["vol"], c["val"], base.get("avg_20d_vol"),
                            base.get("kd_k"), base.get("kd_d"), base.get("hh8"), base.get("ll8"))
        if not hit:
            continue
        pools["b"][code] = {
            "name": base["name"], "industry": base.get("industry", ""), "status": "Watching", "burst_date": today_str,
            "burst_low": c["low"], "burst_high": c["high"], "burst_open": c["open"], "burst_close": c["close"],
            "burst_vol": c["vol"], "burst_ratio": round(hit["ratio"], 2), "trigger": hit["trigger"],
            "kd_k": round(hit["k"], 2), "kd_d": round(hit["d"], 2), "kd_k_prev": round(hit["k_prev"], 2),
            "kd_d_prev": round(hit["d_prev"], 2), "lower_pct_range": round(hit["lower_pct_range"], 1),
            "range_pct": round(hit["range_pct"], 2), "hold_days": 0, "miss_days": 0, "src": "live",
            "last_close": c["close"], "last_date": today_str,
        }
        new.append(code)
    return new


# =========================
# Telegram 訊息
# =========================
LIST_MAX = 15                        # 盤後股池清單每個股池最多列幾檔
EXIT_LIST_MAX = 15


def _p2(v) -> str:
    return "—" if v is None else f"{v:,.2f}"


def _pc(v, digits: int = 1) -> str:
    return "—" if v is None else f"{v:+.{digits}f}%"


def _md(date_str) -> str:
    return _mmdd(date_str) if date_str else "—"


def flow_lines(flow) -> list:
    """通知裡的外盤比那一行。flow = {"status", "ratio", "bid", "ask"}；None = 本日沒有啟用外盤過濾，不顯示。"""
    if not flow:
        return []
    st, ratio = flow.get("status"), flow.get("ratio")
    detail = ""
    if flow.get("ask") is not None and flow.get("bid") is not None:
        detail = f"（外盤 {flow['ask']:,.0f}／內盤 {flow['bid']:,.0f} 張）"
    if st == "ok":
        return [f"▫️ <b>外盤比：</b>{ratio * 100:.1f}% ≥ {A_OUTER_MIN * 100:g}% ✅{detail}"]
    if st == "thin":
        return [f"▫️ <b>外盤比：</b>樣本不足{detail}，未過濾"]
    return ["▫️ <b>外盤比：</b>查不到內外盤資料，未過濾"]


def regime_warning(regime: dict) -> list:
    if regime and regime.get("ok") and regime.get("regime") == "bear":
        return ["⚠️ <b>大盤偏空</b>（加權 < MA20 且 MA20 下彎）：右側突破容易失敗，請降低部位"]
    return []


def build_a_entry_message(sig: dict, n_today: int, now: datetime) -> str:
    """🟢【A池：強勢突破買進訊號】一則訊息一檔股票。"""
    t_now = now.strftime("%H:%M:%S")
    lines = [f"🟢 <b>【A池：強勢突破買進訊號】</b> <b>{sig['code']} {sig['name']}</b>"]
    lines += regime_warning(STATE.get("regime"))
    lines += [
        f"💡 <b>右側突破：</b>現價突破近 {A_HIGH_DAYS} 日高 {sig['high20']:.2f}（{_pc(sig['break_pct'])}）｜已加入股池 A",
        "━━━━━━━━━━━━━━━",
        f"📈 <b>現價：{sig['price']:.2f} ({_pc(sig['chg'])})</b>",
        f"▫️ <b>開/昨收/昨高：</b>{_p2(sig.get('open'))} / {sig['yest_close']:.2f} / {sig['yest_high']:.2f}",
        "",
        "📊 <b>【進場條件（全部成立）】</b>",
        f"▫️ <b>突破：</b>現價 {sig['price']:.2f} > 近 {A_HIGH_DAYS} 日高 {sig['high20']:.2f} ✅",
        f"▫️ <b>均線：</b>MA5 {sig['ma5']:.2f} > MA10 {sig['ma10']:.2f} > MA20 {sig['ma20']:.2f} 多頭排列 ✅",
    ]
    if sig.get("vwap"):
        lines.append(f"▫️ <b>VWAP：</b>{sig['vwap']:.2f}（現價高於 {_pc(sig['vwap_pct'])}）✅")
    lines.append(f"▫️ <b>量能：</b>5日同期 <b>{sig['ratio_avg']:.2f}x</b>（≥ {A_VOL_RATIO:g}x）｜{sig['vol']:,.0f} 張 ✅")
    lines += flow_lines(sig.get("flow"))
    lines += [
        f"▫️ <b>成交額：</b>已成交 {sig['val'] / 1e8:.2f}億 ｜ 預估全日 <b>{sig['est_turnover'] / 1e8:.2f}億</b>",
        "",
        "✂️ <b>【持股與出場（MA10 移動停利）】</b>",
        f"▫️ <b>MA10 停利線：</b><code>{sig['ma10_cut']:.2f}</code>（今日收盤跌破此價 = 收盤跌破 MA10）",
        f"▫️ <b>目前 MA10：</b>{sig['ma10']:.2f}｜現價距 MA10 {_pc((sig['price'] / sig['ma10'] - 1) * 100.0)}",
        "▫️ <b>規則：</b>收盤穩在 MA10 之上續抱；以收盤為準，盤中不砍",
        "",
        "🏷️ <b>【其他資訊】</b>",
        f"▫️ <b>族群：</b>{_short(sig.get('industry'))}",
        f"▫️ <b>確認：</b>{sig.get('first_time', '-')} ➔ {t_now}",
        f"⏱️ <b>{t_now} ｜ 今日第 {n_today} 則</b>",
    ]
    return "\n".join(lines)


def build_b_confirm_message(sig: dict, n_today: int, now: datetime) -> str:
    """🔵【B池：跌深反彈確認切入訊號】"""
    t_now = now.strftime("%H:%M:%S")
    k_txt = ""
    if sig.get("kd_k") is not None and sig.get("kd_d") is not None:
        k_txt = f"KD 低檔金叉（K {sig['kd_k']:.1f} > D {sig['kd_d']:.1f}）＋ "
    ratio_txt = f"爆量 {sig['burst_ratio']:.1f}x 月均量" if sig.get("burst_ratio") else "爆量"
    stop_dist = (sig["price"] / sig["burst_low"] - 1) * 100.0
    lines = [
        f"🔵 <b>【B池：跌深反彈確認切入訊號】</b> <b>{sig['code']} {sig['name']}</b>",
        f"💡 <b>爆量日 {_md(sig['burst_date'])}：</b>{k_txt}{ratio_txt} ＋ {sig.get('trigger', '')}",
        "━━━━━━━━━━━━━━━",
        f"📈 <b>現價：{sig['price']:.2f} ({_pc(sig['chg'])})</b>｜今日最低 {_p2(sig.get('low'))}",
        "",
        "🛑 <b>【絕對防線】</b>",
        f"▫️ <b>爆量日最低價：</b><code>{sig['burst_low']:.2f}</code>｜現價距防線 {_pc(stop_dist)}",
        f"▫️ <b>未破防線：</b>今日最低 {_p2(sig.get('low'))} ≥ 防線（{_pc(sig['low_dist_pct'])}）✅",
        "",
        "📊 <b>【隔天確認：浮額沉澱】</b>",
        f"▫️ <b>量縮：</b>預估全日量 {sig['est_full_vol']:,.0f} 張 = 爆量日 {sig['burst_vol']:,.0f} 張的 "
        f"<b>{sig['shrink_ratio']:.2f}x</b>（≤ {B_SHRINK_RATIO:g}x）✅",
        f"▫️ <b>確認窗：</b>爆量日後第 {sig['offset']} 天（最多 {B_CONFIRM_DAYS} 天）｜目前累積量 5日同期 {sig['ratio_avg']:.2f}x",
        "",
        "✂️ <b>【風控與停損鐵律】</b>",
        f"▫️ <b>停損：</b>帶量跌破 {sig['burst_low']:.2f} → 反彈失敗，無條件出場（會發【B池：左側防線失守停損通知】）",
        f"▫️ <b>收盤：</b>收盤跌破 {sig['burst_low']:.2f} 一律移出（不管有沒有帶量）",
        f"▫️ <b>出口：</b>收盤站上 MA20 視為反彈達標；最長持有 {B_MAX_HOLD_DAYS} 個交易日；若突破近 {A_HIGH_DAYS} 日高 → 轉入 A 池",
        "▫️ <b>性質：</b>左側逆勢訊號，請自行控管部位",
        "",
        "🏷️ <b>【其他資訊】</b>",
        f"▫️ <b>族群：</b>{_short(sig.get('industry'))}",
        f"▫️ <b>確認：</b>{sig.get('first_time', '-')} ➔ {t_now}",
        f"⏱️ <b>{t_now} ｜ 今日第 {n_today} 則</b>",
    ]
    return "\n".join(lines)


def build_b_stop_message(sig: dict, n_today: int, now: datetime) -> str:
    """🔴【B池：左側防線失守停損通知】"""
    t_now = now.strftime("%H:%M:%S")
    pnl = ""
    if sig.get("entry_price"):
        pnl = f"｜切入 {_md(sig.get('entry_date'))} @ {sig['entry_price']:.2f} → 現價損益 {_pc(sig['ret_pct'])}"
    lines = [
        f"🔴 <b>【B池：左側防線失守停損通知】</b> <b>{sig['code']} {sig['name']}</b>",
        f"💥 <b>帶量跌破爆量低點：</b>現價 {sig['price']:.2f} < 防線 {sig['burst_low']:.2f}（{_pc(sig['below_pct'])}）",
        "━━━━━━━━━━━━━━━",
        f"📉 <b>現價：{sig['price']:.2f} ({_pc(sig['chg'])})</b>{pnl}",
        f"▫️ <b>爆量日：</b>{_md(sig['burst_date'])}｜防線（爆量日最低價）{sig['burst_low']:.2f}",
        f"▫️ <b>帶量：</b>累積量 {sig['vol']:,.0f} 張 = 5日同期 {sig['ratio_avg']:.2f}x（≥ {B_STOP_VOL_RATIO:g}x）",
        "",
        "✂️ <b>【處置】</b>",
        "▫️ <b>反彈失敗：</b>左側防線失守，依鐵律無條件出場（秒砍）",
        "▫️ <b>股池：</b>今日收盤後從股池 B 移出",
        f"⏱️ <b>{t_now} ｜ 今日第 {n_today} 則</b>",
    ]
    return "\n".join(lines)


# ---------- 盤後 ----------
def build_close_summary(day_str: str, market_txt: str, regime: dict, a_rows: list, b_rows: list, ev: dict) -> str:
    """📊 盤後總結：大盤、兩個股池的檔數、今日事件、隔日重點關注（離防線最近）。"""
    lines = [f"📊 <b>【盤後總結】雙股池｜{day_str}</b>", market_txt]
    if regime and regime.get("ok"):
        icon, name, advice = REGIME_TEXT[regime["regime"]]
        lines.append(f"{icon} 大盤環境：<b>{name}</b>（MA20 {'上彎' if regime['ma20_up'] else '下彎'}）→ {advice}")
    n_active = sum(1 for r in a_rows if r["status"] == "Active")
    n_pb = len(a_rows) - n_active
    lines += ["",
              f"🟢 <b>股池 A（強勢波段）：{len(a_rows)} 檔</b>（Active {n_active}｜Pullback {n_pb}）",
              f"   今日：進場 {ev['a_enter']}｜MA10 停利 {ev['a_exit']}"]
    nw = sum(1 for r in b_rows if r["status"] == "Watching")
    na = len(b_rows) - nw
    lines += [f"🔵 <b>股池 B（跌深反彈）：{len(b_rows)} 檔</b>（Watching {nw}｜Active {na}）",
              f"   今日：新入池 {ev['b_new']}｜確認切入 {ev['b_confirm']}｜停損 {ev['b_stop']}｜失效 {ev['b_expire']}｜達標/期滿 {ev['b_done']}"]
    watch = []
    for r in a_rows:
        watch.append((r["gap_ma10"], f"🟢 {r['code']} {r['name']}｜MA10 停利線 {r['ma10']:.2f}（距 {_pc(r['gap_ma10'])}）"))
    for r in b_rows:
        watch.append((r["gap_low"], f"🔵 {r['code']} {r['name']}｜爆量低點 {r['burst_low']:.2f}（距 {_pc(r['gap_low'])}）"))
    watch.sort(key=lambda t: t[0])
    if watch:
        lines += ["", "📌 <b>隔日重點關注（離防線最近的前 5 檔）</b>"] + [t[1] for t in watch[:5]]
    return "\n".join(lines)


def build_a_exit_message(day_str: str, exits: list) -> str:
    """🟢【A池：MA10 跌破停利通知】（有出場才發）"""
    lines = [f"🟢 <b>【A池：MA10 跌破停利通知】</b>｜{day_str}",
             f"今日收盤確定跌破 MA10，{len(exits)} 檔觸發移動停利、已從股池 A 剔除："]
    for ex in exits[:EXIT_LIST_MAX]:
        if ex["reason"] != "MA10停利":
            lines += ["", f"<b>{ex['code']} {ex['name']}</b>｜{ex['reason']}"]
            continue
        lines += ["", f"<b>{ex['code']} {ex['name']}</b>｜收 {_p2(ex['exit_price'])} < MA10 {_p2(ex.get('ma10'))}",
                  f"   進場 {_md(ex['entry_date'])} @ {_p2(ex['entry_price'])} → 損益 {_pc(ex['ret_pct'])}｜持有 {ex.get('hold_days', 0)} 天"
                  f"｜期間最高 {_pc(ex.get('mfe_pct'))}"]
    if len(exits) > EXIT_LIST_MAX:
        lines += ["", f"…另有 {len(exits) - EXIT_LIST_MAX} 檔（完整名單見 pool_report.csv）"]
    return "\n".join(lines)


def build_b_close_message(day_str: str, exits: list) -> str:
    """🔴 B 池收盤處置：停損（收盤失守）、失效、達標、到期。只列有發生的。"""
    stops = [e for e in exits if e["reason"] == "停損"]
    others = [e for e in exits if e["reason"] != "停損"]
    lines = [f"🔴 <b>【B池：左側防線失守停損通知】</b>｜{day_str}" if stops else f"🔵 <b>【B池：今日出場 / 失效】</b>｜{day_str}"]
    if stops:
        lines.append("收盤跌破爆量低點（絕對防線），反彈失敗、已從股池 B 剔除：")
        for ex in stops:
            note = "（盤中已通知）" if ex.get("intraday_notified") else "（收盤確認失守）"
            lines += ["", f"<b>{ex['code']} {ex['name']}</b>｜收 {_p2(ex['exit_price'])} 失守防線{note}",
                      f"   切入 {_md(ex['entry_date'])} @ {_p2(ex['entry_price'])} → 損益 {_pc(ex['ret_pct'])}｜持有 {ex.get('hold_days', 0)} 天"]
    if others:
        lines += ["", "其他出場 / 失效："] if stops else ["今日離開股池 B 的標的："]
        for ex in others[:EXIT_LIST_MAX]:
            pnl = f"｜損益 {_pc(ex['ret_pct'])}" if ex.get("ret_pct") is not None else ""
            lines.append(f"• {ex['code']} {ex['name']}｜{ex['reason']}{pnl}")
    return "\n".join(lines)


def build_pool_a_list(day_str: str, rows: list) -> str:
    lines = [f"🟢 <b>【股池 A】強勢波段池｜{len(rows)} 檔</b>（收盤穩在 MA10 之上續抱）"]
    if not rows:
        return "\n".join(lines + ["目前沒有股票。"])
    for r in sorted(rows, key=lambda r: r["gap_ma10"])[:LIST_MAX]:
        mark = "📉 回檔中" if r["status"] == "Pullback" else "✅ 續抱"
        new = " 🆕" if r["new_today"] else ""
        flag = "" if r.get("holds_break") in (True, None) else "｜⚠️ 收盤未守住突破價"
        lines += ["", f"<b>{r['code']} {r['name']}</b>{new}｜{mark}｜進場 {_md(r['entry_date'])} @ {_p2(r['entry_price'])}"
                      f"｜收 {_p2(r['close'])}（{_pc(r['ret_pct'])}）{flag}",
                  f"   🛡️ MA10 停利線 {_p2(r['ma10'])}（距 {_pc(r['gap_ma10'])}）｜明日跌破價 {_p2(r['cut_next'])}｜持有 {r['hold_days']} 天"]
    if len(rows) > LIST_MAX:
        lines += ["", f"…另有 {len(rows) - LIST_MAX} 檔（完整名單見 pool_report.csv）"]
    return "\n".join(lines)


def build_pool_b_list(day_str: str, rows: list) -> str:
    nw = sum(1 for r in rows if r["status"] == "Watching")
    lines = [f"🔵 <b>【股池 B】跌深反彈池｜{len(rows)} 檔</b>（Watching {nw}｜Active {len(rows) - nw}）"]
    if not rows:
        return "\n".join(lines + ["目前沒有股票。"])
    for r in sorted(rows, key=lambda r: r["gap_low"])[:LIST_MAX]:
        new = " 🆕" if r["new_today"] else ""
        kd = f"KD {r['kd_k']:.1f}/{r['kd_d']:.1f} 金叉" if r.get("kd_k") is not None else ""
        burst = f"爆量 {r['burst_ratio']:.1f}x" if r.get("burst_ratio") else "爆量"
        if r["status"] == "Watching":
            lines += ["", f"<b>{r['code']} {r['name']}</b>{new}｜👀 觀察｜爆量日 {_md(r['burst_date'])} 低點 {_p2(r['burst_low'])}"
                          f"｜收 {_p2(r['close'])}（距防線 {_pc(r['gap_low'])}）",
                      f"   {kd}｜{burst}｜{r.get('trigger', '')}｜確認窗剩 {r['days_left']} 天"]
        else:
            lines += ["", f"<b>{r['code']} {r['name']}</b>｜✅ 已切入 {_md(r['entry_date'])} @ {_p2(r['entry_price'])}（{_pc(r['ret_pct'])}）"
                          f"｜收 {_p2(r['close'])}",
                      f"   🛑 防線 {_p2(r['burst_low'])}（距 {_pc(r['gap_low'])}）｜MA20 目標 {_p2(r['ma20'])}｜持有 {r['hold_days']} 天"]
    if len(rows) > LIST_MAX:
        lines += ["", f"…另有 {len(rows) - LIST_MAX} 檔（完整名單見 pool_report.csv）"]
    return "\n".join(lines)


# ---------- 盤前 ----------
def regime_text(regime: dict) -> list:
    if not regime or not regime.get("ok"):
        return ["🌡️ 大盤環境：資料取得失敗，今日不做 A / B 偏重判斷"]
    icon, name, advice = REGIME_TEXT[regime["regime"]]
    ma60 = f"｜MA60 {regime['ma60']:,.0f}" if regime.get("ma60") else ""
    return [f"🌡️ 大盤環境（{_mmdd(regime['date'])} 收盤）：加權 {regime['close']:,.0f}｜MA20 {regime['ma20']:,.0f}"
            f"（{'上彎' if regime['ma20_up'] else '下彎'}）{ma60}",
            f"{icon} <b>{name}</b> → {advice}"]


def momentum_coverage_text(bases: dict) -> str:
    """動能基底（近 20 日高 / KD / 20 日均量）的覆蓋檔數。"""
    liquid = [b for b in bases.values() if b.get("liquid")]
    n = len(liquid)
    h = sum(1 for b in liquid if b.get("high20"))
    k = sum(1 for b in liquid if b.get("kd_k") is not None)
    v = sum(1 for b in liquid if b.get("avg_20d_vol"))
    if not h:
        return "⚠️ 動能基底尚未建立：A（近 20 日高）與 B（KD / 月均量）暫停，請先跑一次 baseline"
    return f"動能基底：近 {A_HIGH_DAYS} 日高 {h}/{n}｜KD {k}/{n}｜{B_AVG_DAYS} 日均量 {v}/{n} 檔"


def build_startup_message(stock_bases: dict, bases_from_cache: bool, bl_text: str, regime: dict, pools: dict,
                          pool_note: str, flow_note: str = "", notes: list = None) -> str:
    """開盤前啟動訊息：行情與基底 → 市場環境 → 兩個股池現況 → 規則摘要。"""
    asof = max(b["asof_date"] for b in stock_bases.values())
    liquid_n = sum(1 for b in stock_bases.values() if b["liquid"])
    src_note = "" if bases_from_cache else "（快取不可用，已向富邦即時預載）"
    sections = [
        [f"📡 <b>【飆股雷達 {RADAR_VERSION}】雙股池監控啟動（右側突破 × 左側低接）</b>",
         f"資料：全部來自富邦 Neo API（快照每 {POLL_INTERVAL} 秒｜日 K｜1 分 K）",
         f"歷史基底：基準日 {asof}，{len(stock_bases):,} 檔{src_note}",
         f"5 日均量 ≥ {MIN_VOL5_LOTS:,} 張共 {liquid_n:,} 檔", bl_text, momentum_coverage_text(stock_bases)],
        regime_text(regime),
    ]
    n_a, n_pb = len(pools["a"]), sum(1 for e in pools["a"].values() if e.get("status") == "Pullback")
    a_lines = [f"🟢 <b>股池 A（強勢波段，MA10 移動停利）：{n_a} 檔</b>（Active {n_a - n_pb}｜Pullback {n_pb}）"]
    near = []
    for code, e in pools["a"].items():
        b = stock_bases.get(code)
        if b and b.get("ma10_cut") and e.get("last_close"):
            near.append(((e["last_close"] / b["ma10_cut"] - 1) * 100.0, f"{code} {e.get('name', code)} 今日收盤跌破 {b['ma10_cut']:.2f} 即停利"))
    near.sort()
    a_lines += [f"   • {t}" for _, t in near[:4]]
    if len(near) > 4:
        a_lines.append(f"   …另有 {len(near) - 4} 檔")
    nw = sum(1 for e in pools["b"].values() if e.get("status") == "Watching")
    b_lines = [f"🔵 <b>股池 B（跌深反彈，爆量低點防線）：{len(pools['b'])} 檔</b>（Watching {nw}｜Active {len(pools['b']) - nw}）"]
    for code, e in list(pools["b"].items())[:5]:
        if e.get("status") == "Watching":
            left = max(0, B_CONFIRM_DAYS - days_since(pools, e["burst_date"], inclusive=False))
            b_lines.append(f"   • 👀 {code} {e.get('name', code)} 防線 {e['burst_low']:.2f}｜確認窗剩 {left} 天（10:00 起確認）")
        else:
            b_lines.append(f"   • ✅ {code} {e.get('name', code)} 防線 {e['burst_low']:.2f}（帶量跌破即停損）")
    if len(pools["b"]) > 5:
        b_lines.append(f"   …另有 {len(pools['b']) - 5} 檔")
    sections += [a_lines, b_lines]
    if pool_note and ("舊版" in pool_note or "找不到" in pool_note or "失敗" in pool_note):
        sections.append([f"⚠️ {pool_note}"])
    sections.append([
        "📋 <b>規則摘要</b>",
        f"🟢 A 進場：突破 {A_HIGH_DAYS} 日高 ＋ MA5>MA10>MA20 ＋ 現價>VWAP ＋ 量≥5日同期 {A_VOL_RATIO:g}x"
        + (f" ＋ 外盤≥{A_OUTER_MIN * 100:g}%（{flow_note or '啟用中'}）" if FLOW_FILTER else ""),
        "🟢 A 出場：收盤跌破 MA10 → 剔除（盤後通知，盤中不砍）",
        f"🔵 B 入池：KD 低檔（K<{B_KD_LOW:g}）金叉 ＋ 爆量≥{B_VOL_RATIO:g}x 月均量 ＋ 長下影線/帶量長紅（13:35 掃描）",
        f"🔵 B 切入：爆量日後 {B_CONFIRM_DAYS} 日內，未破爆量低點 ＋ 量縮≤{B_SHRINK_RATIO:g}x；跌破低點且帶量 → 停損",
        "判斷時段 09:05～13:30｜二次確認：連續兩輪都符合才通知｜13:35 收盤結算",
    ])
    if notes:
        sections.append(list(notes))
    return "\n\n".join("\n".join(s) for s in sections if s)


# ---------- CSV ----------
POOL_FIELDS = ["區別", "狀態", "代號", "名稱", "族群", "進場日/爆量日", "進場價", "爆量低點(防線)", "今日收盤", "報酬(%)",
               "MA5", "MA10", "MA20", "防線價(A:MA10 / B:爆量低點)", "距防線(%)", "明日MA10停利價", "持有/觀察天數",
               "KD(K/D)", "爆量倍數(月均)", "型態/觸發", "備註"]


def build_pool_rows(a_rows: list, b_rows: list, exits: list) -> list:
    rows = []
    for r in a_rows:
        rows.append({"區別": "A池", "狀態": r["status"], "代號": r["code"], "名稱": r["name"], "族群": r["industry"],
                     "進場日/爆量日": r["entry_date"], "進場價": r["entry_price"], "爆量低點(防線)": "",
                     "今日收盤": r["close"], "報酬(%)": round(r["ret_pct"], 2), "MA5": round(r["ma5"], 2),
                     "MA10": round(r["ma10"], 2), "MA20": round(r["ma20"], 2), "防線價(A:MA10 / B:爆量低點)": round(r["ma10"], 2),
                     "距防線(%)": round(r["gap_ma10"], 2), "明日MA10停利價": "" if r["cut_next"] is None else round(r["cut_next"], 2),
                     "持有/觀察天數": r["hold_days"], "KD(K/D)": "", "爆量倍數(月均)": "", "型態/觸發": "突破近20日高",
                     "備註": ("今日進場" if r["new_today"] else "") + ("" if r.get("holds_break") in (True, None) else "；收盤未守住突破價")})
    for r in b_rows:
        kd = "" if r.get("kd_k") is None else f"{r['kd_k']:.1f}/{r['kd_d']:.1f}"
        rows.append({"區別": "B池", "狀態": r["status"], "代號": r["code"], "名稱": r["name"], "族群": r["industry"],
                     "進場日/爆量日": r.get("entry_date") or r["burst_date"], "進場價": r.get("entry_price") or "",
                     "爆量低點(防線)": r["burst_low"], "今日收盤": r["close"],
                     "報酬(%)": "" if r.get("ret_pct") is None else round(r["ret_pct"], 2), "MA5": round(r["ma5"], 2),
                     "MA10": round(r["ma10"], 2), "MA20": round(r["ma20"], 2), "防線價(A:MA10 / B:爆量低點)": r["burst_low"],
                     "距防線(%)": round(r["gap_low"], 2), "明日MA10停利價": "", "持有/觀察天數": r.get("hold_days") or "",
                     "KD(K/D)": kd, "爆量倍數(月均)": r.get("burst_ratio") or "", "型態/觸發": r.get("trigger", ""),
                     "備註": ("今日新入池" if r["new_today"] else "") + (f"確認窗剩 {r['days_left']} 天" if r.get("days_left") is not None else "")})
    for ex in exits:
        rows.append({"區別": f"今日出場（{ex['pool']}池）", "狀態": ex["reason"], "代號": ex["code"], "名稱": ex["name"], "族群": "",
                     "進場日/爆量日": ex.get("entry_date", ""), "進場價": ex.get("entry_price") or "", "爆量低點(防線)": "",
                     "今日收盤": ex.get("exit_price") or "", "報酬(%)": "" if ex.get("ret_pct") is None else ex["ret_pct"],
                     "MA5": "", "MA10": ex.get("ma10", ""), "MA20": "", "防線價(A:MA10 / B:爆量低點)": "", "距防線(%)": "",
                     "明日MA10停利價": "", "持有/觀察天數": ex.get("hold_days") or "", "KD(K/D)": "", "爆量倍數(月均)": "",
                     "型態/觸發": "", "備註": ""})
    return rows


def export_pool_csv(day_str: str, rows: list) -> pathlib.Path:
    n_a = sum(1 for r in rows if r["區別"] == "A池")
    n_b = sum(1 for r in rows if r["區別"] == "B池")
    n_x = sum(1 for r in rows if r["區別"].startswith("今日出場"))
    title = f"雙股池明細：{day_str}｜A池 {n_a} 檔｜B池 {n_b} 檔｜今日出場 {n_x} 檔｜A：收盤跌破 MA10 停利；B：爆量低點為絕對防線"
    with POOL_CSV_FILE.open("w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerow([title])
        writer = csv.DictWriter(fh, fieldnames=POOL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return POOL_CSV_FILE


# =========================
# 狀態檔（stock_states.json：當日已通知的名單，重啟不重發）
# =========================
def load_states() -> None:
    STATE.update({"date": now_tw().strftime("%Y-%m-%d"), "triggered": {}, "signals": [], "b_confirm": {}, "b_stop": {},
                  "flow_block": {}, "regime": {}})
    if not STATE_FILE.exists():
        return
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if raw.get("_date") != STATE["date"]:
        return
    STATE["triggered"] = dict(raw.get("_triggered") or {})
    STATE["signals"] = list(raw.get("_signals") or [])
    STATE["b_confirm"] = dict(raw.get("_b_confirm") or {})
    STATE["b_stop"] = dict(raw.get("_b_stop") or {})
    STATE["flow_block"] = dict(raw.get("_flow_block") or {})


def save_states() -> None:
    raw = {"_date": STATE["date"], "_triggered": STATE["triggered"], "_signals": STATE["signals"],
           "_b_confirm": STATE["b_confirm"], "_b_stop": STATE["b_stop"], "_flow_block": STATE.get("flow_block", {})}
    STATE_FILE.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


def signal_row(sig: dict, now: datetime, label: str) -> dict:
    """STATE['signals'] 裡的一列：盤中通知紀錄（A 進場 / B 確認 / B 停損）。"""
    return {"時間": now.strftime("%H:%M:%S"), "首次符合時間": sig.get("first_time", ""), "池": sig.get("pool", ""),
            "類型": label, "代號": sig["code"], "名稱": sig["name"], "現價": round(sig["price"], 2),
            "漲幅(%)": round(sig.get("chg") or 0.0, 2)}


def get_regime(feed, today: date_cls, live: bool = False) -> dict:
    """大盤環境。用富邦加權指數日 K 收盤；live=True（收盤時）若今天的日 K 還沒更新（富邦盤後 16:30 才更新），補上即時指數。"""
    try:
        closes = feed.index_closes(today)
        if live and (not closes or closes[-1][0] < today):
            px = feed.taiex_price()
            if px and closes and abs(px / closes[-1][1] - 1) < 0.25:      # 擋掉報酬指數（約 2 倍點數）
                closes = closes + [(today, px)]
        return market_regime(closes)
    except Exception as exc:
        print(f"[大盤環境] 計算失敗：{type(exc).__name__}: {exc}", flush=True)
        return {"ok": False}


def market_status_info(feed, today: date_cls) -> dict:
    """大盤 MA20 狀態（只顯示，不當濾網）。富邦加權指數日 K + 富邦即時指數。"""
    try:
        closes = feed.index_closes(today)
        hist = [v for d, v in closes if d < today]
        today_close = next((v for d, v in closes if d == today), None)
        live = feed.taiex_price()
        if live and hist and abs(live / hist[-1] - 1) < 0.25:     # 擋掉抓到報酬指數（約 2 倍點數）的情況
            today_close = live
        elif live and hist:
            print(f"[大盤] 富邦指數 {live} 與昨收 {hist[-1]} 差距過大，不採用", flush=True)
        if len(hist) >= 19 and today_close:
            ma20 = (sum(hist[-19:]) + today_close) / 20
            return {"ok": True, "close": today_close, "ma20": ma20, "above": today_close > ma20}
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
# 收盤結算（13:35）
# =========================
def close_events(pools: dict, today_str: str, a_exits: list, b_exits: list, new_b: list) -> dict:
    return {
        "a_enter": len(STATE["triggered"]), "a_exit": sum(1 for e in a_exits if e["reason"] == "MA10停利"),
        "b_new": len(new_b), "b_confirm": len(STATE["b_confirm"]),
        "b_stop": sum(1 for e in b_exits if e["reason"] == "停損"),
        "b_expire": sum(1 for e in b_exits if e["reason"].startswith(("失效", "資料缺失"))),
        "b_done": sum(1 for e in b_exits if e["reason"] in ("反彈達標", "持有期滿")),
    }


def settle_close(pools: dict, bases: dict, closes: dict, today_str: str):
    """收盤結算（純資料處理，不碰網路與 Telegram）：更新兩個股池、掃描 B 新入池。
    回傳 (a_rows, b_rows, a_exits, b_exits, new_b)。呼叫端負責存檔與發訊息。"""
    if today_str not in pools["days"]:
        pools["days"].append(today_str)
    a_rows, a_exits = pool_a_close_update(pools, bases, closes, today_str)
    b_rows, b_exits = pool_b_close_update(pools, bases, closes, today_str)
    new_b = pool_b_scan(pools, bases, closes, today_str) if B_ENABLED else []
    for code in new_b:
        e, c = pools["b"][code], closes[code]
        ma5, ma10, ma20 = realtime_mas(bases[code], c["close"])
        b_rows.append(b_row_dict(pools, e, code, c["close"], ma5, ma10, ma20, today_str))
    pools["asof"] = today_str
    return a_rows, b_rows, a_exits, b_exits, new_b


async def run_close(bases: dict, baselines: dict, feed, loop, today: date_cls, pools: dict) -> bool:
    today_str = today.strftime("%Y-%m-%d")
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
    dates = {str(m.get("date", "")).replace("-", "") for m in snap["meta"].values()}
    if today_str.replace("-", "") not in dates:
        print(f"[收盤結算] 快照日期 {dates} 不是今天 {today_str}，視為非交易日，不發盤後報告", flush=True)
        return False
    if pools.get("asof") == today_str:
        print(f"[收盤結算] 股池已在 {today_str} 結算過，不重複處理（避免持有天數被重複計算）", flush=True)
        return False

    closes = {}
    for code, row in snap["rows"].items():
        info = close_info_from_row(row)
        if info:
            closes[code] = info
    a_rows, b_rows, a_exits, b_exits, new_b = settle_close(pools, bases, closes, today_str)
    save_pools(pools)
    ev = close_events(pools, today_str, a_exits, b_exits, new_b)
    print(f"[收盤結算] 股池 A {len(a_rows)} 檔、股池 B {len(b_rows)} 檔｜今日 {ev}", flush=True)

    market_info = await loop.run_in_executor(None, market_status_info, feed, today)
    regime = await loop.run_in_executor(None, get_regime, feed, today, True)
    msgs = [build_close_summary(today_str, market_line(market_info), regime, a_rows, b_rows, ev)]
    if a_exits:
        msgs.append(build_a_exit_message(today_str, a_exits))
    if b_exits:
        msgs.append(build_b_close_message(today_str, b_exits))
    msgs += [build_pool_a_list(today_str, a_rows), build_pool_b_list(today_str, b_rows)]
    for i, m in enumerate(msgs):
        if i:
            await asyncio.sleep(1.0)                       # Telegram 同一聊天室每秒限 1 則
        await loop.run_in_executor(None, send_telegram, m)

    today_closed = [r for r in pools["closed"] if r["date"] == today_str]
    csv_path = export_pool_csv(today_str, build_pool_rows(a_rows, b_rows, today_closed))
    await loop.run_in_executor(
        None, send_telegram_document, csv_path,
        f"🧾 雙股池明細：A池 {len(a_rows)} 檔｜B池 {len(b_rows)} 檔｜今日出場 {len(today_closed)} 檔")
    save_states()
    return True


# =========================
# 初始化腳本 / 歷史回測（python radar.py --backfill [--days 120]）
#   資料：富邦 historical.candles 日 K（原始價格 adjusted=false、volume 股→張、turnover 成交金額）。
#   用同一套規則逐日重播：
#   ① 重建股池 A / B 的目前狀態（寫進 signal_history.json，供隔日盤前與盤中載入）
#   ② 輸出回測績效（Telegram 摘要 + backtest_trades.csv）
#   為什麼不用 yfinance：價格預設是還原權息後的，算出來的 MA / KD 會和看盤軟體不同；而且與盤中快照不是同一個資料源。
#   富邦日 K 與盤中快照同源、同價格基準（原始價格），單次區間須小於 1 年，超過會自動分段。
# =========================
def simulate_stock(code: str, s: dict, eval_from: date_cls):
    """單檔逐日重播（A、B 兩條線互相獨立，只在「同一檔不同時持有」與「B 轉入 A」互相影響）。
    日資料近似（與盤中版的差異）：突破用「收盤價 > 前 20 日最高價」；VWAP 用「收盤價 > 當日成交金額/成交股數」；
    量能用「全日量 ≥ 前 5 日均量（排除最大日）× 1.5」；外盤比沒有資料 → 略過；進場價 = 訊號當天收盤價；
    B 的隔天確認用「當天最低 ≥ 爆量低點、全日量 ≤ 爆量日量 × 0.6」，切入價 = 確認當天收盤價。
    回傳 (trades, open_a, open_b)。"""
    dates, o, h, l, c, v, val = s["dates"], s["o"], s["h"], s["l"], s["c"], s["v"], s["val"]
    n = len(dates)
    trades = []
    pos_a = pos_b = None
    kd = kdj_series(h, l, c) if n >= KD_MIN_DAYS else None
    name = s["name"]

    def trade(pool, e, i, reason, exit_price, **extra):
        i0 = e["i0"]
        hi = max(h[i0 + 1:i + 1]) if i > i0 else c[i0]
        lo = min(l[i0 + 1:i + 1]) if i > i0 else c[i0]
        ret = (exit_price / e["entry_price"] - 1) * 100.0
        t = {"pool": pool, "code": code, "name": name, "entry_date": e["entry_date"], "entry_price": round(e["entry_price"], 2),
             "exit_date": dates[i].isoformat(), "exit_price": round(exit_price, 2), "hold_days": i - i0 + 1,
             "ret_pct": round(ret, 2), "reason": reason, "mfe_pct": round((hi / e["entry_price"] - 1) * 100.0, 2),
             "mae_pct": round((lo / e["entry_price"] - 1) * 100.0, 2)}
        t.update(extra)
        trades.append(t)

    for i in range(20, n):
        d = dates[i]
        ma5 = sum(c[i - 4:i + 1]) / 5
        ma10 = sum(c[i - 9:i + 1]) / 10
        ma20 = sum(c[i - 19:i + 1]) / 20
        # ---- B：Watching 確認 / Active 出場 ----
        if pos_b is not None and d >= eval_from:
            if pos_b["status"] == "Watching":
                off = i - pos_b["burst_i"]
                st = b_confirm_stage(l[i], v[i], pos_b["burst_low"], pos_b["burst_vol"])
                if st == "broken":
                    pos_b = None
                elif st is None:
                    pos_b.update({"status": "Active", "entry_date": d.isoformat(), "entry_price": c[i], "i0": i})
                elif off >= B_CONFIRM_DAYS:
                    pos_b = None
            if pos_b is not None and pos_b["status"] == "Active":
                hold = i - pos_b["i0"] + 1
                if c[i] < pos_b["burst_low"]:
                    trade("B", pos_b, i, "停損", c[i]); pos_b = None
                elif c[i] >= ma20:
                    trade("B", pos_b, i, "反彈達標", c[i]); pos_b = None
                elif hold >= B_MAX_HOLD_DAYS:
                    trade("B", pos_b, i, "持有期滿", c[i]); pos_b = None
        # ---- A：MA10 移動停利 ----
        if pos_a is not None and pos_a["i0"] < i:
            pos_a["high_since"] = max(pos_a["high_since"], h[i])
            if c[i] < ma10:
                trade("A", pos_a, i, "MA10停利", c[i]); pos_a = None
        if d < eval_from:
            continue
        # ---- A：進場 ----
        if A_ENABLED and pos_a is None:
            avg5s = sum(v[i - 5:i]) / 5
            if avg5s >= MIN_VOL5_LOTS:
                vw = vwap_of(v[i], val[i])
                ratio = v[i] / _trimmed_avg_vol(v[i - 5:i]) if v[i - 5:i] else None
                chg = (c[i] / c[i - 1] - 1) * 100.0
                if a_stage(c[i], max(h[i - A_HIGH_DAYS:i]), ma5, ma10, ma20, vw, ratio, chg, val[i]) is None:
                    if pos_b is not None:                       # B 轉入 A
                        if pos_b["status"] == "Active":
                            trade("B", pos_b, i, "轉入A池", c[i])
                        pos_b = None
                    pos_a = {"entry_date": d.isoformat(), "entry_price": c[i], "i0": i, "high_since": h[i],
                             "break_level": max(h[i - A_HIGH_DAYS:i])}
                    if c[i] < ma10:                              # 進場當天就收在 MA10 之下 → 當天停利
                        trade("A", pos_a, i, "MA10停利", c[i]); pos_a = None
        # ---- B：掃描新入池 ----
        if B_ENABLED and pos_a is None and pos_b is None and kd is not None and i >= KD_MIN_DAYS:
            avg20 = sum(v[i - B_AVG_DAYS:i]) / B_AVG_DAYS
            hit = b_burst_check(o[i], h[i], l[i], c[i], v[i], val[i], avg20, kd[i - 1][0], kd[i - 1][1],
                                max(h[i - 8:i]), min(l[i - 8:i]))
            if hit:
                pos_b = {"status": "Watching", "burst_i": i, "burst_date": d.isoformat(), "burst_low": l[i], "burst_high": h[i],
                         "burst_open": o[i], "burst_close": c[i], "burst_vol": v[i], "burst_ratio": round(hit["ratio"], 2),
                         "trigger": hit["trigger"], "kd_k": round(hit["k"], 2), "kd_d": round(hit["d"], 2),
                         "kd_k_prev": round(hit["k_prev"], 2), "kd_d_prev": round(hit["d_prev"], 2)}
    # ---- 期末仍持有的部位 → 股池現況 ----
    open_a = open_b = None
    last = n - 1
    if pos_a is not None:
        ma5 = sum(c[last - 4:last + 1]) / 5
        open_a = {"name": name, "industry": "", "entry_date": pos_a["entry_date"], "entry_time": "", "entry_price": pos_a["entry_price"],
                  "break_level": pos_a["break_level"], "high_since": pos_a["high_since"],
                  "status": "Active" if c[last] >= ma5 else "Pullback", "last_close": c[last], "last_date": dates[last].isoformat(),
                  "hold_days": last - pos_a["i0"] + 1, "miss_days": 0, "src": "init"}
    if pos_b is not None:
        e = {k: pos_b[k] for k in ("status", "burst_date", "burst_low", "burst_high", "burst_open", "burst_close", "burst_vol",
                                   "burst_ratio", "trigger", "kd_k", "kd_d", "kd_k_prev", "kd_d_prev")}
        e.update({"name": name, "industry": "", "hold_days": 0, "miss_days": 0, "src": "init",
                  "last_close": c[last], "last_date": dates[last].isoformat()})
        if pos_b["status"] == "Active":
            e.update({"entry_date": pos_b["entry_date"], "entry_price": pos_b["entry_price"], "hold_days": last - pos_b["i0"] + 1})
        open_b = e
    return trades, open_a, open_b


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return 0.0 if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def summarize_trades(trades: list, cost_pct: float = ROUND_TRIP_COST_PCT) -> dict:
    """績效統計。ret = 逐筆報酬（%），net = 扣來回交易成本後。profit_factor = 總獲利 / 總虧損（>1 才賺）。"""
    n = len(trades)
    if not n:
        return {"n": 0}
    rets = [t["ret_pct"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    gross_w, gross_l = sum(wins), -sum(losses)
    by_reason = {}
    for t in trades:
        by_reason.setdefault(t["reason"], []).append(t["ret_pct"])
    return {
        "n": n, "win_rate": len(wins) / n * 100.0, "avg": sum(rets) / n, "median": _median(rets),
        "avg_net": sum(rets) / n - cost_pct, "avg_win": (gross_w / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "profit_factor": (gross_w / gross_l) if gross_l > 0 else float("inf"),
        "avg_hold": sum(t["hold_days"] for t in trades) / n, "best": max(rets), "worst": min(rets),
        "avg_mfe": sum(t["mfe_pct"] for t in trades) / n, "avg_mae": sum(t["mae_pct"] for t in trades) / n,
        "reasons": {k: (len(v), sum(v) / len(v)) for k, v in sorted(by_reason.items())},
    }


def build_backtest_message(stats: dict, period: str, n_stocks: int, open_a: int, open_b: int, days: int, extra: str = "") -> str:
    def block(title, s):
        if not s.get("n"):
            return [title, "   期間內沒有出場的交易"]
        pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
        lines = [title,
                 f"   {s['n']} 筆｜勝率 {s['win_rate']:.0f}%｜平均 {_pc(s['avg'], 2)}（扣成本 {_pc(s['avg_net'], 2)}）｜中位數 {_pc(s['median'], 2)}",
                 f"   均賺 {_pc(s['avg_win'], 2)}／均賠 {_pc(s['avg_loss'], 2)}｜獲利因子 {pf}｜平均持有 {s['avg_hold']:.1f} 天",
                 f"   最好 {_pc(s['best'], 1)}｜最差 {_pc(s['worst'], 1)}｜平均最大浮盈 {_pc(s['avg_mfe'], 1)}／最大浮虧 {_pc(s['avg_mae'], 1)}"]
        lines.append("   出場原因：" + "、".join(f"{k} {c} 筆（均 {_pc(a, 1)}）" for k, (c, a) in s["reasons"].items()))
        return lines
    lines = [f"🧪 <b>【回測】雙股池｜{period}</b>（{days} 個交易日，掃描 {n_stocks:,} 檔）",
             "資料來源：富邦 historical.candles 日 K（原始價格、未還原）",
             "", *block("🟢 <b>股池 A（突破 20 日高 + 多頭排列 + VWAP + 量能 → 收盤跌破 MA10 出場）</b>", stats["A"]),
             "", *block("🔵 <b>股池 B（KD 低檔金叉 + 爆量 + 下影線/長紅 → 確認切入 → 防線停損）</b>", stats["B"]),
             "", f"📌 期末仍持有：股池 A {open_a} 檔｜股池 B {open_b} 檔（未列入績效，已寫入股池）",
             "",
             "⚠️ <b>回測限制</b>（請當作「策略體質檢查」，不是實盤報酬）",
             "• 日 K 近似：沒有盤中 VWAP 走勢、同期量曲線、外盤比（外盤比略過 → 回測訊號比實盤多）",
             "• 進場 = 訊號當天收盤價、出場 = 觸發當天收盤價；實盤盤中進場會更早，停損可能更差（跳空、無量下殺）",
             f"• 「扣成本」= 來回 {ROUND_TRIP_COST_PCT:g}%（手續費 + 證交稅），未計滑價；價格為未還原權息",
             "• 只包含「目前」流動性達標的股票（已下市或近期量縮的不在內），績效會偏樂觀（存活者偏差）",
             "• 單一期間的結果受當時大盤多空影響很大，請看樣本數與獲利因子，不要只看勝率"]
    if extra:
        lines += ["", extra]
    return "\n".join(lines)


TRADE_FIELDS = ["pool", "code", "name", "entry_date", "entry_price", "exit_date", "exit_price", "hold_days", "ret_pct",
                "reason", "mfe_pct", "mae_pct"]


def export_trades_csv(trades: list, title: str) -> pathlib.Path:
    with BACKTEST_CSV_FILE.open("w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerow([title])
        writer = csv.DictWriter(fh, fieldnames=TRADE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(trades, key=lambda t: (t["exit_date"], t["pool"], t["code"])))
    return BACKTEST_CSV_FILE


def run_backtest(series: dict, all_days: list, days: int):
    """純運算（不碰網路與 Telegram）：重播全市場。series = {代號: 序列}。回傳 (trades, open_a, open_b, n_stocks, eval_days)。"""
    eval_days = all_days[-days:]
    eval_from = eval_days[0]
    trades, open_a, open_b = [], {}, {}
    for code, s in series.items():
        if len(s["dates"]) < 25:
            continue
        t, a, b = simulate_stock(code, s, eval_from)
        trades += t
        if s["dates"][-1] == all_days[-1]:                    # 最後一天沒有資料（已下市 / 停牌）的不放進股池
            if a:
                open_a[code] = a
            if b:
                open_b[code] = b
    return trades, open_a, open_b, len(series), eval_days


def backfill_pools(days: int = BACKTEST_DAYS, feed=None) -> bool:
    """初始化：富邦日 K → 重播 → 寫股池 + 回測報告。成功回傳 True。feed=None 時自己登入富邦。"""
    started = time.time()
    if feed is None:
        feed = FubonFeed()
        try:
            feed.login()
        except Exception as exc:
            send_telegram(f"❌ <b>【股池初始化】</b> 富邦登入失敗：{type(exc).__name__}: {exc}")
            return False
    days = max(30, min(int(days), MAX_BACKTEST_DAYS))
    try:
        series, all_days, fail, universe, unit_note = fetch_backtest_series(feed, days)
    except Exception as exc:
        send_telegram(f"❌ <b>【股池初始化】</b> 向富邦抓日 K 失敗：{type(exc).__name__}: {exc}")
        return False
    if not universe or len(series) / len(universe) < FETCH_MIN_COVERAGE or len(all_days) < 30:
        send_telegram(f"❌ <b>【股池初始化】</b> 富邦日 K 只抓到 {len(series)}/{len(universe)} 檔、{len(all_days)} 個交易日，"
                      f"不足以回測（失敗 {fail} 檔）。請稍後重跑（富邦歷史資料盤後 16:30 更新）。")
        return False
    days = min(days, len(all_days) - 25)
    trades, open_a, open_b, n_stocks, eval_days = run_backtest(series, all_days, days)
    period = f"{eval_days[0]} ～ {eval_days[-1]}"
    stats = {"A": summarize_trades([t for t in trades if t["pool"] == "A"]),
             "B": summarize_trades([t for t in trades if t["pool"] == "B"])}

    pools = new_pools()
    pools["days"] = [d.isoformat() for d in all_days][-DAYS_KEEP:]
    pools["asof"] = all_days[-1].isoformat()
    pools["a"], pools["b"] = open_a, open_b
    try:                                                          # 產業別（富邦 intraday.tickers），失敗不影響
        ind = feed.industry_map()
        for pool in (pools["a"], pools["b"]):
            for code, e in pool.items():
                e["industry"] = ind.get(code, "")
    except Exception as exc:
        print(f"[初始化] 產業別查詢失敗：{type(exc).__name__}: {exc}", flush=True)
    recent = [t for t in trades if t["exit_date"] >= pools["days"][-5]]
    for t in sorted(recent, key=lambda t: t["exit_date"]):
        pools["closed"].append({"pool": t["pool"], "code": t["code"], "name": t["name"], "date": t["exit_date"],
                                "reason": t["reason"], "entry_date": t["entry_date"], "entry_price": t["entry_price"],
                                "exit_price": t["exit_price"], "ret_pct": t["ret_pct"], "hold_days": t["hold_days"]})
    save_pools(pools)

    extra = f"ℹ️ {unit_note}" if unit_note else ""
    if fail:
        extra += f"\n⚠️ 有 {fail} 檔日 K 抓取失敗（已略過）"
    send_telegram(build_backtest_message(stats, period, n_stocks, len(open_a), len(open_b), days, extra.strip()))
    path = export_trades_csv(trades, f"回測逐筆交易：{period}｜A池 {stats['A'].get('n', 0)} 筆｜B池 {stats['B'].get('n', 0)} 筆")
    send_telegram_document(path, f"🧾 回測逐筆交易：{period}（A池 {stats['A'].get('n', 0)} 筆｜B池 {stats['B'].get('n', 0)} 筆）")
    send_telegram(f"✅ <b>【股池初始化】</b> 完成（{(time.time() - started) / 60:.1f} 分鐘）｜股池 A {len(open_a)} 檔、股池 B {len(open_b)} 檔，已寫入 signal_history.json\n"
                  f"資料截至 {pools['asof']}（富邦日 K），明天盤前雷達會自動載入。")
    print(f"[初始化] 完成：交易 {len(trades)} 筆，股池 A {len(open_a)}、B {len(open_b)}，耗時 {time.time() - started:.0f}s", flush=True)
    return True


# =========================
# 主程式
# =========================
async def main_async() -> None:
    print("=" * 55, flush=True)
    print(f"飆股雷達 {RADAR_VERSION}：雙股池（右側突破 × 左側低接）｜資料全部來自富邦 Neo API", flush=True)
    print("=" * 55, flush=True)
    current = now_tw()
    print(f"[程序啟動] PID={os.getpid()}, GITHUB_RUN_ID={os.environ.get('GITHUB_RUN_ID', 'LOCAL')}, "
          f"EVENT={os.environ.get('GITHUB_EVENT_NAME', 'unknown')}, 時間={current.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    if not _is_weekday(current):
        print("週末非交易日，結束", flush=True)
        return

    loop = asyncio.get_running_loop()
    today = current.date()
    today_str = today.strftime("%Y-%m-%d")
    load_states()
    after_close = current >= _close_confirm_today(current)

    feed = FubonFeed()
    try:
        await loop.run_in_executor(None, feed.login)
    except Exception as exc:
        send_telegram(f"❌ <b>【飆股雷達】</b> 富邦登入失敗，監控未啟動：{type(exc).__name__}: {exc}")
        raise

    # 歷史基底：優先讀夜間快取（幾秒內完成，09:00 就能開始判斷）；不可用才即時向官方預載（約 2～3 分鐘）
    stock_bases, bases_source = await loop.run_in_executor(None, load_bases_cache, feed, today)
    bases_from_cache = bool(stock_bases)
    if stock_bases:
        print(f"[歷史基底] 使用{bases_source}", flush=True)
    else:
        print(f"[歷史基底] 快取不可用（{bases_source}），改向富邦即時預載日 K（約 10～15 分鐘）", flush=True)
        stock_bases = await _prepare_stock_bases(feed, loop)
        bases_source = "富邦日 K 即時預載（夜間快取不可用）"
    if not stock_bases:
        send_telegram("❌ <b>【飆股雷達】</b> 無法建立歷史基底，監控未啟動")
        return
    baselines, bl_text = load_baseline(stock_bases)
    print(f"[同期量基準] {bl_text}", flush=True)
    pools, pool_note = load_pools()
    print(f"[股池] {pool_note}", flush=True)
    regime = await loop.run_in_executor(None, get_regime, feed, today)
    STATE["regime"] = regime
    print(f"[大盤環境] {regime}", flush=True)

    if after_close:
        print("已過收盤確認時間：直接執行收盤確認（盤中觸發紀錄取自 stock_states.json）", flush=True)
        await run_close(stock_bases, baselines, feed, loop, today, pools)
        return

    # 同期量基準缺漏或過期（例如前一晚 baseline 失敗）：在背景用富邦 1 分 K 自動補算，完成後自動啟用量能條件
    rebuild_task = None
    if not baselines:
        print("[同期量基準] 缺漏或過期，啟動背景自動補算", flush=True)
        rebuild_task = asyncio.ensure_future(
            loop.run_in_executor(None, build_baseline, stock_bases, feed, "雷達自動補算"))
        bl_text += "\n⏳ 已啟動自動補算（約 15～20 分鐘），完成前不會有盤中訊號，完成後自動啟用"

    print(f"[盤前預算] {precompute_summary(stock_bases)}", flush=True)      # 盤前預算摘要只留在 log，不放 Telegram
    flow_state = {"ok": False, "retried": False, "note": "未啟用"}
    if FLOW_FILTER:
        flow_state["ok"], probe_msg = await loop.run_in_executor(None, feed.probe_flow)
        flow_state["note"] = "已啟用" if flow_state["ok"] else f"盤前探測{probe_msg}，09:02 會再探測一次，仍失敗就略過"
        print(f"[外盤比] 探測：{probe_msg}", flush=True)
    notes = []
    if STATE["triggered"] or STATE["b_confirm"] or STATE["b_stop"]:
        notes.append(f"續跑：今日已通知 A 進場 {len(STATE['triggered'])}、B 確認 {len(STATE['b_confirm'])}、B 停損 {len(STATE['b_stop'])} 檔，不會重複通知")
    startup = build_startup_message(stock_bases, bases_from_cache, bl_text, regime, pools, pool_note,
                                    flow_note=flow_state["note"], notes=notes)
    await loop.run_in_executor(None, send_telegram, startup)

    fail_streak, fail_alerted, fail_started = 0, False, None
    polls = 0
    last_total, stall_polls, stall_alerted, stall_started = None, 0, False, None
    funnel = {"total": 0, "chg": 0, "brk": 0, "align": 0, "vwap": 0, "vol": 0, "turn": 0, "flow": 0, "flow_fail": 0,
              "pend": 0, "conf": 0, "bconf": 0, "bstop": 0}
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
            if current.hour * 60 + current.minute > SIGNAL_END_MINUTES:
                await asyncio.sleep(10)            # 13:30 後不再發訊號，等到 13:35 做收盤確認
                continue

            if rebuild_task is not None and rebuild_task.done():
                task, rebuild_task = rebuild_task, None
                try:
                    built = task.result()
                except Exception as exc:
                    built = False
                    print(f"[同期量基準] 自動補算例外：{type(exc).__name__}: {exc}", flush=True)
                    await loop.run_in_executor(None, send_telegram, f"❌ <b>【飆股雷達】</b> 同期量基準自動補算失敗：{type(exc).__name__}: {exc}")
                if built:
                    new_baselines, new_text = load_baseline(stock_bases)
                    baselines.clear()
                    baselines.update(new_baselines)
                    print(f"[同期量基準] 自動補算完成並載入：{new_text}", flush=True)
                    missed = ""
                    if minutes_since_open(now_tw()) > EVAL_START_MINUTE:
                        missed = f"\n開盤後到現在（約 {minutes_since_open(now_tw()):.0f} 分鐘）的訊號已錯過，請自行對照看盤軟體。"
                    await loop.run_in_executor(
                        None, send_telegram,
                        f"✅ <b>【飆股雷達】</b> 同期量基準已補算完成並啟用，現在開始判斷量能條件。\n{new_text}{missed}")
                else:
                    print("[同期量基準] 自動補算失敗，今日不會有盤中訊號（收盤確認仍會執行）", flush=True)

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
                        await loop.run_in_executor(
                            None, send_telegram,
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
                    await loop.run_in_executor(
                        None, send_telegram,
                        f"✅ <b>【飆股雷達】富邦行情已恢復</b>\n中斷約 {lasted / 60:.1f} 分鐘，這段時間的訊號可能已錯過，請自行對照看盤軟體。")
                fail_streak, fail_alerted, fail_started = 0, False, None
            if snap["errors"]:
                print(f"[富邦] 部分市場失敗：{'；'.join(snap['errors'])}", flush=True)

            # 快照日期不是今天 → 今天休市（開盤 3 分鐘後才檢查，避免開盤前的舊快照誤判）
            dates = {str(m.get("date", "")).replace("-", "") for m in snap["meta"].values() if m.get("date")}
            if dates and today_str.replace("-", "") not in dates and current >= open_dt + timedelta(minutes=3):
                print(f"快照日期 {dates} 不是今天 {today_str}，今日休市，結束", flush=True)
                await loop.run_in_executor(None, send_telegram, f"ℹ️ <b>【飆股雷達】</b> 快照日期不是今天（{today_str}），判定今日休市，監控結束。")
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
                    print(f"[快照] 資料恢復更新（凍結約 {lasted:.0f} 秒）", flush=True)
                    await loop.run_in_executor(
                        None, send_telegram,
                        f"✅ <b>【飆股雷達】快照資料已恢復更新</b>\n凍結約 {lasted / 60:.1f} 分鐘，這段時間的訊號可能已錯過，請自行對照看盤軟體。")
                stall_polls, stall_alerted, stall_started = 0, False, None
            if stall_polls >= STALL_ALERT_POLLS and not stall_alerted:
                stall_alerted = True
                await loop.run_in_executor(
                    None, send_telegram,
                    f"⚠️ <b>【飆股雷達】快照資料疑似沒有更新</b>\n全市場累積量連續 {stall_polls} 輪（約 {stall_polls * POLL_INTERVAL / 60:.1f} 分鐘）完全沒變。\n"
                    f"{summary}\n可能是行情服務異常或休市，請對照看盤軟體；這段期間的訊號不可信。")
            last_total = total_vol

            if FLOW_FILTER and not flow_state["ok"] and not flow_state["retried"] and minutes_since_open(current) >= 2:
                flow_state["retried"] = True                       # 開盤前探測可能拿不到內外盤欄位，開盤後 2 分鐘再試一次（早於 09:05 開始判斷）
                flow_state["ok"], probe_msg = await loop.run_in_executor(None, feed.probe_flow)
                print(f"[外盤比] 開盤後再探測：{probe_msg}", flush=True)
                await loop.run_in_executor(
                    None, send_telegram,
                    "✅ <b>【飆股雷達】</b> 外盤比資料已可用，開始套用外盤過濾" if flow_state["ok"]
                    else f"⚠️ <b>【飆股雷達】</b> 外盤比資料不可用（{probe_msg}），本日 A 池略過外盤過濾，其餘條件照常")
            flow_fn = feed.quote_flow if (FLOW_FILTER and flow_state["ok"]) else None
            a_sigs, bc_sigs, bs_sigs = await loop.run_in_executor(
                None, functools.partial(process_poll, snap, stock_bases, baselines, pools, current, funnel, flow_fn))
            sent = 0
            for sig in sorted(a_sigs, key=lambda s: -s["val"]):
                STATE["triggered"][sig["code"]] = {"time": current.strftime("%H:%M:%S"), "price": sig["price"]}
                STATE["signals"].append(signal_row(sig, current, "A進場"))
                pool_a_enter(pools, sig, today_str, current)
                enqueue_alert(build_a_entry_message(sig, len(STATE["triggered"]), current))
                print(f"觸發 A池進場（二次確認通過）：{sig['code']} {sig['name']}（{sig['price']:.2f}，突破 20 日高 {sig['break_pct']:+.1f}%，量 {sig['ratio_avg']:.2f}x）", flush=True)
                sent += 1
            for sig in sorted(bc_sigs, key=lambda s: -s["val"]):
                STATE["b_confirm"][sig["code"]] = {"time": current.strftime("%H:%M:%S"), "price": sig["price"]}
                STATE["signals"].append(signal_row(sig, current, "B確認切入"))
                pool_b_confirm(pools, sig, today_str, current)
                enqueue_alert(build_b_confirm_message(sig, len(STATE["b_confirm"]), current))
                print(f"觸發 B池確認切入（二次確認通過）：{sig['code']} {sig['name']}（{sig['price']:.2f}，防線 {sig['burst_low']:.2f}，量縮 {sig['shrink_ratio']:.2f}x）", flush=True)
                sent += 1
            for sig in bs_sigs:
                STATE["b_stop"][sig["code"]] = {"time": current.strftime("%H:%M:%S"), "price": sig["price"]}
                STATE["signals"].append(signal_row(sig, current, "B停損"))
                enqueue_alert(build_b_stop_message(sig, len(STATE["b_stop"]), current))
                print(f"觸發 B池停損（二次確認通過）：{sig['code']} {sig['name']}（{sig['price']:.2f} < 防線 {sig['burst_low']:.2f}）", flush=True)
                sent += 1
            if sent:
                save_states()
                save_pools(pools)
            await flush_alerts(loop)

            polls += 1
            elapsed = time.time() - started
            print(f"[{current.strftime('%H:%M:%S')}] 第 {polls} 輪：快照 {len(snap['rows'])} 檔，新訊號 {sent}，今日累計 {len(STATE['signals'])}，耗時 {elapsed:.2f}s"
                  f"｜{summary}（{delta:+,.0f}）", flush=True)
            if polls % 4 == 1:
                print(f"    樣本 {sample_line(snap)}", flush=True)
            if polls % 20 == 0:
                print(f"[漏斗/最近 20 輪累計人次] A 判斷 {funnel['total']} → 漲幅≤{A_MAX_CHG_PCT:g}% {funnel['chg']} → 突破{A_HIGH_DAYS}日高 {funnel['brk']} "
                      f"→ 多頭排列 {funnel['align']} → VWAP {funnel['vwap']} → 量能 {funnel['vol']} → 成交額 {funnel['turn']} "
                      f"→ 外盤比 {funnel['flow']}（擋 {funnel['flow_fail']}）→ 待確認 {funnel['pend']} → 通過 {funnel['conf']}"
                      f"｜B 確認 {funnel['bconf']}｜B 停損 {funnel['bstop']}", flush=True)
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
    await run_close(stock_bases, baselines, feed, loop, today, pools)


def _arg_int(flag: str, default: int) -> int:
    if flag in sys.argv:
        try:
            return int(sys.argv[sys.argv.index(flag) + 1])
        except (ValueError, IndexError):
            print(f"{flag} 後面要接整數，改用預設 {default}", flush=True)
    return default


if __name__ == "__main__":
    if "--backfill" in sys.argv or "--init" in sys.argv:
        sys.exit(0 if backfill_pools(_arg_int("--days", BACKTEST_DAYS)) else 1)
    asyncio.run(main_async())
