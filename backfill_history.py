"""一次性補歷史 backfill_history.py：用富邦日 K 往回重播，替 signal_history.json 補上「今天以前」已經存在的 B 池與 A2 追蹤池。
這是上線時只跑一次的作業，不放在 baseline.py 裡；需要同資料夾的 radar.py（v69）。跑完不需要再跑。

【B 池：精確重播】
  對最近 10 個交易日每一天，用「當天收盤後的日 K」重跑 B 夜間掃描（與 baseline 完全相同的 radar.b_scan_last）；
  之後逐日檢查：今日最低價 < B 訊號日最低價 → 失效；第 5 個交易日仍未發動 → B_EXPIRED。基準日仍存活的才放進 B 池（waiting_days 已計好）。
  注意：日 K 看不到盤中，無法重播「確認發動」，所以補進來的 B 一律是 Watching（還在等）。
【A2 追蹤池：日 K 近似重播（只是推估）】
  盤中的 A1 通知 + 收盤確認，用日 K 近似成：
    前一日收盤 ≤ 前一日 max(MA5/10/20)；收盤 > MA5/10/20（含當日收盤）；漲幅 > 2%；收盤 > 開盤；收盤 > 當日均價(成交額/成交量，當 VWAP 近似)；
    當日成交量 ≥ 前 5 日均量 × 1.5 且 ≥ 1,000 張；當日成交額 ≥ 30 億。（5 日均量 ≥ 1,000 張才納入）
  之後逐日套用 A2 追蹤池的移出規則：收盤跌破 MA10、收盤跌破突破日低點（= A1 當日最低價）、滿 10 個交易日。
  限制：沒有 BEAR（2 倍量）判斷、沒有真正的「盤中先通知」、entry_price 用當日收盤近似。補進去的標的 src = "backfill"、origin = "A1_CLOSE_CONFIRMED"。
安全：signal_history.json 已有內容時預設中止；BACKFILL_FORCE=1 才會執行，且只「新增」不存在的代號，不覆蓋既有資料。
環境變數：FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS、TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、
          BACKFILL_ALL=1（掃全市場 1,900 檔，約 40 分鐘；預設只抓成交量預篩後的約 750 檔，約 15 分鐘）、BACKFILL_FORCE=1
輸出：signal_history.json（補好歷史）、backfill_report.csv（每筆重播結果，供你檢查合不合理）
跑完請接著手動跑一次 baseline（force），讓 initial_pools.json 帶進這些標的與它們的分時量矩陣。
"""
import os
import sys
import csv
from datetime import timedelta

import radar as rd

WINDOW = max(rd.B_WAIT_DAYS, rd.A_TRACK_DAYS)          # 往回重播的交易日數（10）
REPORT_FILE = rd.BASE_DIR / "backfill_report.csv"


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y")


def _mean(xs):
    return sum(xs) / len(xs)


def slice_series(s: dict, n: int) -> dict:
    """取日 K 序列的前 n 根（= 模擬「當天收盤後」手上的資料）。"""
    return {k: (v[:n] if isinstance(v, list) else v) for k, v in s.items()}


def trading_calendar(series: dict) -> list:
    """交易日曆：至少一半股票有那天日 K 的日期。"""
    counts = {}
    for s in series.values():
        for d in s["dates"]:
            counts[d] = counts.get(d, 0) + 1
    need = 0.5 * len(series)
    return sorted(d for d, n in counts.items() if n >= need)


# =========================
# B 池重播（精確）
# =========================
def replay_b(series: dict, cal: list) -> list:
    win = cal[-WINDOW:]
    out = []
    for code, s in series.items():
        pos = {d: i for i, d in enumerate(s["dates"])}
        for sd in win:
            i = pos.get(sd)
            if i is None or i + 1 < max(rd.KD_MIN_DAYS, rd.B_AVG_DAYS + 2):
                continue
            hit = rd.b_scan_last(slice_series(s, i + 1))
            if not hit:
                continue
            later = [d for d in cal if d > sd]
            status, end_day = "alive", None
            for k, t in enumerate(later, start=1):
                j = pos.get(t)
                if j is None:
                    continue
                if s["l"][j] < hit["burst_low"]:
                    status, end_day = "dead", t
                    break
                if k >= rd.B_WAIT_DAYS:
                    status, end_day = "expired", t
                    break
            avg5 = _mean(s["v"][max(0, i - 4):i + 1])
            out.append({"code": code, "name": s["name"], "market": s["market"], "signal_day": sd, "hit": hit, "status": status,
                        "end_day": end_day, "waiting_days": len(later), "liquid": avg5 >= rd.MIN_VOL5_LOTS,
                        "last_close": s["c"][pos[cal[-1]]] if cal[-1] in pos else None})
    return out


