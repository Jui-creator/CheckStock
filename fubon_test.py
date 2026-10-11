"""富邦 Neo API 行情測試：登入 → 全市場快照 → 格式／單位／速度檢查。
只讀行情，不下單。不會印出任何金鑰、密碼或帳號。"""
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

TW = timezone(timedelta(hours=8))
CODE_RE = re.compile(r"^[1-9]\d{3}$")   # 與 radar.py 相同的一般股票代號規則


def need_env(name):
    value = os.environ.get(name, "")
    if not value:
        print(f"❌ 缺少環境變數 {name}（請檢查 GitHub Secrets 名稱）")
        sys.exit(1)
    return value


def ts_text(us):
    """lastUpdated 是微秒時間戳記 → 台北時間字串"""
    try:
        return datetime.fromtimestamp(us / 1_000_000, TW).strftime("%H:%M:%S")
    except Exception:
        return str(us)


print("=" * 50)
print("富邦 Neo API 行情測試")
print("=" * 50)
print(f"Python {sys.version.split()[0]}")

# ---------- 0. 檢查憑證檔 ----------
CERT = "fubon.p12"
if not os.path.exists(CERT):
    print(f"❌ 找不到 {CERT}，請檢查 workflow 的「還原憑證」步驟與 FUBON_CERT_B64")
    sys.exit(1)
size = os.path.getsize(CERT)
print(f"憑證檔大小：{size} 位元組（你的原檔應為 2790）")
if size != 2790:
    print("⚠️ 大小與原檔不同，Secret 可能貼錯或被改到（前後多了空白或換行）")

# ---------- 1. 匯入 SDK ----------
try:
    from fubon_neo.sdk import FubonSDK
except Exception as exc:
    print(f"❌ 匯入 fubon_neo 失敗：{type(exc).__name__}: {exc}")
    print("   請確認 vendor/ 內是 Linux x86-64 的 .whl，且 Python 在 3.8～3.13")
    sys.exit(1)

try:
    from fubon_neo.fugle_marketdata.rest.base_rest import FugleAPIError
except Exception:
    FugleAPIError = Exception

# ---------- 2. 登入 ----------
user_id = need_env("FUBON_ID")
api_key = need_env("FUBON_API_KEY")
cert_pass = need_env("FUBON_CERT_PASS")

sdk = FubonSDK()
t0 = time.time()
try:
    res = sdk.apikey_login(user_id, api_key, CERT, cert_pass)
except Exception as exc:
    print(f"❌ 登入發生例外：{type(exc).__name__}: {exc}")
    sys.exit(1)

print(f"登入結果：is_success={res.is_success}，耗時 {time.time() - t0:.2f}s")
if not res.is_success:
    print(f"❌ 登入失敗訊息：{res.message}")
    sys.exit(1)
accounts = res.data if isinstance(res.data, list) else [res.data]
print(f"可用帳號數：{len(accounts)}（帳號內容不顯示）")

# ---------- 3. 建立行情連線 ----------
try:
    sdk.init_realtime()
    stock = sdk.marketdata.rest_client.stock
    print("✅ 行情連線建立完成")
except Exception as exc:
    print(f"❌ init_realtime 失敗：{type(exc).__name__}: {exc}")
    print("   可能是帳號沒有「行情」權限，請回憑證管理頁確認")
    sys.exit(1)


def get_quotes(market):
    """先用 type=COMMONSTOCK（只含一般股票）；參數不支援就退回不帶 type。"""
    try:
        return stock.snapshot.quotes(market=market, type="COMMONSTOCK"), "type=COMMONSTOCK"
    except TypeError:
        return stock.snapshot.quotes(market=market), "未帶 type"


