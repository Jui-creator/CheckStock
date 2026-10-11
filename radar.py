"""飆股雷達 v41：TWSE/TPEx 歷史均線 + MIS 盤中監控 + Telegram 通知。

v41 相對 v40 的變更（只改 MIS 抓取，交易邏輯、通知內容、清單格式都沒動）：
起因：10/07 早上 v38 的 log 顯示，09:00 前兩輪每輪只回 156 檔（48 批裡只有約 4 批有資料）、卻沒有任何錯誤訊息，
之後每輪回 0 檔，滿篇 ServerDisconnectedError / HTTP 502，而且失敗時程式還立刻重試、下一輪幾乎連續送請求。
1. 失敗退避（circuit breaker）：
   - 一輪裡「失敗 + 被中止」的批次超過一半（MIS_FAIL_RATIO），就視為「失敗輪」，暫停 60 秒再抓；
     連續失敗就加倍（60→120→240→300 秒，上限 MIS_BACKOFF_MAX）；成功一輪就歸零。
   - 一輪裡前 4 組（MIS_ABORT_AFTER_FAILS）全部失敗、且沒有任何一組成功，就中止這一輪，不再送剩下的組
     （以前會把 48 批 × 重試全部送完，一輪耗時 54 秒、請求量暴增）。
   - 進入退避時重建連線池（避免重用被伺服器關掉的 keep-alive 連線）。
   - 連續失敗達 MIS_ALERT_AFTER_FAILS（2）輪發一則 Telegram 警告，恢復時再發一則；退避期間不會漏掉收盤流程。
2. 補上診斷：HTTP 200 但 msgArray 是空的，視為失敗並重試，記錄 rtcode / rtmessage；
   另外統計「有回應但沒有成交價（z='-'）」的檔數。每輪有任何異常才印一行 [MIS] 統計。
3. 實驗性請求參數（不保證有效，可用旗標關掉）：
   - 查詢網址加上 &json=1&delay=0（MIS_URL_SUFFIX，網路上常見的 MIS 範例寫法）。
   - 建立連線池後先打開 index.jsp 取得 cookie（MIS_WARMUP）。
注意：POOL_RULE_ID 仍是 "v38"，必須和 left_side.py 的 RULE_ID 相同，不要跟著版本號改。

v40：MIS 改成每分鐘抓全部、上下半錯開 30 秒；站上均線不再乘 1.003（ABOVE_MA_BUF = 1.0）。


v40 相對 v39 的變更（降低 MIS 請求頻率 + 站上均線不再乘 1.003）：
1. MIS 抓取改成「每分鐘抓全部、上下半錯開 30 秒」：把全市場（約 1,900 檔）交錯分成 A、B 兩半（兩半各自保留
   🧭/🚀/🔥/🎯 優先順序），每 30 秒抓一半，A、B 交替；每檔股票每 60 秒更新一次，請求速率約為 v38 的一半。
   POLL_INTERVAL = 60 現在代表「每檔更新間隔」，兩半之間的間隔是 POLL_INTERVAL / 2。
   加權指數兩半都會抓，所以大盤濾網仍然每 30 秒更新。
2. 配合調整：RESET_CONFIRM_ROUNDS 20 → 10（每檔每 60 秒才處理一次，10 輪仍約 10 分鐘）；
   MIS_GROUPS_PER_CHUNK 3 → 1（不再把一批拆成 3 組）；MIS_MAX_CONCURRENCY 8 → 2；MIS_REQUEST_TIMEOUT 10 → 15 秒；
   MIS_ROUND_DEADLINE 25 → 28 秒（現在是「每半」的時間預算）。其餘設定（重試 0.5 秒 ×2、連線池）維持 v39。
3. 🚀 盤中新納入只評估「本輪有抓到價格」的股票，避免股票剛被納入、卻因為這一輪沒有它的報價而漏發通知。
4. 新增 ABOVE_MA_BUF = 1.0：📈 站上均線只要「現價 > MA5/10/20」就通知，不再乘 1.003。
   盤前的 already_above（昨收是否已站上三條均線）同步改用 ABOVE_MA_BUF。
   BREAKOUT_BUF（1.003）保留給 🏔️ 突破新高，不受影響。
注意：POOL_RULE_ID 仍是 "v38"，必須和 left_side.py 的 RULE_ID 相同，不要跟著版本號改。

v39：MIS 連線池、例外重試（舊檔說明保留如下）。


v39 相對 v38 的變更（只改 MIS 即時行情抓取，交易邏輯、通知、清單格式都沒動；其中「每批拆 3 組」在 v40 已取消）：
1. 分批請求：每批 MIS_CHUNK_SIZE=40 檔，再拆成 3 組（14/13/13 檔）依序送出，組與組之間 asyncio.sleep(0.3)。
2. 連線池複用：整個程式只建立一個 aiohttp.ClientSession（含 TCPConnector），每輪掃描共用，
   收盤或程式結束時才呼叫 close_mis_session() 關閉。
3. 例外重試：遇到 ServerDisconnectedError（含 Connection reset、逾時、HTTP 非 200、回應非 JSON）時，
   暫停 0.5 秒後重試，最多重試 2 次；重試用盡仍失敗的那一組，該輪缺資料，其他組不受影響。
4. 單輪時間預算 MIS_ROUND_DEADLINE=25 秒：MIS 變慢時，超過預算就不再送新請求，避免一輪拖過掃描間隔（設 0 = 不限）。
   有重試或失敗時，每輪合併印一行 [MIS] 統計；完全正常時不印。
注意：POOL_RULE_ID 仍是 "v38"，必須和 left_side.py 的 RULE_ID 相同，不要跟著版本號改。


v38 交易架構（六個階段，圖示各自不同）：
  1 🧭 左側潛力股      盤後累積清單（left_side.py 產生），盤中優先監控但不單獨通知
  2 📈 盤中站上均線提醒  只看站上 MA5/10/20（各 ×1.003）；5日均成交額 ≥ 1 億為盤前流動性底線；量比/成交額只當備註
  3 💥 量能確認        09:15 後；預估量比 ≥ 2.0、預估成交額 ≥ 20 億、盤中已成交 ≥ 1.5 億；顯示 📈 的時間
  4 🚀 短線轉強股      站上 MA5/10/20 第 3~5 日、MA5>MA10、外資或投信連買 ≥2 日（盤後清單 + 盤中動態納入）
  5 🔥 強勢股池        20 日漲幅 RS 前 15%（盤後清單含 🧲黏著 + 盤中動態納入）
  6 🎯 回檔買點        🚀／🔥／🎯 觀察池成員：低點落在 MA5~MA10×0.99 支撐區，自今日低點反彈 ≥0.8%、站回 MA10、RS>0
  附加 🏔️ 突破新高     🔥 強勢股池成員突破近 20 日最高收盤

v38 相對 v35 的變更（需搭配 left_side.py v9；規則版本 POOL_RULE_ID 不符時不載入 🚀/🔥/🎯 清單）：
1. 💥 門檻提高：預估成交額 ≥ 20 億，且盤中實質已成交 ≥ 1.5 億；09:00~09:15 不評估。
2. 📈 重置：跌破 MA5 達 1%（MA5×0.990）並連續 20 輪（約 10 分鐘）才重置；冷卻 60 分鐘；
   一般股票（不在任何池內）📈 單日 20 則、💥 單日 10 則上限（ABOVE_MAX_PER_DAY / CONFIRM_MAX_PER_DAY，設 0 為不限）。
3. 🚀 進池：站上第 3~5 日、MA5>MA10、法人連買 ≥2 日（截至 T-1 日；盤中不依賴當日法人）；
   盤中池容量 30 檔，滿了以 20 日漲幅 RS 取代最弱；入池滿 3 日後由 left_side.py 分流到 🔥 或 🎯 回檔專用觀察池。
4. 🎯 回檔買點：支撐區 [MA10×0.99, MA5×1.01]；現價自「今日最低價」反彈 ≥0.8% 且站回 MA10；個股相對大盤強度 RS>0。
   （原「前一日量縮」「現價≥開盤價」兩項因規範文件未列入而移除；PULLBACK_PREV_VOL_RATIO 可重新啟用量縮條件。）
5. 🔥 反轉型：現價需突破近 20 日最高收盤或反彈過程高點；強勢股 RS 門檻改前 15%。
6. 星等：🎯 ⭐⭐⭐、🏔️ ⭐⭐、💥/🚀 ⭐、📈 👀；💥/🚀 同時在 🔥 強勢型池升一級；🎯/🏔️ 僅屬 🔄 反轉型降一級。
   買進檢查未通過：🔴 粗體標示，星等不變。
7. 每次有新訊號就立刻寫 signals_today.csv；收盤時用狀態檔對帳並補登遺失的 🎯/🏔️；Telegram 單則超過 3800 字自動拆開；
   已過收盤才手動執行時，只產出 watchlist_snapshot.csv。

停損參考：🎯 與 🚀 = 收盤跌破 MA10；🏔️ = 突破價 −3%；左側潛力股的 📈/💥 = MA5/10/20 最低者（跌回整理區）；
其他股票的 📈/💥 = 跌破 MA5。

【重要】daily.yml 需把 stock_states.json commit 回 repo；ic_sector_cache.json 用 actions/cache 保存。
"""
import os
import time
import json
import re
import csv
import html
import asyncio
import pathlib
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone, date as date_cls

import aiohttp
import requests

# =========================
# 基本設定
# =========================
TW_TZ = timezone(timedelta(hours=8), name="Asia/Taipei")
BASE_DIR = pathlib.Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "stock_states.json"
WATCHLIST_CSV_FILE = BASE_DIR / "watchlist_snapshot.csv"
IC_CACHE_FILE = BASE_DIR / "ic_sector_cache.json"
LEFT_WATCHLIST_FILE = BASE_DIR / "left_side_watchlist.json"
STRONG_POOL_FILE = BASE_DIR / "strong_stock_pool.json"
SIGNAL_CSV_FILE = BASE_DIR / "signals_today.csv"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ---- 第 2 階段：📈 站上均線 ----
MIN_LIQ_TURNOVER_5D = 100_000_000   # 盤前即知的流動性底線：近 5 日平均成交額 ≥ 1 億
ABOVE_MA_BUF = 1.0                  # 📈 站上 MA5/10/20：現價 > MA × 1.0（不加緩衝，一站上就通知）
BREAKOUT_BUF = 1.003                # 🏔️ 突破新高：需高出近 20 日最高收盤 0.3%（只給突破新高用）
RESET_BUF = 0.990                   # 跌破 MA5 達 1%（MA5×0.990）才開始計入重置
RESET_CONFIRM_ROUNDS = 10           # 連續 10 輪（每檔每 60 秒處理一次，約 10 分鐘）都低於才重置狀態
COOLDOWN_MINUTES = 60
POLL_INTERVAL = 60                  # 每檔股票的更新間隔（秒）；全市場分上下半，每 POLL_INTERVAL/2 秒抓一半
ABOVE_MAX_PER_DAY = 20              # 📈 單日上限（只計一般股票；0 = 不限）
CONFIRM_MAX_PER_DAY = 10            # 💥 單日上限（只計一般股票；0 = 不限）

# ---- 第 3 階段：💥 量能確認 ----
CONFIRM_MIN_VOL_RATIO = 2.0              # 預估量比 ≥ 2.0
CONFIRM_MIN_TURNOVER = 2_000_000_000     # 預估成交額 ≥ 20 億
CONFIRM_MIN_ACTUAL_TURNOVER = 150_000_000  # 盤中實質已成交金額 ≥ 1.5 億
CONFIRM_START_MINUTE = 15                # 開盤後 15 分鐘（09:15）才開始評估

# ---- 第 4 階段：🚀 短線轉強股 ----
SHORT_MIN_STREAK = 3                # 站上 MA5/10/20 第 3 日起
SHORT_MAX_STREAK = 5                # 超過 5 日就不是「剛轉強」
SHORT_MIN_INST_DAYS = 2             # 截至 T-1 日外資或投信連續買超 ≥ 2 日
SHORT_POOL_MAX = 30                 # 🚀 池容量（盤中滿了以 20 日漲幅 RS 取代最弱）
SHORT_POOL_DAYS = 3                 # 提醒窗口：入池滿 3 日，第 4 天起由盤後分流
WATCH_MAX_DAYS = 10                 # 🎯 回檔專用觀察池至多 10 個交易日
SHORT_ENTRY_MAX_PER_DAY = 30        # 🚀 盤中新納入通知每日上限
RS_MIN_COVERAGE = 0.6               # 盤中要有 60% 的候選股已有成交價，才用 RS 排名納入 🔥

