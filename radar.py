"""飆股雷達 v70.5：A1 首發突破 / A2 二次加碼 / B 跌深低接（依 2026-10-17「程式選股新架構」；v70 起「選池」搬到收盤後，baseline 只算法人）。
價格與行情全部來自富邦 Neo API；法人買賣超用證交所＋櫃買官方資料（只當參考，不當篩選條件）。停損由你自己處理：不監控停損、不發停損通知。
【一天的流程（v70）】Google Apps Script 以 workflow_dispatch 呼叫兩條 workflow（GitHub 本身不設 cron）：
  08:00~09:00  daily.yml    → radar.py    ：盤前載入 → 背景預抓全市場日 K → 盤中 15 秒掃描 → 13:35 結算 → 【收盤後選池】→ 寫 initial_pools.json（明天用）
  18:00 後     baseline.yml → baseline.py ：只算官方外資、投信近 5 日買賣超 → 寫 institutional.json（隔天 radar 盤前讀取）
  補歷史（只跑一次）history_backfill.yml → backfill_history.py：用日 K 重播最近 10 日，補好 signal_history.json，最後順便做一次收盤後選池
  radar.py 一律用 Asia/Taipei 判斷時間：09:00 前啟動就等到開盤；09:05 後才啟動視為「延遲啟動」，先暖機 2 輪再開放訊號；
  13:25 後才啟動只做收盤結算＋選池（約 46 分鐘，因為沒有預抓）。同一交易日 stock_states.json 已標 COMPLETED 時，重複觸發直接忽略。
【收盤後選池（13:35 之後，radar 自己做）】
  資料來源：① 背景預抓（radar 一啟動就在背景慢慢抓，富邦限速約每秒 1 次）的「截至昨天」全市場日 K、產業別、候選股 1 分 K；
            ② 13:35 最終快照合成的「今天」日 K 棒（開高低收量額）；③ 即時指數（今天收盤）。
  做的事：A1 候選池（5 日均量 ≥ 1,000 張 ＋ 站上三均線 N 日內，N = A1_LATE_DAYS）、A1 追蹤 / A2 池階段重算、B 池全市場掃描（KD 低檔金叉＋爆量＋收盤位置）、
          分時量矩陣（20 個交易日截至昨天）、指數前 19 日收盤 → 原子寫入 initial_pools.json，並發一則【盤後運算】Telegram。
  官方資料要等到：三大法人（證交所 T86、櫃買）約傍晚；富邦歷史日 K / 1 分 K 當日資料盤後 16:30 才更新 → 所以今天的 K 棒用快照合成
  （成交量比官方日 K 約少 1～2%：官方含盤後零股／定價；隔天預抓到的就是官方值，自動修正）。
【市場趨勢：只有 NORMAL / BEAR 兩態（上市用加權、上櫃用櫃買，各自獨立）】
  即時月線 MA20 =（前 19 個完整交易日指數收盤 + 盤中即時指數）/ 20：前 19 日收盤收盤後算好存進 initial_pools.json（closes19），盤中每 15 秒用最新指數明確算出即時 MA20 再比較；
  NORMAL：A1 預估量 ≥ 5 日均量 × 1.5（搭配同期量比）、A2 開放、B 開放。
  BEAR  ：A1 預估量 ≥ 5 日均量 × 2.0（搭配同期量比）、A2 禁止、B 開放。
  切換防抖：NORMAL→BEAR 連續 2 輪低於即時月線；BEAR→NORMAL 站回即時月線並連續維持 5 分鐘；正式切換才 Telegram 通知一次。
  沒有 Hard Freeze，也沒有 DATA_ERROR_FREEZE。
【A1 追蹤 / A2 池（v70.2 無固定追蹤期限）】A1 收盤確認成功後先進「A1 追蹤」；只要收盤 ≥ MA5 且守住突破日低點，就持續追蹤，不因天數到期移出。
  某天收盤 < MA5、但仍 ≥ MA10 且 ≥ 突破日低點 → 進「A2 池」（連續拉回 1～6 天）；收盤重新 ≥ MA5 → 回 A1 追蹤、拉回天數歸零。
  任一階段收盤 < MA10 或 < 突破日低點 → 立即移出；A2 池連續拉回超過 6 天仍沒站回 MA5 → 移出。只有 A2 池才可能觸發 A2。
【B 候選池】全市場，不篩 5 日均量：KD(9,3,3) 前日 K≤D、當日 K>D 且 K<40 ＋ 成交量 ≥ 前 20 日均量 × 2 ＋ 爆量日成交額 ≥ 15 億（B_MIN_TURNOVER）＋ 收盤 ≥ 最低價 × 1.02 ＋ 收盤位置 ≥ 60%。
【盤中（radar.py）】09:05~13:24:45 每 15 秒一輪，連續 2 輪符合才通知；13:25 起停止所有新的個股訊號。
  A1：站上三均線 N 日內（昨日以前連續站上不超過 N-1 天，N = A1_LATE_DAYS = 2）；現價同時站上盤中即時 MA5/10/20；漲幅 > 2%；現價 > 開盤價；現價 > 即時 VWAP；預估全日量 ≥ 1,000 張；
      同期量比 ≥ 1.5（BEAR 2.0；等價於預估全日量 ≥ 5 日均量 × 倍數）；預估全日成交額 ≥ 20 億。
  A2：僅 A2 池內；今日最低價 ≥ 突破日低點；現價 > 即時 MA5（A2_REQUIRE_ALL_MAS = True 時還要 > MA10、MA20）且 > 即時 VWAP；預估全日量 ≥ 整理期平均量 × 1.2；預估全日成交額 ≥ 20 億；只在 NORMAL 發。
  B ：10:00 後；今日最低價 ≥ B 訊號日最低價；現價 > 即時 MA5 且 > 即時 VWAP；預估全日量 ≤ B 訊號日量 × 0.6；預估全日成交額 ≥ 5 億；NORMAL / BEAR 都發。
  通知優先序 A2 > A1 > B；同一檔同一天只發一種主訊號（ONE_SIGNAL_PER_STOCK）。A1 與 B 候選池可以重疊，沒有 B→A1 升級。
【13:35 收盤結算】用富邦最終快照（closePrice 且非試撮）；A1 收盤確認 → 進 A1 追蹤；A1 追蹤 / A2 池依上面的規則移出；B 每過一個交易日 waiting_days+1，第 5 個交易日收盤仍未發動 → B_EXPIRED 移出
  （不發停損／出場通知）；寫 signal_history.json、pool_report.csv。結算完接著「收盤後選池」；選池成功才發【盤後運算】（取代舊的 13:35 盤後總結，避免同一天通知兩次池）；選池失敗才補發盤後總結＋警告。
【檔案】initial_pools.json（收盤後選池產出，隔天盤中唯讀）、institutional.json（baseline 傍晚產出，radar 盤前讀）、signal_history.json（追蹤清單）、
        stock_states.json（當日已通知名單＋趨勢狀態＋執行鎖）、pool_report.csv
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
RADAR_VERSION = "v70.5"
INITIAL_RULE = "v69"                # initial_pools.json 的規則版本；舊版檔案會被拒用
POOL_RULE = "q2"                    # signal_history.json 規則版本（q1 會自動升級）
POOL_RULES_OK = ("q1", "q2")
CLOSED_KEEP = 300
DAYS_KEEP = 90
ONE_SIGNAL_PER_STOCK = True         # 同一檔同一天只發一種主訊號（優先序 A2 > A1 > B）
# ---- v70：收盤後選池 ＋ 法人檔 ----
SCAN_ENABLED = True                 # True = 13:35 結算後接著選池並寫 initial_pools.json；False = 不選池（要自己另外產生 initial_pools.json）
SCAN_WAIT_PREFETCH_SEC = 3600       # 選池開始時背景預抓還沒做完，最多再等幾秒；仍不足（覆蓋率 < FETCH_MIN_COVERAGE）就放棄這次選池
SCAN_WAIT_CURVES_SEC = 600          # 日 K 預抓完成後，等 1 分 K 預抓再做多久（逾時就用已抓到的，其餘現場補抓）
SCAN_MAX_NEW_CURVES = 150           # 現場最多補抓幾檔 1 分 K（預抓沒涵蓋到的新候選）；超過的略過，盤中不會被確認
SCAN_IC_BUDGET_SECONDS = 60         # 產業鏈補查時間上限（已有 30 天快取，通常只補幾檔；補不到下次再補）
POOL_DETAIL_MAX = 25                # 盤後運算訊息：每個池最多列幾檔，其餘用「…另有 N 檔」帶過
INST_FILE = BASE_DIR / "institutional.json"
INST_RULE = "inst1"

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
A1_LATE_DAYS = 2                    # v70.3：站上三均線後第幾個交易日內都算「首次放量」。N = 1 → 只有站上當天（舊規則：昨收 ≤ 昨日最高均線）；
                                    #   N = 2 → 站上當天，或站上後第 2 天（昨天剛好第一天收在三均線之上，今天才爆量也抓得到，例如 6672 9/23 站上、9/24 爆量）。
                                    #   「這一波」已經發過 A1（在 A1 追蹤 / A2 池、或待收盤確認）的不會在第 2 天重複發。
A1_VOL_RATIO = 1.5                  # NORMAL：同期量比 ≥ 1.5（等價於預估全日量 ≥ 5 日均量 × 1.5）
A1_VOL_RATIO_BEAR = 2.0             # BEAR：× 2.0
A1_MIN_EST_LOTS = 1000              # 預估全日成交量 ≥ 1,000 張
A_MIN_EST_TURNOVER = 2_000_000_000  # 預估全日成交金額 ≥ 20 億（A1、A2 共用）
VWAP_FILTER = True                  # 現價 > 即時 VWAP（硬條件）

# ---- A2（二次加碼）----
A2_ENABLED = True
A2_MIN_DAYS, A2_MAX_DAYS = 1, 6     # 拉回整理：連續「收盤 < MA5 且 ≥ MA10 且 ≥ 突破日低點」1～6 天
A2_VOL_RATIO = 1.2                  # 預估全日量 ≥ 整理期平均量 × 1.2
A2_REQUIRE_VWAP = True              # 現價 > 即時 VWAP
A2_REQUIRE_ALL_MAS = True           # False = 只要現價 > 即時 MA5（你確認過的規則）；True = 現價必須同時 > 即時 MA5、MA10、MA20（「重新站回所有均線」）
# v70.2：A1 追蹤不設固定天數上限。階段完全由收盤相對 MA5 / MA10 與突破日低點決定。
# 追蹤標的的兩個階段（同一份 signal_history.json 的 "a" 區塊，用 stage 欄位區分）：
#   stage = "A1"：A1 收盤確認後追蹤中，目前沒有在拉回整理。這時不會觸發 A2。
#   stage = "A2"：曾收盤跌破 MA5 且守住 MA10 與突破日低點，連續 A2_MIN_DAYS～A2_MAX_DAYS 個交易日 → 進 A2 池，這段期間盤中重新站回才會觸發 A2 二次加碼。

# ---- B（跌深低接）----
B_ENABLED = True
B_KD_LOW = 40.0                     # 前日 K ≤ 前日 D、當日 K > 當日 D、當日 K < 40
B_VOL_RATIO = 2.0                   # 當日成交量 ≥ 前 20 日均量 × 2
B_AVG_DAYS = 20
B_MIN_TURNOVER = 1_500_000_000      # v70.5：爆量日（B 訊號日）最低成交額 ≥ 15 億（元）才進 B 池；0 = 不篩。
                                    #   為什麼是 15 億：B 確認要「預估全日量 ≤ B 訊號日量 × 0.6」又要「預估成交額 ≥ B_MIN_EST_TURNOVER（5 億）」，
                                    #   隔天成交額最多約 0.6 × 訊號日成交額，所以訊號日至少要 5 億 ÷ 0.6 ≈ 8.3 億才有可能確認；
                                    #   取 15 億，讓隔天量縮到訊號日的 33%～60% 都過得了 5 億（10 億只剩 50%～60% 很窄）。
B_LOWCLOSE_PCT = 2.0                # 收盤 ≥ 當日最低價 × (1 + 2%)
B_CLOSE_LOC_MIN = 0.6               # 收盤位置 (收-低)/(高-低) ≥ 60%
B_WAIT_DAYS = 5                     # B 訊號後第 5 個交易日收盤仍未發動 → B_EXPIRED
B_ACTIVE_TRACK_DAYS = 5             # 發動（確認通知）後再追蹤幾個交易日（只管清單）
B_CONFIRM_START_MINUTE = 60         # 10:00 起才判斷
B_SHRINK_RATIO = 0.6                # 預估全日量 ≤ B 訊號日量 × 0.6
B_MIN_EST_TURNOVER = 500_000_000    # 預估全日成交金額 ≥ 5 億
B_REQUIRE_MA5 = True                # 現價 > 即時 MA5
B_REQUIRE_VWAP = True               # 現價 > 即時 VWAP（確認強度）
B_STATUS_MISS_DAYS = 3              # 連續幾天抓不到收盤資料 → 資料缺失移出
# v69.2：B 池「全市場」確認，不篩 5 日均量（1,000 張只限 A1 候選池）
B_FULL_MARKET = True                # 夜間日 K 抓全市場（約 1,900 檔，約 37 分鐘）；False = 回到只抓成交量預篩後約 770 檔
B_MIN_VOL5_LOTS = 0                 # B 候選的 5 日均量下限（張）；0 = 不篩（B 的篩選靠「爆量 ≥ 20 日均量 2 倍」、爆量日成交額 ≥ 15 億與盤中預估成交額 ≥ 5 億）
B_LOWLIQ_MIN_CANDLES = 5            # 5 日均量 < 1,000 張的 B 候選，1 分 K 一天最少幾根才算完整日（低量股很多分鐘沒有成交，不會有分 K）

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

# ---- 指數代號（v69.2：名稱精確比對，不再用「名稱含某字」；清單順序變動也不會挑錯）----
INDEX_EXCHANGE = {"tse": "TWSE", "otc": "TPEx"}
INDEX_EXACT_NAMES = {"tse": ("發行量加權股價指數", "加權股價指數", "加權指數"), "otc": ("櫃買指數",)}
INDEX_KNOWN_SYMBOL = {"tse": "IX0001", "otc": "IX0043"}      # 名稱對不到時，只有「代號真的在該交易所清單裡」才採用

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


async def refresh_ic_chains(codes, budget=None) -> dict:
    budget = IC_FETCH_BUDGET_SECONDS if budget is None else budget
    today = now_tw().date()
    cache = _load_ic_cache()
    todo = [c for c in codes if not (c in cache and _ic_cache_fresh(cache[c], today))]
    print(f"[產業鏈] 快取 {len(codes) - len(todo)} 檔有效，需補查 {len(todo)} 檔（本次最多 {budget} 秒）", flush=True)
    fetched_ok = not_listed = failed = 0
    layout_errors = 0
    if todo:
        started = time.time()
        semaphore = asyncio.Semaphore(IC_MAX_CONCURRENCY)
        connector = aiohttp.TCPConnector(limit=IC_MAX_CONCURRENCY)
        async with aiohttp.ClientSession(connector=connector, headers=IC_HEADERS) as session:
            batch_size = IC_MAX_CONCURRENCY * 10
            for i in range(0, len(todo), batch_size):
                if time.time() - started > budget:
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
        self.index_diag = {}                 # {market: (kind, 說明)}：最近一次指數即時報價讀不到的原因；kind = 'error'（API 例外）/ 'empty'（回應成功但沒有價格）
        self._index_diag_logged = {}
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
        out, names, how = {"tse": None, "otc": None}, [], {}
        for mk, ex in INDEX_EXCHANGE.items():
            try:
                lst = self._index_list(ex)
            except Exception as exc:
                print(f"[富邦] 查詢 {ex} 指數清單失敗：{type(exc).__name__}: {exc}", flush=True)
                lst = []
            names += lst
            cands = [(s, n.strip()) for s, n in lst if "報酬" not in n]
            hit = next(((s, n) for s, n in cands if n in INDEX_EXACT_NAMES[mk]), None)
            if hit:
                out[mk], how[mk] = hit[0], f"名稱精確比對「{hit[1]}」"
            else:
                known = INDEX_KNOWN_SYMBOL[mk]
                hit = next(((s, n) for s, n in cands if s == known), None)
                if hit:
                    out[mk], how[mk] = hit[0], f"名稱不符，但代號 {known} 在 {ex} 清單內（{hit[1]}）"
        if out["tse"] is None:
            out["tse"], how["tse"] = INDEX_KNOWN_SYMBOL["tse"], "清單沒有對到，退回預設代號（未驗證）"
        self.index_syms, self.index_names = out, names
        print(f"[富邦] 指數代號：加權 = {out['tse']}（{how.get('tse', '—')}）｜櫃買 = {out['otc']}（{how.get('otc', '沒有對到；上櫃個股暫用加權指數判斷')}）", flush=True)
        if out["otc"] is None:
            print(f"[富邦] 櫃買 {INDEX_EXCHANGE['otc']} 清單名稱含「櫃買」的指數：{[(s, n) for s, n in names if '櫃買' in n and '報酬' not in n][:20]}", flush=True)
        return out

    def _index_list(self, exchange: str) -> list:
        """某交易所的指數清單 [(代號, 名稱)]。SDK 不認得 exchange 參數時，退回不帶參數（清單會混在一起）。"""
        try:
            with self.lock:
                r = self.stock.intraday.tickers(type="INDEX", exchange=exchange)
        except TypeError:
            with self.lock:
                r = self.stock.intraday.tickers(type="INDEX")
        return [(str(x.get("symbol")), str(x.get("name"))) for x in ((r.get("data") if isinstance(r, dict) else None) or [])]

    def index_price(self, market: str):
        """即時指數（lastPrice，沒有就用 closePrice）。失敗或沒有價格回傳 None，並把原因記在 self.index_diag（原因改變時才印 log，避免每 15 秒洗版）。"""
        def fail(kind: str, text: str):
            self.index_diag[market] = (kind, text)
            if self._index_diag_logged.get(market) != (kind, text):
                self._index_diag_logged[market] = (kind, text)
                print(f"[富邦] 指數 {market} 讀不到價格（{'API 例外' if kind == 'error' else '回應沒有價格'}）：{text}", flush=True)
            return None
        sym = self.index_symbols().get(market)
        if not sym:
            return fail("error", "沒有取得指數代號（指數清單查詢失敗或名稱對不到）")
        try:
            with self.lock:
                q = self.stock.intraday.quote(symbol=sym)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            return fail("error", f"{sym}｜{type(exc).__name__}" + (f" HTTP {status}" if status else "") + f"：{str(getattr(exc, 'response_text', exc))[:120]}")
        if not isinstance(q, dict):
            return fail("error", f"{sym}｜回應不是 dict（{type(q).__name__}）")
        price = _f(q.get("lastPrice")) or _f(q.get("closePrice"))
        if price is None:
            keys = sorted(k for k, v in q.items() if v not in (None, "", [], {}))
            return fail("empty", f"{sym}｜date={q.get('date')}｜lastPrice={q.get('lastPrice')}｜closePrice={q.get('closePrice')}｜"
                                 f"previousClose={q.get('previousClose')}｜有值的欄位 {keys[:14]}")
        if market in self.index_diag:
            self.index_diag.pop(market, None)
            self._index_diag_logged.pop(market, None)
            print(f"[富邦] 指數 {market} 已恢復讀取：{price}", flush=True)
        return price

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
def consecutive_above(closes, end_idx: int, cap: int = 30) -> int:
    """到 end_idx（含）為止，連續「收盤 > MA5 且 > MA10 且 > MA20」的天數（MA 為含當天收盤的真實均線）。第一個沒站上的日子就停；資料不足 20 日算 0。
    0 = 該日收盤不在三條均線之上（= 舊規則的「昨收 ≤ 昨日最高均線」）；1 = 該日是站上三均線的第一天。"""
    n, j = 0, end_idx
    while j >= 19 and n < cap:
        c = closes[j]
        if c > sum(closes[j - 4:j + 1]) / 5 and c > sum(closes[j - 9:j + 1]) / 10 and c > sum(closes[j - 19:j + 1]) / 20:
            n += 1
            j -= 1
        else:
            break
    return n


def above_days_of(base: dict) -> int:
    """基底裡「到昨天為止已連續站上三均線幾天」。舊版 initial_pools.json 沒有這欄：昨收 ≤ 昨日最高均線 = 0，否則視為很多天（99，不符合新鮮條件）。"""
    d = base.get("above_days")
    if d is None:
        return 0 if base["yest_close"] <= base["prev_ma_max"] else 99
    return int(d)


def a1_fresh(base: dict) -> bool:
    """A1「首次放量」資格：昨天以前連續站上三均線不超過 A1_LATE_DAYS - 1 天（N = 2 → 昨天剛好是第 1 天也算）。"""
    return above_days_of(base) <= A1_LATE_DAYS - 1


def a1_late_blocked(code: str, base: dict, pools: dict) -> bool:
    """「這一波沒發過 A1」：已經站上第 2 天以後（above_days ≥ 1）才補抓的，如果這檔已經在 A1 追蹤 / A2 池、或今天已通知待收盤確認，就是同一波，不重複發。
    above_days = 0（今天才是站上第一天）不受影響，維持原本行為。"""
    if above_days_of(base) < 1:
        return False
    return code in (pools.get("a") or {}) or code in (pools.get("a1_pending") or {})


def a1_eligible(code: str, base: dict, pools: dict) -> bool:
    """進 A1 候選池：流動性 ≥ 1,000 張（base["liquid"]）＋ 首次放量資格 ＋ 不是同一波已發過的。市場資料可靠性由呼叫端另外檢查。"""
    return bool(base["liquid"]) and a1_fresh(base) and not a1_late_blocked(code, base, pools)


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
    if not (a1_fresh(base) and price > mas[0] and price > mas[1] and price > mas[2]):                  # 站上三均線第 1～N 天內；今天現價同時站上即時三均線
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


def a_stage_from_days(n) -> str:
    """連續拉回整理天數 n → 階段。1～6 天（A2_MIN_DAYS～A2_MAX_DAYS）= "A2"（A2 池）；0 天（沒拉回）= "A1"（A1 追蹤，不會觸發 A2）。
    超過 6 天不是「回 A1」，而是直接移出（pool_a_close_update / apply_a_stages 處理）；這裡對超過 6 天的舊資料仍回 "A1" 只是保險。"""
    return "A2" if (n is not None and A2_MIN_DAYS <= int(n) <= A2_MAX_DAYS) else "A1"


def entry_pullback_days(e: dict) -> int:
    """追蹤標的目前連續拉回整理天數。優先用 pullback_days；舊版檔案沒有就用夜間算的 a2.n；再沒有就看 status 是否 Pullback（至少 1 天）。"""
    n = e.get("pullback_days")
    if n is None:
        n = (e.get("a2") or {}).get("n")
    if n is None:
        n = 1 if e.get("status") == "Pullback" else 0
    try:
        return int(n or 0)
    except (TypeError, ValueError):
        return 0


def entry_stage(e: dict) -> str:
    return a_stage_from_days(entry_pullback_days(e))


def apply_a_stages(pools: dict, bases: dict, day_str: str = None) -> dict:
    """夜間用日 K 重算每檔追蹤標的的連續拉回整理天數（consolidation_run），寫進 a2 / pullback_days / stage / status。
    連續拉回超過 A2_MAX_DAYS（6）天 → 直接移出（寫進 closed，原因「拉回逾 N 日未站回MA5」），不再退回 A1 追蹤。
    回傳 {"a1": A1 追蹤檔數, "a2": A2 池檔數, "removed": 本次移出檔數}。沒有日 K 的標的維持原值（舊檔案則由 entry_stage 推算）。day_str = 移出紀錄的日期（預設追蹤清單結算日）。"""
    day_str = day_str or pools.get("asof") or now_tw().strftime("%Y-%m-%d")
    n_a1 = n_a2 = n_out = 0
    for code, e in list(pools["a"].items()):
        a2 = (bases.get(code) or {}).get("a2")
        if a2 is not None:
            e["a2"] = a2
            e["pullback_days"] = int(a2.get("n") or 0)
        n = entry_pullback_days(e)
        if n > A2_MAX_DAYS:
            pools["a"].pop(code)
            close_record(pools, "A", code, e, day_str, f"拉回逾{A2_MAX_DAYS}日未站回MA5", e.get("last_close"), {"pullback_days": n})
            n_out += 1
            print(f"[A 追蹤] {code} {e.get('name', '')} 連續拉回 {n} 天（> {A2_MAX_DAYS}）→ 移出", flush=True)
            continue
        e["stage"] = entry_stage(e)
        e["status"] = "Pullback" if n >= 1 else "Active"
        if e["stage"] == "A2":
            n_a2 += 1
        else:
            n_a1 += 1
    return {"a1": n_a1, "a2": n_a2, "removed": n_out}


def a2_stage(a2: dict, floor_low, price, day_low, mas, vwap, est, mode: str):
    """A2：整理期 A2_MIN_DAYS～A2_MAX_DAYS 天（目前 1～6）；今日最低價 ≥ 突破日低點；現價 > 即時 MA5 且 > 即時 VWAP；預估全日量 ≥ 整理期平均量 × 1.2；預估全日金額 ≥ 20 億。只在 NORMAL 發。"""
    if mode != "NORMAL" or not a2 or not a2.get("n") or not a2.get("avg_vol"):
        return "none"
    if not (A2_MIN_DAYS <= a2["n"] <= A2_MAX_DAYS):
        return "days"
    if floor_low and (day_low is None or day_low < floor_low):
        return "floor"
    if price <= mas[0]:
        return "ma5"
    if A2_REQUIRE_ALL_MAS and not (price > mas[1] and price > mas[2]):
        return "mas"
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
    """B 隔日確認：今日最低價 ≥ B 訊號日最低價；預估全日量 ≤ B 訊號日量 × 0.6；現價 > 即時 MA5（可關）且 > 即時 VWAP（可關）；預估全日金額 ≥ 5 億（B_MIN_EST_TURNOVER）。
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
    """前 19 個完整交易日收盤平均 P（= 盤中指數剛好等於即時 MA20 的「穿越價」，只用來顯示與驗算）。
    盤中判斷不直接用 P，而是每輪用 IndexWatch.ma20() 明確算出即時 MA20 =（前 19 日收盤合計 + 即時指數）/ 20 再比較（數學上等價於 指數 ≥ P）。資料不足回傳 None。"""
    return sum(prior_closes[-19:]) / 19.0 if len(prior_closes) >= 19 else None


