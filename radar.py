"""飆股雷達 v54：1～2 週短線波段「A + B + C ＋ 二次確認」進場策略（富邦 Neo API 即時行情）。
【進場條件】09:15～13:30 每 15 秒一輪；同時滿足 A 之一 + B 兩項 + C 全部，且「連續二次掃描都成立」才發 Telegram
            （同一檔同一天只發一次）：
  A（三擇一，滿足任一項即可）
    A1 均線突破首日：昨收 < max(MA5, MA10, MA20)，且現價同時 > MA5、MA10、MA20（MA 含今日現價）。
    A2 強勢跳空：今日開盤 > 昨日最高價 × 1.02。
    A3 均線糾結突破：
         壓縮期  昨日 MA5/MA10/MA20 三者「最高與最低」的差距 ≤ 3%（A3_SQUEEZE_PCT）。
         表態期  現價突破糾結均線群的高點（昨日與今日 MA5/10/20 中最高者）。量能由 B 條件把關。
  B 主力爆量（同期比較法，兩項都要成立）
    B1 目前累積量 ≥ 前 5 日「同時間」累積量平均（每個時間點排除最大的一天）× 1.5
    B2 目前累積量 ≥ 昨日「同時間」累積量
    （同期曲線由 baseline.py 每晚用富邦 1 分 K 計算，存成 volume_baseline.json）
  C 流動性與熱度（全部成立）
    C1 5 日均量 ≥ 1,000 張
    C2 預估成交額 > 6 億（目前成交額 × 全日量 / 同時間量，用同一條同期曲線推算，不用 U 型經驗曲線）
    C3 現價 > 今日開盤（拒絕開高走低）
    C4 漲幅 +2% ~ +6%（用富邦快照的 changePercent，除權息日也正確）
【靜態預算 + 動態即時】
  盤前（baseline.py 盤後存進 stock_bases.json；雷達 08:xx 啟動載入時再用目前參數重算一次）先算好「收盤後就固定」的東西：
    A1 前半段 a1_prev（昨收 < 昨日 MA5/10/20 最高）、A2 觸發價 a2_trigger（昨高 × 1.02）、
    A3 壓縮期 a3_squeezed（昨日 MA5/10/20 差距 ≤ 3%）與糾結群昨日高點 prev_ma_max、MA5 減碼參考價 ma5_cut。
  盤中每 15 秒只做「會跳動的」：現價 vs 今日即時 MA5/10/20、現價 vs 開盤、同期累積量比對，直接讀現成的 True/False。
【實戰防禦】
  ① 09:15 才開始判斷：避開 09:00～09:15 集合競價搓合量釋放的混亂期。
  ② 二次確認（Double-Confirmation）：某檔在某一輪首次符合 A+B+C，先放進「待確認觀察池」，
     下一輪（約 15～30 秒後）必須再次同時符合才發通知；下一輪沒再符合就移出觀察池，過濾點火後撤單的假突破。
【通知】
  盤中：二次確認通過立即通知，並附「MA5 減碼參考價」（= 前 4 日收盤均價；收盤價跌破它，就是收盤跌破 MA5）。
  收盤（13:35）：用最終收盤資料把 A+B+C 重算一次，回報「今日共有 X 檔符合」、大盤 MA20 狀態，
        並列出「盤中觸發但收盤未通過」的股票與原因，另存 close_confirm.csv。
  出場、停損、持倉追蹤都不做，由你自行判斷（第一階段收盤跌破 MA5 減碼一半、第二階段收盤跌破 MA10 清倉）。
【檔案】
  stock_states.json     當日已觸發的股票與訊號紀錄（daily.yml 會 commit 回 repo；換日自動重置）
  volume_baseline.json  同期量基準（baseline.py 每晚產生；缺檔或基準日過期時，雷達啟動後會自動補算）
  stock_bases.json      歷史基底快取（baseline.py 每晚產生：昨收、昨高、MA、均量、族群）。
                        雷達 09:00 啟動時直接讀它，不用再花 2～3 分鐘抓官方資料；基準日不對才改走即時預載
  ic_sector_cache.json  櫃買產業價值鏈快取（actions/cache）
  signals_today.csv     今日盤中訊號
  close_confirm.csv     收盤確認明細
【環境變數】TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS
            （憑證檔 fubon.p12 由 workflow 還原到工作目錄，可用 FUBON_CERT_PATH 改路徑）
            DEBUG_CODES=2330,2317（可選，逗號分隔；盤中每輪印出這幾檔的判斷數字）
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
SIGNAL_CSV_FILE = BASE_DIR / "signals_today.csv"
CLOSE_CSV_FILE = BASE_DIR / "close_confirm.csv"
BASELINE_RULE = "v50"               # baseline.py 與 radar.py 必須一致

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
FUBON_CERT_PATH = os.environ.get("FUBON_CERT_PATH", "fubon.p12")
DEBUG_STOCK_CODES = {c.strip() for c in os.environ.get("DEBUG_CODES", "").split(",") if c.strip()}

# ---- 進場條件 ----
GAP_RATIO = 1.02                    # A2：開盤 > 昨高 × 1.02
A3_SQUEEZE_PCT = 3.0                # A3：昨日 MA5/10/20 最高與最低的差距（%）上限（SOP：2%～3% 以內）
VOL_SURGE = 1.5                     # B1：累積量 ≥ 5 日同期均量 × 1.5
VS_YEST_RATIO = 1.0                 # B2：累積量 ≥ 昨日同時間累積量 × 1.0
MIN_VOL5_LOTS = 1000                # C1：5 日均量（張）
MIN_EST_TURNOVER = 600_000_000      # C2：預估成交額（元）
CHG_MIN, CHG_MAX = 2.0, 6.0         # C4：漲幅區間（%）

# ---- 防雜訊（早盤集合競價量一次進來、基準量太小時，同期比較會失真）----
EVAL_START_MINUTE = 5              # 開盤後第 5 分鐘（09:05）才開始判斷，避開集合競價搓合量釋放的混亂期
MIN_BASELINE_CUM = 50               # 同時間 5 日均量低於 50 張就不判斷
SCALE_CAP = 15.0                    # 預估成交額的放大倍數上限（全日量 / 同時間量）
DAILY_SIGNAL_CAP = 0                # 每日盤中訊號上限（0 = 不限）
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
STALE_DAYS_LIMIT = 10
MA_WINDOW = 20
LOOKBACK_CALENDAR_DAYS = 45
TARGET_TRADING_DAYS = 25
MIN_OFFICIAL_INTERVAL = 2.0
MAX_RETRY_PER_REQUEST = 3
SUSPECTED_BLOCK_THRESHOLD = 10
TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TWSE_FMTQIK_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
TWSE_COMPANY_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_COMPANY_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
OFFICIAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}
TPEX_PAGE_HEADERS = {
    **OFFICIAL_HEADERS,
    "Referer": "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/pricing.html",
    "Origin": "https://www.tpex.org.tw",
}
IC_HEADERS = {
    "User-Agent": OFFICIAL_HEADERS["User-Agent"],
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
HIGH_FALLBACK = {"rows": 0}         # 官方表格沒有「最高價」欄位、改用收盤價代替的筆數
ALERT_QUEUE = []
STATE = {"date": "", "triggered": {}, "signals": []}
PENDING = {}                         # 待確認觀察池：{代號: {"time": datetime, "price": float, "kind": str}}（只存在記憶體）


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


def _normalize_industry_code(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    try:
        return str(int(raw))
    except ValueError:
        return raw


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
def _safe_html(text: str) -> str:
    text = text.replace("<b>", "\x00B\x00").replace("</b>", "\x00/B\x00")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("\x00B\x00", "<b>").replace("\x00/B\x00", "</b>")


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


async def flush_alerts(loop) -> None:
    if not ALERT_QUEUE:
        return
    batch, ALERT_QUEUE[:] = ALERT_QUEUE[:], []
    for i in range(0, len(batch), 5):
        await loop.run_in_executor(None, send_telegram, "\n\n".join(batch[i:i + 5]))
        await asyncio.sleep(1)


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


# =========================
# 官方歷史資料（昨收、昨高、均線、均量）
# =========================
_official_session = requests.Session()
_official_session.headers.update(OFFICIAL_HEADERS)
_official_rate_lock = threading.Lock()
_official_last_request_ts = [0.0]


def _throttle_official() -> None:
    with _official_rate_lock:
        wait = MIN_OFFICIAL_INTERVAL - (time.time() - _official_last_request_ts[0])
        if wait > 0:
            time.sleep(wait)
        _official_last_request_ts[0] = time.time()


def _request_with_retry(method, url, **kwargs):
    retryable = (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
    )
    last_exc = None
    for attempt in range(1, MAX_RETRY_PER_REQUEST + 1):
        _throttle_official()
        try:
            return method(url, **kwargs)
        except retryable as exc:
            last_exc = exc
            print(f"官方請求第{attempt}次失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
            time.sleep(2 * attempt)
    raise last_exc


def _is_excluded_name(name: str) -> bool:
    return any(key in name for key in EXCLUDE_NAME_KEYWORDS) or name.endswith(("特", "甲特", "乙特"))


def _find_data_table(tables, title_keywords=()):
    for table in tables:
        title = str(table.get("title", ""))
        if title_keywords and any(key in title for key in title_keywords) and table.get("data"):
            return table
    for table in tables:
        fields = "".join(map(str, table.get("fields", [])))
        if "代號" in fields and "收盤" in fields and len(table.get("data", [])) > 100:
            return table
    return None


def _to_float(text) -> float:
    return float(str(text).replace(",", "").strip())


def _parse_stock_table(table) -> dict:
    """解析官方每日行情表：代號、名稱、收盤、成交股數、最高價。"""
    if not table:
        return {}
    fields = [str(f).strip() for f in table.get("fields", [])]

    def index_of(keywords):
        for key in keywords:               # 依關鍵字順序找，先找到的優先（例如先找「最高價」再找「最高」）
            for i, field in enumerate(fields):
                if key in field:
                    return i
        return None

    code_i = index_of(["證券代號", "股票代號", "代號"])
    name_i = index_of(["證券名稱", "股票名稱", "名稱"])
    close_i = index_of(["收盤價", "收盤"])
    volume_i = index_of(["成交股數"])
    high_i = index_of(["最高價", "最高"])
    if code_i is None or close_i is None or volume_i is None:
        print(f"[警告] 官方表格欄位無法辨識：{fields}", flush=True)
        return {}
    if high_i is None:
        print(f"[警告] 官方表格找不到「最高價」欄位，A2 的昨高會用收盤價代替：{fields}", flush=True)
    result = {}
    for row in table.get("data", []):
        try:
            code = str(row[code_i]).strip()
            name = str(row[name_i]).strip() if name_i is not None else code
            close = _to_float(row[close_i])
            volume = _to_float(row[volume_i])
        except (ValueError, TypeError, IndexError):
            continue
        high = None
        if high_i is not None:
            try:
                high = _to_float(row[high_i])
            except (ValueError, TypeError, IndexError):
                high = None
        if high is None or high <= 0:
            high = close
            HIGH_FALLBACK["rows"] += 1
        if STOCK_CODE_RE.fullmatch(code) and close > 0 and not _is_excluded_name(name):
            result[code] = {"name": name, "close": close, "volume_shares": volume, "high": high}
    return result


def _fetch_twse_day(day: date_cls):
    """回傳 dict（有資料）、{}（假日/無交易）、None（連線失敗）。"""
    try:
        response = _request_with_retry(
            _official_session.get,
            TWSE_MI_INDEX_URL,
            params={"date": day.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
            timeout=25,
        )
        data = response.json()
    except Exception as exc:
        print(f"TWSE {day} 失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
        return None
    if str(data.get("stat", "")).upper() != "OK":
        return {}
    return _parse_stock_table(_find_data_table(data.get("tables", [])))


def _fetch_tpex_day(day: date_cls):
    """回傳 dict（有資料）、{}（假日/無交易）、None（連線失敗）。"""
    try:
        response = _request_with_retry(
            _official_session.post,
            TPEX_DAILY_QUOTES_URL,
            data={"date": day.strftime("%Y/%m/%d"), "id": "", "response": "json"},
            headers=TPEX_PAGE_HEADERS,
            timeout=25,
        )
        data = response.json()
    except Exception as exc:
        print(f"TPEx {day} 失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
        return None
    if str(data.get("stat", "")).upper() != "OK":
        return {}
    return _parse_stock_table(_find_data_table(data.get("tables", []), ("上櫃股票行情",)))


def _fetch_industry_map() -> dict:
    result = {}
    try:
        response = requests.get(TWSE_COMPANY_URL, headers=OFFICIAL_HEADERS, timeout=15)
        for row in response.json():
            code = str(row.get("公司代號", "")).strip()
            if code:
                raw_code = _normalize_industry_code(str(row.get("產業別", "")))
                result[code] = INDUSTRY_CODE_NAME.get(raw_code, raw_code)
    except Exception as exc:
        print(f"抓取上市公司產業別失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
    try:
        response = requests.get(TPEX_COMPANY_URL, headers=OFFICIAL_HEADERS, timeout=15)
        for row in response.json():
            code = str(row.get("SecuritiesCompanyCode", "")).strip()
            if code and code not in result:
                raw_code = _normalize_industry_code(str(row.get("SecuritiesIndustryCode", "")))
                result[code] = INDUSTRY_CODE_NAME.get(raw_code, raw_code)
    except Exception as exc:
        print(f"抓取上櫃公司產業別失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
    return result


def _backfill_market(name: str, fetch_fn, candidate_dates):
    results, failed_dates = [], []
    consecutive_empty = 0
    for index, day in enumerate(candidate_dates, 1):
        data = fetch_fn(day)
        if data is None:
            failed_dates.append(day)
            consecutive_empty += 1
        elif data:
            results.append((day, data))
            consecutive_empty = 0
        else:
            consecutive_empty += 1
        if index % 5 == 0 or len(results) >= TARGET_TRADING_DAYS:
            print(f"[{name}] 已掃描 {index}/{len(candidate_dates)}，有效交易日 {len(results)}", flush=True)
        if len(results) >= TARGET_TRADING_DAYS:
            break
        if consecutive_empty >= SUSPECTED_BLOCK_THRESHOLD:
            print(f"[{name}] 連續{consecutive_empty}個候選日無資料，提前停止", flush=True)
            break
    return sorted(results, key=lambda item: item[0])[-TARGET_TRADING_DAYS:], failed_dates


def _check_market_gaps(name: str, market_key: str, results, failed_dates) -> None:
    if not results or not failed_dates:
        return
    window_start = [day for day, _ in results][-MA_WINDOW:][0]
    bad = sorted(d for d in failed_dates if d >= window_start)
    if bad:
        UNRELIABLE_MARKETS.add(market_key)
        msg = (
            f"⚠️ <b>【飆股雷達】{name} 歷史資料不完整</b>\n"
            f"最近{MA_WINDOW}個交易日內，以下日期抓取失敗：{', '.join(d.isoformat() for d in bad)}\n"
            f"{name} 個股的均線/均量可能有誤，本次不發 {name} 的通知。建議稍後重新執行。"
        )
        print(msg.replace("<b>", "").replace("</b>", ""), flush=True)
        send_telegram(msg)
    else:
        print(f"[{name}] 抓取失敗日期 {', '.join(d.isoformat() for d in failed_dates)} 不在最近{MA_WINDOW}個交易日內，不影響均線/均量", flush=True)


def _trimmed_avg_vol(v5: list) -> float:
    """5 日均量，排除最大的一天（單日爆量不拉高基準）。"""
    if not v5:
        return 0.0
    if len(v5) < 5:
        return float(sum(v5)) / len(v5)
    trimmed = sorted(v5)[:-1]
    return float(sum(trimmed)) / len(trimmed)


def precompute_static(base: dict) -> dict:
    """盤前靜態預算：只用「昨日收盤後就固定」的數字，算出盤中要用的布林值與門檻，寫回 base。
    盤中不必再重算昨天的數學；參數（GAP_RATIO、A3_SQUEEZE_PCT）改了，載入時重算即可，不必重跑 baseline。"""
    prev = (base["true_ma5"], base["true_ma10"], base["true_ma20"])
    lo, hi = min(prev), max(prev)
    spread = (hi / lo - 1) * 100 if lo > 0 else None
    base["prev_ma_max"] = hi
    base["prev_ma_min"] = lo
    base["a1_prev"] = bool(base["yest_close"] < hi)                         # A1 前半段：昨收 < 昨日均線最高
    base["a2_trigger"] = base["yest_high"] * GAP_RATIO                      # A2：今日開盤 > 這個價
    base["a3_spread"] = spread                                              # A3 壓縮期：昨日 MA5/10/20 差距（%）
    base["a3_squeezed"] = bool(spread is not None and spread <= A3_SQUEEZE_PCT)
    base["ma5_cut"] = base["sum_4"] / 4                                     # MA5 減碼參考價（盤中通知用）
    return base


def precompute_summary(bases: dict) -> str:
    liquid = [b for b in bases.values() if b.get("liquid")]
    a1 = sum(1 for b in liquid if b.get("a1_prev"))
    a3 = sum(1 for b in liquid if b.get("a3_squeezed"))
    return f"盤前預算（{len(liquid)} 檔達標）：A1 前半段成立 {a1} 檔｜A3 壓縮期成立 {a3} 檔｜A2 觸發價（昨高×{GAP_RATIO}）已備妥"


def load_all_stock_bases(include_today=None) -> dict:
    """建立每檔股票的歷史基底（昨收、昨高、MA、均量）。
    include_today：None = 自動（平日 13:35 以後嘗試納入今日官方資料）；False = 一律只用今日以前的資料。"""
    started = time.time()
    now = now_tw()
    today = now.date()
    if include_today is None:
        include_today = _is_weekday(now) and now > _close_confirm_today(now)
    candidate_dates = [today] if include_today else []
    candidate_dates += [
        today - timedelta(days=i)
        for i in range(1, LOOKBACK_CALENDAR_DAYS + 1)
        if (today - timedelta(days=i)).weekday() < 5
    ]
    print(
        f"候選交易日上限 {len(candidate_dates)}，目標 {TARGET_TRADING_DAYS} 個有效交易日 — "
        + ("嘗試納入今日官方資料" if include_today else "僅使用今日以前的歷史資料"),
        flush=True,
    )
    twse_results, twse_failed = _backfill_market("TWSE", _fetch_twse_day, candidate_dates)
    tpex_results, tpex_failed = _backfill_market("TPEx", _fetch_tpex_day, candidate_dates)
    _check_market_gaps("上市(TWSE)", "tse", twse_results, twse_failed)
    _check_market_gaps("上櫃(TPEx)", "otc", tpex_results, tpex_failed)

    per_stock = {}
    twse_codes = set()
    for market_key, market_results in (("tse", twse_results), ("otc", tpex_results)):
        for day, market in market_results:
            for code, item in market.items():
                if market_key == "otc" and code in twse_codes:
                    continue
                if market_key == "tse":
                    twse_codes.add(code)
                rec = per_stock.setdefault(code, {"name": item["name"], "market": market_key, "days": {}})
                rec["days"][day] = (item["close"], item["volume_shares"], item["high"])

    industry_map = _fetch_industry_map()
    stock_bases = {}
    excluded = 0
    for code, rec in per_stock.items():
        dates = sorted(rec["days"].keys())
        if len(dates) < MA_WINDOW:
            excluded += 1
            continue
        last_date = dates[-1]
        if (today - last_date).days > STALE_DAYS_LIMIT:
            continue
        d20 = dates[-MA_WINDOW:]
        c20 = [rec["days"][d][0] for d in d20]
        d5 = dates[-5:]
        v5_lots = [rec["days"][d][1] / 1000 for d in d5]
        turnover_5d = sum(rec["days"][d][0] * rec["days"][d][1] for d in d5) / len(d5)
        official = industry_map.get(code, "")
        avg5_simple = sum(v5_lots) / len(v5_lots)
        stock_bases[code] = {
            "code": code,
            "name": rec["name"],
            "market": rec["market"],            # "tse" / "otc"
            "industry": official,
            "sum_3": float(sum(c20[-3:])),      # 昨日起往前 3 日收盤合計（算「明日 MA5 臨界價」用）
            "sum_4": float(sum(c20[-4:])),      # 昨日起往前 4 日收盤合計（今日 MA5 = (sum_4 + 現價) / 5）
            "sum_9": float(sum(c20[-9:])),
            "sum_19": float(sum(c20[-19:])),
            "true_ma5": sum(c20[-5:]) / 5,      # 昨日的 MA5/10/20（A1 的「昨收 < max(MA)」用）
            "true_ma10": sum(c20[-10:]) / 10,
            "true_ma20": sum(c20) / 20,
            "asof_date": last_date,
            "yest_close": c20[-1],
            "yest_high": rec["days"][last_date][2],
            "yest_vol": v5_lots[-1],            # 官方昨日全日量（張）
            "avg_5d_vol_simple": avg5_simple,   # C1 用
            "avg_5d_vol": _trimmed_avg_vol(v5_lots),
            "avg_turnover_5d": turnover_5d,
            "liquid": avg5_simple >= MIN_VOL5_LOTS,
        }
        precompute_static(stock_bases[code])
    tse_count = sum(base["market"] == "tse" for base in stock_bases.values())
    liquid = sum(1 for b in stock_bases.values() if b["liquid"])
    print(
        f"預載完成：{len(stock_bases)} 檔（上市 {tse_count} / 上櫃 {len(stock_bases) - tse_count}），"
        f"歷史不足排除 {excluded} 檔，5 日均量 ≥ {MIN_VOL5_LOTS:,} 張 {liquid} 檔，"
        f"最高價欄位缺漏改用收盤價 {HIGH_FALLBACK['rows']} 筆，耗時 {time.time() - started:.1f}s",
        flush=True,
    )
    return stock_bases


def apply_ic_chains(stock_bases: dict, chain_map: dict) -> None:
    hit = 0
    for code, base in stock_bases.items():
        chains = chain_map.get(code)
        if chains:
            base["industry"] = _chain_label(chains)
            hit += 1
    print(f"[產業鏈] 採用櫃買產業鏈 {hit} 檔，其餘 {len(stock_bases) - hit} 檔使用官方產業別", flush=True)


# =========================
# 加權指數（只用來在收盤報告顯示大盤 MA20 狀態，不當進場濾網）
# =========================
def _parse_roc_date(text):
    try:
        y, m, d = [int(x) for x in str(text).strip().split("/")]
        return date_cls(y + 1911, m, d)
    except (ValueError, TypeError):
        return None


def fetch_taiex_closes(asof: date_cls):
    """回傳 [(日期, 收盤指數), ...]（由舊到新，只含 asof 以前）。失敗回傳 []。"""
    result = {}
    month_start = asof.replace(day=1)
    for back in range(3):
        y, m = month_start.year, month_start.month - back
        while m <= 0:
            m += 12
            y -= 1
        try:
            resp = _request_with_retry(
                _official_session.get,
                TWSE_FMTQIK_URL,
                params={"date": f"{y}{m:02d}01", "response": "json"},
                timeout=25,
            )
            data = resp.json()
        except Exception as exc:
            print(f"[大盤] FMTQIK {y}-{m:02d} 失敗: {type(exc).__name__}", flush=True)
            continue
        if str(data.get("stat", "")).upper() != "OK":
            continue
        fields = [_norm(f) for f in data.get("fields", [])]
        try:
            d_i = fields.index("日期")
            i_i = next(i for i, f in enumerate(fields) if "加權股價指數" in f)
        except (ValueError, StopIteration):
            print(f"[警告] FMTQIK 欄位無法辨識：{fields}", flush=True)
            continue
        for row in data.get("data", []):
            day = _parse_roc_date(row[d_i])
            value = _f(row[i_i])
            if day and value and day <= asof:
                result[day] = value
    return sorted(result.items())


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
        return {}, "⚠️ 找不到 volume_baseline.json：盤中 B 條件無法判斷，本次不發盤中訊號（請先執行 baseline 工作流程）"
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
    return out, f"同期量基準：基準日 {ref}，涵蓋 {len(out)}/{liquid} 檔（5 日均量 ≥ {MIN_VOL5_LOTS:,} 張）"


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
    payload = {"rule": BASELINE_RULE, "asof": asof, "generated_at": now_tw().isoformat(timespec="seconds"),
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
# 狀態檔（當天已觸發的股票）
# =========================
def load_states() -> None:
    STATE.update({"date": now_tw().strftime("%Y-%m-%d"), "triggered": {}, "signals": []})
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


def save_states() -> None:
    raw = {"_date": STATE["date"], "_triggered": STATE["triggered"], "_signals": STATE["signals"]}
    STATE_FILE.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


# =========================
# 條件判斷
# =========================
def realtime_mas(base: dict, price: float):
    """含今日價格的 MA5/10/20。"""
    return ((base["sum_4"] + price) / 5, (base["sum_9"] + price) / 10, (base["sum_19"] + price) / 20)


def a_conditions(base: dict, price: float, open_: float):
    """回傳 (a1, a2, a3, (ma5, ma10, ma20), a1_prev, a1_now, a3_info)。
    靜態部分（昨日收盤後固定）直接讀 precompute_static 的結果；動態部分（今日即時均線、現價）才在這裡算：
      A1 = a1_prev（預算）且 現價同時 > 今日即時 MA5/10/20
      A2 = 開盤 > a2_trigger（預算：昨高 × 1.02）
      A3 = a3_squeezed（預算：昨日 MA5/10/20 差距 ≤ A3_SQUEEZE_PCT）且 現價 > 均線群高點（昨日群高與今日即時 MA 的最大值）
    A3 不看昨收在均線上或下（與 A1 的「由下往上首次穿越」區分）。"""
    if "a1_prev" not in base:                    # 沒經過預算的 base（例如單元測試）：補算一次
        precompute_static(base)
    mas = realtime_mas(base, price)              # 今日即時 MA：會隨現價跳動，只能盤中算
    ma5, ma10, ma20 = mas
    a1_prev = base["a1_prev"]
    a1_now = price > ma5 and price > ma10 and price > ma20
    a2 = open_ > base["a2_trigger"]
    cluster_high = max(base["prev_ma_max"], ma5, ma10, ma20)
    squeezed = base["a3_squeezed"]
    broke = price > cluster_high
    a3_info = {"spread": base["a3_spread"], "high": cluster_high, "squeezed": squeezed, "broke": broke}
    return (a1_prev and a1_now), a2, (squeezed and broke), mas, a1_prev, a1_now, a3_info


def _kind(a1: bool, a2: bool, a3: bool = False) -> str:
    return "+".join(name for name, flag in (("A1", a1), ("A2", a2), ("A3", a3)) if flag)


def chg_pct(base: dict, row: dict, price: float):
    """漲幅（%）。優先用富邦的 changePercent（已處理除權息參考價），沒有才用 change，最後才用官方昨收。"""
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


def evaluate_intraday(base: dict, bl: dict, row: dict, x: float, funnel: dict):
    """盤中判斷。符合 A+B+C 回傳訊號 dict，否則回傳 None。funnel 累計每一關通過的檔數（除錯用）。"""
    price = _f(row.get("lastPrice"))
    open_ = _f(row.get("openPrice"))
    if not price or not open_ or price <= 0 or open_ <= 0:
        return None
    # C3 現價 > 開盤
    if price <= open_:
        return None
    funnel["red"] += 1
    # C4 漲幅 +2% ~ +6%
    chg = chg_pct(base, row, price)
    if chg is None or not (CHG_MIN <= chg <= CHG_MAX):
        return None
    funnel["chg"] += 1
    # A
    a1, a2, a3, (ma5, ma10, ma20), _, _, a3_info = a_conditions(base, price, open_)
    if not (a1 or a2 or a3):
        return None
    funnel["a"] += 1
    # B 同期比較
    vol = _f(row.get("tradeVolume")) or 0.0
    avg_now = curve_at(bl["avg"], x)
    yest_now = curve_at(bl["yest"], x)
    if avg_now < MIN_BASELINE_CUM:
        return None
    if vol < avg_now * VOL_SURGE or vol < yest_now * VS_YEST_RATIO:
        return None
    funnel["b"] += 1
    # C1 / C2
    if base["avg_5d_vol_simple"] < MIN_VOL5_LOTS:
        return None
    val = _f(row.get("tradeValue")) or 0.0
    scale = min(SCALE_CAP, bl["avg"][-1] / avg_now)          # 全日量 / 同時間量（同一條 5 日均線）
    est_turnover = val * scale
    if est_turnover <= MIN_EST_TURNOVER:
        return None
    funnel["c"] += 1
    return {
        "code": base["code"], "name": base["name"], "market": base["market"], "industry": base["industry"],
        "kind": _kind(a1, a2, a3), "price": price, "open": open_, "chg": chg,
        "yest_close": base["yest_close"], "yest_high": base["yest_high"],
        "a3_spread": a3_info["spread"], "a3_high": a3_info["high"],
        "prev_ma_max": base["prev_ma_max"],
        "vol": vol, "avg_now": avg_now, "yest_now": yest_now,
        "ratio_avg": vol / avg_now, "ratio_yest": (vol / yest_now) if yest_now > 0 else None,
        "val": val, "est_turnover": est_turnover,
        "ma5": ma5, "ma10": ma10, "ma20": ma20,
        "ma5_cut": base["ma5_cut"],         # 收盤價跌破這個價 = 收盤跌破 MA5（盤前預算）
    }


def debug_line(base: dict, bl, row: dict, x: float) -> str:
    price, open_ = _f(row.get("lastPrice")), _f(row.get("openPrice"))
    if not price or not open_:
        return f"[診斷 {base['code']} {base['name']}] 無成交價或開盤價"
    chg = chg_pct(base, row, price)
    a1, a2, a3, mas, a1p, a1n, a3i = a_conditions(base, price, open_)
    vol = _f(row.get("tradeVolume")) or 0.0
    val = _f(row.get("tradeValue")) or 0.0
    if bl:
        avg_now, yest_now = curve_at(bl["avg"], x), curve_at(bl["yest"], x)
        scale = min(SCALE_CAP, bl["avg"][-1] / avg_now) if avg_now > 0 else 0
        btxt = (f"量 {vol:,.0f}｜5日同期 {avg_now:,.0f}（{vol / avg_now if avg_now else 0:.2f}x）"
                f"｜昨日同期 {yest_now:,.0f}（{vol / yest_now if yest_now else 0:.2f}x）｜預估額 {val * scale / 1e8:.2f}億")
    else:
        btxt = "無同期量基準"
    return (f"[診斷 {base['code']} {base['name']}] 價 {price}｜開 {open_}｜漲幅 {chg if chg is None else round(chg, 2)}%"
            f"｜A1 {a1}（昨收<均線最高 {a1p}，現價站上 {a1n}）｜A2 {a2}｜A3 {a3}（昨日糾結 {'無' if a3i['spread'] is None else round(a3i['spread'], 2)}%，群高 {a3i['high']:.2f}）｜MA5/10/20 {mas[0]:.2f}/{mas[1]:.2f}/{mas[2]:.2f}｜{btxt}")


def check_close(base: dict, bl, row: dict):
    """收盤確認：用最終資料把 A+B+C 重算一次。沒有成交回傳 None。"""
    price = _f(row.get("closePrice")) or _f(row.get("lastPrice"))
    open_ = _f(row.get("openPrice"))
    if not price or not open_:
        return None
    vol = _f(row.get("tradeVolume")) or 0.0
    val = _f(row.get("tradeValue")) or 0.0
    chg = chg_pct(base, row, price)
    a1, a2, a3, (ma5, ma10, ma20), a1_prev, a1_now, a3_info = a_conditions(base, price, open_)
    # B：全日量。有同期量基準就用同一個資料來源（富邦 1 分 K 加總），沒有才用官方資料
    if bl:
        full_avg, yest_full = bl["avg"][-1], bl["yest"][-1]
    else:
        full_avg, yest_full = base["avg_5d_vol"], base["yest_vol"]
    b1 = full_avg > 0 and vol >= full_avg * VOL_SURGE
    b2 = vol >= yest_full * VS_YEST_RATIO
    c1 = base["avg_5d_vol_simple"] >= MIN_VOL5_LOTS
    c2 = val > MIN_EST_TURNOVER
    c3 = price > open_
    c4 = chg is not None and CHG_MIN <= chg <= CHG_MAX
    a_ok, b_ok, c_ok = (a1 or a2 or a3), (b1 and b2), (c1 and c2 and c3 and c4)
    ratio_avg = vol / full_avg if full_avg > 0 else 0.0
    ratio_yest = vol / yest_full if yest_full > 0 else 0.0
    reasons = []
    if not a_ok:
        parts = []
        if not a1_prev:
            parts.append("A1：昨日收盤已站上所有均線（非突破首日）")
        elif not a1_now:
            parts.append(f"A1：收盤 {price:.2f} 未同時站上 MA5/10/20（{ma5:.2f}/{ma10:.2f}/{ma20:.2f}）")
        parts.append(f"A2：開盤 {open_:.2f} 未超過昨高×{GAP_RATIO}（{base['a2_trigger']:.2f}）")
        if not a3_info["squeezed"]:
            sp = a3_info["spread"]
            parts.append(f"A3：昨日 MA 糾結幅度 {'無資料' if sp is None else f'{sp:.2f}%'}（需 ≤ {A3_SQUEEZE_PCT:g}%）")
        else:
            parts.append(f"A3：收盤 {price:.2f} 未突破均線群高點 {a3_info['high']:.2f}")
        reasons.append("；".join(parts))
    if not b1:
        reasons.append(f"B：全日量只有 5 日均量的 {ratio_avg:.2f} 倍（需 ≥ {VOL_SURGE}）")
    if not b2:
        reasons.append(f"B：全日量未超過昨日全日量（{ratio_yest:.2f} 倍）")
    if not c1:
        reasons.append(f"C：5 日均量 {base['avg_5d_vol_simple']:,.0f} 張 < {MIN_VOL5_LOTS:,}")
    if not c2:
        reasons.append(f"C：成交額 {val / 1e8:.2f} 億 ≤ {MIN_EST_TURNOVER / 1e8:.0f} 億")
    if not c3:
        reasons.append(f"C：收盤 {price:.2f} 未高於開盤 {open_:.2f}")
    if not c4:
        reasons.append(f"C：收盤漲幅 {'無資料' if chg is None else f'{chg:+.1f}%'} 不在 +{CHG_MIN:.0f}%～+{CHG_MAX:.0f}%")
    return {
        "code": base["code"], "name": base["name"], "market": base["market"], "industry": base["industry"],
        "kind": _kind(a1, a2, a3) if a_ok else "", "price": price, "open": open_, "chg": chg,
        "yest_close": base["yest_close"], "yest_high": base["yest_high"],
        "vol": vol, "full_avg": full_avg, "yest_full": yest_full, "ratio_avg": ratio_avg, "ratio_yest": ratio_yest,
        "val": val, "ma5": ma5, "ma10": ma10, "ma20": ma20,
        "next_ma5_cut": (price + base["sum_3"]) / 4,   # 明日收盤跌破這個價 = 明日收盤跌破 MA5
        "a_ok": a_ok, "b_ok": b_ok, "c_ok": c_ok, "passed": a_ok and b_ok and c_ok, "reasons": reasons,
    }


# =========================
# 通知文字與 CSV
# =========================
SIGNAL_FIELDS = ["時間", "首次符合時間", "代號", "名稱", "市場", "型態", "現價", "漲幅(%)", "開盤", "昨收", "昨高",
                 "累積量(張)", "5日同期均量(張)", "5日同期倍數", "昨日同期倍數",
                 "已成交額(億)", "預估成交額(億)", "MA5", "MA10", "MA20", "MA5減碼參考價", "族群"]


def _mkt_name(mkt: str) -> str:
    return "上市" if mkt == "tse" else "上櫃"


def build_signal_message(sig: dict, n_today: int, now: datetime) -> str:
    names = {"A1": "A1 均線突破首日", "A2": "A2 強勢跳空", "A3": "A3 均線糾結突破"}
    title = " + ".join(names[k] for k in sig["kind"].split("+"))
    lines = [f"🚀 <b>【進場訊號 A+B+C】{sig['code']} {sig['name']}</b>", f"• 型態：<b>{title}</b>"]
    if "A1" in sig["kind"]:
        lines.append(f"• A1：昨收 {sig['yest_close']:.2f} < 昨日均線最高 {sig['prev_ma_max']:.2f}，現價同時站上 MA5/10/20")
    if "A2" in sig["kind"]:
        lines.append(f"• A2：開盤 {sig['open']:.2f} > 昨高 {sig['yest_high']:.2f} × {GAP_RATIO}（{sig['yest_high'] * GAP_RATIO:.2f}）")
    if "A3" in sig["kind"] and sig.get("a3_spread") is not None:
        lines.append(f"• A3：昨日 MA5/10/20 糾結幅度 {sig['a3_spread']:.2f}%（≤ {A3_SQUEEZE_PCT:g}%），現價突破均線群高點 {sig['a3_high']:.2f}")
    ry = "無資料" if sig["ratio_yest"] is None else f"{sig['ratio_yest']:.2f} 倍"
    lines += [
        f"• 現價 <b>{sig['price']:.2f}</b>（{sig['chg']:+.1f}%）｜開盤 {sig['open']:.2f}｜昨收 {sig['yest_close']:.2f}｜昨高 {sig['yest_high']:.2f}",
        f"• B 爆量：累積 {sig['vol']:,.0f} 張＝5 日同期均量 <b>{sig['ratio_avg']:.2f} 倍</b>（{sig['avg_now']:,.0f}）｜昨日同期 {ry}（{sig['yest_now']:,.0f}）",
        f"• C 熱度：已成交 {sig['val'] / 1e8:.2f} 億｜預估全日 {sig['est_turnover'] / 1e8:.2f} 億（門檻 {MIN_EST_TURNOVER / 1e8:.0f} 億）",
        f"• MA5/10/20：{sig['ma5']:.2f} / {sig['ma10']:.2f} / {sig['ma20']:.2f}",
        f"• ✂️ MA5 減碼參考價：<b>{sig['ma5_cut']:.2f}</b>（前 4 日收盤均價；今日收盤跌破此價 = 收盤跌破 MA5）",
        f"• 族群：{sig['industry'] or '未分類'}｜{_mkt_name(sig['market'])}",
        f"• ✅ 二次確認：首次符合 {sig.get('first_time', '-')}（{sig.get('first_price', sig['price']):.2f}）→ 再次符合 {now.strftime('%H:%M:%S')}（{sig['price']:.2f}）",
        f"• 今日第 {n_today} 則｜時間：{now.strftime('%H:%M:%S')}",
    ]
    return "\n".join(lines)


def signal_row(sig: dict, now: datetime) -> dict:
    return {
        "時間": now.strftime("%H:%M:%S"), "首次符合時間": sig.get("first_time", ""), "代號": sig["code"], "名稱": sig["name"], "市場": _mkt_name(sig["market"]),
        "型態": sig["kind"], "現價": round(sig["price"], 2), "漲幅(%)": round(sig["chg"], 2),
        "開盤": round(sig["open"], 2), "昨收": round(sig["yest_close"], 2), "昨高": round(sig["yest_high"], 2),
        "累積量(張)": int(sig["vol"]), "5日同期均量(張)": int(sig["avg_now"]),
        "5日同期倍數": round(sig["ratio_avg"], 2),
        "昨日同期倍數": "" if sig["ratio_yest"] is None else round(sig["ratio_yest"], 2),
        "已成交額(億)": round(sig["val"] / 1e8, 2), "預估成交額(億)": round(sig["est_turnover"] / 1e8, 2),
        "MA5": round(sig["ma5"], 2), "MA10": round(sig["ma10"], 2), "MA20": round(sig["ma20"], 2),
        "MA5減碼參考價": round(sig["ma5_cut"], 2), "族群": sig["industry"],
    }


def export_signal_csv(day_str: str) -> pathlib.Path:
    with SIGNAL_CSV_FILE.open("w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerow([f"訊號日期：{day_str}（盤中 A+B+C，依時間排序）"])
        writer = csv.DictWriter(fh, fieldnames=SIGNAL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(STATE["signals"])
    return SIGNAL_CSV_FILE


CLOSE_FIELDS = ["代號", "名稱", "市場", "族群", "型態", "盤中觸發", "收盤確認", "未通過原因", "收盤價", "開盤", "昨收", "昨高",
                "漲幅(%)", "全日量(張)", "5日均量倍數", "昨日量倍數", "成交額(億)", "MA5", "MA10", "MA20", "明日MA5減碼參考價"]


def close_row(r: dict, notified_time: str) -> dict:
    return {
        "代號": r["code"], "名稱": r["name"], "市場": _mkt_name(r["market"]), "族群": r["industry"], "型態": r["kind"],
        "盤中觸發": notified_time or "否", "收盤確認": "通過" if r["passed"] else "未通過",
        "未通過原因": "；".join(r["reasons"]), "收盤價": round(r["price"], 2), "開盤": round(r["open"], 2),
        "昨收": round(r["yest_close"], 2), "昨高": round(r["yest_high"], 2),
        "漲幅(%)": "" if r["chg"] is None else round(r["chg"], 2), "全日量(張)": int(r["vol"]),
        "5日均量倍數": round(r["ratio_avg"], 2), "昨日量倍數": round(r["ratio_yest"], 2),
        "成交額(億)": round(r["val"] / 1e8, 2), "MA5": round(r["ma5"], 2), "MA10": round(r["ma10"], 2),
        "MA20": round(r["ma20"], 2), "明日MA5減碼參考價": round(r["next_ma5_cut"], 2),
    }


def export_close_csv(day_str: str, rows: list) -> pathlib.Path:
    with CLOSE_CSV_FILE.open("w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerow([f"收盤確認日期：{day_str}（通過者在前；含盤中觸發過的股票）"])
        writer = csv.DictWriter(fh, fieldnames=CLOSE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return CLOSE_CSV_FILE


def build_close_message(day_str: str, passed: list, failed: list, n_signals: int, market_txt: str, note: str) -> str:
    fresh = sum(1 for r in passed if not r["notified"])
    lines = [f"📴 <b>【飆股雷達】收盤確認 {day_str}</b>",
             f"今日共有 <b>{len(passed)} 檔</b>股票符合 A+B+C 進場策略（收盤確認）｜盤中共發出 {n_signals} 則訊號"
             + (f"｜其中 🆕 {fresh} 檔盤中沒通知過、收盤才符合" if fresh else ""),
             market_txt]
    if note:
        lines.append(note)
    if passed:
        lines += ["", f"✅ <b>收盤確認通過 {len(passed)} 檔</b>"]
        for r in passed:
            tag = f"盤中 {r['notified']} 已通知" if r["notified"] else "🆕 收盤才符合"
            lines.append(
                f"• {r['code']} {r['name']}｜{r['kind']}｜收 {r['price']:.2f}（{r['chg']:+.1f}%）"
                f"｜量 {r['ratio_avg']:.2f}x（昨日 {r['ratio_yest']:.2f}x）｜額 {r['val'] / 1e8:.1f} 億"
                f"｜明日 MA5 減碼參考 {r['next_ma5_cut']:.2f}｜{tag}｜{r['industry'] or '未分類'}")
    else:
        lines += ["", "✅ 今日沒有股票通過收盤確認。"]
    if failed:
        lines += ["", f"⚠️ <b>盤中觸發但收盤未通過 {len(failed)} 檔</b>"]
        for r in failed:
            lines.append(f"• {r['code']} {r['name']}｜盤中 {r['notified']} 通知｜{'；'.join(r['reasons'])}")
    return "\n".join(lines)


def market_status_text(feed, today: date_cls) -> str:
    """大盤 MA20 狀態（只顯示，不當濾網）。用官方歷史收盤 + 富邦今日加權指數。"""
    try:
        closes = fetch_taiex_closes(today)
        hist = [v for d, v in closes if d < today]
        today_close = next((v for d, v in closes if d == today), None)
        live = feed.taiex_price() if feed is not None else None
        if live and hist and abs(live / hist[-1] - 1) < 0.25:     # 擋掉抓到報酬指數（約 2 倍點數）的情況
            today_close = live
        elif live and hist:
            print(f"[大盤] 富邦指數 {live} 與官方昨收 {hist[-1]} 差距過大，不採用", flush=True)
        if len(hist) >= 19 and today_close:
            ma20 = (sum(hist[-19:]) + today_close) / 20
            if today_close > ma20:
                return f"大盤：加權指數 {today_close:,.0f}，位於 MA20（{ma20:,.0f}）之上，多頭格局 ✅"
            return f"大盤：加權指數 {today_close:,.0f}，低於 MA20（{ma20:,.0f}），非多頭格局 ⚠️"
    except Exception as exc:
        print(f"[大盤] 狀態計算失敗：{type(exc).__name__}: {exc}", flush=True)
    return "大盤：狀態無法取得（加權指數或官方資料抓取失敗）"


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
# 主程式
# =========================
async def _prepare_stock_bases(include_today=False):
    stock_bases = load_all_stock_bases(include_today=include_today)
    if not stock_bases:
        return {}
    try:
        chain_map = await refresh_ic_chains(sorted(c for c, b in stock_bases.items() if b["liquid"]))
    except Exception as exc:
        print(f"[產業鏈] 查詢流程例外，改用官方產業別: {type(exc).__name__}: {repr(exc)}", flush=True)
        chain_map = {}
    apply_ic_chains(stock_bases, chain_map)
    return stock_bases


def apply_double_confirm(candidates: list, now: datetime) -> list:
    """二次確認：本輪符合 A+B+C 的股票（candidates）與上一輪的待確認觀察池比對。
    - 上一輪在池中、這一輪又符合，且間隔 CONFIRM_MIN_GAP～CONFIRM_MAX_GAP 秒 → 確認通過，回傳。
    - 首次符合（或間隔過久）→ 放進觀察池，等下一輪。
    - 上一輪在池中、這一輪沒再符合 → 移出觀察池（Whipsaw 假突破）。"""
    confirmed, new_pending = [], {}
    for sig in candidates:
        code = sig["code"]
        prev = PENDING.get(code)
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
        new_pending[code] = {"time": now, "price": sig["price"], "kind": sig["kind"]}
        print(f"[待確認] {code} {sig['name']}（{sig['kind']}，{sig['chg']:+.1f}%，量 {sig['ratio_avg']:.2f}x）"
              f"首次符合，等下一輪再確認", flush=True)
    dropped = [c for c in PENDING if c not in new_pending and c not in {s['code'] for s in confirmed}]
    for c in dropped:
        print(f"[待確認] {c} 下一輪沒有再符合，移出觀察池（過濾假突破）", flush=True)
    PENDING.clear()
    PENDING.update(new_pending)
    return confirmed


def process_poll(snap: dict, bases: dict, baselines: dict, now: datetime, funnel: dict) -> list:
    """逐檔判斷，回傳這一輪「通過二次確認」的新訊號（已觸發過的不再判斷）。"""
    xs = {mkt: snapshot_minutes(meta, now) for mkt, meta in snap["meta"].items()}
    candidates = []
    for code, row in snap["rows"].items():
        base = bases.get(code)
        if base is None or not base["liquid"] or base["market"] in UNRELIABLE_MARKETS:
            continue
        bl = baselines.get(code)
        x = xs.get(row["_mkt"], minutes_since_open(now))
        if code in DEBUG_STOCK_CODES:
            print(debug_line(base, bl, row, x), flush=True)
        if bl is None or code in STATE["triggered"] or x < EVAL_START_MINUTE:
            continue
        funnel["total"] += 1
        sig = evaluate_intraday(base, bl, row, x, funnel)
        if sig:
            candidates.append(sig)
    funnel["pend"] += len(candidates)
    confirmed = apply_double_confirm(candidates, now)       # 每一輪都要呼叫（沒有候選時也要清空觀察池）
    funnel["conf"] += len(confirmed)
    return confirmed


async def run_close_confirm(bases: dict, baselines: dict, feed, loop, today: date_cls) -> bool:
    today_str = today.strftime("%Y-%m-%d")
    snap = None
    for attempt in range(1, 5):
        try:
            snap = await loop.run_in_executor(None, feed.snapshot_all)
            break
        except Exception as exc:
            print(f"[收盤確認] 第 {attempt} 次抓快照失敗：{type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(20)
    if snap is None:
        await loop.run_in_executor(None, send_telegram, "❌ <b>【飆股雷達】</b> 收盤確認失敗：抓不到最終快照，請手動確認。")
        return False
    dates = {str(m.get("date", "")).replace("-", "") for m in snap["meta"].values()}
    if today_str.replace("-", "") not in dates:
        print(f"[收盤確認] 快照日期 {dates} 不是今天 {today_str}，視為非交易日，不發收盤報告", flush=True)
        return False

    passed, failed = [], []
    for code, base in bases.items():
        if not base["liquid"] or base["market"] in UNRELIABLE_MARKETS:
            continue
        row = snap["rows"].get(code)
        if row is None:
            continue
        r = check_close(base, baselines.get(code), row)
        if r is None:
            continue
        r["notified"] = (STATE["triggered"].get(code) or {}).get("time", "")
        if r["passed"]:
            passed.append(r)
        elif r["notified"]:
            failed.append(r)
    passed.sort(key=lambda r: -r["val"])
    failed.sort(key=lambda r: r["notified"])

    market_txt = await loop.run_in_executor(None, market_status_text, feed, today)
    notes = []
    if UNRELIABLE_MARKETS:
        notes.append(f"⚠️ {'、'.join('上市' if m == 'tse' else '上櫃' for m in sorted(UNRELIABLE_MARKETS))}歷史資料不完整，這些股票未納入")
    if not baselines:
        notes.append("⚠️ 今日沒有同期量基準，收盤確認的 B 條件改用官方成交量")
    n_signals = len(STATE["signals"])
    message = build_close_message(today_str, passed, failed, n_signals, market_txt, "\n".join(notes))
    await loop.run_in_executor(None, send_telegram, message)

    rows = [close_row(r, r["notified"]) for r in passed + failed]
    path = export_close_csv(today_str, rows)
    await loop.run_in_executor(
        None, send_telegram_document, path,
        f"🧾 收盤確認明細：通過 {len(passed)} 檔、盤中觸發但未通過 {len(failed)} 檔")
    if STATE["signals"]:
        sig_path = export_signal_csv(today_str)
        await loop.run_in_executor(None, send_telegram_document, sig_path, f"🧾 今日盤中訊號 {len(STATE['signals'])} 則")
    save_states()
    return True


async def main_async() -> None:
    print("=" * 55, flush=True)
    print("飆股雷達 v54：1～2 週短線波段 A+B+C＋二次確認（富邦 Neo API）", flush=True)
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
    if stock_bases:
        print(f"[歷史基底] 使用{bases_source}", flush=True)
    else:
        print(f"[歷史基底] 快取不可用（{bases_source}），改向官方即時預載", flush=True)
        stock_bases = await _prepare_stock_bases(include_today=False)
        bases_source = "官方資料即時預載（夜間快取不可用）"
    if not stock_bases:
        send_telegram("❌ <b>【飆股雷達】</b> 無法建立歷史基底，監控未啟動")
        return
    baselines, bl_text = load_baseline(stock_bases)
    print(f"[同期量基準] {bl_text}", flush=True)

    if after_close:
        print("已過收盤確認時間：直接執行收盤確認（盤中觸發紀錄取自 stock_states.json）", flush=True)
        await run_close_confirm(stock_bases, baselines, feed, loop, today)
        return

    # 同期量基準缺漏或過期（例如前一晚 baseline 失敗）：在背景用富邦 1 分 K 自動補算，完成後自動啟用 B 條件
    rebuild_task = None
    if not baselines:
        print("[同期量基準] 缺漏或過期，啟動背景自動補算", flush=True)
        rebuild_task = asyncio.ensure_future(
            loop.run_in_executor(None, build_baseline, stock_bases, feed, "雷達自動補算"))
        bl_text += "\n⏳ 已啟動自動補算（約 15～20 分鐘），完成前不會有盤中訊號，完成後自動啟用"

    liquid_n = sum(1 for b in stock_bases.values() if b["liquid"])
    startup = [
        "📡 <b>【飆股雷達 v54】即時監控啟動（A+B+C＋二次確認）</b>",
        f"行情：富邦 Neo API 快照（每 {POLL_INTERVAL} 秒）｜歷史：TWSE/TPEx 官方",
        f"歷史基底：{bases_source}｜5 日均量 ≥ {MIN_VOL5_LOTS:,} 張共 {liquid_n} 檔",
        bl_text,
        precompute_summary(stock_bases),
        f"A（三擇一）：A1 均線突破首日｜A2 開盤 > 昨高 ×{GAP_RATIO}｜A3 均線糾結（MA5/10/20 差距 ≤ {A3_SQUEEZE_PCT:g}%）後突破群高點",
        f"B：累積量 ≥ 5 日同期均量 ×{VOL_SURGE} 且 ≥ 昨日同期量",
        f"C：預估成交額 > {MIN_EST_TURNOVER / 1e8:.0f} 億、現價 > 開盤、漲幅 +{CHG_MIN:.0f}%～+{CHG_MAX:.0f}%",
        f"判斷時段 09:{EVAL_START_MINUTE:02d}～13:30（避開集合競價）｜二次確認：連續兩輪掃描都符合才通知｜同一檔當天只通知一次｜13:35 收盤確認",
    ]
    if STATE["triggered"]:
        startup.append(f"續跑：今日已觸發 {len(STATE['triggered'])} 檔，不會重複通知")
    if HIGH_FALLBACK["rows"] > 100:
        startup.append(f"⚠️ 官方資料有 {HIGH_FALLBACK['rows']} 筆沒有最高價欄位、改用收盤價，A2 可能不準")
    await loop.run_in_executor(None, send_telegram, "\n".join(startup))

    fail_streak, fail_alerted, fail_started = 0, False, None
    polls = 0
    last_total, stall_polls, stall_alerted, stall_started = None, 0, False, None
    funnel = {"total": 0, "red": 0, "chg": 0, "a": 0, "b": 0, "c": 0, "pend": 0, "conf": 0}
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
                        f"✅ <b>【飆股雷達】</b> 同期量基準已補算完成並啟用，現在開始判斷 B 條件。\n{new_text}{missed}")
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

            signals = process_poll(snap, stock_bases, baselines, current, funnel)
            sent = 0
            for sig in sorted(signals, key=lambda s: -s["val"]):
                if DAILY_SIGNAL_CAP and len(STATE["signals"]) >= DAILY_SIGNAL_CAP:
                    print(f"已達每日訊號上限 {DAILY_SIGNAL_CAP}，略過 {sig['code']} {sig['name']}", flush=True)
                    break
                STATE["triggered"][sig["code"]] = {"time": current.strftime("%H:%M:%S"), "kind": sig["kind"], "price": sig["price"]}
                STATE["signals"].append(signal_row(sig, current))
                enqueue_alert(build_signal_message(sig, len(STATE["signals"]), current))
                print(f"觸發 A+B+C（二次確認通過）：{sig['code']} {sig['name']}（{sig['kind']}，{sig['chg']:+.1f}%，量 {sig['ratio_avg']:.2f}x）", flush=True)
                sent += 1
            if sent:
                save_states()
                export_signal_csv(today_str)
            await flush_alerts(loop)

            polls += 1
            elapsed = time.time() - started
            print(f"[{current.strftime('%H:%M:%S')}] 第 {polls} 輪：快照 {len(snap['rows'])} 檔，新訊號 {sent}，今日累計 {len(STATE['signals'])}，耗時 {elapsed:.2f}s"
                  f"｜{summary}（{delta:+,.0f}）", flush=True)
            if polls % 4 == 1:
                print(f"    樣本 {sample_line(snap)}", flush=True)
            if polls % 20 == 0:
                print(f"[漏斗/最近 20 輪累計人次] 判斷 {funnel['total']} → 現價>開盤 {funnel['red']} → 漲幅區間 {funnel['chg']} "
                      f"→ A {funnel['a']} → B {funnel['b']} → C(符合) {funnel['c']} → 進觀察池/再確認 {funnel['pend']} → 二次確認通過 {funnel['conf']}", flush=True)
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
    await run_close_confirm(stock_bases, baselines, feed, loop, today)


if __name__ == "__main__":
    asyncio.run(main_async())