# ---- 第 5 階段：🔥 強勢股池 ----
RS_TOP_RATIO = 0.15                 # 即時 20 日漲幅前 15%
TREND_NEAR_HIGH = 0.05              # 趨勢型：距 60 日高點 ≤ 5%
REVERSAL_REBOUND = 0.25             # 反轉型：自 60 日低點反彈 ≥ 25%
REVERSAL_VOL_EXPAND = 1.2           # 反轉型：近20日均量 ≥ 前20日均量 × 1.2
STRONG_MAX_EXT = 1.20               # 強勢股池：現價 ≤ MA20 × 1.20
POOL_RULE_ID = "v38"                # 須與 left_side.py 的 RULE_ID 相同

# ---- 第 6 階段：🎯 回檔買點 / 🏔️ 突破新高 ----
STRONG_MAX_PER_DAY = 10             # 每日 🎯 + 🏔️ 合計上限
BUYPOINT_MAX_EXT = 1.20             # 買點前提：現價 ≤ MA20 × 1.20
PULLBACK_TOUCH_BUF = 1.01           # 支撐區上緣：MA5 × 1.01
PULLBACK_HOLD_BUF = 0.99            # 支撐區下緣：MA10 × 0.99（允許刺穿 1% 測試支撐）
PULLBACK_BOUNCE = 0.008             # 現價自今日最低價反彈 ≥ 0.8%
PULLBACK_PREV_VOL_RATIO = None      # 前一日量 < 5日均量 × 比例；None = 不啟用
BREAKOUT_VOL_RATIO = 1.5            # 突破新高需預估量比 ≥ 1.5
BREAKOUT_STOP = 0.97                # 突破停損：突破價 × 0.97
MARKET_INDEX_CH = "tse_t00.tw"
MARKET_INDEX_CODE = "t00"

# ---- 買進檢查 ----
MAX_STOP_PCT = 5.0                  # 停損距離上限（%）
MAX_DAY_CHG_PCT = 7.0               # 今日漲幅上限（%）
CHASE_OK_PCT = 3.0                  # 💥：站上均線後漲幅 ≤ 3% 可考慮
CHASE_NO_PCT = 5.0                  # 💥：> 5% 不追

# ---- MIS 即時行情抓取 ----
MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
MIS_CHUNK_SIZE = 40              # 一批 40 檔（含加權指數）
MIS_GROUPS_PER_CHUNK = 1         # 每批拆成幾組；1 = 不拆（v39 是 3）
MIS_GROUP_SLEEP = 0.3            # 同一批內，每組之間暫停秒數
MIS_MAX_CONCURRENCY = 2          # 同時進行的「批」數（v39 是 8）
MIS_REQUEST_TIMEOUT = 15         # 單一請求逾時秒數（一批 40 檔）
MIS_MAX_RETRIES = 2              # 失敗後最多重試次數（總共最多嘗試 3 次）
MIS_RETRY_PAUSE = 0.5            # 重試前暫停秒數
MIS_ROUND_DEADLINE = 28.0        # 每次抓「一半」的時間預算（秒）；超過就不再送新請求。0 = 不限制
MIS_KEEPALIVE = 15               # 連線閒置多久就主動關閉（避免重用已被伺服器關掉的連線）
MIS_URL_SUFFIX = "&json=1&delay=0"   # 實驗性：網路上常見的 MIS 範例參數；設成 "" 就是 v40 的網址
MIS_INDEX_URL = "https://mis.twse.com.tw/stock/index.jsp"
MIS_WARMUP = True                # 實驗性：建立連線池後先打開 index.jsp 取得 cookie
MIS_FAIL_RATIO = 0.5             # 一輪裡失敗（含被中止/超過預算）的組數比例 ≥ 這個值，就視為「失敗輪」
MIS_ABORT_AFTER_FAILS = 4        # 一輪裡已有這麼多組失敗、而且沒有任何一組成功，就中止這一輪
MIS_BACKOFF_BASE = 60            # 失敗輪之後先暫停幾秒；連續失敗就加倍
MIS_BACKOFF_MAX = 300            # 暫停上限（秒）
MIS_ALERT_AFTER_FAILS = 2        # 連續幾輪失敗才發 Telegram 警告

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

MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE = 9, 0
MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE = 13, 35

DEBUG_STOCK_CODES = {"4931", "5347"}

TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
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
MIS_HEADERS = {
    "User-Agent": OFFICIAL_HEADERS["User-Agent"],
    "Referer": "https://mis.twse.com.tw/stock/index.jsp",
    "Accept": "application/json",
}

STOCK_CODE_RE = re.compile(r"^[1-9]\d{3}$")
EXCLUDE_NAME_KEYWORDS = ["ETF", "期", "權", "存託", "反1", "正2", "債", "基金", "受益", "指數"]

VOL_CURVE = [
    (15, 0.10), (30, 0.16), (60, 0.27), (90, 0.36), (120, 0.44),
    (150, 0.52), (180, 0.60), (210, 0.69), (240, 0.80), (270, 1.00),
]

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

# 通知名稱、基本星等（0=👀 1=⭐ 2=⭐⭐ 3=⭐⭐⭐）
KIND_NAME = {
    "pullback": "🎯 回檔買點",
    "breakout": "🏔️ 突破新高",
    "confirm": "💥 量能確認",
    "short": "🚀 盤中新納入・短線轉強股",
    "above": "📈 盤中站上均線",
}
KIND_BASE_LEVEL = {"pullback": 3, "breakout": 2, "confirm": 1, "short": 1, "above": 0}
STAR_TEXT = {3: ("⭐⭐⭐", "建議買進"), 2: ("⭐⭐", "可考慮買進"), 1: ("⭐", "可小部位試單"), 0: ("👀", "觀察，不買")}

STOCK_STATES = {}
ALERT_QUEUE = []
UNRELIABLE_MARKETS = set()
STRONG_DAY = {"count": 0}                 # 今日已發出的回檔買點 + 突破新高 數
SHORT_DAY = {"count": 0}                  # 今日 🚀 盤中新納入通知數
ABOVE_DAY = {"count": 0}                  # 今日一般股票 📈 通知數
CONFIRM_DAY = {"count": 0}                # 今日一般股票 💥 通知數
SIGNAL_LOG = []                           # 今日所有通知（收盤後輸出 CSV）
MARKET = {"sum19": None, "price": None, "ok": None, "ma20": None, "prev_close": None}
STRONG_BASE = {}                          # 全市場（流動性合格）強勢股計算基準
LAST_PRICE = {}                           # 每檔最新成交價


def now_tw() -> datetime:
    return datetime.now(TW_TZ)


def _market_close_today(dt: datetime) -> datetime:
    return dt.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)


def _market_open_today(dt: datetime) -> datetime:
    return dt.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)


def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5