class IndexWatch:
    """單一指數的趨勢狀態機（v69.3：盤中每 15 秒明確計算即時 MA20）。
    即時 MA20 =（前 19 個完整交易日指數收盤合計 sum19 + 本輪即時指數）/ 20；指數 < 即時 MA20 → 在月線下方。
    NORMAL → BEAR：連續 REGIME_ENTER_BEAR_ROUNDS 輪低於即時 MA20；
    BEAR → NORMAL：站回即時 MA20（≥）並連續維持 REGIME_EXIT_HOLD_SEC 秒，期間任何一輪再跌回月線下方，計時歸零。讀不到指數的那一輪視為無法確認（計數歸零）。
    sum19 來自夜間存進 initial_pools.json 的 closes19；舊檔沒有 closes19 時，用 p × 19 還原。"""

    def __init__(self, name: str, p: float, state: str = "NORMAL", last_close: float = None, closes19=None):
        if closes19 and len(closes19) >= 19:
            self.sum19 = float(sum(closes19[-19:]))
        else:
            self.sum19 = float(p) * 19.0
        self.name, self.state, self.last_close = name, state, last_close
        self.p = self.sum19 / 19.0                  # 穿越價（只用來顯示與驗算）
        self.below_rounds = 0
        self.above_since = None
        self.last_reading = None
        self.last_ma20 = None
        self.switches = 0

    def ma20(self, reading: float) -> float:
        """即時 MA20 =（前 19 個完整交易日收盤合計 + 即時指數）/ 20。"""
        return (self.sum19 + reading) / 20.0

    def update(self, reading, now_sec: float):
        """每輪餵入即時指數與時間（秒）。回傳 'TO_BEAR'（本輪正式轉空頭）/ 'TO_NORMAL'（本輪正式恢復多頭）/ None。"""
        if reading is None:
            self.below_rounds = 0
            self.above_since = None
            return None
        self.last_reading = reading
        self.last_ma20 = self.ma20(reading)
        below = reading < self.last_ma20
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
        watches[mk] = IndexWatch(c.get("name", mk), float(p), state if state in ("NORMAL", "BEAR") else "NORMAL", c.get("last_close"),
                                 c.get("closes19"))
    return Regime(watches)