# =========================
# A1（收盤確認）近似 → A2 追蹤池重播
# =========================
def replay_a(series: dict, cal: list):
    win = cal[-WINDOW:]
    out, per_day = [], {d: 0 for d in win}
    for code, s in series.items():
        pos = {d: i for i, d in enumerate(s["dates"])}
        c, o, l, v, val = s["c"], s["o"], s["l"], s["v"], s["val"]
        for sd in win:
            i = pos.get(sd)
            if i is None or i < 21:
                continue
            avg5 = _mean(v[i - 5:i])                                   # 前 5 個交易日均量（早上看到的 5 日均量）
            if avg5 < rd.MIN_VOL5_LOTS:
                continue
            pc = c[i - 1]
            prev_ma_max = max(_mean(c[i - 5:i]), _mean(c[i - 10:i]), _mean(c[i - 20:i]))
            if pc > prev_ma_max:
                continue
            ma5, ma10, ma20 = _mean(c[i - 4:i + 1]), _mean(c[i - 9:i + 1]), _mean(c[i - 19:i + 1])
            if not (c[i] > ma5 and c[i] > ma10 and c[i] > ma20):
                continue
            if (c[i] / pc - 1) * 100.0 <= rd.A1_CHG_MIN or c[i] <= o[i]:
                continue
            vw = rd.vwap_of(v[i], val[i])
            if vw is None or c[i] <= vw:
                continue
            if v[i] < rd.A1_VOL_RATIO * avg5 or v[i] < rd.A1_MIN_EST_LOTS or val[i] < rd.A_MIN_EST_TURNOVER:
                continue
            per_day[sd] += 1
            later = [d for d in cal if d > sd]
            status, end_day, why = "alive", None, ""
            for k, t in enumerate(later, start=1):
                j = pos.get(t)
                if j is None:
                    continue
                if c[j] < _mean(c[j - 9:j + 1]):
                    status, end_day, why = "dead", t, "收盤跌破MA10"
                    break
                if c[j] < l[i]:
                    status, end_day, why = "dead", t, "收盤跌破突破日低點"
                    break
                if k >= rd.A_TRACK_DAYS:
                    status, end_day, why = "expired", t, "追蹤期滿"
                    break
            last = pos.get(cal[-1])
            out.append({"code": code, "name": s["name"], "market": s["market"], "signal_day": sd, "entry_price": c[i], "breakout_low": l[i],
                        "status": status, "end_day": end_day, "why": why, "age": len(later),
                        "last_close": c[last] if last is not None else None,
                        "above_ma5": (c[last] >= _mean(c[last - 4:last + 1])) if last is not None and last >= 4 else True,
                        "end_close": c[pos[end_day]] if end_day in pos else None})
    return out, per_day


