"""盤後選股 v9：🧭 左側潛力庫（累積）＋ 🚀 短線轉強股（提醒窗口＋監控窗口）＋ 🔥 強勢股池（黏著）＋ 🎯 回檔專用觀察池。

v9 相對 v8 的變更（對應新架構規範）：
1. 🧭 左側：入選逾 20 個交易日移出（原 60）。收盤 > MA20×1.15「已經發動」移出時，當晚優先評估是否已在 🔥；
   否則評估 🚀（豁免「站上第 3~5 日」，仍需站上 MA5/10/20、MA5>MA10、外資或投信連買 ≥2 日）；皆不符合才單純移出。
2. 🚀 短線轉強股：進池＝站上 MA5/10/20 第 3~5 日、MA5>MA10、截至 T-1 日外資或投信連續買超 ≥2 日。
   - 同時記錄「入池天數 in_pool_days」與「站上均線天數 days_on_ma」。
   - 提醒窗口 3 日：入池第 4 天起移出 🚀；已在 🔥 → 轉入 🔥；未達 🔥 但收盤未跌破 MA10 → 轉入 🎯 回檔專用觀察池（至多 10 個交易日）。
   - 退場：收盤跌破 MA20，或連續 2 日收盤跌破 MA10（單純跌破 MA5 不移出）。
   - 池上限 30 檔，超過依 20 日漲幅 RS 淘汰最弱。
3. 🔥 強勢股：入池 RS 前 15%（原 20%）；已在池內者 RS 前 30% 且未連續 2 日收盤跌破 MA10 → 🧲黏著保留。
   反轉型新增「現價 > 近 20 日最高收盤，或 > 自 60 日低點反彈過程的最高收盤」。
4. 產出 pool_diagnosis.csv：每檔股票各條件是否通過與第一個沒通過的條件。
5. strong_stock_pool.json 增加 rule 欄位；雷達會檢查規則版本，不符就不載入 🚀/🔥/🎯（避免用到舊規則清單）。
   第一次執行時，舊規則清單的成員不沿用（不會把舊的 RS 前 20% 名單灌進黏著池），🔥/🚀/🎯 從當晚起重新累積。

左側潛力庫入選條件（三項同時成立）：
1. 均線糾結：連續 3 日，MA5/MA10/MA20 最高與最低差距 ÷ MA20 ≤ 2%
2. 量縮：近3日平均量 < 近20日平均量 × 0.6，且20日均量 ≥ 300 張
3. 法人分流（滿足其一，資料截至最新已收盤日）：投信鎖股 / 土洋同買 / 外資佈局
移出：收盤 < MA20×0.95、收盤 > MA20×1.15、入選超過 20 個交易日、連續 3 日無交易資料。

流動性：均量 ≥ 1,000 張，且平均成交額 ≥ 1 億，兩項各自可用 20 日或 60 日平均。
※ 60 日高低點使用收盤價（快取只存收盤價）。
執行時間：三大法人資料約 17:30 後才更新，建議 18:00 後執行。
"""

import re
import csv
import json
import math
import pathlib
from datetime import datetime, timedelta, date as date_cls

import requests

import radar as rd  # 共用官方資料抓取、Telegram、產業鏈快取

# =========================
# 參數
# =========================
BASE_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "left_data"
WATCHLIST_JSON = BASE_DIR / "left_side_watchlist.json"
WATCHLIST_CSV = BASE_DIR / "left_side_watchlist.csv"
STRONG_JSON = BASE_DIR / "strong_stock_pool.json"
STRONG_CSV = BASE_DIR / "strong_stock_pool.csv"
SHORT_CSV = BASE_DIR / "short_term_pool.csv"
DIAG_CSV = BASE_DIR / "pool_diagnosis.csv"
RULE_ID = "v38"                 # 與 radar.py 的 POOL_RULE_ID 相同；不符代表清單是舊規則產生的

TARGET_TRADING_DAYS = 65
LOOKBACK_CALENDAR_DAYS = 110
DATA_KEEP_DAYS = 160
OTC_CHIP_RETRY_DAYS = 10
MIN_HISTORY_DAYS = 22

# 左側入選條件
MA_TANGLE_MAX = 0.02
TANGLE_DAYS = 3
LOW_VOL_DAYS = 3
LOW_VOL_RATIO = 0.6
MIN_AVG_VOL_LOTS = 300
CAPITAL_SPLIT_E8 = 80
TRUST_A_MIN_LOTS = 500
FOREIGN_C_MIN_LOTS = 2000
BOTH_BUY_MIN_RATIO = 0.05
LARGE_VOL_FALLBACK = 20000

# 左側移出條件
EXIT_BREAKDOWN = 0.95
EXIT_RUNAWAY = 1.15
EXIT_MAX_DAYS = 20
EXIT_SUSPEND_DAYS = 3

# 流動性（強勢股、短線轉強股共用；20 日或 60 日任一達標）
STRONG_MIN_DAYS = 61
STRONG_MIN_VOL_LOTS = 1000
STRONG_MIN_TURNOVER = 1e8

# 強勢股
TREND_NEAR_HIGH = 0.05
REVERSAL_REBOUND = 0.25
REVERSAL_VOL_EXPAND = 1.2
RS_TOP_RATIO = 0.15                # 🔥 入池：20 日漲幅前 15%
RS_KEEP_RATIO = 0.30               # 🔥 黏著：已在池內者前 30% 才保留
STRONG_MAX_EXT = 1.20
MA20_RISING_LOOKBACK = 5

# 短線轉強股
SHORT_MIN_STREAK = 3
SHORT_MAX_STREAK = 5
SHORT_MIN_INST_DAYS = 2           # 外資或投信連續買超 ≥ 2 日
SHORT_POOL_MAX = 30               # 🚀 池容量
SHORT_POOL_DAYS = 3               # 🚀 提醒窗口：入池滿 3 日，第 4 天起分流
WATCH_MAX_DAYS = 10               # 🎯 回檔專用觀察池至多 10 個交易日
STREAK_CAP = 30                 # 計算連續站上天數的上限

CORR_DAYS = 60
CORR_STRONG = 0.6
DUEL_DAYS = 20
DUEL_MIN_DAYS = 3
TELEGRAM_LIST_MAX = 30

TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_FMTQIK_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
TPEX_INSTI_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
TPEX_INSTI_OLD_URL = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"


# =========================
# 工具
# =========================
def _norm(text) -> str:
    text = re.sub(r"</?br\s*/?>", "", str(text), flags=re.I)
    text = re.sub(r"\s", "", text)
    return text.replace("（", "(").replace("）", ")")