# =========================
# 股池狀態（signal_history.json，rule = q2）
#   a1_pending：今日盤中 A1 已通知、等收盤確認   a：A1_CLOSE_CONFIRMED（A1／A2 狀態追蹤，無固定期限）   b：B 池（Watching 等待 5 日 / Active 已發動）
# =========================
def new_pools() -> dict:
    return {"rule": POOL_RULE, "asof": "", "updated": "", "days": [], "a1_pending": {}, "a": {}, "b": {}, "closed": []}


def _closed_key(r: dict):
    return (r.get("pool"), r.get("code"), r.get("date"), str(r.get("reason", "")).replace("（補歷史）", ""), r.get("entry_date") or r.get("burst_date") or "")


def dedupe_closed(closed: list) -> list:
    """移出紀錄去重：同一池／代號／移出日／原因／進場日只留一筆。補歷史（backfill_history）重跑時會把同一批「（補歷史）」推估的移出紀錄一再累加，
    也會和 radar 當天真實結算的紀錄重複；真實結算的優先於「（補歷史）」推估的。維持原本的時間順序。"""
    out, pos = [], {}
    for r in closed or []:
        k = _closed_key(r)
        if k not in pos:
            pos[k] = len(out)
            out.append(r)
        elif "（補歷史）" in str(out[pos[k]].get("reason", "")) and "（補歷史）" not in str(r.get("reason", "")):
            out[pos[k]] = r
    return out


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
                  "b": dict(data.get("b") or {}), "closed": dedupe_closed(list(data.get("closed") or []))})
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
    pools["closed"] = dedupe_closed(pools.get("closed") or [])[-CLOSED_KEEP:]
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
            "days_left": None, "a2_dates": e.get("a2_dates") or [], "new_today": new_today,
            "provisional": bool(e.get("close_provisional")), "stage": entry_stage(e), "pullback_days": entry_pullback_days(e)}