def main() -> int:
    print("=" * 55, flush=True)
    print("一次性補歷史 backfill_history.py（日 K 重播 → signal_history.json）", flush=True)
    print("=" * 55, flush=True)
    full_market = _truthy("BACKFILL_ALL")
    force = _truthy("BACKFILL_FORCE")
    pools, pool_note = rd.load_pools()
    print(f"[追蹤清單] {pool_note}", flush=True)
    if any(pools[k] for k in ("a", "b", "a1_pending")) and not force:
        msg = "signal_history.json 已經有內容，補歷史會中止（避免覆蓋）。確定要補請設 BACKFILL_FORCE=1（只會新增不存在的代號）。"
        print(msg, flush=True)
        rd.send_telegram(f"⚠️ <b>【補歷史】</b> {msg}")
        return 1

    feed = rd.FubonFeed()
    try:
        feed.login()
    except Exception as exc:
        rd.send_telegram(f"❌ <b>【補歷史】</b> 富邦登入失敗：{type(exc).__name__}: {exc}")
        return 1

    snap = feed.snapshot_all()
    universe = rd.fubon_universe(snap, 0.0) if full_market else rd.pick_universe(snap)
    today = rd.now_tw().date()
    start = today - timedelta(days=rd.LOOKBACK_CALENDAR_DAYS)
    print(f"[補歷史] 範圍：{'全市場' if full_market else '快照成交量預篩後'} {len(universe):,} 檔（全市場快照 {len(snap['rows']):,} 檔），"
          f"抓日 K {start} ～ {today}，約 {len(universe) * rd.BASELINE_CALL_INTERVAL / 60:.0f} 分鐘", flush=True)
    series, fail = rd.fetch_series(feed, universe, start, today)
    if not universe or len(series) / len(universe) < rd.FETCH_MIN_COVERAGE:
        msg = f"日 K 成功率過低（{len(series)}/{len(universe)}，失敗 {fail}），中止"
        print(msg, flush=True)
        rd.send_telegram(f"❌ <b>【補歷史】</b> {msg}。富邦歷史資料盤後 16:30 才更新，請稍後重跑。")
        return 1
    last_dates = {}
    for s in series.values():
        last_dates[s["dates"][-1]] = last_dates.get(s["dates"][-1], 0) + 1
    asof = max(last_dates, key=lambda d: (last_dates[d], d))
    series = {c: s for c, s in series.items() if s["dates"][-1] == asof}
    print(f"[補歷史] 基準日 {asof}；{rd.normalize_units(feed, series, snap, asof)}", flush=True)
    cal = [d for d in trading_calendar(series) if d <= asof]
    if len(cal) < WINDOW + 25:
        msg = f"交易日曆只有 {len(cal)} 天，不夠重播（需要至少 {WINDOW + 25} 天）"
        print(msg, flush=True)
        rd.send_telegram(f"❌ <b>【補歷史】</b> {msg}")
        return 1
    asof_str = asof.isoformat()
    print(f"[補歷史] 交易日曆 {len(cal)} 天，重播最近 {WINDOW} 天：{cal[-WINDOW].isoformat()} ～ {asof_str}", flush=True)

    # ---- 重播 ----
    b_all = replay_b(series, cal)
    a_all, a_per_day = replay_a(series, cal)
    b_by_day = {}
    for r in b_all:
        b_by_day[r["signal_day"]] = b_by_day.get(r["signal_day"], 0) + 1
    print("[B 訊號] 每天新出現檔數：" + "｜".join(f"{d.strftime('%m/%d')} {b_by_day.get(d, 0)}" for d in cal[-WINDOW:]), flush=True)
    print("[A1 近似] 每天出現檔數：" + "｜".join(f"{d.strftime('%m/%d')} {a_per_day.get(d, 0)}" for d in cal[-WINDOW:]), flush=True)

    # ---- 寫進追蹤清單 ----
    pools["days"] = sorted(set(pools.get("days") or []) | {d.isoformat() for d in cal[-40:]})
    pools["asof"] = asof_str
    added_a = added_b = 0
    latest_a, latest_b = {}, {}
    for r in a_all:
        if r["status"] == "alive":
            if r["code"] not in latest_a or r["signal_day"] > latest_a[r["code"]]["signal_day"]:
                latest_a[r["code"]] = r
    for r in b_all:
        if r["status"] == "alive":
            if r["code"] not in latest_b or r["signal_day"] > latest_b[r["code"]]["signal_day"]:
                latest_b[r["code"]] = r
    for r in a_all:                                          # 已移出的：寫進 closed（報表用）
        if r["status"] != "alive":
            rd.close_record(pools, "A", r["code"], {"name": r["name"], "entry_date": r["signal_day"].isoformat(), "entry_price": r["entry_price"],
                                                  "origin": "A1_CLOSE_CONFIRMED(backfill)"},
                            r["end_day"].isoformat(), f"{r['why']}（補歷史）", r["end_close"])
    for r in b_all:
        if r["status"] != "alive":
            reason = "失效：跌破B低點防線（補歷史）" if r["status"] == "dead" else "B_EXPIRED:NO_ACTIVATION_WITHIN_5_TRADING_DAYS（補歷史）"
            rd.close_record(pools, "B", r["code"], {"name": r["name"], "burst_date": r["hit"]["burst_date"]}, r["end_day"].isoformat(), reason, None,
                            {"status": "B_EXPIRED" if r["status"] == "expired" else "B_INVALID", "waiting_days": r["waiting_days"]})
    for code, r in sorted(latest_a.items()):
        if code in pools["a"]:
            continue
        pools["a"][code] = {"name": r["name"], "industry": "", "market": r["market"], "entry_date": r["signal_day"].isoformat(), "entry_time": "",
                            "entry_price": r["entry_price"], "breakout_low": r["breakout_low"], "status": "Active" if r["above_ma5"] else "Pullback",
                            "origin": "A1_CLOSE_CONFIRMED", "last_close": r["last_close"], "last_date": asof_str, "miss_days": 0, "a2_dates": [],
                            "close_src": "backfill_daily", "close_provisional": False, "src": "backfill"}
        added_a += 1
    for code, r in sorted(latest_b.items()):
        if code in pools["b"]:
            continue
        e = {"name": r["name"], "industry": "", "market": r["market"], "status": "Watching", "waiting_days": r["waiting_days"], "miss_days": 0,
             "src": "backfill", "inst5": [], "inst_t": None}
        e.update(r["hit"])
        pools["b"][code] = e
        added_b += 1
    rd.save_pools(pools)

    # ---- 報表 ----
    rows = []
    for r in sorted(a_all, key=lambda x: (x["signal_day"], x["code"])):
        rows.append({"類型": "A1收盤確認(日K推估)", "代號": r["code"], "名稱": r["name"], "市場": r["market"], "訊號日": r["signal_day"].isoformat(),
                     "狀態": r["status"], "結束日": r["end_day"].isoformat() if r["end_day"] else "", "原因": r["why"],
                     "突破日低點/B防線": round(r["breakout_low"], 2), "價格": round(r["entry_price"], 2), "5日均量>=1000張": "是", "備註": "近似：收盤價當通知價"})
    for r in sorted(b_all, key=lambda x: (x["signal_day"], x["code"])):
        rows.append({"類型": "B訊號(精確重播)", "代號": r["code"], "名稱": r["name"], "市場": r["market"], "訊號日": r["signal_day"].isoformat(),
                     "狀態": r["status"], "結束日": r["end_day"].isoformat() if r["end_day"] else "",
                     "原因": {"dead": "跌破B低點防線", "expired": "等滿5日未發動", "alive": ""}[r["status"]],
                     "突破日低點/B防線": round(r["hit"]["burst_low"], 2), "價格": round(r["hit"]["burst_close"], 2),
                     "5日均量>=1000張": "是" if r["liquid"] else "否", "備註": f"爆量 {r['hit']['burst_ratio']}x｜K {r['hit']['kd_k']}/D {r['hit']['kd_d']}｜{r['hit']['trigger']}"})
    fields = ["類型", "代號", "名稱", "市場", "訊號日", "狀態", "結束日", "原因", "突破日低點/B防線", "價格", "5日均量>=1000張", "備註"]
    with REPORT_FILE.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    n_b_illiquid = sum(1 for r in latest_b.values() if not r["liquid"])
    lines = [f"🧾 <b>【補歷史完成】</b> 基準日 {asof_str}｜重播最近 {WINDOW} 個交易日｜範圍 {'全市場' if full_market else '預篩'} {len(series):,} 檔",
             f"🔵 B：期間共出現 {len(b_all)} 次訊號 → 仍存活（Watching）{len(latest_b)} 檔，新增進 B 池 {added_b} 檔"
             + (f"（其中 5 日均量 < 1,000 張 {n_b_illiquid} 檔）" if n_b_illiquid else ""),
             "　每天新出現：" + "｜".join(f"{d.strftime('%m/%d')} {b_by_day.get(d, 0)}" for d in cal[-WINDOW:]),
             f"🟢 A1 近似：期間共 {len(a_all)} 次 → 仍在 A2 追蹤池 {len(latest_a)} 檔，新增 {added_a} 檔（推估，非實際通知）",
             "　每天出現：" + "｜".join(f"{d.strftime('%m/%d')} {a_per_day.get(d, 0)}" for d in cal[-WINDOW:]),
             "下一步：手動跑一次 baseline（force），讓 initial_pools.json 帶進這些標的與分時量矩陣。明細見 backfill_report.csv。"]
    rd.send_telegram("\n".join(lines))
    print("\n".join(l.replace("<b>", "").replace("</b>", "") for l in lines), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