def _num(text):
    try:
        return float(str(text).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 20:
        return None
    n = len(pairs)
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    return sxy / math.sqrt(sxx * syy)


# =========================
# 三大法人
# =========================
def fetch_twse_chips(day: date_cls):
    """回傳 {代號: [外資買賣超張, 投信買賣超張]}；{}=無資料；None=失敗。"""
    try:
        resp = rd._request_with_retry(
            rd._official_session.get,
            TWSE_T86_URL,
            params={"date": day.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"},
            timeout=25,
        )
        data = resp.json()
    except Exception as exc:
        print(f"T86 {day} 失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
        return None
    if str(data.get("stat", "")).upper() != "OK":
        return {}
    fields = [_norm(f) for f in data.get("fields", [])]
    try:
        code_i = fields.index("證券代號")
        f_i = fields.index("外陸資買賣超股數(不含外資自營商)")
        t_i = fields.index("投信買賣超股數")
    except ValueError:
        print(f"[警告] T86 欄位結構變更，無法辨識：{fields}", flush=True)
        return None
    result = {}
    for row in data.get("data", []):
        try:
            code = str(row[code_i]).strip()
        except IndexError:
            continue
        f_v, t_v = _num(row[f_i]), _num(row[t_i])
        if rd.STOCK_CODE_RE.fullmatch(code) and f_v is not None and t_v is not None:
            result[code] = [f_v / 1000, t_v / 1000]
    return result


def _parse_tpex_chip_rows(fields, rows):
    nf = [_norm(f) for f in fields]
    code_i = next((i for i, f in enumerate(nf) if "代號" in f), 0)
    f_i = next((i for i, f in enumerate(nf) if "外資" in f and "買賣超" in f and "不含" in f), None)
    if f_i is None:
        f_i = next((i for i, f in enumerate(nf) if "外資" in f and "買賣超" in f), None)
    t_i = next((i for i, f in enumerate(nf) if "投信" in f and "買賣超" in f), None)
    if (f_i is None or t_i is None) and rows and len(rows[0]) >= 14:
        print("[警告] 上櫃三大法人欄位名稱無法辨識，改用固定欄位位置（外資=4、投信=13）", flush=True)
        f_i, t_i = 4, 13
    if f_i is None or t_i is None:
        return None
    result = {}
    for row in rows:
        try:
            code = str(row[code_i]).strip()
        except IndexError:
            continue
        f_v, t_v = _num(row[f_i]), _num(row[t_i])
        if rd.STOCK_CODE_RE.fullmatch(code) and f_v is not None and t_v is not None:
            result[code] = [f_v / 1000, t_v / 1000]
    return result


def fetch_tpex_chips(day: date_cls):
    """上櫃三大法人。先試新版網站 API，失敗再試舊版。"""
    try:
        resp = rd._request_with_retry(
            rd._official_session.post,
            TPEX_INSTI_URL,
            data={"type": "Daily", "sect": "EW", "date": day.strftime("%Y/%m/%d"), "response": "json"},
            headers=rd.TPEX_PAGE_HEADERS,
            timeout=25,
        )
        data = resp.json()
        if str(data.get("stat", "")).upper() != "OK":
            return {}
        tables = data.get("tables") or []
        if tables and tables[0].get("data"):
            parsed = _parse_tpex_chip_rows(tables[0].get("fields", []), tables[0]["data"])
            if parsed is not None:
                return parsed
        elif tables:
            return {}
    except Exception as exc:
        print(f"TPEx 三大法人(新版) {day} 失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
    try:
        roc = f"{day.year - 1911}/{day.month:02d}/{day.day:02d}"
        resp = rd._request_with_retry(
            rd._official_session.get,
            TPEX_INSTI_OLD_URL,
            params={"l": "zh-tw", "se": "EW", "t": "D", "d": roc, "o": "json"},
            headers=rd.TPEX_PAGE_HEADERS,
            timeout=25,
        )
        data = resp.json()
        rows = data.get("aaData") or []
        if not rows:
            return {}
        return _parse_tpex_chip_rows(data.get("fields") or [], rows)
    except Exception as exc:
        print(f"TPEx 三大法人(舊版) {day} 失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
        return None


# =========================
# 加權指數（大盤濾網用）
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
            resp = rd._request_with_retry(
                rd._official_session.get,
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
            value = _num(row[i_i])
            if day and value and day <= asof:
                result[day] = value
    return sorted(result.items())


# =========================
# 每日資料（含快取）
# =========================
def _day_file(day: date_cls) -> pathlib.Path:
    return DATA_DIR / f"{day.strftime('%Y%m%d')}.json"


def _pack_quotes(quotes: dict) -> dict:
    return {c: [q["name"], q["close"], q["volume_shares"] / 1000] for c, q in quotes.items()}


def load_day(day: date_cls, today: date_cls):
    """回傳當日資料 dict、{"holiday": True}，或 None（上市資料抓取失敗）。"""
    path = _day_file(day)
    cached = None
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            cached = None
    if cached is not None:
        if cached.get("holiday"):
            return cached
        recent = (today - day).days <= OTC_CHIP_RETRY_DAYS
        changed = False
        if recent and not cached.get("otc_quotes", True):
            oq = rd._fetch_tpex_day(day)
            if oq:
                for code, q in _pack_quotes(oq).items():
                    cached["quotes"].setdefault(code, q)
                cached["otc_codes"] = sorted(oq.keys())
                cached["otc_quotes"] = True
                changed = True
                print(f"[左側] {day} 上櫃報價補抓完成", flush=True)
        if recent and cached.get("otc_quotes", True) and not cached.get("otc_chips"):
            oc = fetch_tpex_chips(day)
            if oc:
                otc = set(cached.get("otc_codes", []))
                cached["chips"].update({c: v for c, v in oc.items() if c in otc})
                cached["otc_chips"] = True
                changed = True
        if changed:
            path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
        return cached

    tq = rd._fetch_twse_day(day)
    oq = rd._fetch_tpex_day(day)
    if not tq:
        if tq == {} and oq == {}:
            rec = {"holiday": True}
            if day < today:
                path.write_text(json.dumps(rec), encoding="utf-8")
            return rec
        return None
    oq = oq or {}

    quotes = _pack_quotes(tq)
    for code, q in _pack_quotes(oq).items():
        quotes.setdefault(code, q)
    tc = fetch_twse_chips(day)
    oc = fetch_tpex_chips(day) if oq else None
    chips = {}
    for code, v in (tc or {}).items():
        if code in tq:
            chips[code] = v
    for code, v in (oc or {}).items():
        if code in oq and code not in chips:
            chips[code] = v
    rec = {
        "quotes": quotes,
        "chips": chips,
        "otc_codes": sorted(oq.keys()),
        "twse_chips": bool(tc),
        "otc_quotes": bool(oq),
        "otc_chips": bool(oc),
    }
    if not oq:
        print(f"[左側] {day} 上櫃報價缺漏，已標記待補，之後執行時會補抓", flush=True)
    if rec["twse_chips"]:
        path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return rec


def collect_days(today: date_cls):
    DATA_DIR.mkdir(exist_ok=True)
    days, failed = [], []
    for i in range(0, LOOKBACK_CALENDAR_DAYS + 1):
        day = today - timedelta(days=i)
        if day.weekday() >= 5:
            continue
        rec = load_day(day, today)
        if rec is None:
            failed.append(day)
            continue
        if rec.get("holiday"):
            continue
        days.append((day, rec))
        if len(days) % 10 == 0:
            print(f"[左側] 已收集 {len(days)} 個交易日（目前 {day}）", flush=True)
        if len(days) >= TARGET_TRADING_DAYS:
            break
    days.sort(key=lambda x: x[0])
    while days and not days[-1][1].get("twse_chips"):
        print(f"[左側] {days[-1][0]} 上市三大法人資料尚未公布，基準日往前一天", flush=True)
        days.pop()
    return days, failed


def prune_data_dir(today: date_cls) -> None:
    cutoff = today - timedelta(days=DATA_KEEP_DAYS)
    for path in DATA_DIR.glob("*.json"):
        try:
            if datetime.strptime(path.stem, "%Y%m%d").date() < cutoff:
                path.unlink()
        except ValueError:
            continue


# =========================
# 公司股本
# =========================
def fetch_capital_map() -> dict:
    result = {}
    try:
        rows = requests.get(rd.TWSE_COMPANY_URL, headers=rd.OFFICIAL_HEADERS, timeout=20).json()
        for row in rows:
            code = str(row.get("公司代號", "")).strip()
            cap = _num(row.get("實收資本額"))
            if code and cap:
                result[code] = cap / 1e8
    except Exception as exc:
        print(f"抓取上市股本失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
    try:
        rows = requests.get(rd.TPEX_COMPANY_URL, headers=rd.OFFICIAL_HEADERS, timeout=20).json()
        for row in rows:
            code = str(row.get("SecuritiesCompanyCode", "")).strip()
            if not code or code in result:
                continue
            cap = None
            for key, value in row.items():
                k = key.lower().replace(".", "").replace("_", "")
                if "paidin" in k and "capital" in k:
                    cap = _num(value)
                    break
            if cap:
                result[code] = cap / 1e8
    except Exception as exc:
        print(f"抓取上櫃股本失敗: {type(exc).__name__}: {repr(exc)}", flush=True)
    print(f"[左側] 取得股本 {len(result)} 檔", flush=True)
    return result


# =========================
# 指標計算
# =========================
def _ma_at(closes, n, end):
    """以 closes[end-1] 為最後一天的 n 日均線。"""
    return sum(closes[end - n:end]) / n


def _tangle_at(closes, end):
    ma5, ma10, ma20 = _ma_at(closes, 5, end), _ma_at(closes, 10, end), _ma_at(closes, 20, end)
    return (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / ma20, ma20


def _above_all_at(closes, end):
    """以 closes[end-1] 為當日：當日收盤是否高於「當日」MA5/10/20。"""
    if end < 20:
        return False
    close = closes[end - 1]
    return (close > _ma_at(closes, 5, end) and close > _ma_at(closes, 10, end)
            and close > _ma_at(closes, 20, end))


def _above_streak(closes, end=None, cap=STREAK_CAP):
    """截至 closes[end-1]，連續幾日收盤都在「當日」MA5/10/20 之上（上限 cap）。"""
    end = len(closes) if end is None else end
    streak = 0
    while streak < cap and end - streak >= 20 and _above_all_at(closes, end - streak):
        streak += 1
    return streak


def _liquid(closes, vols):
    """流動性：均量 ≥ 1,000 張 且 平均成交額 ≥ 1 億，兩項各自可用 20 日或 60 日平均。"""
    n = len(closes)
    if n < STRONG_MIN_DAYS:
        return False
    vol_ok = (sum(vols[-20:]) / 20 >= STRONG_MIN_VOL_LOTS) or (sum(vols[-60:]) / 60 >= STRONG_MIN_VOL_LOTS)
    t20 = sum(c * v * 1000 for c, v in zip(closes[-20:], vols[-20:])) / 20
    t60 = sum(c * v * 1000 for c, v in zip(closes[-60:], vols[-60:])) / 60
    return vol_ok and (t20 >= STRONG_MIN_TURNOVER or t60 >= STRONG_MIN_TURNOVER)


def build_series(days):
    """回傳 {代號: dict(name, market, dates, closes, vols, chips)}，只含基準日有交易的股票。"""
    asof = days[-1][0]
    codes = set(days[-1][1]["quotes"].keys())
    otc_codes = set(days[-1][1].get("otc_codes", []))
    series = {}
    for code in codes:
        rows = [(d, rec["quotes"].get(code), rec["chips"].get(code)) for d, rec in days]
        rows = [(d, q, c) for d, q, c in rows if q is not None]
        if len(rows) < MIN_HISTORY_DAYS or rows[-1][0] != asof:
            continue
        series[code] = {
            "name": rows[-1][1][0],
            "market": "上櫃" if code in otc_codes else "上市",
            "dates": [r[0] for r in rows],
            "closes": [r[1][1] for r in rows],
            "vols": [r[1][2] for r in rows],
            "chips": [r[2] for r in rows],
        }
    return series


def metrics(s, capital):
    closes, vols, chips = s["closes"], s["vols"], s["chips"]
    n = len(closes)
    tangle, ma20 = _tangle_at(closes, n)
    streak = 0
    for k in range(n - 19):
        if _tangle_at(closes, n - k)[0] <= MA_TANGLE_MAX:
            streak += 1
        else:
            break
    vol20 = sum(vols[-20:]) / 20
    vol3 = sum(vols[-LOW_VOL_DAYS:]) / LOW_VOL_DAYS
    m = {
        "close": closes[-1], "ma20": ma20, "tangle": tangle,
        "tangle_streak": streak, "tangle_ok": streak >= TANGLE_DAYS,
        "vol20": vol20, "vol3": vol3, "vol_ratio": vol3 / vol20 if vol20 else 99,
        "vol_today": vols[-1], "capital": capital,
    }
    last5 = chips[-5:]
    m["chips_ok"] = len(last5) == 5 and all(c is not None for c in last5)
    if m["chips_ok"]:
        m["foreign5"] = sum(c[0] for c in last5)
        m["trust5"] = sum(c[1] for c in last5)
        m["foreign3"] = sum(c[0] for c in last5[-3:])
        m["trust3"] = sum(c[1] for c in last5[-3:])
    else:
        m.update(foreign5=None, trust5=None, foreign3=None, trust3=None)

    if (capital is not None and capital > 100) or vol20 > 20000:
        m["kind"] = "外資主導型"
    elif (capital is not None and capital < 50) or 1000 <= vol20 <= 5000:
        m["kind"] = "投信主導型"
    else:
        m["kind"] = "中間型"

    pct = [None] + [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, n)]
    pct60, chips60 = pct[-CORR_DAYS:], chips[-CORR_DAYS:]
    m["corr_f"] = _pearson(pct60, [c[0] if c else None for c in chips60])
    m["corr_t"] = _pearson(pct60, [c[1] if c else None for c in chips60])
    profile = []
    if m["corr_f"] is not None and m["corr_f"] > CORR_STRONG:
        profile.append("外資盤")
    if m["corr_t"] is not None and m["corr_t"] > CORR_STRONG:
        profile.append("投信盤")
    m["profile"] = "、".join(profile) or "無明顯"

    duel = [pct[i] for i in range(max(1, n - DUEL_DAYS), n)
            if chips[i] and chips[i][1] > 0 and chips[i][0] < 0 and pct[i] is not None]
    if len(duel) >= DUEL_MIN_DAYS:
        avg = sum(duel) / len(duel)
        winner = "投信勝出" if avg >= 0.05 else ("外資勝出" if avg <= -0.05 else "勢均力敵")
        m["duel"] = f"{winner}（{len(duel)}天，平均{avg:+.2f}%）"
    else:
        m["duel"] = "對作天數不足"
    return m


def entry_strategy(m):
    if not m["tangle_ok"] or m["vol20"] < MIN_AVG_VOL_LOTS or m["vol_ratio"] >= LOW_VOL_RATIO:
        return ""
    if not m["chips_ok"]:
        return ""
    capital = m["capital"]
    is_large = capital >= CAPITAL_SPLIT_E8 if capital is not None else m["vol20"] >= LARGE_VOL_FALLBACK
    cond_a = (not is_large) and m["trust5"] > TRUST_A_MIN_LOTS
    cond_b = (m["foreign3"] > 0 and m["trust3"] > 0
              and (m["foreign3"] + m["trust3"]) >= m["vol20"] * BOTH_BUY_MIN_RATIO)
    cond_c = is_large and m["foreign5"] > FOREIGN_C_MIN_LOTS
    return "+".join(label for label, ok in (("投信鎖股", cond_a), ("土洋同買", cond_b), ("外資佈局", cond_c)) if ok)


def exit_reason(m, days_in):
    if m["close"] < m["ma20"] * EXIT_BREAKDOWN:
        return f"跌破整理區（收盤 < MA20×{EXIT_BREAKDOWN}）"
    if m["close"] > m["ma20"] * EXIT_RUNAWAY:
        return f"已經發動（收盤 > MA20×{EXIT_RUNAWAY}）"
    if days_in > EXIT_MAX_DAYS:
        return f"入選超過 {EXIT_MAX_DAYS} 個交易日"
    return ""


# =========================
# 🧭 左側累積清單
# =========================
def load_previous_list() -> dict:
    if not WATCHLIST_JSON.exists():
        return {}
    try:
        data = json.loads(WATCHLIST_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}
    stocks = data.get("stocks") or {}
    for info in stocks.values():
        info.setdefault("added", data.get("asof", ""))
    return stocks


def _days_in(info, asof, trading_dates):
    try:
        added = date_cls.fromisoformat(info.get("added", ""))
    except ValueError:
        added = asof
    return sum(1 for d in trading_dates if d >= added) or 1


def _missing_days(code, info, days):
    """連續幾個交易日沒有這檔股票的資料。上櫃報價整天缺漏的日子不計入。"""
    market = info.get("market")
    if not market:
        market = "上櫃" if any(code in rec.get("otc_codes", []) for _, rec in days) else "上市"
    last_seen = max((d for d, rec in days if code in rec["quotes"]), default=None)
    return sum(
        1 for d, rec in days
        if (last_seen is None or d > last_seen)
        and (market == "上市" or rec.get("otc_quotes", True))
    )


def update_watchlist(days, series, capital_map):
    asof = days[-1][0]
    trading_dates = [d for d, _ in days]
    previous = load_previous_list()
    kept, removed, new = {}, [], {}

    for code, info in previous.items():
        days_in = _days_in(info, asof, trading_dates)
        s = series.get(code)
        if s is None:
            name = info.get("name", "")
            missing = _missing_days(code, info, days)
            if missing >= EXIT_SUSPEND_DAYS:
                removed.append({"code": code, "name": name, "days_in": days_in,
                                "reason": f"連續 {missing} 個交易日無交易資料（停牌或下市）"})
                continue
            if days_in > EXIT_MAX_DAYS:
                removed.append({"code": code, "name": name, "days_in": days_in,
                                "reason": f"入選超過 {EXIT_MAX_DAYS} 個交易日"})
                continue
            note = f"已 {missing} 日無資料" if missing else "今日上櫃報價缺漏"
            kept[code] = {**info, "name": name, "days_in": days_in, "status": f"持續（{note}）", "m": None}
            continue
        m = metrics(s, capital_map.get(code))
        reason = exit_reason(m, days_in)
        if reason:
            removed.append({"code": code, "name": s["name"], "reason": reason, "days_in": days_in})
            continue
        kept[code] = {**info, "name": s["name"], "days_in": days_in, "status": "持續", "m": m, "market": s["market"]}

    for code, s in series.items():
        if code in kept:
            continue
        m = metrics(s, capital_map.get(code))
        strategy = entry_strategy(m)
        if not strategy:
            continue
        new[code] = {
            "name": s["name"], "strategy": strategy, "added": asof.isoformat(),
            "tangle_pct": round(m["tangle"] * 100, 2), "days_in": 1,
            "status": "新增", "m": m, "market": s["market"],
        }
    print(f"[左側] 既有 {len(previous)} 檔：保留 {len(kept)}、移出 {len(removed)}；新入選 {len(new)} 檔", flush=True)
    return asof, kept, new, removed

# =========================
# 🔥 強勢股（入池嚴、出池寬）＋ 🚀 短線轉強股（提醒窗口 + 監控窗口）＋ 🎯 回檔專用觀察池
# =========================
def _vol_ok(closes, vols):
    n = len(closes)
    up = [vols[i] for i in range(n - 20, n) if closes[i] > closes[i - 1]]
    down = [vols[i] for i in range(n - 20, n) if closes[i] < closes[i - 1]]
    if not up:
        return False
    return not down or sum(up) / len(up) > sum(down) / len(down)


def _buy_streak(chips, idx):
    """截至最新交易日，外資(idx=0)或投信(idx=1)連續買超（>0）的天數。資料缺漏即中斷。"""
    n = 0
    for c in reversed(chips):
        if c is None or c[idx] <= 0:
            break
        n += 1
    return n


def _two_below_ma10(closes):
    """連續 2 日收盤都跌破「當日」MA10。"""
    n = len(closes)
    if n < 11:
        return False
    return closes[-1] < _ma_at(closes, 10, n) and closes[-2] < _ma_at(closes, 10, n - 1)


def _last_low_index(window):
    low = min(window)
    return max(i for i, v in enumerate(window) if v == low)


def calc_base(series):
    """流動性合格股票的計算基準（盤中雷達用）。回傳 (base, [(代號, 20日漲幅)...] 由強到弱)。"""
    base, ret_list = {}, []
    for code, s in series.items():
        closes, vols, chips = s["closes"], s["vols"], s["chips"]
        n = len(closes)
        if not _liquid(closes, vols):
            continue
        vol20 = sum(vols[-20:]) / 20
        vol_prev20 = sum(vols[-40:-20]) / 20
        last5 = chips[-5:]
        inst = None
        if len(last5) == 5 and all(c is not None for c in last5):
            inst = sum(c[0] + c[1] for c in last5)
        f_streak, t_streak = _buy_streak(chips, 0), _buy_streak(chips, 1)
        w59 = closes[-59:]  # 盤中：59 個已收盤日 + 今日即時價 = 60 日
        li = _last_low_index(w59)
        base[code] = {
            "close_20ago": closes[-20],
            "ma20_prev5": round(_ma_at(closes, 20, n - 4), 4),
            "high20": max(closes[-20:]),
            "high60": max(w59),
            "low60": min(w59),
            "rebound_high": max(w59[li:]),          # 自 60 日低點反彈過程中的最高收盤
            "vol_ok": _vol_ok(closes, vols),
            "vol_expand": vol20 >= vol_prev20 * REVERSAL_VOL_EXPAND,
            "inst_plus": bool(inst and inst > 0),
            "inst5": round(inst) if inst is not None else None,
            "above_streak": _above_streak(closes, n),
            "foreign_streak": f_streak,
            "trust_streak": t_streak,
            "inst_streak": max(f_streak, t_streak),  # 外資或投信，任一方連續買超天數
        }
        ret_list.append((code, closes[-1] / closes[-21] - 1))
    ret_list.sort(key=lambda x: -x[1])
    return base, ret_list


def load_previous_pools():
    """讀取前一次的 strong_stock_pool.json。回傳 (asof, rule, 🔥, 🚀, 🎯觀察池)。"""
    if not STRONG_JSON.exists():
        return "", "", {}, {}, {}
    try:
        data = json.loads(STRONG_JSON.read_text(encoding="utf-8"))
    except Exception:
        return "", "", {}, {}, {}
    asof = str(data.get("asof", ""))
    rule = str(data.get("rule", ""))
    strong = dict(data.get("stocks") or {})
    for info in strong.values():
        info.setdefault("added", asof)
    if rule != RULE_ID:
        print(f"[盤後選股] 前一份清單規則版本 {rule!r} ≠ {RULE_ID!r}：舊規則（RS 前 20%）的 🔥/🚀/🎯 成員不沿用，"
              f"從今晚起以新規則重新累積（避免舊清單灌入黏著池）", flush=True)
        return asof, rule, {}, {}, {}
    return asof, rule, strong, dict(data.get("short_stocks") or {}), dict(data.get("watch_stocks") or {})


def _days_since(added_text, asof, trading_dates):
    try:
        added = date_cls.fromisoformat(added_text)
    except (TypeError, ValueError):
        added = asof
    return sum(1 for d in trading_dates if d >= added) or 1


def eval_strong_entry(s, b):
    """🔥 入池條件（不含 RS 排名）。回傳 dict：kind（趨勢型/反轉型/None）、fails（沒通過的條件）、指標。"""
    closes = s["closes"]
    n = len(closes)
    close = closes[-1]
    ma5, ma10, ma20 = _ma_at(closes, 5, n), _ma_at(closes, 10, n), _ma_at(closes, 20, n)
    ma20_prev = _ma_at(closes, 20, n - MA20_RISING_LOOKBACK)
    high60, low60 = max(closes[-60:]), min(closes[-60:])
    dist_high = (high60 - close) / high60
    rebound = close / low60 - 1
    ext = close / ma20 - 1
    fails = []
    ma_order = ma5 > ma10 > ma20
    ma20_up = ma20 > ma20_prev
    if not ma_order:
        fails.append("MA5>MA10>MA20 未成立")
    if not ma20_up:
        fails.append("MA20 未高於 5 日前")
    if close > ma20 * STRONG_MAX_EXT:
        fails.append(f"距 MA20 {ext*100:.1f}% > 20%")
    if not b["vol_ok"]:
        fails.append("上漲日均量 ≤ 下跌日均量")
    kind = None
    kind_note = ""
    if dist_high <= TREND_NEAR_HIGH:
        kind = "趨勢型"
    else:
        rev_fails = []
        if rebound < REVERSAL_REBOUND:
            rev_fails.append(f"自 60 日低點僅反彈 {rebound*100:.0f}%（<25%）")
        if not b["vol_expand"]:
            rev_fails.append("近 20 日均量未達前 20 日 1.2 倍")
        w60 = closes[-60:]
        li = _last_low_index(w60)
        rb_prior = max(w60[li:-1]) if li < len(w60) - 1 else None
        high20_prior = max(closes[-21:-1])
        if not (close > high20_prior or (rb_prior is not None and close > rb_prior)):
            rev_fails.append("未突破近 20 日高點或反彈過程高點")
        if not rev_fails:
            kind = "反轉型"
        else:
            kind_note = f"距 60 日高點 {dist_high*100:.1f}% > 5%，反轉型也不成立：" + "；".join(rev_fails)
    if kind is None and kind_note:
        fails.append(kind_note)
    return {
        "kind": kind if not fails else None,
        "fails": fails,
        "ma5": ma5, "ma10": ma10, "ma20": ma20,
        "high60": high60, "low60": low60,
        "dist_high": dist_high, "rebound": rebound, "ext": ext,
        "ma_order": ma_order, "ma20_up": ma20_up, "type_hint": kind,
    }


def update_strong(series, base, rank_of, ret_map, total, prev_strong, asof, trading_dates, diag):
    """🔥 強勢股：入池看 RS 前 15%；已在池內者 RS 前 30% 且未連續 2 日收盤跌破 MA10 就黏著保留。"""
    top_n = max(1, int(total * RS_TOP_RATIO))
    keep_n = max(1, int(total * RS_KEEP_RATIO))
    pool, removed = {}, []
    for code, b in base.items():
        s = series[code]
        rank = rank_of[code]
        ev = eval_strong_entry(s, b)
        d = diag.setdefault(code, {})
        d.update({
            "rank": rank, "rs_top": round(rank / total * 100, 1), "ret20": round(ret_map[code] * 100, 2),
            "ma_order": ev["ma_order"], "ma20_up": ev["ma20_up"], "ext_pct": round(ev["ext"] * 100, 2),
            "vol_ok": b["vol_ok"], "type_hint": ev["type_hint"] or "",
        })
        rs_ok = rank <= top_n
        entry_fail = ([] if rs_ok else [f"20日漲幅未進前 {RS_TOP_RATIO:.0%}（第 {rank}/{total} 名，前 {rank/total:.0%}）"]) + ev["fails"]
        prev = prev_strong.get(code)
        qualifies = rs_ok and ev["kind"] is not None
        status = None
        kind = ev["kind"]
        if qualifies:
            status = "持續" if prev else "新入選"
        elif prev:
            closes = s["closes"]
            if rank > keep_n:
                removed.append({"pool": "🔥", "code": code, "name": s["name"],
                                "days": _days_since(prev.get("added"), asof, trading_dates),
                                "reason": f"20日漲幅排名掉出前 {RS_KEEP_RATIO:.0%}（第 {rank}/{total} 名）"})
            elif _two_below_ma10(closes):
                removed.append({"pool": "🔥", "code": code, "name": s["name"],
                                "days": _days_since(prev.get("added"), asof, trading_dates),
                                "reason": "連續 2 日收盤跌破 MA10"})
            else:
                status = "🧲黏著"
                kind = prev.get("type") or ev["type_hint"] or "趨勢型"
        d["strong_result"] = {"新入選": "入選", "持續": "入選（持續）", "🧲黏著": "黏著保留"}.get(status, "未入選")
        d["strong_fail"] = "" if qualifies else (entry_fail[0] if entry_fail else "")
        if status is None:
            continue
        closes = s["closes"]
        pool[code] = {
            "name": s["name"], "market": s["market"], "type": kind, "status": status,
            "added": prev.get("added") if prev else asof.isoformat(),
            "days_in": _days_since(prev.get("added") if prev else asof.isoformat(), asof, trading_dates),
            "close": closes[-1],
            "ma5": round(ev["ma5"], 2), "ma10": round(ev["ma10"], 2), "ma20": round(ev["ma20"], 2),
            "high20": b["high20"], "high60": ev["high60"], "low60": ev["low60"],
            "dist_high_pct": round(ev["dist_high"] * 100, 2), "rebound_pct": round(ev["rebound"] * 100, 2),
            "ret20_pct": round(ret_map[code] * 100, 2), "rs_rank": rank, "rs_top_pct": round(rank / total * 100, 1),
            "ext_pct": round(ev["ext"] * 100, 2), "inst5": b["inst5"], "inst_plus": b["inst_plus"],
            "why_not": "" if qualifies else (entry_fail[0] if entry_fail else ""),
        }
    for code, prev in prev_strong.items():
        if code not in base:
            removed.append({"pool": "🔥", "code": code, "name": prev.get("name", ""),
                            "days": _days_since(prev.get("added"), asof, trading_dates),
                            "reason": "流動性不足或無交易資料"})
    print(f"[🔥強勢股] 流動性合格 {total} 檔；入池 RS 前 {RS_TOP_RATIO:.0%}（{top_n} 檔內）、保留 RS 前 {RS_KEEP_RATIO:.0%}（{keep_n} 檔內）；"
          f"池內 {len(pool)} 檔（新入選 {sum(1 for p in pool.values() if p['status']=='新入選')}、"
          f"黏著 {sum(1 for p in pool.values() if p['status']=='🧲黏著')}），移出 {len(removed)} 檔", flush=True)
    return pool, removed


def update_short(series, base, ret_map, strong_pool, prev_short, prev_watch, handoff_codes,
                 asof, trading_dates, diag):
    """🚀 提醒窗口（站上均線第 3~5 日 + 法人連買）→ 入池滿 3 日分流：🔥 / 🎯 回檔專用觀察池（至多 10 日）。
    回傳 (short, watch, removed, handoff_notes, funnel, dropped_new)。"""
    short, watch, removed, notes = {}, {}, [], {}

    def pack(code, extra):
        s = series[code]
        b = base[code]
        closes = s["closes"]
        n = len(closes)
        close = closes[-1]
        ma5, ma10, ma20 = _ma_at(closes, 5, n), _ma_at(closes, 10, n), _ma_at(closes, 20, n)
        high60, low60 = max(closes[-60:]), min(closes[-60:])
        item = {
            "name": s["name"], "market": s["market"], "close": close,
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
            "ext_pct": round((close / ma20 - 1) * 100, 2),
            "dist_high_pct": round((high60 - close) / high60 * 100, 2),
            "rebound_pct": round((close / low60 - 1) * 100, 2),
            "ret20_pct": round(ret_map.get(code, 0) * 100, 2),
            "days_on_ma": b["above_streak"],
            "inst_streak": b["inst_streak"], "foreign_streak": b["foreign_streak"], "trust_streak": b["trust_streak"],
            "inst5": b["inst5"], "inst_plus": b["inst_plus"],
            "also_strong": code in strong_pool,
            "high60": high60, "low60": low60,
        }
        item.update(extra)
        return item

    def rm(pool, code, name, days, reason, moved=False):
        removed.append({"pool": pool, "code": code, "name": name, "days": days, "reason": reason, "moved": moved})

    # ---- 既有 🚀 ----
    for code, info in prev_short.items():
        in_days = _days_since(info.get("added"), asof, trading_dates)
        s, b = series.get(code), base.get(code)
        name = info.get("name", "")
        if s is None or b is None:
            rm("🚀", code, name, in_days, "流動性不足或無交易資料")
            continue
        closes = s["closes"]
        n = len(closes)
        close = closes[-1]
        ma10, ma20 = _ma_at(closes, 10, n), _ma_at(closes, 20, n)
        if close < ma20:
            rm("🚀", code, name, in_days, "收盤跌破 MA20")
        elif _two_below_ma10(closes):
            rm("🚀", code, name, in_days, "連續 2 日收盤跌破 MA10")
        elif in_days > SHORT_POOL_DAYS:
            if code in strong_pool:
                rm("🚀", code, name, in_days, f"入池滿 {SHORT_POOL_DAYS} 日，轉入 🔥 強勢股池", moved=True)
            elif close >= ma10:
                watch[code] = pack(code, {"watch_added": asof.isoformat(), "watch_days": 1,
                                          "short_added": info.get("added"), "origin": info.get("origin", "盤後")})
                rm("🚀", code, name, in_days, f"入池滿 {SHORT_POOL_DAYS} 日，未達 🔥 且未跌破 MA10，轉入 🎯 回檔專用觀察池", moved=True)
            else:
                rm("🚀", code, name, in_days, f"入池滿 {SHORT_POOL_DAYS} 日，收盤跌破 MA10，不轉入觀察池")
        else:
            short[code] = pack(code, {"added": info.get("added", asof.isoformat()), "in_pool_days": in_days,
                                      "origin": info.get("origin", "盤後"), "status": "持續"})

    # ---- 既有 🎯 回檔專用觀察池 ----
    for code, info in prev_watch.items():
        w_days = _days_since(info.get("watch_added"), asof, trading_dates)
        s, b = series.get(code), base.get(code)
        name = info.get("name", "")
        if s is None or b is None:
            rm("🎯", code, name, w_days, "流動性不足或無交易資料")
            continue
        closes = s["closes"]
        n = len(closes)
        close = closes[-1]
        if close < _ma_at(closes, 20, n):
            rm("🎯", code, name, w_days, "收盤跌破 MA20")
        elif _two_below_ma10(closes):
            rm("🎯", code, name, w_days, "連續 2 日收盤跌破 MA10")
        elif code in strong_pool:
            rm("🎯", code, name, w_days, "轉入 🔥 強勢股池", moved=True)
        elif w_days > WATCH_MAX_DAYS:
            rm("🎯", code, name, w_days, f"觀察期滿 {WATCH_MAX_DAYS} 個交易日")
        else:
            watch[code] = pack(code, {"watch_added": info.get("watch_added", asof.isoformat()), "watch_days": w_days,
                                      "short_added": info.get("short_added"), "origin": info.get("origin", "盤後")})

    # ---- 新入池：站上均線第 3~5 日 + MA5>MA10 + 外資或投信連續買超 ≥ 2 日 ----
    funnel = {"total": len(base), "streak": 0, "ma": 0, "inst": 0}
    for code, b in base.items():
        s = series[code]
        d = diag.setdefault(code, {})
        closes = s["closes"]
        n = len(closes)
        streak = b["above_streak"]
        ma5, ma10 = _ma_at(closes, 5, n), _ma_at(closes, 10, n)
        d.update({"streak": streak, "ma5_gt_ma10": ma5 > ma10, "inst_streak": b["inst_streak"],
                  "foreign_streak": b["foreign_streak"], "trust_streak": b["trust_streak"]})
        if code in short or code in watch:
            d["short_result"] = "已在 🚀" if code in short else "已在 🎯 觀察池"
            d["short_fail"] = ""
            continue
        fail = ""
        if streak < SHORT_MIN_STREAK:
            fail = f"站上 MA5/10/20 僅 {streak} 日（需 {SHORT_MIN_STREAK}~{SHORT_MAX_STREAK} 日）"
        elif streak > SHORT_MAX_STREAK:
            fail = f"已站上 {streak} 日，超過 {SHORT_MAX_STREAK} 日不算剛轉強"
        else:
            funnel["streak"] += 1
            if not ma5 > ma10:
                fail = "MA5 未高於 MA10"
            else:
                funnel["ma"] += 1
                if b["inst_streak"] < SHORT_MIN_INST_DAYS:
                    last_chip = s["chips"][-1]
                    fail = ("法人資料缺漏" if last_chip is None
                            else f"外資/投信連續買超僅 {b['inst_streak']} 日（需 ≥{SHORT_MIN_INST_DAYS} 日）")
                else:
                    funnel["inst"] += 1
        d["short_fail"] = fail
        d["short_result"] = "入選" if not fail else "未入選"
        if not fail:
            short[code] = pack(code, {"added": asof.isoformat(), "in_pool_days": 1, "origin": "盤後", "status": "新入選"})

    # ---- 左側潛力庫「已經發動」移出 → 當晚優先接 🔥，其次 🚀（豁免站上第 3~5 日）----
    for code in handoff_codes:
        if code in strong_pool:
            notes[code] = "→ 已在 🔥 強勢股池"
        elif code in short or code in watch:
            notes[code] = "→ 已在 🚀/🎯 池內"
        elif code not in base:
            notes[code] = "→ 流動性不足或無資料，單純移出"
        else:
            s, b = series[code], base[code]
            closes = s["closes"]
            n = len(closes)
            close = closes[-1]
            ma5, ma10, ma20 = _ma_at(closes, 5, n), _ma_at(closes, 10, n), _ma_at(closes, 20, n)
            if close > ma5 and close > ma10 and close > ma20 and ma5 > ma10 and b["inst_streak"] >= SHORT_MIN_INST_DAYS:
                short[code] = pack(code, {"added": asof.isoformat(), "in_pool_days": 1, "origin": "左側轉入", "status": "左側轉入"})
                notes[code] = "→ 轉入 🚀 短線轉強股（豁免站上第 3~5 日限制）"
            else:
                notes[code] = "→ 不符合 🔥/🚀，單純移出"

    # ---- 容量：🚀 最多 SHORT_POOL_MAX 檔，依 20 日漲幅 RS 淘汰最弱 ----
    dropped_new = 0
    if len(short) > SHORT_POOL_MAX:
        ordered = sorted(short.items(), key=lambda kv: -ret_map.get(kv[0], -9))
        for code, info in ordered[SHORT_POOL_MAX:]:
            if info.get("status") == "持續":
                rm("🚀", code, info.get("name", ""), info.get("in_pool_days", 1),
                   f"池滿 {SHORT_POOL_MAX} 檔，20日漲幅 RS 較弱被淘汰")
            else:
                dropped_new += 1
            del short[code]
    print(f"[🚀短線轉強] 漏斗：流動性合格 {funnel['total']} → 站上 {SHORT_MIN_STREAK}~{SHORT_MAX_STREAK} 日 {funnel['streak']} "
          f"→ MA5>MA10 {funnel['ma']} → 法人連買≥{SHORT_MIN_INST_DAYS}日 {funnel['inst']}；"
          f"🚀 池內 {len(short)} 檔、🎯 觀察池 {len(watch)} 檔，移出 {len(removed)} 檔，池滿未納入 {dropped_new} 檔", flush=True)
    return short, watch, removed, notes, funnel, dropped_new


def write_strong_outputs(asof, base, strong_pool, short_pool, watch_pool, taiex, industry_label):
    STRONG_JSON.write_text(json.dumps({
        "rule": RULE_ID,
        "asof": asof.isoformat(),
        "generated_at": rd.now_tw().isoformat(timespec="seconds"),
        "count": len(strong_pool),
        "short_count": len(short_pool),
        "watch_count": len(watch_pool),
        "taiex": taiex,
        "stocks": strong_pool,
        "short_stocks": short_pool,
        "watch_stocks": watch_pool,
        "base": base,
    }, ensure_ascii=False), encoding="utf-8")

    fields = ["代號", "名稱", "市場", "產業族群", "狀態", "池內天數", "類型", "收盤價", "MA5", "MA10", "MA20",
              "距MA20(%)", "近20日最高收盤", "60日高點", "距60日高點(%)", "60日低點", "自低點反彈(%)",
              "20日漲幅(%)", "RS排名", "RS前(%)", "法人5日合計(張)", "今日未達入池原因"]
    order = sorted(strong_pool.items(), key=lambda kv: kv[1]["rs_rank"])
    with STRONG_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([f"資料基準日：{asof.isoformat()}"])
        writer.writerow(fields)
        for code, p in order:
            writer.writerow([
                code, p["name"], p["market"], industry_label.get(code, ""), p["status"], p["days_in"], p["type"],
                p["close"], p["ma5"], p["ma10"], p["ma20"], p["ext_pct"], p["high20"], p["high60"],
                p["dist_high_pct"], p["low60"], p["rebound_pct"], p["ret20_pct"], p["rs_rank"], p["rs_top_pct"],
                "" if p["inst5"] is None else p["inst5"], p.get("why_not", ""),
            ])

    fields = ["代號", "名稱", "市場", "產業族群", "池", "狀態", "入池日", "入池天數", "站上均線天數",
              "外資連買(日)", "投信連買(日)", "收盤價", "MA5", "MA10", "MA20", "距MA20(%)", "60日高點",
              "距60日高點(%)", "60日低點", "自低點反彈(%)", "20日漲幅(%)", "同時為強勢股", "來源"]
    with SHORT_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([f"資料基準日：{asof.isoformat()}"])
        writer.writerow(fields)
        for code, p in sorted(short_pool.items(), key=lambda kv: (-kv[1]["ret20_pct"], kv[0])):
            writer.writerow([
                code, p["name"], p["market"], industry_label.get(code, ""), "🚀 短線轉強", p.get("status", ""),
                p.get("added", ""), p.get("in_pool_days", ""), p["days_on_ma"], p["foreign_streak"], p["trust_streak"],
                p["close"], p["ma5"], p["ma10"], p["ma20"], p["ext_pct"], p["high60"], p["dist_high_pct"],
                p["low60"], p["rebound_pct"], p["ret20_pct"], "是" if p["also_strong"] else "否", p.get("origin", ""),
            ])
        for code, p in sorted(watch_pool.items(), key=lambda kv: kv[0]):
            writer.writerow([
                code, p["name"], p["market"], industry_label.get(code, ""), "🎯 回檔觀察池", f"第 {p['watch_days']}/{WATCH_MAX_DAYS} 日",
                p.get("short_added", ""), p.get("watch_days", ""), p["days_on_ma"], p["foreign_streak"], p["trust_streak"],
                p["close"], p["ma5"], p["ma10"], p["ma20"], p["ext_pct"], p["high60"], p["dist_high_pct"],
                p["low60"], p["rebound_pct"], p["ret20_pct"], "是" if p["also_strong"] else "否", p.get("origin", ""),
            ])


def write_diagnosis(asof, series, base, diag, left_state):
    """pool_diagnosis.csv：每檔股票各條件是否通過、第一個沒通過的條件。"""
    fields = ["代號", "名稱", "市場", "流動性", "20日漲幅(%)", "RS排名", "RS前(%)",
              "MA5>MA10>MA20", "MA20上彎", "距MA20(%)", "量價配合", "型態判斷",
              "🔥結果", "🔥第一個沒通過的條件",
              "站上均線天數", "MA5>MA10", "外資連買(日)", "投信連買(日)", "🚀結果", "🚀第一個沒通過的條件", "🧭左側"]
    with DIAG_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([f"資料基準日：{asof.isoformat()}"])
        writer.writerow(fields)
        for code, s in sorted(series.items()):
            d = diag.get(code, {})
            liquid = code in base
            yn = lambda v: "" if v is None or v == "" else ("是" if v else "否")
            writer.writerow([
                code, s["name"], s["market"], "合格" if liquid else "不足",
                d.get("ret20", ""), d.get("rank", ""), d.get("rs_top", ""),
                yn(d.get("ma_order")), yn(d.get("ma20_up")), d.get("ext_pct", ""), yn(d.get("vol_ok")), d.get("type_hint", ""),
                d.get("strong_result", "流動性不足" if not liquid else ""), d.get("strong_fail", ""),
                d.get("streak", ""), yn(d.get("ma5_gt_ma10")), d.get("foreign_streak", ""), d.get("trust_streak", ""),
                d.get("short_result", "流動性不足" if not liquid else ""), d.get("short_fail", ""),
                left_state.get(code, ""),
            ])


def _pool_stats_line(label, pool, key="status"):
    counts = {}
    for p in pool.values():
        counts[p.get(key, "")] = counts.get(p.get(key, ""), 0) + 1
    return f"{label} {len(pool)} 檔（" + "、".join(f"{k} {v}" for k, v in counts.items() if k) + "）" if counts else f"{label} {len(pool)} 檔"


def build_short_message(asof, base, short_pool, watch_pool, removed, funnel, dropped_new):
    stat = {}
    for p in short_pool.values():
        stat[p.get("status", "")] = stat.get(p.get("status", ""), 0) + 1
    lines = [f"🚀 <b>【短線轉強股】{asof.isoformat()} 盤後</b>",
             f"🚀 池內 {len(short_pool)} 檔（新入選 {stat.get('新入選', 0)}、持續 {stat.get('持續', 0)}、左側轉入 {stat.get('左側轉入', 0)}）"
             f"｜🎯 回檔專用觀察池 {len(watch_pool)} 檔｜今日移出 {len([r for r in removed if r['pool'] in ('🚀', '🎯')])} 檔",
             f"進池：站上 MA5/10/20 第 {SHORT_MIN_STREAK}~{SHORT_MAX_STREAK} 日、MA5>MA10、外資或投信連續買超 ≥{SHORT_MIN_INST_DAYS} 日；"
             f"入池滿 {SHORT_POOL_DAYS} 日後分流（🔥 / 🎯 觀察池至多 {WATCH_MAX_DAYS} 日）；池上限 {SHORT_POOL_MAX} 檔",
             f"篩選：流動性合格 {funnel['total']} → 站上 {SHORT_MIN_STREAK}~{SHORT_MAX_STREAK} 日 {funnel['streak']} → "
             f"MA5>MA10 {funnel['ma']} → 法人連買≥{SHORT_MIN_INST_DAYS}日 {funnel['inst']}"
             + (f"（池滿未納入 {dropped_new} 檔）" if dropped_new else "")]
    order = sorted(short_pool.items(), key=lambda kv: (-kv[1]["ret20_pct"], kv[0]))
    for code, p in order[:TELEGRAM_LIST_MAX]:
        lines.append(
            f"• {code} {p['name']}｜{p.get('status', '')}（入池第 {p.get('in_pool_days', 1)}/{SHORT_POOL_DAYS} 日）"
            f"｜站上均線第 {p['days_on_ma']} 日｜距MA20 {p['ext_pct']:+.1f}%｜20日 {p['ret20_pct']:+.1f}%"
            f"｜法人連買 {p['inst_streak']} 日{'｜🔥同為強勢股' if p['also_strong'] else ''}"
        )
    if len(order) > TELEGRAM_LIST_MAX:
        lines.append(f"…另有 {len(order) - TELEGRAM_LIST_MAX} 檔，詳見 CSV")
    if watch_pool:
        lines.append("")
        lines.append(f"🎯 <b>回檔專用觀察池</b>（明日只監控回檔買點，至多 {WATCH_MAX_DAYS} 日）")
        for code, p in sorted(watch_pool.items())[:TELEGRAM_LIST_MAX]:
            lines.append(f"• {code} {p['name']}｜第 {p['watch_days']}/{WATCH_MAX_DAYS} 日｜距MA20 {p['ext_pct']:+.1f}%")
    moved = [r for r in removed if r["pool"] in ("🚀", "🎯")]
    if moved:
        lines.append("")
        lines.append("📤 <b>今日移出／分流</b>")
        for r in moved[:20]:
            lines.append(f"• {r['pool']} {r['code']} {r['name']}｜池內第 {r['days']} 日｜{r['reason']}")
        if len(moved) > 20:
            lines.append(f"…另有 {len(moved) - 20} 檔")
    if not short_pool:
        lines.append("盤後沒有 🚀 池內股票（盤中仍會即時判斷）。")
    return "\n".join(lines)


def build_strong_message(asof, base, pool, removed, taiex):
    stat = {}
    for p in pool.values():
        stat[p["status"]] = stat.get(p["status"], 0) + 1
    lines = [f"🔥 <b>【強勢股池】{asof.isoformat()} 盤後</b>",
             f"池內 {len(pool)} 檔（🆕新入選 {stat.get('新入選', 0)}、持續 {stat.get('持續', 0)}、🧲黏著 {stat.get('🧲黏著', 0)}）"
             f"｜📤移出 {len(removed)} 檔",
             f"入池：20日漲幅前 {RS_TOP_RATIO:.0%}；保留：前 {RS_KEEP_RATIO:.0%} 且未連續 2 日收盤跌破 MA10；"
             f"盤中另以即時價判斷全市場 {len(base)} 檔"]
    if taiex:
        state = "站上" if taiex["close"] > taiex["ma20"] else "低於"
        lines.append(f"大盤：加權指數 {taiex['close']:,.0f}，{state} MA20 {taiex['ma20']:,.0f}")
        if taiex["close"] <= taiex["ma20"]:
            lines.append("⚠️ 大盤在 MA20 之下，明日開盤若仍未站回，回檔買點會暫停")
    else:
        lines.append("⚠️ 加權指數資料抓取失敗，明日大盤濾網不啟用")
    order = sorted(pool.items(), key=lambda kv: kv[1]["rs_rank"])
    for code, p in order[:TELEGRAM_LIST_MAX]:
        if p["type"] == "反轉型":
            pos = f"🔄 反轉型｜自低點 +{p['rebound_pct']:.0f}%｜距前高 {p['dist_high_pct']:.1f}%"
        else:
            pos = f"🔥 強勢型｜距高點 {p['dist_high_pct']:.1f}%"
        lines.append(
            f"• {code} {p['name']}｜{p['status']}（第 {p['days_in']} 日）｜{pos}｜20日 {p['ret20_pct']:+.1f}%"
            f"（前 {p['rs_top_pct']:.0f}%）{'｜法人買' if p['inst_plus'] else ''}"
        )
        if p.get("why_not"):
            lines.append(f"　　↳ 今日未達入池：{p['why_not']}")
    if len(order) > TELEGRAM_LIST_MAX:
        lines.append(f"…另有 {len(order) - TELEGRAM_LIST_MAX} 檔，詳見 CSV")
    if removed:
        lines.append("")
        lines.append("📤 <b>今日移出</b>")
        for r in removed[:20]:
            lines.append(f"• {r['code']} {r['name']}｜池內第 {r['days']} 日｜{r['reason']}")
        if len(removed) > 20:
            lines.append(f"…另有 {len(removed) - 20} 檔")
    if not pool:
        lines.append("盤後沒有符合條件的強勢股（盤中仍會即時判斷）。")
    return "\n".join(lines)


# =========================
# 左側輸出
# =========================
def _fmt(v, digits=2):
    return "" if v is None else round(v, digits)


def _int(v):
    return "" if v is None else int(round(v))


def write_outputs(asof, kept, new, removed, industry_label):
    members = {**new, **kept}
    WATCHLIST_JSON.write_text(json.dumps({
        "asof": asof.isoformat(),
        "generated_at": rd.now_tw().isoformat(timespec="seconds"),
        "count": len(members),
        "stocks": {
            code: {
                "name": info["name"],
                "strategy": info.get("strategy", ""),
                "added": info.get("added", asof.isoformat()),
                "days_in": info.get("days_in", 1),
                "tangle_pct": info.get("tangle_pct"),
                "tangle_now": round(info["m"]["tangle"] * 100, 2) if info.get("m") else info.get("tangle_now"),
                "tangle_streak": info["m"]["tangle_streak"] if info.get("m") else info.get("tangle_streak"),
                "market": info.get("market", ""),
            }
            for code, info in members.items()
        },
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    fields = [
        "代號", "名稱", "市場", "產業族群", "狀態", "入選日", "入選天數", "入選策略", "類型",
        "股本(億)", "收盤價", "MA20", "目前糾結度(%)", "糾結連續天數", "入選時糾結度(%)",
        "量比(近3日/20日均量)", "最新成交量(張)", "20日均量(張)",
        "投信5日(張)", "外資5日(張)", "投信3日(張)", "外資3日(張)",
        "外資相關係數", "投信相關係數", "籌碼屬性", "土洋對作",
    ]
    order = sorted(members.items(), key=lambda kv: (kv[1]["status"] != "新增", kv[1].get("added", ""), kv[0]))
    with WATCHLIST_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerow([f"資料基準日：{asof.isoformat()}"])
        writer = csv.writer(fh)
        writer.writerow(fields)
        for code, info in order:
            m = info.get("m") or {}
            writer.writerow([
                code, info["name"], info.get("market", ""), industry_label.get(code, ""),
                info["status"], info.get("added", ""), info.get("days_in", ""), info.get("strategy", ""),
                m.get("kind", ""), _fmt(m.get("capital"), 1), m.get("close", ""), _fmt(m.get("ma20")),
                _fmt(m["tangle"] * 100) if m.get("tangle") is not None else "",
                m.get("tangle_streak", ""), info.get("tangle_pct", ""),
                _fmt(m.get("vol_ratio")), _int(m.get("vol_today")), _int(m.get("vol20")),
                _int(m.get("trust5")), _int(m.get("foreign5")), _int(m.get("trust3")), _int(m.get("foreign3")),
                _fmt(m.get("corr_f")), _fmt(m.get("corr_t")), m.get("profile", ""), m.get("duel", ""),
            ])
        if removed:
            writer.writerow([])
            writer.writerow(["今日移出"])
            writer.writerow(["代號", "名稱", "入選天數", "移出原因", "接棒去向"])
            for r in removed:
                writer.writerow([r["code"], r["name"], r["days_in"], r["reason"], r.get("handoff", "")])
    return len(members)


def _industry_labels(codes) -> dict:
    cache = rd._load_ic_cache()
    labels = {}
    for code in codes:
        chains = (cache.get(code) or {}).get("chains")
        if chains:
            labels[code] = rd._chain_label(chains)
    if len(labels) < len(codes):
        official = rd._fetch_industry_map()
        for code in codes:
            labels.setdefault(code, official.get(code, ""))
    return labels


def build_message(asof, kept, new, removed, total, warnings):
    lines = [
        f"🧭 <b>【左側潛力庫】{asof.isoformat()} 盤後</b>",
        f"清單共 {total} 檔（新增 {len(new)}、持續 {len(kept)}、移出 {len(removed)}），明日盤中優先監控",
    ]
    if new:
        lines += ["", "🆕 <b>新入選</b>"]
        for code, info in sorted(new.items()):
            m = info["m"]
            lines.append(
                f"• {code} {info['name']}｜{info['strategy']}｜均線糾結連續 {m['tangle_streak']} 日"
                f"（{m['tangle']*100:.1f}%）｜量比 {m['vol_ratio']:.2f}"
            )
    if removed:
        lines += ["", "📤 <b>移出</b>"]
        for r in removed:
            lines.append(f"• {r['code']} {r['name']}｜第 {r['days_in']} 天｜{r['reason']}"
                         + (f"｜{r['handoff']}" if r.get("handoff") else ""))
    if kept:
        lines += ["", "📌 <b>持續追蹤</b>"]
        ordered = sorted(kept.items(), key=lambda kv: -int(kv[1].get("days_in", 1)))
        for code, info in ordered[:TELEGRAM_LIST_MAX]:
            m = info.get("m")
            if m is None:
                tangle_txt = info["status"].replace("持續", "").strip("（）")
            elif m["tangle_streak"]:
                tangle_txt = f"糾結連續 {m['tangle_streak']} 日"
            else:
                tangle_txt = "均線已發散"
            lines.append(
                f"• {code} {info['name']}｜{info.get('strategy', '')}｜入選第 {info.get('days_in', '?')} 天｜{tangle_txt}"
            )
        if len(ordered) > TELEGRAM_LIST_MAX:
            lines.append(f"…另有 {len(ordered) - TELEGRAM_LIST_MAX} 檔，詳見 CSV")
    if not (new or kept):
        lines.append("目前清單是空的：今天沒有符合「連續3日均線糾結 + 量縮 + 法人進場」的股票。")
    lines.extend(warnings)
    return "\n".join(lines)


def main() -> None:
    print("=" * 55, flush=True)
    print("盤後選股 v9：🧭左側潛力庫 + 🚀短線轉強股(提醒/監控窗口) + 🔥強勢股池(黏著) + 🎯回檔觀察池 + 診斷檔", flush=True)
    print("=" * 55, flush=True)
    today = rd.now_tw().date()

    days, failed = collect_days(today)
    prune_data_dir(today)
    if len(days) < MIN_HISTORY_DAYS:
        msg = f"❌ <b>【盤後選股】</b> 交易日資料不足（{len(days)} 天），本次未更新清單"
        print(msg, flush=True)
        rd.send_telegram(msg)
        return
    series = build_series(days)
    capital_map = fetch_capital_map()

    # ---- 🧭 左側潛力庫 ----
    asof, kept, new, removed = update_watchlist(days, series, capital_map)
    trading_dates = [d for d, _ in days]

    # ---- 計算基準與 20 日漲幅排名 ----
    strong_base, ret_list = calc_base(series)
    total = len(ret_list)
    ret_map = dict(ret_list)
    rank_of = {code: i for i, (code, _) in enumerate(ret_list, 1)}
    prev_asof, prev_rule, prev_strong, prev_short, prev_watch = load_previous_pools()
    diag = {}

    # ---- 🔥 強勢股（黏著）----
    strong_pool, strong_removed = ({}, [])
    if total:
        strong_pool, strong_removed = update_strong(series, strong_base, rank_of, ret_map, total,
                                                    prev_strong, asof, trading_dates, diag)

    # ---- 🚀 短線轉強 + 🎯 回檔觀察池（含左側「已經發動」接棒）----
    handoff_codes = [r["code"] for r in removed if str(r.get("reason", "")).startswith("已經發動")]
    short_pool, watch_pool, pool_removed, handoff_notes, funnel, dropped_new = update_short(
        series, strong_base, ret_map, strong_pool, prev_short, prev_watch, handoff_codes,
        asof, trading_dates, diag)
    for r in removed:
        if r["code"] in handoff_notes:
            r["handoff"] = handoff_notes[r["code"]]
    left_state = {}
    for code, info in {**kept, **new}.items():
        left_state[code] = f"在清單（{info.get('status', '')}）"
    for r in removed:
        left_state[r["code"]] = f"今日移出：{r['reason']}" + (f" {r['handoff']}" if r.get("handoff") else "")

    taiex = None
    closes = fetch_taiex_closes(asof)
    if len(closes) >= 20 and closes[-1][0] == asof:
        values = [v for _, v in closes]
        taiex = {
            "asof": asof.isoformat(),
            "close": values[-1],
            "ma20": round(sum(values[-20:]) / 20, 2),
            "sum19": round(sum(values[-19:]), 2),
        }
        print(f"[大盤] 加權指數 {taiex['close']:,.2f}，MA20 {taiex['ma20']:,.2f}", flush=True)
    else:
        print(f"[大盤] 加權指數資料不足（取得 {len(closes)} 天），明日大盤濾網不啟用", flush=True)

    labels = _industry_labels(list({**kept, **new, **strong_pool, **short_pool, **watch_pool}.keys()))
    total_left = write_outputs(asof, kept, new, removed, labels)
    write_strong_outputs(asof, strong_base, strong_pool, short_pool, watch_pool, taiex, labels)
    write_diagnosis(asof, series, strong_base, diag, left_state)

    warnings = []
    window_start = days[-min(20, len(days))][0]
    recent_failed = [d for d in failed if d >= window_start]
    if recent_failed:
        warnings.append(f"⚠️ 以下日期資料抓取失敗，結果可能不完整：{', '.join(d.isoformat() for d in recent_failed)}")
    otc_quote_missing = [d.isoformat() for d, rec in days[-5:] if not rec.get("otc_quotes", True)]
    if otc_quote_missing:
        warnings.append(f"⚠️ 上櫃報價缺漏（{', '.join(otc_quote_missing)}），上櫃股票可能無法入選")
    otc_missing = [d.isoformat() for d, rec in days[-5:] if not rec.get("otc_chips")]
    if otc_missing:
        warnings.append(f"⚠️ 上櫃三大法人資料缺漏（{', '.join(otc_missing)}），上櫃股票的法人連買天數為 0，不會入選 🚀")
    if len(days) < STRONG_MIN_DAYS:
        warnings.append(f"⚠️ 交易日資料只有 {len(days)} 天，強勢股/短線轉強股需要 {STRONG_MIN_DAYS} 天，本次可能為空")

    rd.send_telegram(build_message(asof, kept, new, removed, total_left, warnings))
    rd.send_telegram_document(WATCHLIST_CSV, f"🧭 左側潛力庫（資料基準日 {asof.isoformat()}），共 {total_left} 檔")
    rd.send_telegram(build_short_message(asof, strong_base, short_pool, watch_pool, pool_removed, funnel, dropped_new))
    rd.send_telegram_document(SHORT_CSV, f"🚀 短線轉強股 {len(short_pool)} 檔 + 🎯 回檔觀察池 {len(watch_pool)} 檔（資料基準日 {asof.isoformat()}）")
    rd.send_telegram(build_strong_message(asof, strong_base, strong_pool, strong_removed, taiex))
    rd.send_telegram_document(STRONG_CSV, f"🔥 強勢股池（資料基準日 {asof.isoformat()}），共 {len(strong_pool)} 檔")
    rd.send_telegram_document(DIAG_CSV, f"🔎 選股診斷（資料基準日 {asof.isoformat()}）：每檔股票各條件與第一個沒通過的條件")
    print(f"[盤後選股] 完成：基準日 {asof}，🧭 {total_left} 檔，🚀 {len(short_pool)} 檔，🎯 {len(watch_pool)} 檔，"
          f"🔥 {len(strong_pool)} 檔（計算基準 {len(strong_base)} 檔）", flush=True)


if __name__ == "__main__":
    main()