def a1_confirm_settle(pools: dict, bases: dict, closes: dict, today_str: str, skip=()):
    """A1 盤中通知 → 收盤確認：收盤價站上（含收盤價重算的）MA5/MA10/MA20 ＋ 收盤未跌破突破日低點 → A1_CLOSE_CONFIRMED，先進「A1 追蹤」（stage = A1；之後某天收盤跌破 MA5 守住 MA10 才進 A2 池）（突破日低點 = 當日最低價）。
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
                 "breakout_low": low, "status": "Active", "stage": "A1", "pullback_days": 0, "origin": "A1_CLOSE_CONFIRMED",
                 "last_close": close, "last_date": today_str,
                 "miss_days": 0, "a2_dates": [], "close_src": "final_snapshot", "close_provisional": False, "src": "live"}
        pools["a"][code] = new_e
        codes.append(code)
        rows.append(_a_row(pools, code, new_e, close, ma5, ma10, ma20, True))
    return rows, codes, failed


def pool_a_close_update(pools: dict, bases: dict, closes: dict, today_str: str, skip=()):
    """A1 追蹤 / A2 池收盤結算（只處理「今天以前」已確認的）。回傳 (列表列, 今日移出紀錄)。
    移出：① 收盤跌破 MA10 ② 收盤跌破突破日低點 ③ 連續拉回超過 A2_MAX_DAYS（6）天仍沒站回 MA5（直接移出，不回 A1）。
    不設固定追蹤天數：收盤 ≥ MA5 → 持續留在 A1 追蹤；收盤 < MA5 且守住 MA10 / 突破日低點 → 進 A2 池（1～6 天）；重新站回 MA5 → 回 A1 追蹤。"""
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
        # 連續拉回整理天數：今天收盤 < MA5（且上面已確認守住 MA10 與突破日低點）→ +1；站回 MA5 以上 → 歸 0
        n_pb = (entry_pullback_days(e) + 1) if close < ma5 else 0
        if n_pb > A2_MAX_DAYS:                                      # 拉回連續超過 6 天都沒站回 MA5 → 直接移出（不回 A1 追蹤）
            pools["a"].pop(code)
            exits.append(close_record(pools, "A", code, e, today_str, f"拉回逾{A2_MAX_DAYS}日未站回MA5", close, {"age": age, "pullback_days": n_pb}))
            continue
        e["pullback_days"] = n_pb
        e["stage"] = a_stage_from_days(n_pb)                        # 1～6 天 = A2 池；0 天或超過 6 天 = 仍在 A1 追蹤
        e["status"] = "Pullback" if n_pb >= 1 else "Active"
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


def day_curves(candles: list, days: list, min_candles: int = None):
    """把 1 分 K 依日期整理成 {日期: 長度 MATRIX_POINTS 的累積量 list}：cum[m] = 開盤後第 m 分鐘『開始前』的累積量（張），cum[271] = 全日量。
    某天分 K 不足 min_candles（預設 BASELINE_MIN_CANDLES_PER_DAY）根就不放進結果。"""
    min_candles = BASELINE_MIN_CANDLES_PER_DAY if min_candles is None else min_candles
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
        if counts[d] < min_candles:
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
    return {"r": r, "last": last, "last_day": days[-1] if last is not None else None, "n": len(full)}


def blend_r(entries: dict, ref=None) -> None:
    """個股佔比與全市場佔比（各分鐘的中位數）以 R_BLEND（70:30）混合，就地修改。
    ref = 只用這些代號的曲線算「全市場曲線」（低量股的曲線很零碎，不拿來拉動 A1 候選股的市場曲線）；沒有指定或交集為空 → 用全部。"""
    if not entries:
        return
    src = [e for c, e in entries.items() if ref is None or c in ref] or list(entries.values())
    mkt = [_median([e["r"][j] for e in src]) for j in range(MATRIX_POINTS)]
    for e in entries.values():
        e["r"] = [round(R_BLEND[0] * a + R_BLEND[1] * b, 4) for a, b in zip(e["r"], mkt)]
        e["r"][-1] = 1.0


def trading_days(feed, asof: date_cls) -> list:
    """用 2330 的日 K 取得最近 R_DAYS 個交易日（含基準日）。"""
    start = (asof - timedelta(days=int(R_DAYS * 7 / 5) + 14)).isoformat()
    rows = feed.candles("2330", start, asof.isoformat(), timeframe="D", fields=None)
    days = sorted({str(r.get("date"))[:10] for r in rows if str(r.get("date"))[:10] <= asof.isoformat()})
    return days[-R_DAYS:]


def fetch_minute_curves(feed, code: str, days: list, chunk: int, min_candles: int = None):
    """抓 1 分 K 並整理成 {日期: 累積量曲線}；days 依 chunk 分段請求（富邦單次回傳筆數上限沒有文件，所以可調）。"""
    candles = []
    for i in range(0, len(days), chunk):
        seg = days[i:i + chunk]
        feed._hist_pace()
        candles += feed.candles(code, seg[0], seg[-1], timeframe="1")
    return day_curves(candles, days, min_candles), len(candles)


class CurveFetcher:
    """1 分 K → 累積量曲線。前 3 檔驗證「一次請求多日」是否回傳完整；不完整就自動改成每 5 個交易日一段（背景預抓與現場補抓共用）。"""

    def __init__(self, feed, days: list):
        self.feed, self.days, self.chunk, self.probe = feed, days, BASELINE_CHUNK_DAYS, []

    def fetch(self, code: str, min_candles: int = None) -> dict:
        curves, n_candles = fetch_minute_curves(self.feed, code, self.days, self.chunk, min_candles)
        if len(self.probe) < 3:
            self.probe.append(len(curves) / max(1, len(self.days)))
            print(f"  [診斷] {code}：分K {n_candles} 根（每段 {self.chunk} 日），完整日 {len(curves)}/{len(self.days)}", flush=True)
            if len(self.probe) == 3 and self.chunk > 5 and sum(1 for p in self.probe if p < 0.7) >= 2:
                self.chunk = 5
                print("  [診斷] 一次請求多日只拿到部分日期 → 改成每 5 個交易日一段（耗時會變長）", flush=True)
                send_telegram(f"⚠️ <b>【分時量矩陣】</b> 富邦 1 分 K 一次請求 {BASELINE_CHUNK_DAYS} 個交易日回傳不完整，已自動改成每 5 日一段（耗時約 4 倍）。")
                curves, n_candles = fetch_minute_curves(self.feed, code, self.days, self.chunk, min_candles)
        return curves


def check_against_daily(entries: dict, series: dict) -> str:
    """分 K 加總（最後一個取樣日的全日量）對照同一天的日 K 成交量，確認單位。
    官方日 K 的量含盤後零股 / 定價，通常比分 K 加總多約 1～2%，所以容許 ±5%。"""
    import statistics
    ratios = []
    for code, e in entries.items():
        s = series.get(code)
        if not s or not e.get("last") or not e.get("last_day"):
            continue
        try:
            i = [d.isoformat() for d in s["dates"]].index(e["last_day"])
        except ValueError:
            continue
        if s["v"][i] and s["v"][i] > 0:
            ratios.append(e["last"] / s["v"][i])
    if len(ratios) < 10:
        return "分K / 日K 對照略過（可對照的股票太少）"
    med = statistics.median(ratios)
    near = sum(1 for r in ratios if abs(r - 1) <= 0.05) / len(ratios)
    verdict = "✅ 一致" if abs(med - 1) <= 0.05 else "⚠️ 不一致，請檢查單位或 13:30 收盤集合競價那一根"
    return f"分K加總 / 日K成交量：中位數 {med:.3f}，±5% 內 {near:.0%}（{len(ratios)} 檔）{verdict}"


def build_volume_matrix(codes, asof: date_cls, feed, lowliq=None, curves_cache=None, max_new=None, series=None):
    """用富邦 1 分 K 為「候選股」預先算好分時累積佔比矩陣。成功回傳 (entries, days, 說明)；失敗（會發 Telegram）回傳 (None, None, 原因)。
    取樣日 = 截至 asof 富邦已經有的最近 R_DAYS 個交易日（13:35 收盤後算的時候，今天的 1 分 K 還沒更新，所以是截至昨天；傍晚以後算就會含今天）。
    curves_cache = 背景預抓好的 {代號: 曲線}（低量股的完整日門檻不同，不用快取）；max_new = 現場最多補抓幾檔，超過的略過；series = 日 K（用來對照單位）。
    entries = {代號: {"r": [272 個佔比], "last": 最後取樣日全日量, "n": 完整日數}}；寫檔由呼叫端一次寫進 initial_pools.json。
    lowliq = 5 日均量 < 1,000 張的候選（B 池全市場）：分 K 完整日門檻降為 B_LOWLIQ_MIN_CANDLES，且不參與「全市場曲線」的計算。
    回傳的說明字串會註明有幾檔低量股沒算出矩陣（這些股票盤中不會被 B 確認）。"""
    lowliq = set(lowliq or ())
    curves_cache = curves_cache or {}
    started = time.time()
    title = "【分時量矩陣】盤後預計算"
    universe = sorted(codes)
    try:
        days = trading_days(feed, asof)
    except Exception as exc:
        msg = f"查詢富邦交易日失敗：{type(exc).__name__}: {exc}"
        send_telegram(f"❌ <b>{title}</b> {msg}")
        return None, None, msg
    if len(days) < R_MIN_DAYS:
        msg = f"富邦只查到 {len(days)} 個交易日（至少要 {R_MIN_DAYS} 個）"
        send_telegram(f"❌ <b>{title}</b> {msg}")
        return None, None, msg
    print(f"[分時量矩陣] 基準日 {asof}，候選 {len(universe)} 檔（預抓 {sum(1 for c in universe if c in curves_cache)} 檔），取樣 {len(days)} 日：{days[0]} ～ {days[-1]}", flush=True)
    fetcher = CurveFetcher(feed, days)
    entries, skipped, errors, new_fetched = {}, [], 0, 0
    for n, code in enumerate(universe, 1):
        low = code in lowliq
        curves = None if low else curves_cache.get(code)
        if curves is None:
            if max_new is not None and new_fetched >= max_new:
                skipped.append(code)
                continue
            new_fetched += 1
            try:
                curves = fetcher.fetch(code, B_LOWLIQ_MIN_CANDLES if low else None)
            except Exception as exc:
                errors += 1
                skipped.append(code)
                print(f"  {code} 抓取失敗：{type(exc).__name__}: {exc}", flush=True)
                continue
        entry = build_entry(curves, days)
        if entry is None:
            skipped.append(code)
        else:
            entries[code] = entry
        if n % 100 == 0:
            print(f"  進度 {n}/{len(universe)}，成功 {len(entries)}，略過 {len(skipped)}，現場補抓 {new_fetched}，已用 {(time.time() - started) / 60:.1f} 分鐘", flush=True)
    coverage = len(entries) / len(universe) if universe else 0
    if coverage < BASELINE_MIN_COVERAGE:
        msg = f"成功率只有 {coverage:.0%}（{len(entries)}/{len(universe)}，抓取錯誤 {errors}）"
        send_telegram(f"❌ <b>{title}</b> {msg}，未更新 initial_pools.json。請看 Actions log 的診斷行。")
        return None, None, msg
    check_text = check_against_daily(entries, series) if series else "（未對照日 K）"
    print(f"[分時量矩陣] {check_text}", flush=True)
    blend_r(entries, ref=set(entries) - lowliq)
    minutes = (time.time() - started) / 60
    skipped_txt = f"｜略過 {len(skipped)} 檔（完整日不足 {R_MIN_DAYS} 天、抓取失敗或超過現場補抓上限）" if skipped else ""
    skipped_low = [c for c in skipped if c in lowliq]
    if skipped_low:
        skipped_txt += f"，其中低量股 {len(skipped_low)} 檔（{'、'.join(skipped_low[:8])}{'…' if len(skipped_low) > 8 else ''}）盤中不會被 B 確認"
    note = f"涵蓋 {len(entries)}/{len(universe)} 檔{skipped_txt}｜現場補抓 {new_fetched} 檔，耗時 {minutes:.1f} 分鐘｜{check_text}"
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
    """要抓日 K 的股票範圍。B_FULL_MARKET = True（預設）：全市場（B 池不篩 5 日均量，要全市場確認）。
    否則預篩（快照全日量 ≥ 5 日均量門檻 × PREFILTER_RATIO）；預篩後太少（例如盤前快照量為 0）→ 改抓全部股票。"""
    if B_FULL_MARKET:
        return fubon_universe(snap, 0.0)
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
    base["above_days"] = consecutive_above(s["c"], len(s["c"]) - 1)                 # v70.3：到基準日為止連續站上三均線幾天（0 = 基準日收盤不在三均線之上）
    base["b_hit"] = b_scan_last(s) if (B_ENABLED and avg5 >= B_MIN_VOL5_LOTS) else None      # B 不看 liquid（5 日均量 1,000 張）；B_MIN_VOL5_LOTS = 0 → 全市場
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
# 收盤後選池（v70）：歷史日 K（背景預抓）＋ 當天最終快照 → 明天的 A1 / A2 / B 池
# =========================
class ScanError(Exception):
    """收盤後選池失敗（資料不足 / 抓取失敗）。訊息會顯示在 Telegram。"""


def bars_from_snapshot(snap: dict, day: date_cls) -> dict:
    """最終快照 → 當天日 K 棒 {代號: {date, open, high, low, close, volume(張), value(元)}}。
    只收「收盤價已產生且不是試撮」而且有成交的股票（停牌 / 暫緩收盤的略過，今晚不納入選池）。"""
    out = {}
    for code, row in snap["rows"].items():
        info, _ = close_info_from_row(row)
        if not info or info["vol"] <= 0:
            continue
        close = info["close"]
        out[code] = {"date": day, "open": info["open"] or close, "high": info["high"], "low": info["low"], "close": close,
                     "volume": info["vol"], "value": info["val"] or close * info["vol"] * 1000.0}
    return out


def merge_today_bar(series: dict, bars: dict, day: date_cls) -> int:
    """把當天的 K 棒接在歷史日 K 後面（歷史已經有當天的官方資料就不動）。回傳接上幾檔。"""
    n = 0
    for code, s in series.items():
        bar = bars.get(code)
        if bar is None or not s["dates"] or s["dates"][-1] >= day:
            continue
        s["dates"].append(day)
        s["o"].append(bar["open"])
        s["h"].append(bar["high"])
        s["l"].append(bar["low"])
        s["c"].append(bar["close"])
        s["v"].append(bar["volume"])
        s["val"].append(bar["value"])
        n += 1
    return n


def unit_sanity(feed, series: dict, bars: dict, day: date_cls) -> str:
    """歷史日 K 最後一天（昨天）的成交量 ÷ 今天快照的成交量，中位數應該在 1 附近（相鄰兩天量能差不多）。
    若比值約 0.001 → 日 K 的量其實已經是「張」，自動 ×1000 校正；差很多就在訊息裡提醒。"""
    import statistics
    ratios = [s["v"][-1] / bars[c]["volume"] for c, s in series.items()
              if c in bars and s["dates"] and s["dates"][-1] < day and s["v"][-1] > 0 and bars[c]["volume"] > 0]
    if len(ratios) < 20:
        return ""
    med = statistics.median(ratios)
    if 0.0008 <= med <= 0.0012:
        for s in series.values():
            s["v"] = [x * 1000.0 for x in s["v"]]
        feed.daily_vol_div = 1.0
        return f"日K成交量單位其實是「張」（昨日 / 今日快照量中位數 {med:.4f}），已自動校正"
    if not (0.2 <= med <= 5.0):
        return f"日K成交量與今日快照量級差很多（昨日 / 今日中位數 {med:.3f}），請檢查單位"
    return ""


def build_universe(snap: dict, pools: dict) -> dict:
    """要抓日 K 的股票：pick_universe（B_FULL_MARKET = 全市場）＋ 追蹤清單內的代號（即使被濾掉也要有日 K）。"""
    uni = pick_universe(snap)
    for code in set(tracked_from_pools(pools)) | extra_codes_from_pools(pools):
        if code in snap["rows"] and code not in uni:
            row = snap["rows"][code]
            uni[code] = {"name": str(row.get("name") or code), "market": row["_mkt"], "snap_vol": _f(row.get("tradeVolume")) or 0.0}
    return uni


class MarketPrefetch:
    """背景預抓（v70）：radar 一啟動就在背景執行緒慢慢抓「截至昨天」的全市場日 K、產業別、一般股的 1 分 K
    （富邦限速約每秒 1 次：日 K 約 37 分鐘、1 分 K 約 11 分鐘，盤中就會抓完）。13:35 收盤後要選明天的池時，資料已經在記憶體，不用再等。
    主迴圈與背景共用同一把 SDK 鎖，每次呼叫只占用一次 HTTP 的時間，不會卡住 15 秒一輪的快照。"""

    def __init__(self, feed, universe: dict, start: date_cls, end: date_cls):
        self.feed, self.universe, self.start_day, self.end_day = feed, universe, start, end
        self.series, self.industry, self.curves = {}, {}, {}
        self.fail, self.error = 0, None
        self.stop = threading.Event()
        self.hist_done = threading.Event()
        self.all_done = threading.Event()
        self._thread = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="market-prefetch", daemon=True)
        self._thread.start()

    def run_blocking(self) -> None:
        self._run()

    def wait_hist(self, timeout: float) -> bool:
        return self.hist_done.wait(timeout)

    def wait_all(self, timeout: float) -> bool:
        return self.all_done.wait(timeout)

    def coverage(self):
        return len(self.series), len(self.universe)

    def _run(self) -> None:
        try:
            try:
                self.industry = self.feed.industry_map()
            except Exception as exc:
                print(f"[預抓] 產業別查詢失敗：{type(exc).__name__}: {exc}", flush=True)
            self._fetch_hist()
            self.hist_done.set()
            self._fetch_curves()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            print(f"[預抓] 中斷：{self.error}", flush=True)
        finally:
            self.hist_done.set()
            self.all_done.set()

    def _fetch_hist(self) -> None:
        started, total = time.time(), len(self.universe)
        print(f"[預抓] 日K {total:,} 檔（{self.start_day} ～ {self.end_day}），約 {total * BASELINE_CALL_INTERVAL / 60:.0f} 分鐘", flush=True)
        for i, (code, u) in enumerate(sorted(self.universe.items()), 1):
            if self.stop.is_set():
                return
            try:
                rows = self.feed.daily_candles(code, self.start_day, self.end_day)
            except Exception as exc:
                self.fail += 1
                print(f"  {code} 日K抓取失敗：{type(exc).__name__}: {exc}", flush=True)
                continue
            if rows:
                self.series[code] = series_from_candles(u["name"], u["market"], rows)
            if i % 200 == 0 or i == total:
                print(f"[預抓] 日K進度 {i}/{total}，成功 {len(self.series)}，失敗 {self.fail}，已用 {(time.time() - started) / 60:.1f} 分鐘", flush=True)

    def _fetch_curves(self) -> None:
        codes = sorted(c for c, s in self.series.items() if len(s["v"]) >= 5 and sum(s["v"][-5:]) / 5 >= MIN_VOL5_LOTS)
        days = trading_days(self.feed, self.end_day)
        if len(days) < R_MIN_DAYS or not codes:
            return
        started = time.time()
        print(f"[預抓] 1 分 K：一般股 {len(codes)} 檔，取樣 {len(days)} 日（{days[0]} ～ {days[-1]}），約 {len(codes) * BASELINE_CALL_INTERVAL / 60:.0f} 分鐘", flush=True)
        fetcher = CurveFetcher(self.feed, days)
        for i, code in enumerate(codes, 1):
            if self.stop.is_set():
                return
            try:
                self.curves[code] = fetcher.fetch(code)
            except Exception as exc:
                print(f"  {code} 1 分 K 預抓失敗：{type(exc).__name__}: {exc}", flush=True)
            if i % 100 == 0 or i == len(codes):
                print(f"[預抓] 1 分 K 進度 {i}/{len(codes)}，成功 {len(self.curves)}，已用 {(time.time() - started) / 60:.1f} 分鐘", flush=True)


def start_prefetch(feed, pools: dict, today: date_cls):
    """啟動背景預抓。失敗回傳 None（13:35 選池時改成現場抓，約 46 分鐘）。"""
    try:
        snap = feed.snapshot_all()
        pf = MarketPrefetch(feed, build_universe(snap, pools), today - timedelta(days=LOOKBACK_CALENDAR_DAYS), today)
        pf.start()
        return pf
    except Exception as exc:
        print(f"[預抓] 啟動失敗（{type(exc).__name__}: {exc}），收盤後選池時改成現場抓", flush=True)
        return None


def scan_core(feed, asof: date_cls, series: dict, universe: dict, pools: dict, *, industry=None, curves_cache=None, snap=None, notes=None, ic_budget=None,
              max_new_curves=SCAN_MAX_NEW_CURVES):
    """收盤後選池核心（radar 13:35 之後、補歷史最後共用）。series 必須已經含 asof 當天的 K 棒；pools 必須已經結算過 asof。
    做的事（等同 v69 夜間 baseline 的選池部分）：全市場基底 → A1 候選池 → A1 追蹤 / A2 池階段重算 → B 池掃描 → 分時量矩陣 → 指數基準 → 原子寫入 initial_pools.json。
    不做：官方法人（baseline 傍晚算，radar 隔天盤前讀 institutional.json）。成功回傳 (init, bases, index_ctx, info)；失敗丟 ScanError。
    max_new_curves = 現場最多補抓幾檔 1 分 K（radar 有背景預抓，所以限 150 檔只補漏；補歷史沒有預抓，要傳 None = 不設上限，約 460 檔 × 1.15 秒 ≈ 9 分鐘）。"""
    asof_str = asof.isoformat()
    ok_series = {c: s for c, s in series.items() if s["dates"] and s["dates"][-1] == asof}
    if not universe or len(ok_series) / len(universe) < FETCH_MIN_COVERAGE:
        raise ScanError(f"日 K 覆蓋率過低（有 {asof_str} 這一天的 {len(ok_series)} 檔 / 範圍 {len(universe)} 檔）")
    mark_unreliable(universe, ok_series)
    tracked, extra = tracked_from_pools(pools), extra_codes_from_pools(pools)
    all_bases = {}
    for code, s in ok_series.items():
        b = make_base(code, s, (industry or {}).get(code, ""), tracked.get(code))
        if b:
            all_bases[code] = b
    bases = {c: b for c, b in all_bases.items()
             if (b["liquid"] or c in extra or b.get("b_hit")) and b["market"] not in UNRELIABLE_MARKETS}
    liquid = {c: b for c, b in bases.items() if b["liquid"]}
    if not liquid:
        raise ScanError("沒有可用的股票（日 K 資料不完整或均量不足）")
    n_b_hit = sum(1 for b in all_bases.values() if b.get("b_hit"))
    print(f"掃描 {len(all_bases):,} 檔｜達標（5 日均量 ≥ {MIN_VOL5_LOTS:,} 張）{len(liquid):,} 檔｜今晚 B 型態命中 {n_b_hit} 檔", flush=True)
    try:
        chain_map = asyncio.run(refresh_ic_chains(sorted(liquid), budget=ic_budget))
    except Exception as exc:
        print(f"[產業鏈] 查詢流程例外，改用富邦產業別: {type(exc).__name__}: {exc!r}", flush=True)
        chain_map = {}
    apply_ic_chains(bases, chain_map)
    stage_cnt = apply_a_stages(pools, bases, asof_str)
    print(f"[A 追蹤] A1 追蹤 {stage_cnt['a1']} 檔｜A2 池（拉回 {A2_MIN_DAYS}～{A2_MAX_DAYS} 天）{stage_cnt['a2']} 檔｜拉回逾 {A2_MAX_DAYS} 天移出 {stage_cnt.get('removed', 0)} 檔", flush=True)
    for cd, e in sorted(pools["a"].items()):
        print(f"    {cd} {e.get('name', '')}｜{e.get('stage')}｜A1 確認 {e.get('entry_date')}｜連續拉回 {e.get('pullback_days', 0)} 天", flush=True)
    merge = merge_b_candidates(pools, bases)
    print(f"[B 池] 今晚新增 {merge['added']} 檔、略過 {merge['skipped']} 檔（已在池中或同一訊號日已移出）", flush=True)
    a1_codes = [c for c, b in bases.items() if a1_eligible(c, b, pools)]
    matrix_codes = sorted(c for c in (set(a1_codes) | set(pools["a"]) | set(pools["b"])) if c in bases)
    lowliq = {c for c in matrix_codes if not bases[c]["liquid"]}
    print(f"[候選池] A1 候選 {len(a1_codes)} 檔、A1 追蹤＋A2 池 {len(pools['a'])} 檔、B 池 {len(pools['b'])} 檔 → 分時量矩陣 {len(matrix_codes)} 檔（低量股 {len(lowliq)} 檔）", flush=True)
    matrix, matrix_days, matrix_note = build_volume_matrix(matrix_codes, asof, feed, lowliq, curves_cache=curves_cache,
                                                           max_new=max_new_curves, series=ok_series)
    if matrix is None:
        raise ScanError(f"分時量矩陣失敗：{matrix_note}")
    index_ctx = index_regime_snapshot(feed, asof)
    if not index_ctx:
        print("[指數] 沒有取得任何指數基準，隔天 radar 會自己補抓（盤前）", flush=True)
    init = assemble_initial_pools(bases, pools, asof_str, index_ctx, {}, matrix, matrix_days, matrix_note, notes=list(notes or []))
    save_initial_pools(init)                                # 先存 initial_pools.json（原子寫入），再存追蹤清單
    save_pools(pools)
    info = {"market_total": len(snap["rows"]) if snap else len(universe), "n_scanned": len(all_bases), "n_liquid": len(liquid),
            "n_b_new": merge["added"], "stage": stage_cnt}
    return init, bases, index_ctx, info


# =========================
# 法人檔 institutional.json（baseline 傍晚算；radar 隔天盤前讀）
# =========================
def build_inst_table(inst_by_day: dict, days: list) -> dict:
    """{代號: {"m": 市場, "f": [外資近 N 日], "t": [投信近 N 日]}}（張；最舊→最新；該市場那天沒有表 = None；有表但這檔不在表中 = 0）。"""
    keys = [d.isoformat() for d in days]
    out = {}
    for mk in ("tse", "otc"):
        codes = set()
        for k in keys:
            tbl = (inst_by_day.get(k) or {}).get(mk)
            if tbl:
                codes |= set(tbl)
        for code in codes:
            f, t = [], []
            for k in keys:
                tbl = (inst_by_day.get(k) or {}).get(mk)
                if tbl is None:
                    f.append(None)
                    t.append(None)
                else:
                    fv, tv = tbl.get(code, (0.0, 0.0))
                    f.append(round(fv / 1000.0, 1))
                    t.append(round(tv / 1000.0, 1))
            out[code] = {"m": mk, "f": f, "t": t}
    return out


def save_institutional(obj: dict) -> None:
    atomic_write_json(INST_FILE, obj)


def load_institutional():
    """回傳 (法人檔 或 None, 說明)。"""
    if not INST_FILE.exists():
        return None, "找不到 institutional.json（baseline 還沒跑過？）"
    try:
        data = json.loads(INST_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"institutional.json 讀取失敗（{type(exc).__name__}）"
    if data.get("rule") != INST_RULE:
        return None, f"institutional.json 規則版本不符（{data.get('rule')}）"
    m = data.get("meta") or {}
    return data, f"T 日 {m.get('t_date')}｜上市 {'✅' if m.get('tse_ok') else '❌'} 上櫃 {'✅' if m.get('otc_ok') else '❌'}｜{len(data.get('codes') or {})} 檔"


def _fill_inst(d: dict, rec: dict) -> None:
    f, t = rec["f"], rec["t"]
    d["inst5_f"], d["inst5_t"] = f, t
    d["inst5"] = [None if (x is None or y is None) else round(x + y, 1) for x, y in zip(f, t)]
    d["inst_t"] = d["inst5"][-1] if d["inst5"] else None


def apply_institutional(init: dict, pools: dict, inst) -> str:
    """把 baseline 傍晚算好的外資 / 投信近 5 日買賣超貼到夜間資料（A1 通知的法人燈號、A2 / B 的法人行都靠這個）。回傳警告（沒問題回傳空字串）。
    法人檔的 T 日不是基準日就不貼（避免把「前一日」標成錯的日期）；沒有法人檔就保留 initial_pools.json 裡原有的（舊版 baseline 寫進去的）。"""
    have_old = any(b.get("inst5_f") for b in list((init.get("stock_bases") or {}).values())[:20])
    if not inst:
        return "" if have_old else "尚無法人檔（institutional.json），A1 通知不顯示法人燈號"
    base_date = init["metadata"]["baseline_date"]
    t_date = (inst.get("meta") or {}).get("t_date")
    if t_date != base_date:
        return f"法人檔 T 日 {t_date} ≠ 基準日 {base_date}（昨晚 baseline 可能沒跑成功），今天不貼法人資料"
    codes = inst.get("codes") or {}
    for code, b in (init.get("stock_bases") or {}).items():
        if code in codes:
            _fill_inst(b, codes[code])
    for code, e in (init.get("B_candidates") or {}).items():
        if code in codes:
            _fill_inst(e, codes[code])
    for code, e in (pools.get("b") or {}).items():
        if code in codes:
            _fill_inst(e, codes[code])
    return ""


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
        nets, nets_f, nets_t = [], [], []
        for k in keys:
            tbl = (inst_by_day.get(k) or {}).get(b["market"])
            if tbl is None:
                nets.append(None)
                nets_f.append(None)
                nets_t.append(None)
            else:
                f, t = tbl.get(code, (0.0, 0.0))
                nets.append(round((f + t) / 1000.0, 1))
                nets_f.append(round(f / 1000.0, 1))
                nets_t.append(round(t / 1000.0, 1))
        b["inst5"] = nets
        b["inst5_f"] = nets_f                           # v69.7：外資單獨（張，最舊…最新）；A1 訊息用紅綠燈顯示
        b["inst5_t"] = nets_t                           # v69.7：投信單獨
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
        if hist and hist[-1][0] < asof:                    # 富邦歷史日 K 盤後 16:30 才更新：13:35 收盤後選池時還沒有基準日當天 → 用即時指數（此時已是收盤價）補上
            live = feed.index_price(mk)
            if live and abs(live / hist[-1][1] - 1) <= 0.25:
                print(f"[指數] {label} 歷史日 K 最後一天是 {hist[-1][0]}，基準日 {asof} 用即時指數 {live:,.2f} 補上", flush=True)
                hist.append((asof, float(live)))
        if len(hist) < 19:
            print(f"[指數] {label} 歷史不足 19 日（{len(hist)}），略過", flush=True)
            continue
        all_c = [float(v) for _, v in hist]
        closes = all_c[-19:]
        dates19 = [d.isoformat() for d, _ in hist][-19:]
        sum19 = sum(closes)
        p = sum19 / 19.0
        ma20_flat = (sum19 + closes[-1]) / 20.0               # 明天開盤若持平於昨收，盤中即時 MA20 的起點
        ma20_asof, dropped = None, None
        if len(all_c) >= 20:                                   # 看盤軟體在基準日當天看到的 MA20 = 含基準日在內的最近 20 個收盤平均
            ma20_asof = sum(all_c[-20:]) / 20.0
            dropped = (hist[-20][0].isoformat(), all_c[-20])  # 隔天的 19 日窗口會丟掉的那一天
        print(f"[指數] {label} 前 19 日收盤（{dates19[0]} ～ {dates19[-1]}）："
              + "、".join(f"{d[5:]} {v:,.2f}" for d, v in zip(dates19, closes)), flush=True)
        print(f"[指數] {label} 驗算：前 19 日合計 {sum19:,.2f}｜穿越價 P = 合計/19 = {p:,.2f}｜昨收 {closes[-1]:,.2f}｜"
              f"明天開盤持平的即時 MA20 = (合計 + 昨收)/20 = {ma20_flat:,.2f}", flush=True)
        if ma20_asof is not None:
            print(f"[指數] {label} 與看盤軟體對帳：{dates19[-1]} 當天 MA20（含當天的最近 20 日）應為 {ma20_asof:,.2f}"
                  f"（比 19 日窗口多一天：{dropped[0]} 收盤 {dropped[1]:,.2f}，隔天的窗口會把它丟掉）", flush=True)
        out[mk] = {"name": label, "symbol": syms[mk], "last_date": hist[-1][0].isoformat(), "stale": hist[-1][0] != asof,
                   "closes19": [round(v, 2) for v in closes], "closes19_dates": dates19, "sum19": round(sum19, 2), "p": round(p, 4),
                   "last_close": closes[-1], "ma20_flat": round(ma20_flat, 2),
                   "ma20_asof": None if ma20_asof is None else round(ma20_asof, 2),
                   "dropped_date": None if dropped is None else dropped[0], "dropped_close": None if dropped is None else round(dropped[1], 2),
                   "init_state": "NORMAL" if closes[-1] >= ma20_flat else "BEAR"}
    return out


def merge_b_candidates(pools: dict, bases: dict) -> dict:
    """把夜間掃到的 B 訊號合進 B 池（Watching）。已在池中、或同一 B 訊號日已移出過的不重複加入（重跑 baseline 也安全）。法人只當參考。"""
    done = {(r["code"], r.get("burst_date")) for r in pools.get("closed", []) if r.get("pool") == "B"}
    added = skipped = 0
    for code, base in sorted(bases.items()):
        h = base.get("b_hit")
        if not h or base["avg_5d_vol_simple"] < B_MIN_VOL5_LOTS or base["market"] in UNRELIABLE_MARKETS:
            continue                                         # B 不篩 1,000 張流動性（全市場），只看 B_MIN_VOL5_LOTS（預設 0）
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
    """組出 initial_pools.json：A1 候選池（5 日均量 ≥ 1,000 張 ＋ 昨收 ≤ 昨日最高均線）、A1 追蹤（收盤確認後還沒拉回）、A2 池（拉回整理 1～6 天）、B 候選池、
    分時量矩陣（候選股 × 開盤後每分鐘）、兩個市場的趨勢基準（前 19 日收盤）、法人參考資料。"""
    a1 = sorted(c for c, b in bases.items()
                if b["market"] not in UNRELIABLE_MARKETS and a1_eligible(c, b, pools))
    a2_pool, a1_track = {}, {}                  # A2 池 = 拉回整理 1～6 天；A1 追蹤 = A1 收盤確認後還沒拉回（或拉回已超過窗口）
    for code, e in pools["a"].items():
        item = dict(e)
        item["a2"] = (bases.get(code) or {}).get("a2") or e.get("a2") or {"n": 0, "avg_vol": None}
        item["pullback_days"] = int((item["a2"] or {}).get("n") or entry_pullback_days(e))
        item["stage"] = a_stage_from_days(item["pullback_days"])
        (a2_pool if item["stage"] == "A2" else a1_track)[code] = item
    b_cand = {}
    for code, e in pools["b"].items():
        item = dict(e)
        base = bases.get(code)
        if base and base.get("inst5"):
            item["inst5"], item["inst_t"] = base["inst5"], base.get("inst_t")
        b_cand[code] = item
    codes = set(a1) | set(a2_pool) | set(a1_track) | set(b_cand)
    stock_bases = {c: _clean_base(bases[c]) for c in sorted(codes) if c in bases}
    vtm = {c: matrix[c]["r"] for c in sorted(codes) if matrix and c in matrix}
    meta = {"rule": INITIAL_RULE, "data_version": 1, "baseline_date": asof_iso, "trade_date": next_weekday(date_cls.fromisoformat(asof_iso)).isoformat(),
            "built_at": now_tw().isoformat(timespec="seconds"), "complete": True,
            "counts": {"a1": len(a1), "a1_track": len(a1_track), "a2": len(a2_pool), "b": len(b_cand), "bases": len(stock_bases), "matrix": len(vtm)},
            "notes": list(notes or [])}
    return {
        "metadata": meta,
        "indicator_parameters": {
            "min_vol5_lots": MIN_VOL5_LOTS, "a1_chg_min": A1_CHG_MIN, "a1_late_days": A1_LATE_DAYS, "a1_vol_ratio": A1_VOL_RATIO, "a1_vol_ratio_bear": A1_VOL_RATIO_BEAR,
            "a1_min_est_lots": A1_MIN_EST_LOTS, "a_min_est_turnover": A_MIN_EST_TURNOVER, "a2_days": [A2_MIN_DAYS, A2_MAX_DAYS],
            "a2_vol_ratio": A2_VOL_RATIO, "a_track_days": None, "a_track_unlimited": True, "b_kd_low": B_KD_LOW, "b_vol_ratio": B_VOL_RATIO, "b_min_turnover": B_MIN_TURNOVER,
            "b_lowclose_pct": B_LOWCLOSE_PCT, "b_close_loc_min": B_CLOSE_LOC_MIN, "b_shrink_ratio": B_SHRINK_RATIO,
            "b_min_est_turnover": B_MIN_EST_TURNOVER, "b_wait_days": B_WAIT_DAYS, "regime_enter_bear_rounds": REGIME_ENTER_BEAR_ROUNDS,
            "regime_exit_hold_sec": REGIME_EXIT_HOLD_SEC, "r_blend": list(R_BLEND), "r_floor": R_FLOOR},
        "listed_market_regime": (index_ctx or {}).get("tse") or {},
        "otc_market_regime": (index_ctx or {}).get("otc") or {},
        "stock_bases": stock_bases,
        "A1_candidates": a1,
        "A1_tracking_pool": a1_track,
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
    return data, (f"基準日 {meta.get('baseline_date')}｜A1 候選 {c.get('a1', 0)} 檔、A1 追蹤 {c.get('a1_track', 0)} 檔、A2 池 {c.get('a2', 0)} 檔、B 候選 {c.get('b', 0)} 檔｜"
                  f"矩陣 {c.get('matrix', 0)} 檔")


def sync_pools_from_initial(pools: dict, init: dict) -> dict:
    """盤前：把夜間快照（initial_pools.json）與追蹤清單（signal_history.json）對齊。
    A1 追蹤 / A2 池（initial_pools.json 的 A1_tracking_pool、A2_tracking_pool；舊檔只有 A2_tracking_pool 且內含全部追蹤標的，也能讀）：
    快照有、清單沒有的補進去；拉回整理標記（a2、pullback_days、stage）一律以快照為準。
    B 候選：快照有、清單沒有且沒移出過的補進去（清單裡的狀態不覆蓋）。"""
    stats = {"a_added": 0, "b_added": 0}
    for key in ("A1_tracking_pool", "A2_tracking_pool"):
        for code, e in (init.get(key) or {}).items():
            mine = pools["a"].get(code)
            if mine is None:
                pools["a"][code] = {k: v for k, v in e.items()}
                mine = pools["a"][code]
                stats["a_added"] += 1
            else:
                mine["a2"] = e.get("a2")
                if e.get("pullback_days") is not None:
                    mine["pullback_days"] = e.get("pullback_days")
            mine["stage"] = entry_stage(mine)
            mine["status"] = "Pullback" if entry_pullback_days(mine) >= 1 else "Active"
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


def inst_dots(vals) -> str:
    """法人買賣超 → 紅綠燈：🔴 買超（>0）、🟢 賣超（<0）、⚪ 持平（0）、➖ 該日沒有資料。由左而右 = 前一日、前 2 日、前 3 日…（輸入是最舊→最新，這裡反轉）。"""
    def dot(v):
        if v is None:
            return "➖"
        return "🔴" if v > 0 else ("🟢" if v < 0 else "⚪")
    return " ".join(dot(v) for v in reversed(list(vals or [])))


def inst_dot_lines(sig: dict) -> list:
    """A1 訊息的法人區塊：有外資 / 投信分開的資料就各列一行；舊的夜間資料只有合計時，退回顯示合計一行；都沒有就不顯示。"""
    f, t = sig.get("inst5_f"), sig.get("inst5_t")
    if f and t:
        return ["▫️ <b>法人近 5 日買賣超</b>", f"      外資：{inst_dots(f)}", f"      投信：{inst_dots(t)}"]
    if sig.get("inst5"):
        return ["▫️ <b>法人近 5 日買賣超</b>（外資＋投信合計）", f"      {inst_dots(sig['inst5'])}"]
    return []


def inst_line(inst5) -> list:
    """近 5 日外資＋投信合計淨買賣超（張，由舊到新；None = 該日資料沒取得）。只當參考。"""
    if not inst5:
        return []
    cells = ["—" if v is None else f"{v:+,.0f}" for v in inst5]
    return [f"▫️ <b>法人參考（外資＋投信，近 {len(inst5)} 日，張）：</b>" + "／".join(cells)]


def bear_line(sig) -> list:
    return [f"⚠️ <b>大盤空頭（BEAR）</b>：A1 同期量比門檻提高為 {A1_VOL_RATIO_BEAR:g} 倍"] if sig.get("mode") == "BEAR" else []


def build_a1_message(sig: dict, n_today: int, now: datetime) -> str:
    """【A1：首發突破】一則訊息一檔股票（v69.7 版面）。不附停損價（停損你自己處理）。盤中通知；收盤站穩三均線才會進 A1 追蹤。"""
    t_now = now.strftime("%H:%M:%S")
    est = sig["est"]
    need = A1_VOL_RATIO_BEAR if sig.get("mode") == "BEAR" else A1_VOL_RATIO
    lines = [f"<b>【A1：首發突破】</b> <b>{sig['code']} {sig['name']}</b>"]
    if sig.get("mode") == "BEAR":
        lines.append(f"🔴 <b>大盤空頭</b>：同期量比提高為 {A1_VOL_RATIO_BEAR:g} 倍")
    if (sig.get("above_days") or 0) >= 1 and sig.get("above_days") != 99:
        lines.append(f"🕐 <b>站上三均線第 {sig['above_days'] + 1} 天首次放量</b>（昨天已收在三均線之上）")
    lines += [
        "━━━━━━━━━━━━━━━",
        f"📈 <b>現價：{sig['price']:.2f} ({_pc(sig['chg'])})</b>｜開 {_p2(sig.get('open'))}",
        "",
        "📊 <b>【進場條件（全部成立）】</b>",
        "▫️ 現價 > 即時 MA5、MA10、MA20",
        f"▫️ <b>VWAP：</b>{sig['vwap']:.2f}（現價高於 {_pc(sig['vwap_pct'])}）",
        f"▫️ <b>量比：</b>同期量比≥ {need:g}x",
        f"▫️ <b>預估全日量</b> {est['est_vol']:,.0f} 張（5 日均量 {sig['avg5']:,.0f}，≥ {A1_MIN_EST_LOTS:,} 張）",
        f"▫️ <b>成交額：</b>預估全日 {est['est_val'] / 1e8:.2f} 億（≥ {A_MIN_EST_TURNOVER / 1e8:g} 億）",
    ]
    lines += inst_dot_lines(sig)
    lines += ["", "🏷️ <b>【其他資訊】</b>", f"▫️ <b>族群：</b>{_short(sig.get('industry'))}",
              f"▫️ <b>確認：</b>{sig.get('first_time', '-')} ➔ {t_now}",
              "▫️ 收盤站穩三均線才進 A1 追蹤",
              "▫️ 之後拉回整理才進 A2 池",
              "",
              f"⏱️ <b>{t_now} ｜ 今日第 {n_today} 則</b>"]
    return "\n".join(lines)


def build_a2_message(sig: dict, n_today: int, now: datetime) -> str:
    """🟡【A2：二次加碼】"""
    t_now = now.strftime("%H:%M:%S")
    est = sig["est"]
    chg_since = _pc((sig["price"] / sig["entry_price"] - 1) * 100.0) if sig.get("entry_price") else "—"
    lines = [f"🟡 <b>【A2：二次加碼】</b> <b>{sig['code']} {sig['name']}</b>",
             f"💡 <b>再發動：</b>A1 收盤確認後進 A2 池：拉回整理 {sig['consol_days']} 天（收盤 < MA5、守住 MA10 與突破日低點），今日現價重新站上即時 MA5"
             + ("、MA10、MA20" if A2_REQUIRE_ALL_MAS else ""),
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
    ma = f"{w.last_ma20:,.2f}" if w.last_ma20 is not None else "—"
    if ev == "TO_BEAR":
        return (f"🔴 <b>【市場趨勢：空頭 BEAR】</b> {t_now}\n{w.name} 連續 {REGIME_ENTER_BEAR_ROUNDS} 輪低於盤中即時月線"
                f"（現 {cur}｜即時 MA20 {ma}｜穿越價 {w.p:,.2f}）\nA1 同期量比門檻提高為 {A1_VOL_RATIO_BEAR:g} 倍｜A2 禁止｜B 照常（{w.name} 對應的{'上市' if w.name == '加權' else '上櫃'}個股）")
    return (f"🟢 <b>【市場趨勢：多頭 NORMAL】</b> {t_now}\n{w.name} 站回盤中即時月線並連續維持 {REGIME_EXIT_HOLD_SEC // 60} 分鐘"
            f"（現 {cur}｜即時 MA20 {ma}｜穿越價 {w.p:,.2f}）\nA1 門檻回到 {A1_VOL_RATIO:g} 倍｜A2 恢復開放｜B 照常")


# ---------- 盤後總結（v69.8：A1 追蹤 / A2 池 / B 池合併成一則）----------
def _slash_date(day) -> str:
    """'2026-10-16' → '2026/10/16'。"""
    return str(day).replace("-", "/") if day else "—"


def _close_a1_lines(rows: list, hint: str = "pool_report.csv") -> list:
    out = [f"🟢 <b>A1 追蹤：{len(rows)} 檔</b>"]
    for r in sorted(rows, key=lambda r: r["gap_ma10"])[:LIST_MAX]:
        out.append(f" • {r['code']} {r['name']}｜{_slash_date(r['entry_date'])}納入｜已追蹤 {r.get('age', 0)} 日")
    if len(rows) > LIST_MAX:
        out.append(f" …另有 {len(rows) - LIST_MAX} 檔（完整名單見 {hint}）")
    out.append("移出：【收盤跌破 MA10／突破日低點】或【A2 連續拉回逾 6 日未站回 MA5】；A1 無固定期限")
    return out


def _close_a2_lines(rows: list, hint: str = "pool_report.csv") -> list:
    out = [f"🟡 <b>A2 池：{len(rows)} 檔</b>"]
    for r in sorted(rows, key=lambda r: r["gap_ma10"])[:LIST_MAX]:
        out.append(f" • {r['code']} {r['name']}｜📉 拉回第 {r.get('pullback_days', 0)} 天｜A1 {_md(r['entry_date'])}")
    if len(rows) > LIST_MAX:
        out.append(f" …另有 {len(rows) - LIST_MAX} 檔（完整名單見 {hint}）")
    out.append(f"移出：【{A2_MAX_DAYS}日未站回MA5】或是【收盤跌破 MA10/突破日低點】")
    return out


def exit_rule_b() -> str:
    """B 池的移出說明（盤後總結、夜間運算共用，文字只維護這一處）。"""
    return f"移出：【{B_WAIT_DAYS}日未站上 MA5/VWAP】或是【跌破最低點】"


def _close_b_lines(rows: list) -> list:
    out = [f"🔵 <b>B 池：{len(rows)} 檔</b>"]
    for r in sorted(rows, key=lambda r: r["gap_low"])[:LIST_MAX]:
        kd = f"KD {r['kd_k']:.1f}/{r['kd_d']:.1f}｜" if r.get("kd_k") is not None else ""
        if r["status"] == "Watching":
            out += [f" • {r['code']} {r['name']}｜👀 等待 {r['waiting_days']}/{B_WAIT_DAYS} 天｜{_md(r['burst_date'])} 最低 {_p2(r['burst_low'])}｜收 {_p2(r['close'])}（距 {_pc(r['gap_low'])}）",
                    f"   {kd}爆量 {r['burst_ratio']:.1f}x｜{r.get('trigger', '')}"]
        else:
            out += [f" • {r['code']} {r['name']}｜✅ 已發動 {_md(r['entry_date'])} @ {_p2(r['entry_price'])}（{_pc(r['ret_pct'])}）｜收 {_p2(r['close'])}",
                    f"   B 防線 {_p2(r['burst_low'])}（距 {_pc(r['gap_low'])}）｜追蹤剩 {r['days_left']} 日"]
    if len(rows) > LIST_MAX:
        out.append(f" …另有 {len(rows) - LIST_MAX} 檔（完整名單見 pool_report.csv）")
    out.append(exit_rule_b())
    return out


def build_close_report(day_str: str, regime, a_rows: list, b_rows: list, unsettled: list) -> str:
    """盤後總結（一則）：市場趨勢 → A1 追蹤 → A2 池 → B 池，每區塊最後一行是移出條件。只列「現在池中有誰」，不列今天移出了誰。區塊之間空一行（太長時 send_telegram 依空行拆則）。"""
    sections = [[f"📊 <b>【盤後總結】｜{day_str}</b>"] + regime_lines(regime, compact=True)]
    sections.append(_close_a1_lines([r for r in a_rows if r.get("stage") != "A2"]))
    sections.append(_close_a2_lines([r for r in a_rows if r.get("stage") == "A2"]))
    sections.append(_close_b_lines(b_rows))
    if unsettled:
        sections.append([f"⚠️ 以下標的 {SETTLE_DEADLINE[0]}:{SETTLE_DEADLINE[1]:02d} 仍未取得收盤價（暫緩收盤或資料延遲），今日不結算：", "、".join(unsettled)])
    return "\n\n".join("\n".join(sec) for sec in sections)


# ---------- 盤後運算（v70：收盤後選池完成才發，一則）----------
def _night_base_of(code, bases, init):
    return bases.get(code) or (init.get("stock_bases") or {}).get(code) or {}


def _night_a_rows(entries: dict, bases: dict, init: dict, pools: dict) -> list:
    """A1 追蹤 / A2 池 → 盤後總結同款的列（共用 _close_a1_lines / _close_a2_lines，文字一致）。
    A1 無固定期限；顯示已追蹤交易日數。排序依收盤距 MA10。"""
    rows = []
    for code, e in entries.items():
        b = _night_base_of(code, bases, init)
        yc, ma10 = b.get("yest_close"), b.get("true_ma10")
        age = days_since(pools, e["entry_date"], inclusive=False)
        rows.append({"code": code, "name": e.get("name", code), "entry_date": e.get("entry_date"), "age": age,
                     "days_left": None, "pullback_days": int(e.get("pullback_days") or 0),
                     "gap_ma10": ((yc / ma10 - 1) * 100.0) if (yc and ma10) else 0.0})
    return rows


def _night_b_lines(bc: dict, bases: dict, init: dict, pools: dict) -> list:
    """B 池：一檔一行。「明天第 N/5 天」= B 訊號日後第幾個交易日（追蹤日曆已含基準日，所以 +1）；收 = 基準日收盤。"""
    def off_of(e):
        return days_since(pools, e["burst_date"], inclusive=False) + 1
    out = [f"🔵 <b>B 池：{len(bc)} 檔</b>"]
    items = sorted(bc.items(), key=lambda kv: (0 if kv[1].get("status") == "Watching" else 1, -off_of(kv[1]), kv[0]))
    for code, e in items[:POOL_DETAIL_MAX]:
        b = _night_base_of(code, bases, init)
        low, yc = e["burst_low"], b.get("yest_close")
        if e.get("status") == "Watching":
            status = f"👀 明天第 {off_of(e)}/{B_WAIT_DAYS} 天"
        else:
            status = f"✅ 已發動 {_md(e.get('entry_date'))} @ {_p2(e.get('entry_price'))}"
        parts = [f" • {code} {e.get('name', code)}", status, f"{_md(e['burst_date'])} 最低 {_p2(low)}",
                 f"收 {_p2(yc)}（距 {_pc((yc / low - 1) * 100.0) if yc else '—'}）"]
        if e.get("kd_k") is not None:
            parts.append(f"KD {e['kd_k']:.1f}/{e['kd_d']:.1f}")
        parts.append(f"爆量 {float(e.get('burst_ratio') or 0):.1f}x")
        if e.get("trigger"):
            parts.append(str(e["trigger"]))
        out.append("｜".join(parts))
    if len(items) > POOL_DETAIL_MAX:
        out.append(f" …另有 {len(items) - POOL_DETAIL_MAX} 檔（完整名單見 initial_pools.json 的 B_candidates）")
    out.append(exit_rule_b())
    return out


def b_confirm_possible(e: dict, base: dict = None) -> bool:
    """B 確認同時要「預估全日量 ≤ B 訊號日量 × B_SHRINK_RATIO」和「預估全日成交額 ≥ B_MIN_EST_TURNOVER」。
    成交額 ≈ 量 × 價，所以預估成交額最多 ≈ B_SHRINK_RATIO × B 訊號日量 × 最高可能價（前收 × 1.1 漲停）；這個上限還不到門檻的，盤中怎麼走都不可能確認。"""
    vol = float(e.get("burst_vol") or 0.0)
    px = float((base or {}).get("yest_close") or e.get("burst_close") or 0.0)
    return B_SHRINK_RATIO * vol * 1000.0 * px * 1.1 >= B_MIN_EST_TURNOVER


def build_night_message(init: dict, bases: dict, pools: dict, asof_str: str, index_ctx, *, market_total: int, n_scanned: int,
                        n_liquid: int, n_b_new: int, warns=None) -> str:
    """盤後運算（一則）：摘要（檔數＋指數今收與趨勢）→ [異常才有的 ⚠️ 注意] → A1 追蹤 → A2 池 → B 池。區塊之間空一行（太長時 send_telegram 依空行拆則）。"""
    meta, c = init["metadata"], init["metadata"]["counts"]
    bc = init.get("B_candidates") or {}
    n_b_low = sum(1 for cd in bc if cd in bases and not bases[cd]["liquid"])
    head = [f"📦 <b>【盤後運算】</b>基準日 {asof_str}，下一個交易日 {meta['trade_date']}",
            f"全市場 {market_total:,} 檔，掃描 {n_scanned:,} 檔｜5 日均量 ≥ {MIN_VOL5_LOTS:,} 張：{n_liquid:,} 檔",
            f"🟢 A1 候選池 {c['a1']} 檔（5 日均量 ≥ {MIN_VOL5_LOTS:,} 張＋站上三均線 {A1_LATE_DAYS} 日內）",
            f"🟢 A1 追蹤 {c.get('a1_track', 0)} 檔（無固定期限；尚未拉回時不會觸發 A2）",
            f"🟡 A2 池 {c['a2']} 檔（拉回整理 {A2_MIN_DAYS}～{A2_MAX_DAYS} 天，明天盤中重新站回才觸發 A2）",
            f"🔵 B 池 {c['b']} 檔（全市場，不篩 5 日均量；今晚新增 {n_b_new}｜其中 5 日均量 < {MIN_VOL5_LOTS:,} 張 {n_b_low} 檔）"]
    for v in (index_ctx or {}).values():
        icon = "🔴 BEAR" if v.get("init_state") == "BEAR" else "🟢 NORMAL"
        head.append(f"🌡️ {v['name']}：今收 {v['last_close']:,.2f}｜{icon}")
    warns = list(warns or [])
    if not index_ctx:
        warns.append("沒有取得指數基準，明天盤前 radar 會自己補抓")
    elif "otc" not in index_ctx:
        warns.append("沒有櫃買指數基準：明天上櫃個股暫用加權指數判斷趨勢")
    matrix = init.get("volume_time_matrix") or {}
    a2_nomx = sorted(cd for cd in (init.get("A2_tracking_pool") or {}) if cd not in matrix)
    if a2_nomx:
        warns.append(f"A2 池有 {len(a2_nomx)} 檔沒有分時量矩陣，盤中不會觸發 A2：{'、'.join(a2_nomx[:10])}{'…' if len(a2_nomx) > 10 else ''}")
    b_dead = sorted(cd for cd, e in bc.items() if e.get("status") == "Watching" and not b_confirm_possible(e, bases.get(cd) or (init.get("stock_bases") or {}).get(cd)))
    if b_dead:
        need = B_MIN_EST_TURNOVER / B_SHRINK_RATIO / 1e8
        warns.append(f"B 池 {len(b_dead)}/{len(bc)} 檔在目前規則下不可能確認：B 確認要「預估量 ≤ B 訊號日量 × {B_SHRINK_RATIO:g}」又要「預估成交額 ≥ {B_MIN_EST_TURNOVER / 1e8:g} 億」，"
                     f"所以 B 訊號日成交額至少要約 {need:.1f} 億（{'、'.join(b_dead[:8])}{'…' if len(b_dead) > 8 else ''}）。"
                     + (f"這幾檔是 B_MIN_TURNOVER（現為 {B_MIN_TURNOVER / 1e8:g} 億）生效前進池的，最多 {B_WAIT_DAYS} 個交易日後自然到期，新進池的標的不會再有這個問題"
                        if B_MIN_TURNOVER >= need * 1e8 else
                        f"要讓 B 有機會觸發：調低 B_MIN_EST_TURNOVER，或把 B_MIN_TURNOVER 設成 ≥ {need:.1f} 億讓這類標的不進池"))
    sections = [head]
    if warns:
        sections.append(["⚠️ <b>注意</b>"] + [f" • {w}" for w in warns])
    sections.append(_close_a1_lines(_night_a_rows(init.get("A1_tracking_pool") or {}, bases, init, pools), hint="initial_pools.json"))
    sections.append(_close_a2_lines(_night_a_rows(init.get("A2_tracking_pool") or {}, bases, init, pools), hint="initial_pools.json"))
    sections.append(_night_b_lines(bc, bases, init, pools))
    return "\n\n".join("\n".join(sec) for sec in sections)


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
            if w.last_reading is not None:
                ma_txt = f"｜現 {w.last_reading:,.0f}｜即時 MA20 {w.ma20(w.last_reading):,.0f}"
            elif w.last_close:
                ma_txt = f"｜即時 MA20（以昨收估）{w.ma20(w.last_close):,.0f}"
            else:
                ma_txt = ""
            out.append(f"🌡️ {w.name}：<b>{icon}</b>{lc}{ma_txt}｜穿越價 {w.p:,.0f}")
    if "otc" not in regime.w and not compact:
        out.append("ℹ️ 沒有櫃買指數基準，上櫃個股暫用加權指數判斷")
    return out


def _stock_list_lines(entries: dict, codes) -> list:
    """追蹤池清單：每檔一行「代號(名稱)」，沒有標的時顯示（無）。"""
    codes = sorted(codes)
    if not codes:
        return ["      （無）"]
    return [f"      {c}({(entries.get(c) or {}).get('name') or c})" for c in codes]


def _startup_trend_lines(regime) -> list:
    """🌡 市場趨勢（啟動訊息用）：昨收＋月線（= 即時 MA20；還沒有即時讀數時，以昨收估）。"""
    out = ["🌡<b>市場趨勢</b>"]
    if regime is None or not regime.w:
        return out + ["   • ⚠️ initial_pools.json 沒有指數基準資料（前 19 日收盤），暫以 NORMAL 處理"]
    for mk, w in regime.w.items():
        icon = "🔴 BEAR 空頭" if w.state == "BEAR" else "🟢 NORMAL 多頭"
        ref = w.last_reading if w.last_reading is not None else w.last_close
        ma = f"{w.ma20(ref):,.0f}" if ref else "—"
        lc = f"{w.last_close:,.0f}" if w.last_close else "—"
        out.append(f"   • {w.name}：{icon} ｜ 昨收：{lc} ｜ 月線：{ma}")
    if "otc" not in regime.w:
        out.append("   • ℹ️ 沒有櫃買指數基準，上櫃個股暫用加權指數判斷")
    return out


def build_startup_message(init: dict, init_note: str, regime, pools: dict, pool_note: str, check_note: str, notes: list = None) -> str:
    """盤前啟動訊息（v69.6 版面）：資料來源 → 市場趨勢 → 規則摘要 → 大盤切換 → 追蹤池狀態（逐檔列出代號與名稱）。"""
    meta = init["metadata"]
    started = now_tw().strftime("%Y-%m-%d %H:%M")                              # ⏱ = 雷達啟動時間
    a_all, b_all = pools["a"], pools["b"]
    a2_codes = [c for c, e in a_all.items() if entry_stage(e) == "A2"]        # A2 池：拉回整理 1～6 天（今天盤中站回才會觸發）
    a1_codes = [c for c in a_all if c not in set(a2_codes)]                   # A1 追蹤：收盤確認後目前未處於拉回
    ma_txt = "MA5/10/20" if A2_REQUIRE_ALL_MAS else "MA5"
    sections = [
        ["📡<b>【飆股雷達】即時監控中</b>",
         f"⏱ {started} ｜ 基準：{meta.get('baseline_date')}",
         "🔌<b>資料來源</b>",
         f"   • 富邦 Neo API (每 {POLL_INTERVAL} 秒)",
         "   • 歷史：昨日收盤後預算（盤中不抓歷史）",
         "   • 法人：證交所及櫃買市場"],
    ]
    warn = [ln for ln in str(check_note or "").splitlines() if "⚠️" in ln or "失敗" in ln]     # 自檢正常時不顯示，異常才提醒
    if warn:
        sections.append(["⚠️<b>啟動自檢異常</b>"] + [f"   • {ln}" for ln in warn])
    sections.append(_startup_trend_lines(regime))
    if pool_note and ("舊版" in pool_note or "找不到" in pool_note or "失敗" in pool_note):
        sections.append([f"⚠️ {pool_note}"])
    sections.append([
        "📋<b>規則摘要</b>",
        "   • <b>A1 突破</b>：",
        f"      1️⃣站上 MA5/10/20（站上{A1_LATE_DAYS}日內）",
        f"      2️⃣漲>{A1_CHG_MIN:g}% ＋ 紅K ＋ 現價>VWAP",
        f"      3️⃣量比≥{A1_VOL_RATIO:g} (BEAR 需量比≥{A1_VOL_RATIO_BEAR:g})",
        f"      4️⃣預估量≥{A1_MIN_EST_LOTS:,}張",
        f"      5️⃣金額≥{A_MIN_EST_TURNOVER / 1e8:g}億"])
    sections.append([
        "   • <b>A2 回檔</b> (🐻 BEAR 停止觸發)：",
        "      1️⃣跌破MA5守住MA10與低點",
        f"      2️⃣{A2_MIN_DAYS}~{A2_MAX_DAYS}日內重站回{ma_txt}與VWAP",
        f"      3️⃣量≥{A2_VOL_RATIO:g}倍",
        f"      4️⃣金額≥{A_MIN_EST_TURNOVER / 1e8:g}億"])
    sections.append([
        "   • <b>B 池轉折</b>(10:00起確認)：",
        "      1️⃣KD低檔金叉",
        f"      2️⃣爆量≥{B_VOL_RATIO:g}x" + (f"、金額≥{B_MIN_TURNOVER / 1e8:g}億" if B_MIN_TURNOVER else ""),
        f"      3️⃣收盤≥{B_CLOSE_LOC_MIN * 100:.0f}%",
        f"      4️⃣守低、量縮≤{B_SHRINK_RATIO:g}、站MA5/VWAP、金額≥{B_MIN_EST_TURNOVER / 1e8:g}億"])
    sections.append([
        "⚙️<b>大盤切換</b>：",
        "   • ≥月線=NORMAL",
        f"   • 連續{REGIME_ENTER_BEAR_ROUNDS}輪低於=BEAR",
        f"   • 站回{REGIME_EXIT_HOLD_SEC // 60}分鐘=NORMAL"])
    sections.append(
        ["🎯<b>追蹤池狀態</b>",
         f"   • <b>A1 突破追蹤</b>：{len(a1_codes)} 檔"] + _stock_list_lines(a_all, a1_codes)
        + [f"   • <b>A2 二次啟動</b>：{len(a2_codes)} 檔"] + _stock_list_lines(a_all, a2_codes)
        + [f"   • <b>B 池低檔轉折</b>：{len(b_all)} 檔"] + _stock_list_lines(b_all, b_all))
    if notes:
        sections.append(list(notes))
    return "\n\n".join("\n".join(s) for s in sections if s)


# ---------- CSV ----------
POOL_FIELDS = ["區別", "狀態", "代號", "名稱", "族群", "通知日/B訊號日", "通知價", "B防線/突破日低點", "今日收盤", "報酬(%)",
               "MA5", "MA10", "MA20", "距MA10/B防線(%)", "追蹤/等待天數", "剩餘天數", "A2通知日", "法人T日淨(張)", "備註"]


def build_pool_rows(a_rows: list, b_rows: list, exits: list, unsettled: list) -> list:
    rows = []
    for r in a_rows:
        rows.append({"區別": "A2池" if r.get("stage") == "A2" else "A1追蹤", "狀態": r["status"], "代號": r["code"], "名稱": r["name"], "族群": r["industry"], "通知日/B訊號日": r["entry_date"],
                     "通知價": r["entry_price"], "B防線/突破日低點": "" if r.get("breakout_low") is None else r["breakout_low"], "今日收盤": r["close"],
                     "報酬(%)": "" if r.get("ret_pct") is None else round(r["ret_pct"], 2),
                     "MA5": round(r["ma5"], 2), "MA10": round(r["ma10"], 2), "MA20": round(r["ma20"], 2), "距MA10/B防線(%)": round(r["gap_ma10"], 2),
                     "追蹤/等待天數": r["age"], "剩餘天數": r["days_left"], "A2通知日": "、".join(r["a2_dates"]), "法人T日淨(張)": "",
                     "備註": ("今日A1收盤確認" if r["new_today"] else "") + ("；收盤價暫定（待夜間核對）" if r.get("provisional") else "")
                             + (f"；連續拉回 {r.get('pullback_days', 0)} 天" if r.get("pullback_days") else "")})
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
    n_a1 = sum(1 for r in rows if r["區別"] == "A1追蹤")
    n_a2 = sum(1 for r in rows if r["區別"] == "A2池")
    n_b = sum(1 for r in rows if r["區別"] == "B池")
    n_x = sum(1 for r in rows if r["區別"].startswith("今日移出"))
    title = (f"追蹤清單明細：資料基準日 {day_str}（13:35 富邦最終快照，暫定）｜A1追蹤 {n_a1} 檔｜A2池 {n_a2} 檔｜B池 {n_b} 檔｜"
             f"今日移出 {n_x} 檔｜停損由使用者自行處理")
    with POOL_CSV_FILE.open("w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerow([title])
        writer = csv.DictWriter(fh, fieldnames=POOL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return POOL_CSV_FILE


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
            "price": price, "open": open_, "chg": chg, "yest_close": base["yest_close"], "prev_ma_max": base["prev_ma_max"], "above_days": above_days_of(base),
            "ma5": mas[0], "ma10": mas[1], "ma20": mas[2], "vwap": vwap, "vwap_pct": (price / vwap - 1) * 100.0,
            "vol": vol, "val": val, "est": est, "avg5": base["avg_5d_vol_simple"], "inst5": base.get("inst5"),
            "inst5_f": base.get("inst5_f"), "inst5_t": base.get("inst5_t"), "mode": mode}


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
    return (f"[診斷 {base['code']} {base['name']}] 趨勢 {mode}｜價 {price}｜昨收 {base['yest_close']} 昨日最高均線 {base['prev_ma_max']:.2f}｜已連續站上三均線 {above_days_of(base)} 天（N={A1_LATE_DAYS}）"
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
        if base["market"] in UNRELIABLE_MARKETS:
            continue
        liquid = base["liquid"]                  # 5 日均量 ≥ 1,000 張：只限制 A1、A2；B 全市場確認，不看這個
        mkt = row["_mkt"]
        mode = regime.mode(mkt) if regime is not None else "NORMAL"
        x = xs.get(mkt, minutes_since_open(now))
        if code in DEBUG_STOCK_CODES:
            print(debug_line(base, table, row, x, mode, pools), flush=True)
        if x < EVAL_START_MINUTE:
            continue
        done = False
        a_entry = pools["a"].get(code)
        if A2_ENABLED and a_entry and liquid and entry_stage(a_entry) == "A2" and code not in STATE["a2"]:      # 只有「已進 A2 池（拉回整理 1～6 天）」的才可能觸發 A2
            sig = evaluate_a2(a_entry, base, table, row, x, mode)
            if sig:
                a2c.append(sig)
                done = True
        if A1_ENABLED and liquid and code in a1_set and code not in STATE["triggered"] and not a1_late_blocked(code, base, pools) \
                and not (done and ONE_SIGNAL_PER_STOCK):
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
    parts = []
    for mk, v in readings.items():
        label = "加權" if mk == "tse" else "櫃買"
        if v is not None:
            parts.append(f"{label} {v:,.2f}")
            continue
        kind, why = (getattr(feed, "index_diag", {}) or {}).get(mk, ("error", "原因不明"))
        pre_open = now_tw() < _market_open_today(now_tw())
        if kind == "empty":
            parts.append(f"{label} 尚無報價（盤前，開盤後再讀）" if pre_open else f"{label} 讀取失敗：回應沒有價格")
        else:
            parts.append(f"{label} 讀取失敗：{why[:80]}")
    notes.append("指數：" + "｜".join(parts))
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


async def run_close(bases: dict, feed, loop, today: date_cls, pools: dict, regime=None, init: dict = None, defer_summary: bool = False) -> dict:
    """13:35 收盤結算。回傳 {"ok": 這次有結算, "why": 沒結算的原因（snapshot / not_trading_day / already_settled）, "snap": 最後一份快照, "report": 盤後總結文字, "res": 結算結果}。
    defer_summary = True 時不發盤後總結（收盤後選池成功才發【盤後運算】，失敗才補發總結）。"""
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
            return {"ok": False, "why": "snapshot", "snap": None}
        if first:
            first = False
            dates = {str(m.get("date", "")).replace("-", "") for m in snap["meta"].values()}
            if today_str.replace("-", "") not in dates:
                print(f"[收盤結算] 快照日期 {dates} 不是今天 {today_str}，視為非交易日，不發盤後報告", flush=True)
                return {"ok": False, "why": "not_trading_day", "snap": snap}
            if not any("isTrial" in r for r in snap["rows"].values()):
                print("[收盤結算] ⚠️ 快照列沒有 isTrial 欄位：無法判斷是否為試撮，只靠 closePrice 是否有值（暫緩收盤個股可能誤用試撮價，請確認富邦回傳欄位）", flush=True)
            if pools.get("asof") == today_str:
                print(f"[收盤結算] 追蹤清單已在 {today_str} 結算過，不重複處理（避免追蹤天數被重複計算）", flush=True)
                return {"ok": False, "why": "already_settled", "snap": snap}
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
    print(f"[收盤結算] A1 追蹤＋A2 池 {len(res['a_rows'])} 檔、B 池 {len(res['b_rows'])} 檔｜今日 {ev}｜未取得收盤價 {res['unsettled']}", flush=True)
    report = build_close_report(today_str, regime, res["a_rows"], res["b_rows"], res["unsettled"])
    if not defer_summary:                                    # 盤後只發這一則總結（不再發「移出」「A1 收盤未確認」事件訊息）
        await loop.run_in_executor(None, send_telegram, report)
    today_closed = [r for r in pools["closed"] if r["date"] == today_str]
    csv_path = export_pool_csv(today_str, build_pool_rows(res["a_rows"], res["b_rows"], today_closed, res["unsettled"]))
    await loop.run_in_executor(None, send_telegram_document, csv_path,
                               f"🧾 追蹤清單明細（資料基準日 {today_str}）：A1 追蹤 {sum(1 for r in res['a_rows'] if r.get('stage') != 'A2')}｜A2 池 {sum(1 for r in res['a_rows'] if r.get('stage') == 'A2')}｜B 池 {len(res['b_rows'])}｜今日移出 {len(today_closed)}")
    save_states()
    return {"ok": True, "why": "", "snap": snap, "report": report, "res": res}


async def run_scan(feed, loop, today: date_cls, pools: dict, prefetch, snap_hint) -> bool:
    """收盤後選池：用背景預抓的歷史日 K ＋ 最終快照合成的今天 K 棒，算明天的 A1 / A2 / B 池，寫 initial_pools.json，成功後發【盤後運算】。
    沒有預抓（13:25 後才啟動 / 預抓啟動失敗）就現場抓（約 46 分鐘）。失敗會發警告，回傳 False（stock_states 不標 COMPLETED，可手動重跑）。"""
    today_str = today.strftime("%Y-%m-%d")
    t0 = time.time()
    try:
        if prefetch is None:
            if snap_hint is None:
                raise ScanError("沒有快照可以決定要抓哪些股票")
            prefetch = MarketPrefetch(feed, build_universe(snap_hint, pools), today - timedelta(days=LOOKBACK_CALENDAR_DAYS), today)
            print("[選池] 沒有背景預抓，現場抓全市場日 K 與 1 分 K（約 46 分鐘）", flush=True)
            await loop.run_in_executor(None, prefetch.run_blocking)
        else:
            if not await loop.run_in_executor(None, prefetch.wait_hist, SCAN_WAIT_PREFETCH_SEC):
                print("[選池] 背景預抓的日 K 逾時還沒做完，用已抓到的部分", flush=True)
            if not await loop.run_in_executor(None, prefetch.wait_all, SCAN_WAIT_CURVES_SEC):
                print("[選池] 1 分 K 預抓逾時，其餘現場補抓", flush=True)
            prefetch.stop.set()
        got, want = prefetch.coverage()
        if not want or got / want < FETCH_MIN_COVERAGE:
            raise ScanError(f"歷史日 K 只預抓到 {got}/{want} 檔（失敗 {prefetch.fail}）" + (f"；{prefetch.error}" if prefetch.error else ""))
        snap = await loop.run_in_executor(None, feed.snapshot_all)                  # 重抓一次最終快照（13:35 之後暫緩收盤的股票也有收盤價了）
        dates = {str(m.get("date", "")).replace("-", "") for m in snap["meta"].values()}
        if today_str.replace("-", "") not in dates:
            raise ScanError(f"快照日期 {sorted(dates)} 不是今天")
        bars = bars_from_snapshot(snap, today)
        warns = []
        unit_note = unit_sanity(feed, prefetch.series, bars, today)
        if unit_note:
            warns.append(unit_note)
        n_merged = merge_today_bar(prefetch.series, bars, today)
        if len(bars) < 0.9 * len(snap["rows"]):
            warns.append(f"只有 {len(bars)}/{len(snap['rows'])} 檔有今天的最終價（暫緩收盤 / 停牌的今晚不納入選池）")
        print(f"[選池] 今天 K 棒 {len(bars)} 檔（接上歷史 {n_merged} 檔）｜預抓日 K {got}/{want} 檔、1 分 K {len(prefetch.curves)} 檔", flush=True)
        curves_cache = dict(prefetch.curves)
        init, bases, index_ctx, info = await loop.run_in_executor(None, functools.partial(
            scan_core, feed, today, prefetch.series, prefetch.universe, pools, industry=prefetch.industry, curves_cache=curves_cache,
            snap=snap, notes=[f"收盤後選池（今天 K 棒由 13:35 後最終快照合成）｜預抓日 K {got}/{want}"], ic_budget=SCAN_IC_BUDGET_SECONDS))
        if UNRELIABLE_MARKETS:
            warns.append(f"{'、'.join('上市' if m == 'tse' else '上櫃' for m in sorted(UNRELIABLE_MARKETS))}日 K 資料不完整，該市場今晚不納入選池")
        text = build_night_message(init, bases, pools, today_str, index_ctx, market_total=info["market_total"], n_scanned=info["n_scanned"],
                                   n_liquid=info["n_liquid"], n_b_new=info["n_b_new"], warns=warns)
        await loop.run_in_executor(None, send_telegram, text)
        print(f"[選池] 完成，耗時 {(time.time() - t0) / 60:.1f} 分鐘", flush=True)
        return True
    except ScanError as exc:
        reason = str(exc)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
    print(f"[選池] 失敗：{reason}", flush=True)
    await loop.run_in_executor(None, send_telegram,
                               f"❌ <b>【盤後運算失敗】</b>{reason}\n明天的候選池沒有更新，明天盤中不會發訊號。請今晚手動重跑 radar（daily workflow，13:35 之後；會現場抓資料約 46 分鐘），或執行補歷史（backfill_history）。")
    return False


async def run_post_close(bases: dict, feed, loop, today: date_cls, pools: dict, regime, init: dict, prefetch) -> bool:
    """13:35 結算 → 收盤後選池。回傳 True = 這一天可以標 COMPLETED。"""
    today_str = today.strftime("%Y-%m-%d")
    cl = await run_close(bases, feed, loop, today, pools, regime, init, defer_summary=SCAN_ENABLED)
    if cl["why"] in ("snapshot", "not_trading_day"):
        return False
    if not SCAN_ENABLED:
        return cl["ok"]
    ok = await run_scan(feed, loop, today, pools, prefetch, cl.get("snap"))
    if not ok and cl.get("report"):
        await loop.run_in_executor(None, send_telegram, cl["report"])        # 選池失敗：補發盤後總結，至少看得到今天結算後的池
    return ok


# =========================
# 主程式
# =========================
async def main_async() -> None:
    print("=" * 55, flush=True)
    print(f"飆股雷達 {RADAR_VERSION}：A1 / A2 / B｜行情富邦 Neo API｜收盤後預算 initial_pools.json｜盤中只查表", flush=True)
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
        send_telegram(f"❌ <b>【飆股雷達】</b> 無法載入夜間資料：{init_note}\n今天不發盤中訊號。請先確認昨天收盤後的選池已完成，或手動補跑（backfill_history）。")
        return
    baseline_date = init["metadata"]["baseline_date"]
    if baseline_date == today_str:
        print(f"[選池] 夜間資料的基準日就是今天（{today_str} 的收盤後選池已經完成），不需要再跑", flush=True)
        set_run_status("COMPLETED")
        return
    ok, why = await validate_baseline(feed, loop, today, baseline_date)
    print(f"[基準日] {why}｜{'通過' if ok else '不符'}", flush=True)
    if not ok:
        send_telegram(f"❌ <b>【飆股雷達】</b> 夜間資料的基準日不是最近交易日（{why}），今天不發盤中訊號。\n"
                      f"請確認昨天 13:35 收盤後的選池已完成（Telegram 有【盤後運算】）並 commit 了 initial_pools.json。")
        return
    bases, matrix, a1_set = init["stock_bases"], init["volume_time_matrix"], set(init["A1_candidates"])
    sync_stats = sync_pools_from_initial(pools, init)
    print(f"[對齊] 夜間快照 → 追蹤清單：補進 A1 追蹤 / A2 池 {sync_stats['a_added']} 檔、B 池 {sync_stats['b_added']} 檔", flush=True)
    inst, inst_note = load_institutional()
    inst_warn = apply_institutional(init, pools, inst)
    print(f"[法人檔] {inst_note}" + (f"｜⚠️ {inst_warn}" if inst_warn else "｜已貼到夜間資料"), flush=True)
    save_pools(pools)
    ctx_idx = {mk: v for mk, v in (("tse", init.get("listed_market_regime")), ("otc", init.get("otc_market_regime"))) if v and v.get("p")}
    if not ctx_idx:
        print("[趨勢] initial_pools.json 沒有指數基準，改向富邦補抓前 19 日收盤", flush=True)
        ctx_idx = await loop.run_in_executor(None, index_regime_snapshot, feed, date_cls.fromisoformat(baseline_date))
    regime = build_regime(ctx_idx, STATE.get("regime"))
    if after_close:
        print("已過收盤確認時間：直接執行收盤結算（通知紀錄取自 stock_states.json）", flush=True)
        done = await run_post_close(bases, feed, loop, today, pools, regime, init, None)
        if done:
            set_run_status("COMPLETED")
        return
    readings, check_note = await self_check(feed, loop)
    for mk, w in regime.w.items():
        if mk not in STATE["regime"] and readings.get(mk) is not None:                     # 今天第一次啟動：用目前讀數決定起始狀態
            w.last_reading = readings[mk]
            w.last_ma20 = w.ma20(readings[mk])
            w.state = "NORMAL" if readings[mk] >= w.last_ma20 else "BEAR"
    STATE["regime"] = {mk: {"state": w.state, "p": w.p} for mk, w in regime.w.items()}
    save_states()
    print(f"[趨勢] {[(mk, w.name, '前19日合計', round(w.sum19, 2), '穿越價', round(w.p, 2), w.state) for mk, w in regime.w.items()]}", flush=True)
    late = current >= _market_open_today(current) + timedelta(minutes=EVAL_START_MINUTE)
    notes = []
    if inst_warn:
        notes.append(f"⚠️ {inst_warn}")
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
    prefetch = await loop.run_in_executor(None, start_prefetch, feed, pools, today) if SCAN_ENABLED else None   # 背景預抓全市場日 K（盤中抓完，13:35 選池直接用）
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
            trend_txt = "｜".join(f"{w.name} {w.state} 現 {w.last_reading:,.2f} / 即時MA20 {w.last_ma20:,.2f}"
                                  for w in regime.w.values() if w.last_reading is not None and w.last_ma20 is not None)
            print(f"[{current.strftime('%H:%M:%S')}] 第 {polls} 輪：快照 {len(snap['rows'])} 檔，新訊號/事件 {sent}，今日累計 {len(STATE['signals'])}，"
                  f"耗時 {elapsed:.2f}s｜{summary}（{delta:+,.0f}）" + (f"｜趨勢 {trend_txt}" if trend_txt else ""), flush=True)
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
    done = await run_post_close(bases, feed, loop, today, pools, regime, init, prefetch)
    if done:
        set_run_status("COMPLETED")


if __name__ == "__main__":
    asyncio.run(main_async())