# ---------- 4. 全市場快照 ----------
snapshots = {}
for market in ("TSE", "OTC"):
    print("-" * 50)
    t = time.time()
    try:
        r, mode = get_quotes(market)
    except FugleAPIError as exc:
        status = getattr(exc, "status_code", "?")
        print(f"❌ {market} 快照失敗：HTTP {status}｜{getattr(exc, 'response_text', exc)}")
        continue
    except Exception as exc:
        print(f"❌ {market} 快照失敗：{type(exc).__name__}: {exc}")
        continue
    elapsed = time.time() - t
    rows = r.get("data", [])
    snapshots[market] = rows
    stocks = [x for x in rows if CODE_RE.fullmatch(str(x.get("symbol", "")))]
    types = {}
    for x in rows:
        types[x.get("type")] = types.get(x.get("type"), 0) + 1
    trial = sum(1 for x in rows if x.get("isTrial"))
    no_last = sum(1 for x in rows if x.get("lastPrice") in (None, 0))
    traded = sum(1 for x in rows if (x.get("tradeVolume") or 0) > 0)
    print(f"{market}（{mode}）：日期 {r.get('date')}，快照時間 {r.get('time')}，耗時 {elapsed:.2f}s")
    print(f"  總筆數 {len(rows)}｜符合 4 碼股票代號 {len(stocks)}｜類型分布 {types}")
    print(f"  試撮 {trial} 檔｜無 lastPrice {no_last} 檔｜已有成交量 {traded} 檔")
    if rows:
        print(f"  欄位：{sorted(rows[0].keys())}")

# ---------- 5. 單位與格式檢查 ----------
print("-" * 50)
print("單位檢查：tradeValue ÷ (價格 × 成交量) 應接近 1000（成交量單位為「張」時）")
for market, rows in snapshots.items():
    for x in rows:
        if x.get("symbol") in ("2330", "6488", "3008"):
            price = x.get("lastPrice") or x.get("closePrice") or 0
            vol = x.get("tradeVolume") or 0
            val = x.get("tradeValue") or 0
            ratio = val / (price * vol) if price and vol else 0
            print(f"  {x['symbol']} {x.get('name')}｜lastPrice={x.get('lastPrice')}"
                  f"｜close={x.get('closePrice')}｜開={x.get('openPrice')}｜高={x.get('highPrice')}"
                  f"｜低={x.get('lowPrice')}｜量={vol}｜額={val:,.0f}｜isTrial={x.get('isTrial')}"
                  f"｜更新={ts_text(x.get('lastUpdated'))}｜比值≈{ratio:.0f}")

# ---------- 6. 連續抓 3 次，確認資料會更新、速度穩定 ----------
if "TSE" in snapshots:
    print("-" * 50)
    print("連續抓上市快照 3 次（間隔 10 秒）：")
    for i in range(3):
        t = time.time()
        try:
            r, _ = get_quotes("TSE")
            row = next((x for x in r["data"] if x["symbol"] == "2330"), {})
            print(f"  第{i + 1}次：{time.time() - t:.2f}s｜快照時間 {r.get('time')}"
                  f"｜2330 lastPrice={row.get('lastPrice')}｜量={row.get('tradeVolume')}"
                  f"｜更新={ts_text(row.get('lastUpdated'))}")
        except Exception as exc:
            print(f"  第{i + 1}次失敗：{type(exc).__name__}: {exc}")
        if i < 2:
            time.sleep(10)

# ---------- 7. 加權指數（大盤濾網用）----------
print("-" * 50)
print("加權指數探測（失敗不影響前面結果）：")
try:
    idx = stock.intraday.tickers(type="INDEX")
    rows = idx.get("data", []) if isinstance(idx, dict) else []
    print(f"  INDEX 清單 {len(rows)} 筆，前 5 筆：{rows[:5]}")
    for x in rows:
        if "加權" in str(x.get("name", "")) and "櫃" not in str(x.get("name", "")):
            q = stock.intraday.quote(symbol=x["symbol"])
            print(f"  {x['symbol']} {x.get('name')} → {q}")
            break
except Exception as exc:
    print(f"  指數查詢失敗：{type(exc).__name__}: {exc}")

print("=" * 50)
print("測試結束")
