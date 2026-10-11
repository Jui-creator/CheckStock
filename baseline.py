"""同期量基準 baseline.py：每個交易日盤後執行，產生明天盤中 B 條件要用的 volume_baseline.json。

做什麼：
  1. 用 radar.py 的官方資料載入器取得「5 日均量 ≥ 1,000 張」的股票清單與基準日（最近一個已收盤交易日）。
  2. 用富邦歷史行情的「1 分 K」抓每檔最近 5 個交易日的每分鐘成交量（張），算成「開盤後第 m 分鐘開始前的累積量」。
  3. 存兩條曲線（取樣點見 radar.MARKS，點與點之間線性內插）：
       avg   前 5 日同時間累積量的平均（每個時間點排除最大的一天，與 radar 的 _trimmed_avg_vol 同一個做法）
       yest  基準日（昨日）同時間累積量
  4. 與富邦快照的全日成交量對照一次，確認「分 K 加總 = 快照 tradeVolume」（單位、來源一致），結果會寫進 Telegram。

時間：富邦歷史資料盤後 16:30 前更新；官方資料約 15:00 後齊全，建議 17:00 以後執行。
      富邦歷史行情限速 60 次/分，每檔 1 次，約 900 檔需要 15～20 分鐘（程式固定間隔 1.15 秒）。
環境變數：TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS
"""
import sys
import json
import time
import statistics
from datetime import datetime, timedelta, date as date_cls

import radar as rd

CALL_INTERVAL = 1.15                 # 每次歷史行情呼叫的最小間隔（秒），限速是 60 次/分
MIN_COVERAGE = 0.6                   # 成功算出基準的檔數 / 候選檔數 低於此值就視為失敗，不寫檔
MIN_CANDLES_PER_DAY = 30             # 某一天的分 K 少於這個根數，視為該日資料不完整
BASELINE_DAYS = 5
MAX_MINUTE = rd.OPEN_TO_CLOSE_MINUTES - 1      # 270 = 13:30 那一根


def candle_minute(ts: str):
    """'2026-07-09T13:30:00.000+08:00' → ('2026-07-09', 270)；解析失敗回傳 None。"""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(rd.TW_TZ)
    except Exception:
        return None
    return dt.strftime("%Y-%m-%d"), dt.hour * 60 + dt.minute - (rd.MARKET_OPEN_HOUR * 60 + rd.MARKET_OPEN_MINUTE)


def day_curves(candles: list, days: list):
    """把分 K 依日期整理成 {日期: 取樣點上的累積量 list}。某天分 K 不足就不放進結果。"""
    by_day = {d: [0.0] * (MAX_MINUTE + 1) for d in days}
    counts = {d: 0 for d in days}
    for c in candles:
        parsed = candle_minute(c.get("date"))
        vol = rd._f(c.get("volume"))
        if parsed is None or vol is None or parsed[0] not in by_day:
            continue
        day, minute = parsed
        if 0 <= minute <= MAX_MINUTE:
            by_day[day][minute] += vol
            counts[day] += 1
    out = {}
    for d in days:
        if counts[d] < MIN_CANDLES_PER_DAY:
            continue
        cum = [0.0] * (MAX_MINUTE + 2)          # cum[m] = 第 m 分鐘開始前的累積量；cum[271] = 全日量
        for i in range(MAX_MINUTE + 1):
            cum[i + 1] = cum[i] + by_day[d][i]
        out[d] = [cum[m] for m in rd.MARKS]
    return out


def build_entry(curves: dict, days: list):
    """5 天都齊才算。avg = 每個取樣點去掉最大的一天後平均；yest = 最後一天的曲線。"""
    if len(curves) < BASELINE_DAYS or any(d not in curves for d in days):
        return None
    avg = []
    for j in range(len(rd.MARKS)):
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
        tv = rd._f(row.get("tradeVolume")) if row else None
        if tv and tv > 0:
            ratios.append(e["yest"][-1] / tv)
    if not ratios:
        return "快照沒有可對照的資料"
    med = statistics.median(ratios)
    near = sum(1 for r in ratios if abs(r - 1) <= 0.02) / len(ratios)
    verdict = "✅ 一致" if abs(med - 1) <= 0.02 and near >= 0.8 else "⚠️ 不一致，請檢查單位或 13:30 收盤集合競價那一根"
    return f"分K加總 / 快照全日量：中位數 {med:.3f}，±2% 內 {near:.0%}（{len(ratios)} 檔）{verdict}"