def _normalize_industry_code(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    try:
        return str(int(raw))
    except ValueError:
        return raw


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
# 櫃買中心產業價值鏈
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
    print(
        f"[產業鏈] 快取 {len(codes) - len(todo)} 檔有效，需補查 {len(todo)} 檔"
        f"（本次最多 {IC_FETCH_BUDGET_SECONDS} 秒）",
        flush=True,
    )
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
        print(
            f"[產業鏈] 補查完成：取得 {fetched_ok} 檔、平台未收錄 {not_listed} 檔、"
            f"失敗 {failed} 檔，耗時 {time.time() - started:.1f}s",
            flush=True,
        )
        if layout_errors:
            send_telegram("⚠️ <b>【飆股雷達】</b> 櫃買中心產業價值鏈平台查詢失敗，本次族群改用官方產業別顯示。")
    return {c: cache[c]["chains"] for c in codes if c in cache and cache[c].get("chains")}


def _chain_label(chains, top_n=IC_LABEL_TOP_N) -> str:
    return "、".join(f"{top}>{sub}" for top, sub in chains[:top_n])


# =========================
# 盤後清單載入：左側潛力庫 / 強勢股基準 / 短線轉強股
# =========================
def _load_pool_file(path: pathlib.Path, label: str, stock_bases: dict):
    """讀取盤後檔案；基準日必須與本次預載相同。回傳 (data 或 None, 說明文字)。"""
    if not path.exists():
        print(f"[{label}] 找不到 {path.name}，本次不使用", flush=True)
        return None, f"{label}：未載入（尚未產生）"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[{label}] 讀取失敗：{type(exc).__name__}: {repr(exc)}", flush=True)
        return None, f"{label}：未載入（檔案讀取失敗）"
    list_asof = str(data.get("asof", ""))
    base_asof = max(b["asof_date"] for b in stock_bases.values()).isoformat()
    if list_asof != base_asof:
        print(f"[{label}] 基準日 {list_asof} 與本次預載基準日 {base_asof} 不同，本次不使用", flush=True)
        return None, f"{label}：未載入（檔案日期 {list_asof or '不明'}，與基準日 {base_asof} 不符）"
    return data, ""


def load_left_watchlist(stock_bases: dict) -> str:
    data, msg = _load_pool_file(LEFT_WATCHLIST_FILE, "左側潛力庫", stock_bases)
    if data is None:
        return msg
    hit = new = illiquid = 0
    for code, info in (data.get("stocks") or {}).items():
        base = stock_bases.get(code)
        if base is None:
            continue
        base["left"] = {
            "strategy": info.get("strategy", ""),
            "added": info.get("added", data.get("asof", "")),
            "days_in": int(info.get("days_in", 1)),
            "tangle_pct": info.get("tangle_pct"),
            "tangle_now": info.get("tangle_now"),
            "tangle_streak": info.get("tangle_streak"),
        }
        hit += 1
        if base["left"]["days_in"] == 1:
            new += 1
        if base["avg_turnover_5d"] < MIN_LIQ_TURNOVER_5D:
            illiquid += 1
    print(f"[左側潛力庫] 載入 {hit} 檔（新入選 {new} 檔，未達流動性底線 {illiquid} 檔）", flush=True)
    text = f"🧭 左側潛力庫：{hit} 檔（新入選 {new} 檔），優先監控"
    if illiquid:
        text += f"；其中 {illiquid} 檔 5日均成交額 < 1億，不發站上均線通知"
    return text


def load_strong_base(stock_bases: dict) -> str:
    """讀取 strong_stock_pool.json：全市場計算基準、🔥 強勢股、🚀 短線轉強、🎯 回檔觀察池、大盤資料。"""
    STRONG_BASE.clear()
    data, msg = _load_pool_file(STRONG_POOL_FILE, "強勢股/短線轉強股", stock_bases)
    if data is None:
        MARKET["sum19"] = None
        return msg + "；大盤濾網：未啟用"
    taiex = data.get("taiex") or {}
    MARKET["sum19"] = taiex.get("sum19")
    MARKET["prev_close"] = taiex.get("close")
    market_txt = (
        f"大盤濾網：加權指數需站上 MA20（前一日 MA20 {taiex.get('ma20', 0):,.0f}）"
        if MARKET["sum19"] else "大盤濾網：資料不足未啟用"
    )
    if data.get("rule") != POOL_RULE_ID:
        print(f"[強勢股/短線轉強股] 清單規則版本 {data.get('rule')!r} 與雷達 {POOL_RULE_ID!r} 不符，"
              f"不載入 🚀/🔥/🎯；請先重跑「左側潛力庫盤後篩選」（left_side.yml）", flush=True)
        return (f"🚀🔥🎯 盤後清單為舊規則（{data.get('rule') or '無版本'}），本次不載入，盤中納入暫停；"
                f"請先重跑盤後選股；{market_txt}")
    for code, info in (data.get("base") or {}).items():
        if code in stock_bases:
            STRONG_BASE[code] = info
    strong_n = short_n = watch_n = sticky = 0
    for code, info in (data.get("stocks") or {}).items():
        base = stock_bases.get(code)
        if base is None:
            continue
        base["strong"] = {**info, "source": "盤後"}
        strong_n += 1
        sticky += info.get("status") == "🧲黏著"
    for code, info in (data.get("short_stocks") or {}).items():
        base = stock_bases.get(code)
        if base is None:
            continue
        base["short"] = {**info, "source": "盤後"}
        short_n += 1
    for code, info in (data.get("watch_stocks") or {}).items():
        base = stock_bases.get(code)
        if base is None:
            continue
        base["watch"] = {**info, "source": "盤後"}
        watch_n += 1
    print(f"[強勢股/短線轉強股] 計算基準 {len(STRONG_BASE)} 檔，盤後清單：🔥 {strong_n}（黏著 {sticky}）、"
          f"🚀 {short_n}、🎯 觀察池 {watch_n}；{market_txt}", flush=True)
    return (f"🚀 短線轉強股：盤後清單 {short_n} 檔（站上 {SHORT_MIN_STREAK}~{SHORT_MAX_STREAK} 日、法人連買 ≥{SHORT_MIN_INST_DAYS} 日）"
            f"｜🎯 回檔觀察池 {watch_n} 檔｜🔥 強勢股池 {strong_n} 檔（RS 前 {RS_TOP_RATIO:.0%}，含 🧲黏著 {sticky} 檔）\n"
            f"　盤中即時判斷 {len(STRONG_BASE)} 檔（流動性合格），新符合者自動納入；"
            f"🎯/🏔️ 每日最多 {STRONG_MAX_PER_DAY} 則；{market_txt}")


# =========================
# 狀態與 CSV
# =========================
_STATE_KEYS = ("status", "below_ma5_count", "alert_price", "alert_ratio", "alert_turnover",
               "alert_time", "confirm_sent", "strong_sent", "short_sent")


def _new_state(status="BELOW"):
    return {"status": status, "last_notified": None, "below_ma5_count": 0,
            "alert_price": None, "alert_ratio": None, "alert_turnover": None, "alert_time": None,
            "confirm_sent": False, "strong_sent": [], "short_sent": False}


def load_states() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if raw.get("_date") != now_tw().strftime("%Y-%m-%d"):
        return {}
    STRONG_DAY["count"] = int(raw.get("_strong_count", 0))
    SHORT_DAY["count"] = int(raw.get("_short_count", 0))
    ABOVE_DAY["count"] = int(raw.get("_above_count", 0))
    CONFIRM_DAY["count"] = int(raw.get("_confirm_count", 0))
    SIGNAL_LOG[:] = list(raw.get("_signals") or [])
    result = {}
    for code, value in raw.items():
        if code.startswith("_") or not isinstance(value, dict):
            continue
        last = datetime.fromisoformat(value["last_notified"]) if value.get("last_notified") else None
        if last and last.tzinfo is None:
            last = last.replace(tzinfo=TW_TZ)
        state = _new_state(value.get("status", "BELOW"))
        state["last_notified"] = last
        for key in _STATE_KEYS:
            if key in value and key != "status":
                state[key] = value[key]
        state["below_ma5_count"] = int(state.get("below_ma5_count") or 0)
        state["strong_sent"] = list(state.get("strong_sent") or [])
        state["short_sent"] = bool(state.get("short_sent"))
        result[code] = state
    return result


def save_states(states: dict) -> None:
    raw = {"_date": now_tw().strftime("%Y-%m-%d"), "_strong_count": STRONG_DAY["count"],
           "_short_count": SHORT_DAY["count"], "_above_count": ABOVE_DAY["count"],
           "_confirm_count": CONFIRM_DAY["count"], "_signals": SIGNAL_LOG}
    for code, value in states.items():
        item = {k: value.get(k) for k in _STATE_KEYS}
        item["last_notified"] = value["last_notified"].isoformat() if value.get("last_notified") else None
        raw[code] = item
    STATE_FILE.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


CSV_FIELDS = [
    "代號", "名稱", "市場", "產業族群", "最新收盤價", "MA5", "MA10", "MA20",
    "最新成交量日期", "最新成交量(張)", "5日均量(張)", "5日均量區間",
    "訊號用均量(張,排除最大量)", "已站上均線",
]


def export_stock_bases_csv(stock_bases: dict) -> pathlib.Path:
    asof = max(base["asof_date"] for base in stock_bases.values()) if stock_bases else None
    rows = []
    for code, base in stock_bases.items():
        rows.append({
            "代號": code,
            "名稱": base["name"],
            "市場": "上市" if base["market"] == "tse" else "上櫃",
            "產業族群": base["industry"],
            "最新收盤價": round(base["yest_close"], 2),
            "MA5": round(base["true_ma5"], 2),
            "MA10": round(base["true_ma10"], 2),
            "MA20": round(base["true_ma20"], 2),
            "最新成交量日期": base["asof_date"].isoformat(),
            "最新成交量(張)": int(round(base["yest_vol"])),
            "5日均量(張)": int(round(base["avg_5d_vol_simple"])),
            "5日均量區間": base["vol5_range"],
            "訊號用均量(張,排除最大量)": int(round(base["avg_5d_vol"])),
            "已站上均線": "是" if base.get("already_above") else "否",
        })
    rows.sort(key=lambda row: (row["市場"] != "上市", row["代號"]))
    with WATCHLIST_CSV_FILE.open("w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerow([f"資料基準日：{asof.isoformat() if asof else ''}"])
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return WATCHLIST_CSV_FILE


# =========================
# 官方歷史資料
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
    if not table:
        return {}
    fields = [str(f).strip() for f in table.get("fields", [])]

    def index_of(keywords):
        return next((i for i, field in enumerate(fields) if any(k in field for k in keywords)), None)

    code_i = index_of(["證券代號", "股票代號", "代號"])
    name_i = index_of(["證券名稱", "股票名稱", "名稱"])
    close_i = index_of(["收盤價", "收盤"])
    volume_i = index_of(["成交股數"])
    if code_i is None or close_i is None or volume_i is None:
        print(f"[警告] 官方表格欄位無法辨識：{fields}", flush=True)
        return {}
    result = {}
    for row in table.get("data", []):
        try:
            code = str(row[code_i]).strip()
            name = str(row[name_i]).strip() if name_i is not None else code
            close = _to_float(row[close_i])
            volume = _to_float(row[volume_i])
        except (ValueError, TypeError, IndexError):
            continue
        if STOCK_CODE_RE.fullmatch(code) and close > 0 and not _is_excluded_name(name):
            result[code] = {"name": name, "close": close, "volume_shares": volume}
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
        print(
            f"[{name}] 抓取失敗日期 {', '.join(d.isoformat() for d in failed_dates)} "
            f"不在最近{MA_WINDOW}個交易日內，不影響均線/均量",
            flush=True,
        )


def _trimmed_avg_vol(v5: list) -> float:
    if not v5:
        return 0.0
    if len(v5) < 5:
        return float(sum(v5)) / len(v5)
    trimmed = sorted(v5)[:-1]
    return float(sum(trimmed)) / len(trimmed)


def load_all_stock_bases() -> dict:
    started = time.time()
    now = now_tw()
    today = now.date()
    include_today = _is_weekday(now) and now > _market_close_today(now)

    candidate_dates = [today] if include_today else []
    candidate_dates += [
        today - timedelta(days=i)
        for i in range(1, LOOKBACK_CALENDAR_DAYS + 1)
        if (today - timedelta(days=i)).weekday() < 5
    ]
    print(
        f"候選交易日上限 {len(candidate_dates)}，目標 {TARGET_TRADING_DAYS} 個有效交易日 — "
        + ("已過收盤時間，嘗試納入今日官方資料" if include_today else "僅使用今日以前的歷史資料"),
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
                rec["days"][day] = (item["close"], item["volume_shares"])

    industry_map = _fetch_industry_map()
    stock_bases = {}
    excluded = 0
    already_above_count = 0

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
        # 近 5 日平均成交額（元）= 收盤價 × 成交股數 的平均，第 2 階段流動性底線用
        turnover_5d = sum(rec["days"][d][0] * rec["days"][d][1] for d in d5) / len(d5)

        true_ma5 = sum(c20[-5:]) / 5
        true_ma10 = sum(c20[-10:]) / 10
        true_ma20 = sum(c20) / 20
        asof_close = c20[-1]
        already_above = (
            asof_close > true_ma5 * ABOVE_MA_BUF
            and asof_close > true_ma10 * ABOVE_MA_BUF
            and asof_close > true_ma20 * ABOVE_MA_BUF
        )
        if already_above:
            already_above_count += 1

        official = industry_map.get(code, "")
        stock_bases[code] = {
            "code": code,
            "name": rec["name"],
            "market": rec["market"],
            "official_industry": official,
            "industry": official,
            "industry_source": "官方產業別",
            "mis_ch": f"{rec['market']}_{code}.tw",
            "sum_4": float(sum(c20[-4:])),
            "sum_9": float(sum(c20[-9:])),
            "sum_19": float(sum(c20[-19:])),
            "true_ma5": true_ma5,
            "true_ma10": true_ma10,
            "true_ma20": true_ma20,
            "asof_date": last_date,
            "yest_close": asof_close,
            "already_above": already_above,
            "yest_vol": v5_lots[-1],
            "avg_5d_vol_simple": sum(v5_lots) / len(v5_lots),
            "avg_5d_vol": _trimmed_avg_vol(v5_lots),
            "avg_turnover_5d": turnover_5d,
            "vol5_range": f"{d5[0].isoformat()}~{d5[-1].isoformat()}",
        }

    tse_count = sum(base["market"] == "tse" for base in stock_bases.values())
    liquid = sum(1 for b in stock_bases.values() if b["avg_turnover_5d"] >= MIN_LIQ_TURNOVER_5D)
    print(
        f"預載完成：{len(stock_bases)} 檔（上市 {tse_count} / 上櫃 {len(stock_bases) - tse_count}），"
        f"歷史不足排除 {excluded} 檔，基準日已站上5/10/20均線 {already_above_count} 檔，"
        f"5日均成交額 ≥ 1億 {liquid} 檔，耗時 {time.time()-started:.1f}s",
        flush=True,
    )
    return stock_bases


def apply_ic_chains(stock_bases: dict, chain_map: dict) -> None:
    hit = 0
    for code, base in stock_bases.items():
        chains = chain_map.get(code)
        if chains:
            base["industry"] = _chain_label(chains)
            base["industry_source"] = "櫃買產業價值鏈"
            hit += 1
    print(f"[產業鏈] 採用櫃買產業鏈 {hit} 檔，其餘 {len(stock_bases) - hit} 檔使用官方產業別", flush=True)


# =========================
# MIS 即時行情
# =========================
def _opt_float(text):
    try:
        value = float(str(text).replace(",", ""))
        return value if value > 0 else None
    except (ValueError, TypeError):
        return None


class MisBadStatus(Exception):
    """MIS 回應的 HTTP 狀態碼不是 200。"""


class MisEmptyResponse(Exception):
    """HTTP 200，但 msgArray 是空的（常見於被限流/擋掉時）。"""


# 可以重試的例外。ServerDisconnectedError 是 ClientConnectionError 的子類，這裡列出來是為了可讀性。
MIS_RETRYABLE = (
    aiohttp.ServerDisconnectedError,
    aiohttp.ClientConnectionError,   # 含 ClientOSError、ClientConnectorError（Connection reset by peer）
    aiohttp.ClientPayloadError,
    asyncio.TimeoutError,
    MisBadStatus,
    MisEmptyResponse,
    ValueError,                      # 回應不是合法 JSON（例如 HTML 錯誤頁）
)

# 全域唯一的連線池：第一次抓取時建立，之後每輪共用，收盤時關閉
_MIS_SESSION = None
_MIS_SESSION_LOOP = None
MIS_SESSIONS_CREATED = 0             # 累計建立過幾個 session（正常一天應該只有 1）
MIS_LAST_STATS = {}                  # 最近一輪的統計


async def _warm_up_mis(session) -> None:
    """實驗性：先打開 index.jsp，讓這個連線池取得 cookie。失敗只印一行，不影響後續抓取。"""
    if not MIS_WARMUP:
        return
    try:
        async with session.get(MIS_INDEX_URL, timeout=aiohttp.ClientTimeout(total=5)) as response:
            await response.read()
            print(f"[MIS] 已建立連線池並預熱 index.jsp（HTTP {response.status}，cookie {len(session.cookie_jar)} 個）", flush=True)
    except Exception as exc:
        print(f"[MIS] 預熱 index.jsp 失敗（略過，不影響抓取）: {type(exc).__name__}: {exc}", flush=True)


async def get_mis_session() -> aiohttp.ClientSession:
    """取得全域唯一的 ClientSession；第一次呼叫（或連線池被重建、事件迴圈換了）才建立。"""
    global _MIS_SESSION, _MIS_SESSION_LOOP, MIS_SESSIONS_CREATED
    loop = asyncio.get_running_loop()
    if _MIS_SESSION is None or _MIS_SESSION.closed or _MIS_SESSION_LOOP is not loop:
        connector = aiohttp.TCPConnector(
            limit=MIS_MAX_CONCURRENCY + 2,
            limit_per_host=MIS_MAX_CONCURRENCY + 2,
            keepalive_timeout=MIS_KEEPALIVE,
            ttl_dns_cache=300,
        )
        _MIS_SESSION = aiohttp.ClientSession(connector=connector, headers=MIS_HEADERS)
        _MIS_SESSION_LOOP = loop
        MIS_SESSIONS_CREATED += 1
        await _warm_up_mis(_MIS_SESSION)
    return _MIS_SESSION


async def close_mis_session() -> None:
    """收盤或程式結束時呼叫一次。"""
    global _MIS_SESSION
    if _MIS_SESSION is not None and not _MIS_SESSION.closed:
        await _MIS_SESSION.close()
    _MIS_SESSION = None


def _split_groups(items: list, n: int) -> list:
    """把 items 平均拆成 n 組（例如 40 檔 → 14/13/13）。不會產生空組。"""
    if not items:
        return []
    n = max(1, min(n, len(items)))
    base, extra = divmod(len(items), n)
    groups, i = [], 0
    for k in range(n):
        size = base + (1 if k < extra else 0)
        groups.append(items[i:i + size])
        i += size
    return groups


def _parse_mis_msg_array(data: dict) -> dict:
    """只用成交價 z；加權指數只取價格；其餘股票要有累計量 v。"""
    parsed = {}
    for item in data.get("msgArray", []):
        code = str(item.get("c", "")).strip()
        price = _opt_float(item.get("z"))
        if price is None:
            continue
        if code == MARKET_INDEX_CODE:
            parsed[code] = {"price": price, "volume": 0.0}
            continue
        volume = _opt_float(item.get("v"))
        if volume is None:
            continue
        parsed[code] = {
            "price": price,
            "volume": volume,
            "open": _opt_float(item.get("o")),
            "high": _opt_float(item.get("h")),
            "low": _opt_float(item.get("l")),
        }
    return parsed


def _new_mis_stats() -> dict:
    return {"groups": 0, "ok_groups": 0, "retries": 0,
            "failed_groups": 0, "failed_stocks": 0,
            "skipped_groups": 0, "skipped_stocks": 0,        # 超過本輪時間預算而沒送
            "aborted_groups": 0, "aborted_stocks": 0,        # 前幾組全部失敗 → 中止本輪而沒送
            "abort": False, "no_price_stocks": 0, "rt": Counter(),
            "errors": Counter(), "returned": 0, "expected": 0, "elapsed": 0.0,
            "fail_ratio": 0.0, "failed_round": False}


def _parse_mis_response(data, group: list, stats: dict) -> dict:
    """解析一組的回應。msgArray 空 → 丟 MisEmptyResponse（會重試）；同時記錄 rtcode/rtmessage 與「沒有成交價」的檔數。"""
    if not isinstance(data, dict):
        raise ValueError("回應不是 JSON 物件")
    rtcode = str(data.get("rtcode", "")).strip()
    rtmsg = str(data.get("rtmessage", "")).strip()
    if rtmsg and rtmsg.upper() != "OK":
        stats["rt"][f"{rtcode or '?'}:{rtmsg}"] += 1
    items = data.get("msgArray") or []
    if not items:
        raise MisEmptyResponse(f"msgArray 為空（rtcode={rtcode or '無'}，rtmessage={rtmsg or '無'}）")
    parsed = _parse_mis_msg_array(data)
    stats["no_price_stocks"] += max(0, len(items) - len(parsed))   # 有回應但沒有成交價 z（'-'）的檔數
    return parsed


async def _fetch_mis_group(session, group: list, stats: dict, deadline):
    """送出一組。失敗回傳 {}（該組本輪缺資料），不丟例外。"""
    stats["groups"] += 1
    url = MIS_URL + "?ex_ch=" + "|".join(group) + MIS_URL_SUFFIX
    for attempt in range(MIS_MAX_RETRIES + 1):
        if stats["abort"]:                       # 前幾組全部失敗：不要再往已經拒絕我們的伺服器送請求
            stats["aborted_groups"] += 1
            stats["aborted_stocks"] += len(group)
            return {}
        if deadline is not None and time.monotonic() >= deadline:
            stats["skipped_groups"] += 1
            stats["skipped_stocks"] += len(group)
            return {}
        timeout = MIS_REQUEST_TIMEOUT
        if deadline is not None:
            timeout = max(1.0, min(MIS_REQUEST_TIMEOUT, deadline - time.monotonic()))
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                if response.status != 200:
                    raise MisBadStatus(f"HTTP {response.status}")
                data = await response.json(content_type=None)
            parsed = _parse_mis_response(data, group, stats)
            stats["ok_groups"] += 1
            return parsed
        except MIS_RETRYABLE as exc:
            stats["errors"][type(exc).__name__] += 1
            if attempt < MIS_MAX_RETRIES:
                stats["retries"] += 1
                await asyncio.sleep(MIS_RETRY_PAUSE)
    stats["failed_groups"] += 1
    stats["failed_stocks"] += len(group)
    if stats["ok_groups"] == 0 and stats["failed_groups"] >= MIS_ABORT_AFTER_FAILS:
        stats["abort"] = True
    return {}


async def _fetch_mis_chunk(session, semaphore, chunk: list, stats: dict, deadline) -> dict:
    """一批（≤40 檔）依 MIS_GROUPS_PER_CHUNK 拆組（預設 1 = 不拆），組與組之間 sleep。整批佔用 1 個併發名額。"""
    out = {}
    async with semaphore:
        for i, group in enumerate(_split_groups(chunk, MIS_GROUPS_PER_CHUNK)):
            if i > 0:
                await asyncio.sleep(MIS_GROUP_SLEEP)
            out.update(await _fetch_mis_group(session, group, stats, deadline))
    return out


async def poll_all_mis(chunks):
    """抓取一半的批次，回傳 {代號: {price, volume, open, high, low}}。本輪統計放在 MIS_LAST_STATS。"""
    session = await get_mis_session()
    stats = _new_mis_stats()
    stats["expected"] = sum(len(c) for c in chunks)
    started = time.monotonic()
    deadline = started + MIS_ROUND_DEADLINE if MIS_ROUND_DEADLINE else None
    semaphore = asyncio.Semaphore(MIS_MAX_CONCURRENCY)
    results = await asyncio.gather(
        *[_fetch_mis_chunk(session, semaphore, chunk, stats, deadline) for chunk in chunks],
        return_exceptions=True,
    )
    combined = {}
    for result in results:
        if isinstance(result, dict):
            combined.update(result)
        else:   # 非預期例外：不讓整輪掃描中斷
            stats["errors"]["未預期:" + type(result).__name__] += 1
    stats["returned"] = len(combined)
    stats["elapsed"] = time.monotonic() - started
    lost = stats["failed_stocks"] + stats["skipped_stocks"] + stats["aborted_stocks"]
    stats["fail_ratio"] = lost / stats["expected"] if stats["expected"] else 0.0
    stats["failed_round"] = bool(stats["expected"]) and stats["fail_ratio"] >= MIS_FAIL_RATIO
    MIS_LAST_STATS.clear()
    MIS_LAST_STATS.update(stats)
    if (stats["retries"] or stats["failed_groups"] or stats["skipped_groups"] or stats["aborted_groups"]
            or len(stats["errors"]) or len(stats["rt"])):
        errs = "、".join(f"{name}×{n}" for name, n in stats["errors"].most_common())
        rts = "、".join(f"{name}×{n}" for name, n in stats["rt"].most_common())
        print(
            f"[MIS] 本輪 {stats['groups']} 組請求（成功 {stats['ok_groups']}）：重試 {stats['retries']} 次（{errs or '無'}）；"
            f"失敗 {stats['failed_groups']} 組（{stats['failed_stocks']} 檔）；"
            f"超過時間預算未送 {stats['skipped_groups']} 組（{stats['skipped_stocks']} 檔）；"
            f"連續失敗而中止未送 {stats['aborted_groups']} 組（{stats['aborted_stocks']} 檔）；"
            f"伺服器訊息：{rts or '無'}；有回應但無成交價 {stats['no_price_stocks']} 檔；"
            f"失敗比例 {stats['fail_ratio']:.0%}{'（失敗輪）' if stats['failed_round'] else ''}；"
            f"耗時 {stats['elapsed']:.1f}s",
            flush=True,
        )
    return combined


def update_market_filter(snapshot: dict) -> None:
    """用加權指數即時價與前 19 日收盤，判斷大盤是否在 MA20 之上。"""
    idx = snapshot.get(MARKET_INDEX_CODE)
    if not MARKET["sum19"] or not idx:
        return
    MARKET["price"] = idx["price"]
    ma20 = (MARKET["sum19"] + idx["price"]) / 20
    ok = idx["price"] > ma20
    if MARKET["ok"] is not None and ok != MARKET["ok"]:
        print(f"[大盤濾網] 加權指數 {idx['price']:,.2f} {'站上' if ok else '跌破'} MA20 {ma20:,.2f}", flush=True)
    MARKET["ok"] = ok
    MARKET["ma20"] = ma20


def market_allows_strong() -> bool:
    if not MARKET["sum19"]:
        return True   # 資料不足時不啟用濾網
    return bool(MARKET["ok"])


# =========================
# 第 4、5 階段：🚀 短線轉強股 / 🔥 強勢股池（盤中即時判斷）
# =========================
def _realtime_mas(base: dict, price: float):
    return ((base["sum_4"] + price) / 5, (base["sum_9"] + price) / 10, (base["sum_19"] + price) / 20)


def _strong_eval(base: dict, sb: dict, price: float):
    """🔥 強勢股條件（不含 RS 排名）。回傳 info dict 或 None。"""
    ma5, ma10, ma20 = _realtime_mas(base, price)
    if not (ma5 > ma10 > ma20 and ma20 > sb["ma20_prev5"]):
        return None
    if price > ma20 * STRONG_MAX_EXT or not sb.get("vol_ok"):
        return None
    high60 = max(sb["high60"], price)
    low60 = min(sb["low60"], price)
    dist_high = (high60 - price) / high60
    rebound = price / low60 - 1
    if dist_high <= TREND_NEAR_HIGH:
        kind = "趨勢型"
    elif (rebound >= REVERSAL_REBOUND and sb.get("vol_expand")
          and (price > sb["high20"] or price > (sb.get("rebound_high") or float("inf")))):
        # 反轉型：自 60 日低點反彈 ≥25%、近20日均量放大，且突破近 20 日高點或反彈過程高點
        kind = "反轉型"
    else:
        return None
    return {
        "type": kind,
        "high20": sb["high20"],
        "high60": high60,
        "low60": low60,
        "dist_high_pct": round(dist_high * 100, 2),
        "rebound_pct": round(rebound * 100, 2),
        "inst_plus": sb.get("inst_plus", False),
    }


def _short_eval(base: dict, sb: dict, price: float):
    """🚀 進池條件：今日為站上 MA5/10/20 第 3~5 日、MA5>MA10、截至 T-1 日外資或投信連續買超 ≥2 日。
    盤後算好「截至基準日的連續站上天數」，加上今日即時價也站上即時均線，今日就是第 N+1 日。"""
    prev_streak = sb.get("above_streak")
    if prev_streak is None:
        return None
    today_streak = prev_streak + 1
    if not (SHORT_MIN_STREAK <= today_streak <= SHORT_MAX_STREAK):
        return None
    if (sb.get("inst_streak") or 0) < SHORT_MIN_INST_DAYS:
        return None
    ma5, ma10, ma20 = _realtime_mas(base, price)
    if not (price > ma5 and price > ma10 and price > ma20 and ma5 > ma10):
        return None
    high60 = max(sb["high60"], price)
    low60 = min(sb["low60"], price)
    return {
        "days_on_ma": today_streak,
        "in_pool_days": 1,
        "inst_streak": sb.get("inst_streak", 0),
        "foreign_streak": sb.get("foreign_streak", 0),
        "trust_streak": sb.get("trust_streak", 0),
        "inst_plus": sb.get("inst_plus", False),
        "ext_pct": round((price / ma20 - 1) * 100, 2),
        "dist_high_pct": round((high60 - price) / high60 * 100, 2),
        "rebound_pct": round((price / low60 - 1) * 100, 2),
    }


def _admit_short(stock_bases: dict, code: str, info: dict, ret_of: dict) -> bool:
    """🚀 池容量 SHORT_POOL_MAX：滿了就比較 20 日漲幅 RS，較強者擠掉最弱的一檔。"""
    members = [c for c, b in stock_bases.items() if b.get("short")]
    if len(members) >= SHORT_POOL_MAX:
        weakest = min(members, key=lambda c: ret_of.get(c, -9.0))
        if ret_of.get(code, -9.0) <= ret_of.get(weakest, -9.0):
            return False
        stock_bases[weakest]["short"] = None
        print(f"[短線轉強] 池滿 {SHORT_POOL_MAX} 檔：{weakest} {stock_bases[weakest]['name']} RS 最弱，被 {code} 取代", flush=True)
    return True


def update_pools(stock_bases: dict, snapshot: dict):
    """每輪用即時價更新：
    - 🚀 短線轉強股：符合進池條件即納入（當天保留；盤後清單的股票開盤就在池內），並發「盤中新納入」通知
    - 🔥 強勢股池：即時 20 日漲幅排名前 15% 且符合條件者納入（當天保留）
    回傳 (本輪新納入強勢股數, 本輪新納入短線轉強股數)。"""
    if not STRONG_BASE:
        return 0, 0
    for code, real in snapshot.items():
        if code in STRONG_BASE:
            LAST_PRICE[code] = real["price"]
    # 只用「今天已有成交價」的股票排名，不用昨收價冒充（開盤初期很多股票還沒成交）
    rets = []
    for code, sb in STRONG_BASE.items():
        if code not in stock_bases or not sb.get("close_20ago") or code not in LAST_PRICE:
            continue
        price = LAST_PRICE[code]
        rets.append((code, price / sb["close_20ago"] - 1, price))
    rets.sort(key=lambda x: -x[1])
    ret_of = {code: r for code, r, _ in rets}
    total = len(rets)

    # ---- 🚀 ----
    new_short = 0
    now = now_tw()
    # 只評估「本輪有抓到價格」的股票：全市場分上下半輪流抓，若評估上一輪的舊價格，股票會被納入但本輪沒有報價可發通知
    for code in snapshot:
        sb = STRONG_BASE.get(code)
        base = stock_bases.get(code)
        price = LAST_PRICE.get(code)
        if sb is None or base is None or price is None or base.get("short") or base.get("watch"):
            continue
        info = _short_eval(base, sb, price)
        if info is None:
            continue
        if not _admit_short(stock_bases, code, info, ret_of):
            continue
        info.update({"source": "盤中新納入", "status": "盤中新納入", "added": now.strftime("%Y-%m-%d"),
                     "also_strong": bool(base.get("strong"))})
        base["short"] = info
        new_short += 1
        print(f"[短線轉強] 盤中新納入 {code} {base['name']}（站上均線第 {info['days_on_ma']} 日，"
              f"法人連買 {info['inst_streak']} 日，距MA20 {info['ext_pct']:+.1f}%）", flush=True)
        state = STOCK_STATES.setdefault(code, _new_state())
        if (not state.get("short_sent") and SHORT_DAY["count"] < SHORT_ENTRY_MAX_PER_DAY
                and base["market"] not in UNRELIABLE_MARKETS and code in snapshot):
            state["short_sent"] = True
            SHORT_DAY["count"] += 1
            _short_entry_alert(base, snapshot[code], now)

    # ---- 🔥 ----
    new_strong = 0
    if total >= max(30, int(len(STRONG_BASE) * RS_MIN_COVERAGE)):
        top_n = max(1, int(total * RS_TOP_RATIO))
        for rank, (code, ret20, price) in enumerate(rets[:top_n], 1):
            base = stock_bases[code]
            info = _strong_eval(base, STRONG_BASE[code], price)
            if info is None:
                continue
            info["ret20_pct"] = round(ret20 * 100, 2)
            info["rs_top_pct"] = round(rank / total * 100, 1)
            if base.get("strong"):
                keep = base["strong"]
                keep.update({k: v for k, v in info.items() if k != "type"})
                continue
            info["source"] = "盤中新納入"
            info["status"] = "盤中新納入"
            base["strong"] = info
            new_strong += 1
            print(f"[強勢股] 盤中新納入 {code} {base['name']}（{info['type']}，20日 {info['ret20_pct']:+.1f}%，"
                  f"排名前 {info['rs_top_pct']:.0f}%）", flush=True)
    return new_strong, new_short


# =========================
# 通知文字、星等與買進檢查
# =========================
def _vol_progress(elapsed_min: float) -> float:
    """台股盤中累計成交量佔全天的比例（經驗 U 型曲線：開盤、尾盤量密集，盤中量疏）。"""
    prev_minute, prev_ratio = 0.0, 0.0
    for minute, ratio in VOL_CURVE:
        if elapsed_min <= minute:
            return prev_ratio + (ratio - prev_ratio) * (elapsed_min - prev_minute) / (minute - prev_minute)
        prev_minute, prev_ratio = minute, ratio
    return 1.0


def _estimates(base: dict, real: dict, now: datetime):
    """回傳 (預估量比, 預估成交額, 實質已成交金額)。"""
    price, cum = real["price"], real["volume"]
    market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    elapsed_min = max(1.0, min(270.0, (now - market_start).total_seconds() / 60))
    est_volume = cum / max(_vol_progress(elapsed_min), 0.03)
    avg = base["avg_5d_vol"]
    return (est_volume / avg if avg > 0 else 0.0), price * est_volume * 1000, price * cum * 1000


def _confirm_window_open(now: datetime) -> bool:
    return now >= _market_open_today(now) + timedelta(minutes=CONFIRM_START_MINUTE)


def _failure_reasons(price, ma5, ma10, ma20, turnover_5d, status):
    reasons = []
    if price <= ma5 * ABOVE_MA_BUF:
        reasons.append(f"未站上MA5({ma5*ABOVE_MA_BUF:.2f})")
    if price <= ma10 * ABOVE_MA_BUF:
        reasons.append(f"未站上MA10({ma10*ABOVE_MA_BUF:.2f})")
    if price <= ma20 * ABOVE_MA_BUF:
        reasons.append(f"未站上MA20({ma20*ABOVE_MA_BUF:.2f})")
    if turnover_5d < MIN_LIQ_TURNOVER_5D:
        reasons.append(f"5日均成交額 {turnover_5d/1e8:.2f}億 < 1億（流動性底線）")
    if status != "BELOW":
        reasons.append(f"狀態為{status}")
    return reasons or ["所有條件符合"]


def _left_line(left: dict) -> str:
    parts = [left["strategy"] or "左側潛力股"]
    streak = left.get("tangle_streak")
    tangle = left.get("tangle_now")
    if tangle is None:
        tangle = left.get("tangle_pct")
    if streak:
        txt = f"均線糾結連續 {streak} 日"
        if tangle is not None:
            txt += f"（{float(tangle):.1f}%）"
        parts.append(txt)
    elif streak == 0:
        parts.append("均線已發散")
    elif tangle is not None:
        parts.append(f"糾結 {float(tangle):.1f}%")
    parts.append(f"入選後第 {left['days_in']} 個交易日（{left['added']} 盤後入選）")
    return "• 🧭 左側潛力庫：" + "｜".join(parts) + "\n"


def _strong_line(info: dict) -> str:
    if info.get("type") == "反轉型":
        txt = (f"🔄 反轉型｜60日低點反彈 +{info.get('rebound_pct', 0):.0f}%"
               f"｜距前高 {info.get('high60', 0):,.2f} 還有 {info.get('dist_high_pct', 0):.1f}%")
    else:
        txt = f"🔥 強勢型｜距60日高點 {info.get('dist_high_pct', 0):.1f}%"
    txt += f"｜20日漲幅 {info.get('ret20_pct', 0):+.1f}%（前 {info.get('rs_top_pct', 0):.0f}%）"
    if info.get("status") == "🧲黏著":
        txt += "｜🧲黏著保留"
    if info.get("inst_plus"):
        txt += "｜法人加分"
    txt += f"｜{info.get('source', '盤後')}"
    return "• 強勢股池：" + txt + "\n"


def _short_line(info: dict) -> str:
    if info.get("source") == "盤中新納入":
        txt = (f"🚀 短線轉強型｜盤中新納入：今日為站上 MA5/10/20 第 {info.get('days_on_ma', '?')} 日"
               f"（提醒窗口 {SHORT_MIN_STREAK}~{SHORT_MAX_STREAK} 日）")
    else:
        txt = (f"🚀 短線轉強型｜入池第 {info.get('in_pool_days', 1)}/{SHORT_POOL_DAYS} 日（{info.get('added', '')} 入池，"
               f"{info.get('origin', '盤後')}）｜站上均線第 {info.get('days_on_ma', '?')} 日")
    txt += f"｜法人連買 {info.get('inst_streak', 0)} 日"
    txt += f"｜距MA20 {info.get('ext_pct', 0):+.1f}%｜距60日高點 {info.get('dist_high_pct', 0):.1f}%"
    return "• 短線轉強：" + txt + "\n"


def _watch_line(info: dict) -> str:
    return (f"• 🎯 回檔專用觀察池：🚀 入池滿 {SHORT_POOL_DAYS} 日後轉入（{info.get('watch_added', '')}），"
            f"第 {info.get('watch_days', 1)}/{WATCH_MAX_DAYS} 日｜距MA20 {info.get('ext_pct', 0):+.1f}%\n")


def _pool_labels(base: dict) -> str:
    labels = []
    if base.get("short"):
        labels.append("🚀 短線轉強型")
    strong = base.get("strong")
    if strong:
        labels.append("🔄 反轉型" if strong.get("type") == "反轉型" else "🔥 強勢型")
    if base.get("watch"):
        labels.append("🔭 回檔觀察型")
    return " + ".join(labels)


def _pool_lines(base: dict) -> str:
    return ((_short_line(base["short"]) if base.get("short") else "")
            + (_watch_line(base["watch"]) if base.get("watch") else "")
            + (_strong_line(base["strong"]) if base.get("strong") else ""))


def _star_level(kind: str, base: dict):
    """回傳 (星等數, 調整說明)。💥/🚀 同時在 🔥 強勢型池 → 升一級；🎯/🏔️ 僅屬 🔄 反轉型 → 降一級。"""
    level = KIND_BASE_LEVEL[kind]
    note = ""
    strong = base.get("strong")
    if kind in ("confirm", "short") and strong and strong.get("type") == "趨勢型":
        level = min(3, level + 1)
        note = f"基本 {STAR_TEXT[KIND_BASE_LEVEL[kind]][0]}，同時在 🔥 強勢型池，升一級"
    elif (kind in ("pullback", "breakout") and strong and strong.get("type") == "反轉型"
          and not base.get("short") and not base.get("watch")):
        level = max(0, level - 1)
        note = f"基本 {STAR_TEXT[KIND_BASE_LEVEL[kind]][0]}，僅屬 🔄 反轉型（上方有套牢區），降一級"
    return level, note


def _rel_strength(base: dict, price: float):
    """盤中相對大盤強度（%）：個股今日漲幅 − 加權指數今日漲幅。資料不足回傳 None。"""
    prev, now_idx = MARKET.get("prev_close"), MARKET.get("price")
    if not prev or not now_idx or not base.get("yest_close"):
        return None
    return ((price / base["yest_close"] - 1) - (now_idx / prev - 1)) * 100


def _guidance(kind, base, price, stop, stop_label, ratio, now, rise_since=None, combos=None,
              first_time=None, merged=False):
    """通知內的優先序／買進檢查／停損／建議文字，同時把這則訊號記進 SIGNAL_LOG。
    買進檢查未通過：🔴 粗體標示，星等不變。"""
    level, adjust = _star_level(kind, base)
    stars, action = STAR_TEXT[level]
    base_stars, base_action = STAR_TEXT[KIND_BASE_LEVEL[kind]]
    stop_pct = (price - stop) / price * 100 if stop else None
    day_chg = (price / base["yest_close"] - 1) * 100 if base.get("yest_close") else 0.0
    fails, checks, plain = [], [], []

    def add(ok, text, fail_text):
        checks.append(f"{text} " + ("✅" if ok else "🔴<b>未通過</b>"))
        plain.append(f"{text} " + ("通過" if ok else "未通過"))
        if not ok:
            fails.append(fail_text)

    if stop_pct is not None:
        add(stop_pct <= MAX_STOP_PCT, f"停損距離 {stop_pct:.1f}%", f"停損距離 > {MAX_STOP_PCT:.0f}%")
    add(day_chg <= MAX_DAY_CHG_PCT, f"今日漲幅 {day_chg:+.1f}%", f"今日漲幅 > {MAX_DAY_CHG_PCT:.0f}%")
    if MARKET["sum19"] and MARKET["ok"] is not None:
        add(bool(MARKET["ok"]), "大盤 MA20", "大盤在 MA20 之下")
        market_txt = "MA20之上" if MARKET["ok"] else "MA20之下"
    else:
        checks.append("大盤 MA20 未知（請自行確認）")
        plain.append("大盤 MA20 未知")
        market_txt = "未知"

    if kind == "above":
        verdict_plain = "量能未確認，先觀察；等 💥 量能確認或 🎯 回檔買點再處理"
        verdict = verdict_plain
    elif fails:
        verdict_plain = f"未通過：{'、'.join(fails)}，先不買"
        verdict = f"🔴 <b>{verdict_plain}</b>"
    elif kind == "confirm" and merged:
        verdict_plain = verdict = "符合買進檢查（同時首次站上均線，可考慮）"
    elif kind == "confirm" and rise_since is not None:
        if rise_since <= CHASE_OK_PCT:
            verdict_plain = verdict = f"符合買進檢查（站上均線後 {rise_since:+.1f}%，可考慮）"
        elif rise_since <= CHASE_NO_PCT:
            verdict_plain = verdict = f"站上均線後已 {rise_since:+.1f}%，小部位或等回檔"
        else:
            verdict_plain = verdict = f"站上均線後已 {rise_since:+.1f}%，不追，等回檔"
    elif kind == "breakout":
        verdict_plain = verdict = "符合檢查；屬追價型買點，留意假突破"
    elif kind == "short":
        verdict_plain = verdict = "符合買進檢查，可小部位試單；較佳買點等 🎯 回檔買點"
    else:
        verdict_plain = verdict = "符合買進檢查"

    combos = [c for c in (combos or []) if c]
    lines = f"• 優先序：<b>{stars} {action}</b>"
    if adjust:
        lines += f"（{adjust}）"
    lines += "\n"
    lines += f"• 買進檢查：{'｜'.join(checks)}\n"
    lines += f"• 停損參考：{stop_label} <b>{stop:.2f}</b>\n"
    lines += f"• 建議：<b>{verdict}</b>\n" if not fails or kind == "above" else f"• 建議：{verdict}\n"
    if combos:
        lines += f"• ⚡ 訊號加強：{'、'.join(combos)}\n"
    SIGNAL_LOG.append({
        "時間": now.strftime("%H:%M:%S"),
        "代號": base["code"],
        "名稱": base["name"],
        "通知類型": KIND_NAME[kind],
        "優先序": f"{stars} {action}",
        "原始優先序": f"{base_stars} {base_action}",
        "星等調整": adjust,
        "星等數": level,
        "建議": verdict_plain,
        "買進檢查": "｜".join(plain),
        "價格": round(price, 2),
        "停損參考": round(stop, 2) if stop else "",
        "停損方式": stop_label,
        "停損距離(%)": round(stop_pct, 2) if stop_pct is not None else "",
        "今日漲幅(%)": round(day_chg, 2),
        "預估量比": round(ratio, 2),
        "首次站上均線時間": first_time or "",
        "站上均線後漲幅(%)": round(rise_since, 2) if rise_since is not None else "",
        "大盤": market_txt,
        "訊號加強": "、".join(combos),
        "型態": _pool_labels(base),
        "族群": base.get("industry", ""),
    })
    return lines


SIGNAL_FIELDS = ["時間", "代號", "名稱", "通知類型", "型態", "優先序", "原始優先序", "星等調整", "建議", "買進檢查",
                 "價格", "停損參考", "停損方式", "停損距離(%)", "今日漲幅(%)", "預估量比", "首次站上均線時間",
                 "站上均線後漲幅(%)", "大盤", "訊號加強", "族群"]


def export_signal_csv(day_str: str) -> pathlib.Path:
    rows = sorted(SIGNAL_LOG, key=lambda r: (-int(r.get("星等數", 0)), str(r.get("時間", ""))))
    with SIGNAL_CSV_FILE.open("w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerow([f"訊號日期：{day_str}（依優先序排列）"])
        writer = csv.DictWriter(fh, fieldnames=SIGNAL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return SIGNAL_CSV_FILE


BUY_SIGNAL_NAMES = (KIND_NAME["pullback"], KIND_NAME["breakout"])


def reconcile_signal_log(stock_bases: dict) -> int:
    """收盤對帳：狀態檔記錄「已發 🎯/🏔️」但訊號紀錄遺失者，補登一列（缺價格與停損）。"""
    have = {(r.get("代號"), r.get("通知類型")) for r in SIGNAL_LOG}
    added = 0
    for code, state in STOCK_STATES.items():
        base = stock_bases.get(code)
        if base is None:
            continue
        for kind in ("pullback", "breakout"):
            if kind not in (state.get("strong_sent") or []) or (code, KIND_NAME[kind]) in have:
                continue
            stars, action = STAR_TEXT[KIND_BASE_LEVEL[kind]]
            SIGNAL_LOG.append({
                "時間": "（未記錄）", "代號": code, "名稱": base["name"], "通知類型": KIND_NAME[kind],
                "優先序": f"{stars} {action}", "原始優先序": f"{stars} {action}", "星等數": KIND_BASE_LEVEL[kind],
                "建議": "訊號紀錄遺失，依狀態補登（無價格與停損資料）",
                "型態": _pool_labels(base), "族群": base.get("industry", ""),
            })
            have.add((code, KIND_NAME[kind]))
            added += 1
    return added


# =========================
# 第 6 階段：🎯 回檔買點 / 🏔️ 突破新高
# =========================
def _check_buypoints(base, state, real, ma5, ma10, ma20, ratio, now) -> bool:
    """🎯 回檔買點（🚀／🔥／🎯 觀察池成員）／🏔️ 突破新高（僅 🔥 池成員）。"""
    strong, short, watch = base.get("strong"), base.get("short"), base.get("watch")
    if not (strong or short or watch) or base["market"] in UNRELIABLE_MARKETS:
        return False
    if STRONG_DAY["count"] >= STRONG_MAX_PER_DAY or not market_allows_strong():
        return False
    price = real["price"]
    if price > ma20 * BUYPOINT_MAX_EXT:
        return False
    sent = state.setdefault("strong_sent", [])
    code = base["code"]
    labels = _pool_labels(base)
    common = (
        f"<b>{code} {base['name']}</b>\n"
        f"{_left_line(base['left']) if base.get('left') else ''}"
        f"{_pool_lines(base)}"
        f"• 族群：{base['industry'] or '未分類'}\n"
    )
    inst_plus = bool((strong or {}).get("inst_plus") or (short or {}).get("inst_plus"))
    combos = ["法人買超" if inst_plus else "",
              "短線轉強＋強勢股池" if (short and strong) else "",
              "同時在左側潛力庫" if base.get("left") else ""]
    risk = "• ⚠️ 上方有套牢區，接近前高時留意賣壓\n" if (strong or {}).get("type") == "反轉型" else ""

    # 🎯 回檔買點：盤中低點落在支撐區 [MA10×0.99, MA5×1.01]，現價自「今日最低價」反彈 ≥0.8% 且站回 MA10，
    # 並且盤中相對大盤強度 RS > 0。L_touch 就是 MIS 的今日最低價，隨今日新低自動向下刷新。
    low = real.get("low")
    if "pullback" not in sent and low:
        zone_lo, zone_hi = ma10 * PULLBACK_HOLD_BUF, ma5 * PULLBACK_TOUCH_BUF
        if zone_lo <= low <= zone_hi:
            bounce = (price / low - 1) * 100
            rs = _rel_strength(base, price)
            vol_ok = (PULLBACK_PREV_VOL_RATIO is None
                      or base["yest_vol"] < base["avg_5d_vol_simple"] * PULLBACK_PREV_VOL_RATIO)
            if bounce >= PULLBACK_BOUNCE * 100 and price >= ma10 and (rs is None or rs > 0) and vol_ok:
                sent.append("pullback")
                STRONG_DAY["count"] += 1
                touched = "MA10" if low <= ma10 * 1.005 else "MA5"
                guide = _guidance("pullback", base, price, ma10, "收盤跌破 MA10", ratio, now, combos=combos)
                rs_txt = f"｜相對大盤 {rs:+.1f}%" if rs is not None else ""
                enqueue_alert(
                    f"🎯 <b>【回檔買點】{labels}</b>\n"
                    f"{common}"
                    f"{guide}"
                    f"• 盤中低點 {low:.2f} 回測 {touched}（支撐區 {zone_lo:.2f}～{zone_hi:.2f}），"
                    f"自低點反彈 {bounce:+.1f}%，已站回 MA10{rs_txt}\n"
                    f"• 當前價：<b>{price:.2f}</b>（開盤 {real.get('open') or 0:.2f}，昨收 {base['yest_close']:.2f}）\n"
                    f"• MA5/10/20：{ma5:.2f} / {ma10:.2f} / {ma20:.2f}\n"
                    f"{risk}"
                    f"• 今日回檔買點/突破新高：第 {STRONG_DAY['count']}/{STRONG_MAX_PER_DAY} 則\n"
                    f"• 時間：{now.strftime('%H:%M:%S')}"
                )
                print(f"觸發回檔買點：{code} {base['name']}（{labels}）", flush=True)
                return True

    # 🏔️ 突破新高（僅 🔥 強勢股池）
    high20 = (strong or {}).get("high20")
    if ("breakout" not in sent and high20 and price > high20 * BREAKOUT_BUF
            and ratio >= BREAKOUT_VOL_RATIO):
        sent.append("breakout")
        STRONG_DAY["count"] += 1
        guide = _guidance("breakout", base, price, high20 * BREAKOUT_STOP, "跌回突破價 −3%",
                          ratio, now, combos=combos)
        enqueue_alert(
            f"🏔️ <b>【突破新高】{labels}</b>\n"
            f"{common}"
            f"{guide}"
            f"• 突破近20日最高收盤 {high20:.2f}\n"
            f"• 當前價：<b>{price:.2f}</b>｜預估量比 <b>{ratio:.2f}x</b>\n"
            f"• MA5/10/20：{ma5:.2f} / {ma10:.2f} / {ma20:.2f}\n"
            f"{risk}"
            f"• 今日回檔買點/突破新高：第 {STRONG_DAY['count']}/{STRONG_MAX_PER_DAY} 則\n"
            f"• 時間：{now.strftime('%H:%M:%S')}"
        )
        print(f"觸發突破新高：{code} {base['name']}", flush=True)
        return True
    return False


# =========================
# 第 2、3、4 階段通知 + 逐檔處理
# =========================
def _stop_for(base, ma5, ma10, ma20):
    """站上均線類通知的停損參考：左側潛力股 = 跌回整理區（MA5/10/20 最低）；其他 = 跌破 MA5。"""
    if base.get("left"):
        return min(ma5, ma10, ma20), "跌回整理區（MA5/10/20 最低）"
    return ma5, "跌破 MA5"


def _is_ordinary(base: dict) -> bool:
    """一般股票：不在 🧭/🚀/🔥/🎯 任何池內（📈、💥 單日上限只限制這類）。"""
    return not (base.get("left") or base.get("short") or base.get("strong") or base.get("watch"))


def _short_entry_alert(base, real, now):
    """🚀 盤中新納入通知。"""
    price = real["price"]
    ratio, est_turnover, actual = _estimates(base, real, now)
    ma5, ma10, ma20 = _realtime_mas(base, price)
    combos = ["同時在 🔥 強勢股池" if base.get("strong") else "",
              "同時在左側潛力庫" if base.get("left") else "",
              "法人買超" if (base.get("short") or {}).get("inst_plus") else ""]
    guide = _guidance("short", base, price, ma10, "收盤跌破 MA10", ratio, now, combos=combos)
    enqueue_alert(
        f"🚀 <b>【盤中新納入・短線轉強股】</b>\n"
        f"<b>{base['code']} {base['name']}</b>\n"
        f"{_left_line(base['left']) if base.get('left') else ''}"
        f"{_pool_lines(base)}"
        f"{guide}"
        f"• 族群：{base['industry'] or '未分類'}\n"
        f"• 當前價：<b>{price:.2f}</b>\n"
        f"• MA5/10/20：{ma5:.2f} / {ma10:.2f} / {ma20:.2f}\n"
        f"• 備註：預估量比 {ratio:.2f}x｜預估成交額 {est_turnover/1e8:.2f}億｜已成交 {actual/1e8:.2f}億\n"
        f"• 今日 🚀 盤中新納入：第 {SHORT_DAY['count']}/{SHORT_ENTRY_MAX_PER_DAY} 則\n"
        f"• 時間：{now.strftime('%H:%M:%S')}"
    )
    print(f"🚀 盤中新納入通知：{base['code']} {base['name']}", flush=True)


def _confirm_alert(base, state, real, ma5, ma10, ma20, ratio, est_turnover, actual, now, merged):
    """💥 量能確認。merged=True 表示與 📈 同一時刻達標，合併成一則。"""
    price = real["price"]
    left = base.get("left")
    change = 0.0 if merged else (price / state["alert_price"] - 1) * 100
    combos = ["同時在左側潛力庫" if left else "",
              "同時在強勢股池" if base.get("strong") else "",
              "同時在短線轉強股" if base.get("short") else ""]
    stop, stop_label = _stop_for(base, ma5, ma10, ma20)
    guide = _guidance("confirm", base, price, stop, stop_label, ratio, now,
                      rise_since=change, combos=combos,
                      first_time=state["alert_time"], merged=merged)
    if merged:
        first_txt = f"• ⏱ {state['alert_time']} <b>同時首次站上均線</b>，量能與成交額已達標\n"
    else:
        first_txt = (
            f"• ⏱ {state['alert_time']} 首次站上均線（量比 {state['alert_ratio']:.2f}x，"
            f"預估成交額 {(state['alert_turnover'] or 0)/1e8:.2f}億）\n"
            f"• → 現在量比 <b>{ratio:.2f}x</b>，預估成交額 <b>{est_turnover/1e8:.2f}億</b>\n"
        )
    enqueue_alert(
        f"💥 <b>【量能確認】</b>\n"
        f"<b>{base['code']} {base['name']}</b>\n"
        f"{_left_line(left) if left else ''}"
        f"{_pool_lines(base)}"
        f"{guide}"
        f"{first_txt}"
        f"• 已成交金額 {actual/1e8:.2f}億（≥{CONFIRM_MIN_ACTUAL_TURNOVER/1e8:.1f}億）\n"
        f"• 當前價：<b>{price:.2f}</b>"
        + ("" if merged else f"（首次站上均線時 {state['alert_price']:.2f}，{change:+.1f}%）")
        + "\n"
        f"• MA5/10/20：{ma5:.2f} / {ma10:.2f} / {ma20:.2f}\n"
        f"• 時間：{now.strftime('%H:%M:%S')}"
    )


def process_stock(base: dict, real: dict) -> int:
    """回傳本輪對這檔股票發出的通知數。"""
    code = base["code"]
    price = real["price"]
    now = now_tw()
    ma5, ma10, ma20 = _realtime_mas(base, price)
    ratio, est_turnover, actual_turnover = _estimates(base, real, now)
    above_all = price > ma5 * ABOVE_MA_BUF and price > ma10 * ABOVE_MA_BUF and price > ma20 * ABOVE_MA_BUF
    liquid_ok = base["avg_turnover_5d"] >= MIN_LIQ_TURNOVER_5D
    # 💥：09:15 後才評估；預估量比 ≥2、預估成交額 ≥20 億、盤中實質已成交 ≥1.5 億
    confirm_ok = (_confirm_window_open(now) and ratio >= CONFIRM_MIN_VOL_RATIO
                  and est_turnover >= CONFIRM_MIN_TURNOVER and actual_turnover >= CONFIRM_MIN_ACTUAL_TURNOVER)
    ordinary = _is_ordinary(base)
    state = STOCK_STATES.setdefault(code, _new_state())
    for key, value in _new_state().items():
        state.setdefault(key, value)
    if code in DEBUG_STOCK_CODES:
        reasons = _failure_reasons(price, ma5, ma10, ma20, base["avg_turnover_5d"], state["status"])
        print(
            f"[診斷 {code} {base['name']}] price={price:.2f}, MA5={ma5:.2f}, MA10={ma10:.2f}, "
            f"MA20={ma20:.2f}, 量比={ratio:.2f}x, 預估額={est_turnover/1e8:.2f}億, 已成交={actual_turnover/1e8:.2f}億, "
            f"status={state['status']}, below={state['below_ma5_count']}, 🚀={'是' if base.get('short') else '否'}, "
            f"🔥={'是' if base.get('strong') else '否'}, 結果={'；'.join(reasons)}",
            flush=True,
        )
    sent = 0
    # ---- 第 6 階段：回檔買點 / 突破新高（與均線突破狀態無關）----
    if _check_buypoints(base, state, real, ma5, ma10, ma20, ratio, now):
        sent += 1
    left = base.get("left")
    reliable = base["market"] not in UNRELIABLE_MARKETS

    # ---- 第 2 階段：📈 站上均線（量比、成交額只當備註）----
    if above_all and liquid_ok and state["status"] == "BELOW" and reliable:
        cooling = bool(state["last_notified"]
                       and (now - state["last_notified"]).total_seconds() / 60 < COOLDOWN_MINUTES)
        capped = ordinary and ABOVE_MAX_PER_DAY and ABOVE_DAY["count"] >= ABOVE_MAX_PER_DAY
        if not cooling and not capped:
            merged = confirm_ok and not (ordinary and CONFIRM_MAX_PER_DAY and CONFIRM_DAY["count"] >= CONFIRM_MAX_PER_DAY)
            state["status"] = "ABOVE"
            state["last_notified"] = now
            state["below_ma5_count"] = 0
            state["alert_price"] = price
            state["alert_ratio"] = round(ratio, 2)
            state["alert_turnover"] = est_turnover
            state["alert_time"] = now.strftime("%H:%M")
            if ordinary:
                ABOVE_DAY["count"] += 1
            if merged:
                # 站上均線的同一刻量能也達標 → 合併成一則「💥 量能確認」
                state["confirm_sent"] = True
                if ordinary:
                    CONFIRM_DAY["count"] += 1
                _confirm_alert(base, state, real, ma5, ma10, ma20, ratio, est_turnover, actual_turnover, now, merged=True)
                print(f"觸發量能確認（同時首次站上均線）：{code} {base['name']}", flush=True)
                return sent + 1
            stop, stop_label = _stop_for(base, ma5, ma10, ma20)
            guide = _guidance("above", base, price, stop, stop_label, ratio, now,
                              combos=["同時在強勢股池" if base.get("strong") else "",
                                      "同時在短線轉強股" if base.get("short") else ""],
                              first_time=state["alert_time"])
            enqueue_alert(
                f"📈 <b>【盤中站上均線提醒】</b>\n"
                f"<b>{code} {base['name']}</b>\n"
                f"{_left_line(left) if left else ''}"
                f"{_pool_lines(base)}"
                f"{guide}"
                f"• 族群：{base['industry'] or '未分類'}\n"
                f"• 當前價：<b>{price:.2f}</b>\n"
                f"• MA5/10/20：{ma5:.2f} / {ma10:.2f} / {ma20:.2f}\n"
                f"• 備註：預估量比 {ratio:.2f}x（未確認）｜預估成交額 {est_turnover/1e8:.2f}億｜"
                f"5日均成交額 {base['avg_turnover_5d']/1e8:.2f}億\n"
                f"• 時間：{now.strftime('%H:%M:%S')}"
            )
            print(f"觸發站上均線：{code} {base['name']}", flush=True)
            return sent + 1

    # ---- 第 3 階段：💥 量能確認（先站上均線，之後量能達標）----
    if (state["status"] == "ABOVE" and not state["confirm_sent"] and state["alert_price"]
            and confirm_ok and price > ma5 and reliable):
        if not (ordinary and CONFIRM_MAX_PER_DAY and CONFIRM_DAY["count"] >= CONFIRM_MAX_PER_DAY):
            state["confirm_sent"] = True
            if ordinary:
                CONFIRM_DAY["count"] += 1
            _confirm_alert(base, state, real, ma5, ma10, ma20, ratio, est_turnover, actual_turnover, now, merged=False)
            print(f"觸發量能確認：{code} {base['name']}", flush=True)
            return sent + 1

    # ---- 跌破 MA5 達 1%、連續 20 輪（約 10 分鐘）才重置 ----
    reset_price = ma5 * RESET_BUF
    state["below_ma5_count"] = state["below_ma5_count"] + 1 if price < reset_price else 0
    if state["status"] == "ABOVE" and state["below_ma5_count"] >= RESET_CONFIRM_ROUNDS:
        print(f"[狀態重置] {code} {base['name']} price={price:.2f}, MA5={ma5:.2f}, 連續{state['below_ma5_count']}輪低於 MA5×{RESET_BUF}", flush=True)
        state["status"] = "BELOW"
        state["below_ma5_count"] = 0
    return sent


# =========================
# 主程式
# =========================
def _init_states_from_history(stock_bases: dict) -> None:
    above = below = 0
    for code, base in stock_bases.items():
        if code in STOCK_STATES:
            continue
        status = "BELOW" if base.get("left") else ("ABOVE" if base.get("already_above") else "BELOW")
        STOCK_STATES[code] = _new_state(status)
        if status == "ABOVE":
            above += 1
        else:
            below += 1
    print(f"[初始化] 狀態=ABOVE {above} 檔（昨收已站上 MA5/10/20，今日不發站上均線通知）；BELOW {below} 檔", flush=True)


async def _prepare_stock_bases():
    stock_bases = load_all_stock_bases()
    if not stock_bases:
        return {}, {}
    try:
        chain_map = await refresh_ic_chains(sorted(stock_bases.keys()))
    except Exception as exc:
        print(f"[產業鏈] 查詢流程例外，改用官方產業別: {type(exc).__name__}: {repr(exc)}", flush=True)
        chain_map = {}
    apply_ic_chains(stock_bases, chain_map)
    return stock_bases, chain_map


async def run_offday_csv_only(reason: str) -> None:
    print(f"{reason}：只產出監控清單 CSV，不進入盤中監控、不重送收盤訊息", flush=True)
    stock_bases, _ = await _prepare_stock_bases()
    if not stock_bases:
        send_telegram("❌ <b>【飆股雷達】</b> 非盤中時段產出清單失敗：無法建立歷史基底")
        return
    asof = max(base["asof_date"] for base in stock_bases.values())
    csv_path = export_stock_bases_csv(stock_bases)
    send_telegram_document(csv_path, f"📋 非盤中時段監控清單（資料基準日 {asof.isoformat()}），共 {len(stock_bases)} 檔")


async def finish_day(stock_bases: dict, current: datetime, loop) -> bool:
    """收盤：存狀態、對帳、寫 signals_today.csv、傳收盤訊息與 CSV。"""
    strong_n = sum(1 for b in stock_bases.values() if b.get("strong"))
    short_n = sum(1 for b in stock_bases.values() if b.get("short"))
    watch_n = sum(1 for b in stock_bases.values() if b.get("watch"))
    save_states(STOCK_STATES)
    added = reconcile_signal_log(stock_bases)
    path = export_signal_csv(current.strftime("%Y-%m-%d")) if SIGNAL_LOG else None
    buy_rows = sum(1 for r in SIGNAL_LOG if r.get("通知類型") in BUY_SIGNAL_NAMES)
    message = (
        f"📴 <b>【飆股雷達】</b> 收盤，今日監控結束\n"
        f"🚀 短線轉強股 {short_n} 檔｜🎯 回檔觀察池 {watch_n} 檔｜🔥 強勢股池 {strong_n} 檔｜"
        f"🎯/🏔️ 買點 {STRONG_DAY['count']} 則"
    )
    if added:
        message += f"\n⚠️ 有 {added} 則 🎯/🏔️ 的訊號紀錄遺失，已依狀態補登（缺價格與停損）"
    if buy_rows != STRONG_DAY["count"]:
        message += f"\n⚠️ 買點計數 {STRONG_DAY['count']} 與 CSV 的 🎯/🏔️ {buy_rows} 列不一致"
    close_ok = await loop.run_in_executor(None, send_telegram, message)
    if path:
        top = sum(1 for r in SIGNAL_LOG if int(r.get("星等數", 0)) >= 3)
        sent = await loop.run_in_executor(
            None, send_telegram_document, path,
            f"🧾 今日盤中訊號 {len(SIGNAL_LOG)} 則（⭐⭐⭐ {top} 則），依優先序排列",
        )
        if not sent:
            await loop.run_in_executor(
                None, send_telegram, "⚠️ signals_today.csv 傳送失敗，請到 Actions 的 Artifacts 下載")
    return close_ok


async def main_async() -> None:
    print("=" * 55, flush=True)
    print("飆股雷達 v41：🧭左側 → 📈站上均線 → 💥量能確認 → 🚀短線轉強 → 🔥強勢股 → 🎯回檔買點", flush=True)
    print("=" * 55, flush=True)
    current = now_tw()
    print(
        f"[程序啟動] PID={os.getpid()}, GITHUB_RUN_ID={os.environ.get('GITHUB_RUN_ID', 'LOCAL')}, "
        f"EVENT={os.environ.get('GITHUB_EVENT_NAME', 'unknown')}, 時間={current.strftime('%Y-%m-%d %H:%M:%S')}",
        flush=True,
    )
    if not _is_weekday(current):
        await run_offday_csv_only("週末非交易日")
        return
    if current > _market_close_today(current):
        await run_offday_csv_only("已過收盤時間")
        return
    global STOCK_STATES
    STOCK_STATES = load_states()
    stock_bases, chain_map = await _prepare_stock_bases()
    if not stock_bases:
        send_telegram("❌ <b>【飆股雷達】</b> 無法建立歷史基底，監控未啟動")
        return
    left_summary = load_left_watchlist(stock_bases)
    pool_summary = load_strong_base(stock_bases)
    _init_states_from_history(stock_bases)
    liquid_n = sum(1 for b in stock_bases.values() if b["avg_turnover_5d"] >= MIN_LIQ_TURNOVER_5D)
    send_telegram(
        f"📡 <b>【飆股雷達】即時監控啟動</b>\n"
        f"全台股 {len(stock_bases)} 檔預載完畢（資料來源：TWSE/TPEx官方）\n"
        f"族群來源：櫃買產業價值鏈 {len(chain_map)} 檔，其餘為官方產業別\n"
        f"{left_summary}\n"
        f"📈 站上均線提醒：{liquid_n} 檔符合流動性底線（5日均成交額 ≥ 1億）；"
        f"一般股票單日上限 {ABOVE_MAX_PER_DAY or '不限'} 則、重置需跌破 MA5 達 1% 並維持約 {RESET_CONFIRM_ROUNDS * POLL_INTERVAL // 60} 分鐘\n"
        f"💥 量能確認：09:15 後；預估量比 ≥ {CONFIRM_MIN_VOL_RATIO:.1f}x、預估成交額 ≥ {CONFIRM_MIN_TURNOVER/1e8:.0f}億、"
        f"已成交 ≥ {CONFIRM_MIN_ACTUAL_TURNOVER/1e8:.1f}億\n"
        f"{pool_summary}\n"
        f"掃描方式：全市場分上下半，每 {POLL_INTERVAL // 2} 秒抓一半，每檔每 {POLL_INTERVAL} 秒更新一次；"
        f"MIS 連續失敗會自動退避並通知"
    )
    csv_path = export_stock_bases_csv(stock_bases)
    send_telegram_document(csv_path, f"📋 今日監控清單，共 {len(stock_bases)} 檔")
    ordered = sorted(
        stock_bases.values(),
        key=lambda b: 0 if b.get("left") else (1 if (b.get("strong") or b.get("short") or b.get("watch")) else 2),
    )
    # 交錯分成 A、B 兩半：兩半都保留 🧭/🚀/🔥/🎯 優先順序；加權指數兩半都抓，大盤濾網每 30 秒更新
    all_channels = [base["mis_ch"] for base in ordered]
    halves = []
    for part in (all_channels[0::2], all_channels[1::2]):
        channels = [MARKET_INDEX_CH] + part
        halves.append([channels[i:i + MIS_CHUNK_SIZE] for i in range(0, len(channels), MIS_CHUNK_SIZE)])
    slot_seconds = POLL_INTERVAL / 2
    print(
        f"MIS 分批：全市場 {len(all_channels)} 檔 → A 半 {len(halves[0])} 批 / B 半 {len(halves[1])} 批（每批 ≤{MIS_CHUNK_SIZE} 檔），"
        f"每 {slot_seconds:.0f} 秒抓一半、A/B 交替，每檔每 {POLL_INTERVAL} 秒更新；同時最多 {MIS_MAX_CONCURRENCY} 批；"
        f"單請求逾時 {MIS_REQUEST_TIMEOUT}s、重試 {MIS_MAX_RETRIES} 次（間隔 {MIS_RETRY_PAUSE}s）、"
        f"每半預算 {MIS_ROUND_DEADLINE or '不限'}s；失敗比例 ≥ {MIS_FAIL_RATIO:.0%} 即暫停 {MIS_BACKOFF_BASE}~{MIS_BACKOFF_MAX} 秒，"
        f"前 {MIS_ABORT_AFTER_FAILS} 組全失敗即中止該輪；網址後綴 {MIS_URL_SUFFIX or '無'}、預熱 index.jsp {'開' if MIS_WARMUP else '關'}",
        flush=True,
    )
    loop = asyncio.get_running_loop()
    scan_count = 0
    close_message_sent = False
    logged_rows = len(SIGNAL_LOG)
    slot = 0                      # 偶數輪抓 A 半、奇數輪抓 B 半
    next_slot_at = None           # 下一次抓取的預定時間（用固定節奏，避免每輪耗時不同造成累積飄移）
    mis_fail_streak = 0           # 連續失敗輪數
    backoff_until = None          # 退避到什麼時候才再抓
    mis_alert_sent = False        # 這一串失敗是否已發過 Telegram 警告
    mis_fail_started = None       # 這一串失敗開始的時間（恢復通知要用）
    try:
        while True:
            current = now_tw()
            if current > _market_close_today(current):
                print(f"[{current.strftime('%H:%M:%S')}] 收盤，監控結束", flush=True)
                close_message_sent = await finish_day(stock_bases, current, loop)
                break
            market_open = _market_open_today(current)
            if current < market_open:
                wait_seconds = int((market_open - current).total_seconds()) + 5
                print(f"[{current.strftime('%H:%M:%S')}] 尚未開盤，等待 {wait_seconds}s...", flush=True)
                await asyncio.sleep(min(wait_seconds, 60))
                continue
            if backoff_until is not None:           # MIS 連續失敗：退避中，不送請求（每次最多睡 30 秒，才不會錯過收盤）
                remaining = (backoff_until - current).total_seconds()
                if remaining > 0:
                    await asyncio.sleep(min(30.0, remaining))
                    continue
                backoff_until = None
                next_slot_at = None
            started = time.time()
            if next_slot_at is None:
                next_slot_at = now_tw()
            half = slot % 2
            snapshot = await poll_all_mis(halves[half])
            update_market_filter(snapshot)
            new_strong, new_short = update_pools(stock_bases, snapshot)
            scan_count += 1
            hit_count = 0
            for code, real_data in snapshot.items():
                base = stock_bases.get(code)
                if base is None:
                    continue
                try:
                    hit_count += process_stock(base, real_data)
                except Exception as exc:
                    print(f"process_stock {code} 例外: {type(exc).__name__}: {repr(exc)}", flush=True)
            await flush_alerts(loop)
            save_states(STOCK_STATES)
            if len(SIGNAL_LOG) != logged_rows:   # 有新訊號就立刻寫 CSV，中途失敗也不會遺失
                export_signal_csv(current.strftime("%Y-%m-%d"))
                logged_rows = len(SIGNAL_LOG)
            elapsed = time.time() - started
            failed_round = bool(MIS_LAST_STATS.get("failed_round"))
            market_txt = ""
            if MARKET["ok"] is not None:
                market_txt = f"，大盤{'在' if MARKET['ok'] else '低於'}MA20"
            print(
                f"[{current.strftime('%H:%M:%S')}] 第{scan_count}次掃描（{'AB'[half]} 半），MIS回傳 {len(snapshot)} 檔，"
                f"通知 {hit_count} 則，新納入 強勢股 {new_strong}/短線轉強 {new_short} 檔{market_txt}，"
                f"耗時 {elapsed:.2f}s",
                flush=True,
            )
            slot += 1
            if failed_round:
                # 失敗輪：重建連線池、暫停（連續失敗就加倍），下一次換抓另一半
                mis_fail_streak += 1
                pause = min(MIS_BACKOFF_BASE * 2 ** (mis_fail_streak - 1), MIS_BACKOFF_MAX)
                backoff_until = now_tw() + timedelta(seconds=pause)
                next_slot_at = None
                if mis_fail_started is None:
                    mis_fail_started = now_tw()
                await close_mis_session()
                print(f"[MIS] 第 {mis_fail_streak} 次連續失敗輪（失敗比例 {MIS_LAST_STATS.get('fail_ratio', 0):.0%}），"
                      f"暫停 {pause} 秒後重試，並重建連線池", flush=True)
                if mis_fail_streak >= MIS_ALERT_AFTER_FAILS and not mis_alert_sent:
                    mis_alert_sent = True
                    await loop.run_in_executor(
                        None, send_telegram,
                        f"⚠️ <b>【飆股雷達】證交所 MIS 連續 {mis_fail_streak} 輪抓取失敗</b>\n"
                        f"超過 {MIS_FAIL_RATIO:.0%} 的請求沒有回應（可能被限流或 MIS 當下過載），"
                        f"已暫停抓取 {pause} 秒後重試，連續失敗會逐次加倍（上限 {MIS_BACKOFF_MAX} 秒）。\n"
                        f"這段期間不會有站上均線、回檔買點等即時通知，恢復後會再通知你。",
                    )
                continue
            if mis_fail_streak:
                lasted = (now_tw() - mis_fail_started).total_seconds() if mis_fail_started else 0
                print(f"[MIS] 已恢復（連續失敗 {mis_fail_streak} 輪，歷時約 {lasted:.0f} 秒）", flush=True)
                if mis_alert_sent:
                    await loop.run_in_executor(
                        None, send_telegram,
                        f"✅ <b>【飆股雷達】MIS 已恢復</b>\n連續失敗 {mis_fail_streak} 輪，歷時約 {lasted / 60:.1f} 分鐘；"
                        f"這段時間內的站上均線等訊號可能已經錯過，請自行對照看盤軟體。",
                    )
                mis_fail_streak, mis_alert_sent, mis_fail_started = 0, False, None
            next_slot_at += timedelta(seconds=slot_seconds)
            delay = (next_slot_at - now_tw()).total_seconds()
            if delay < 1.0:               # 這一半抓太久、已經落後：不補抓，直接以現在為基準繼續
                delay = 1.0
                next_slot_at = now_tw() + timedelta(seconds=1.0)
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        save_states(STOCK_STATES)
        raise
    except Exception as exc:
        print(f"主迴圈發生未處理例外: {type(exc).__name__}: {repr(exc)}", flush=True)
        save_states(STOCK_STATES)
        await loop.run_in_executor(None, send_telegram, f"⚠️ <b>【飆股雷達】</b> 監控異常中止：{type(exc).__name__}")
        raise
    finally:
        await close_mis_session()
        if now_tw().hour >= 14 and not close_message_sent:
            print("程式離開時已過收盤，但正常收盤訊息尚未成功送出", flush=True)


if __name__ == "__main__":
    asyncio.run(main_async())