def main() -> int:
    started = time.time()
    print("=" * 55, flush=True)
    print("同期量基準 baseline.py（富邦 1 分 K → volume_baseline.json）", flush=True)
    print("=" * 55, flush=True)

    bases = rd.load_all_stock_bases(include_today=None)
    if not bases:
        rd.send_telegram("❌ <b>【同期量基準】</b> 無法建立歷史基底，未更新 volume_baseline.json")
        return 1
    asof = max(b["asof_date"] for b in bases.values())
    today = rd.now_tw()
    if rd._is_weekday(today) and today.hour >= 15 and asof < today.date():
        rd.send_telegram(f"ℹ️ <b>【同期量基準】</b> 官方資料基準日是 {asof}，不是今天（{today.date()}）。"
                         f"若今天是交易日，代表官方資料還沒公布，請稍後重跑；若今天休市可忽略。")
    universe = sorted(c for c, b in bases.items() if b["liquid"])
    print(f"基準日 {asof}，候選 {len(universe)} 檔（5 日均量 ≥ {rd.MIN_VOL5_LOTS:,} 張）", flush=True)

    feed = rd.FubonFeed()
    try:
        feed.login()
        days = trading_days(feed, asof)
    except Exception as exc:
        rd.send_telegram(f"❌ <b>【同期量基準】</b> 富邦登入或查詢交易日失敗：{type(exc).__name__}: {exc}")
        return 1
    if len(days) < BASELINE_DAYS or days[-1] != asof.isoformat():
        rd.send_telegram(f"❌ <b>【同期量基準】</b> 富邦日 K 的最後交易日是 {days[-1] if days else '無'}，與官方基準日 {asof} 不符"
                         f"（富邦歷史資料盤後 16:30 才更新，請稍後重跑）。")
        return 1
    print(f"基準日期：{', '.join(days)}", flush=True)

    entries, skipped, errors = {}, [], 0
    last_call = 0.0
    for n, code in enumerate(universe, 1):
        wait = CALL_INTERVAL - (time.time() - last_call)
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
    if coverage < MIN_COVERAGE:
        rd.send_telegram(f"❌ <b>【同期量基準】</b> 成功率只有 {coverage:.0%}（{len(entries)}/{len(universe)}，抓取錯誤 {errors}），"
                         f"未更新 volume_baseline.json。請看 Actions log 的診斷行。")
        return 1

    check_text = check_against_snapshot(feed, entries, asof.isoformat())
    print(check_text, flush=True)
    payload = {
        "rule": rd.BASELINE_RULE,
        "asof": asof.isoformat(),
        "days": days,
        "marks": rd.MARKS,
        "generated_at": rd.now_tw().isoformat(timespec="seconds"),
        "stocks": entries,
    }
    tmp = rd.BASELINE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(rd.BASELINE_FILE)

    minutes = (time.time() - started) / 60
    skipped_txt = f"｜略過 {len(skipped)} 檔（分K不足 5 天或抓取失敗）" if skipped else ""
    rd.send_telegram(
        f"✅ <b>【同期量基準】完成</b>\n基準日 {asof}｜取樣日期 {days[0]} ~ {days[-1]}\n"
        f"涵蓋 {len(entries)}/{len(universe)} 檔{skipped_txt}｜耗時 {minutes:.1f} 分鐘\n{check_text}\n"
        f"明天盤中 B 條件使用這份基準。")
    print(f"完成：{len(entries)}/{len(universe)} 檔，耗時 {minutes:.1f} 分鐘，檔案 {rd.BASELINE_FILE.stat().st_size / 1024:.0f} KB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
