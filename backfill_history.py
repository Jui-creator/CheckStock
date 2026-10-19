"""一次性補歷史 backfill_history.py（v3）：用富邦日 K 往回重播，替 signal_history.json 補上「今天以前」已經存在的 B 池與 A1 追蹤 / A2 池，
並把每一次 A2 觸發（二次加碼）也重播出來寫進報表；最後順便做一次「收盤後選池」，直接產生明天要用的 initial_pools.json。
這是上線時只跑一次的作業，不放在 baseline.py / radar.py 裡；需要同資料夾的 radar.py（v70）。跑完不需要再跑。
v3 跟著 v70 架構更新：
  ① A1 追蹤 / A2 池的重播改用 v69.9 以後的移出規則（見下）；補進來的標的寫好 stage / pullback_days / a2（拉回天數與整理期均量），不用靠隔天重算。
  ② 不再叫你「接著手動跑 baseline」：選池已經搬到收盤後（radar），所以這支最後直接呼叫 radar.scan_core 產生 initial_pools.json（BACKFILL_NO_SCAN=1 可跳過）。
  ③ 範圍固定是全市場（B 池要全市場；約 37 分鐘），BACKFILL_ALL 不再需要。
  ④ 傍晚 16:30 以前富邦歷史日 K 還沒有今天：收盤後（13:35 之後）跑會自動用最終快照合成今天的 K 棒，和 radar 收盤後選池一樣。
【B 池：精確重播】
  對最近 10 個交易日每一天，用「當天收盤後的日 K」重跑 B 掃描（與 radar 完全相同的 radar.b_scan_last）；
  之後逐日檢查：今日最低價 < B 訊號日最低價 → 失效；第 5 個交易日仍未發動 → B_EXPIRED。基準日仍存活的才放進 B 池（waiting_days 已計好）。
  注意：日 K 看不到盤中，無法重播「確認發動」，所以補進來的 B 一律是 Watching（還在等）。
【A1 追蹤 / A2 池：日 K 近似重播（只是推估）】
  盤中的 A1 通知 + 收盤確認，用日 K 近似成：
    前一日收盤 ≤ 前一日 max(MA5/10/20)；收盤 > MA5/10/20（含當日收盤）；漲幅 > 2%；收盤 > 開盤；收盤 > 當日均價(成交額/成交量，當 VWAP 近似)；
    當日成交量 ≥ 前 5 日均量 × 1.5 且 ≥ 1,000 張；當日成交額 ≥ 30 億。（5 日均量 ≥ 1,000 張才納入）
  之後逐日套用追蹤規則（與 radar 13:35 結算一致）：收盤跌破 MA10 → 移出；收盤跌破突破日低點（= A1 當日最低價）→ 移出；
    連續「收盤 < MA5」超過 A2_MAX_DAYS（6）天 → 移出（拉回逾 6 日未站回 MA5）；滿 10 個交易日而且當下不在拉回 → 移出；已在 A2 池（拉回中）的不受 10 天限制。
    仍存活的：目前連續拉回 1～6 天 = A2 池，0 天 = A1 追蹤。
【A2 觸發：日 K 近似重播（只是推估）】
  對每一筆 A1 收盤確認，在它還在追蹤池的每一天（含移出當天，因為盤中還在池內）逐日檢查：
    ① 到前一天為止，連續「收盤 < MA5 且 ≥ MA10 且 ≥ 突破日低點」的整理天數 n 落在 A2_MIN_DAYS ～ A2_MAX_DAYS（讀 radar.py 的常數）
    ② 當天最低價 ≥ 突破日低點；③ 當天收盤 > MA5（A2_REQUIRE_ALL_MAS = True 時還要 > MA10、MA20，含當日收盤）；④ 當天收盤 > 當日均價（VWAP 近似）
    ⑤ 當天成交量 ≥ 整理期平均量 × A2_VOL_RATIO（1.2）；⑥ 當天成交額 ≥ 30 億
  全部成立就記一筆「A2觸發(日K推估)」。同一檔同一天只記一筆（不同 A1 訊號重疊時取較早的那筆）。
  限制：沒有 BEAR（A2 在空頭禁止）的判斷；成交量用「實際全日量」而不是盤中預估量，所以會比實盤寬鬆（偏樂觀）；
        沒有真正的「連續 2 輪」二次確認；通知價用收盤價近似。補進去的標的 src = "backfill"、origin = "A1_CLOSE_CONFIRMED"。
安全：signal_history.json 已有內容時預設中止；BACKFILL_FORCE=1 才會執行，且只「新增」不存在的代號，不覆蓋既有資料。
環境變數：FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS、TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、BACKFILL_FORCE=1、BACKFILL_NO_SCAN=1（只補 signal_history.json，不做收盤後選池）
輸出：signal_history.json（補好歷史）、initial_pools.json（選池，明天盤中用）、backfill_report.csv（每筆重播結果，供你檢查合不合理）
跑完請確認 history_backfill.yml 會把 signal_history.json、initial_pools.json、backfill_report.csv 一起 commit。
"""
import os
import sys
import csv
from datetime import timedelta

import radar as rd

WINDOW = max(rd.B_WAIT_DAYS, rd.A_TRACK_DAYS)          # 往回重播的交易日數（10）
REPORT_FILE = rd.BASE_DIR / "backfill_report.csv"

TYPE_A_MEMBER = "A1收盤確認→進追蹤（日K推估）"
TYPE_A_TRIGGER = "A2觸發（二次加碼，日K推估）"
TYPE_B = "B訊號(精確重播)"


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
# A1（收盤確認）近似 → A1 追蹤 / A2 池成員重播
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
            status, end_day, why, n_pb = "alive", None, "", 0
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
                n_pb = (n_pb + 1) if c[j] < _mean(c[j - 4:j + 1]) else 0           # 連續「收盤 < MA5」的天數
                if n_pb > rd.A2_MAX_DAYS:
                    status, end_day, why = "dead", t, f"拉回逾{rd.A2_MAX_DAYS}日未站回MA5"
                    break
                if k >= rd.A_TRACK_DAYS and n_pb == 0:
                    status, end_day, why = "expired", t, f"追蹤滿{rd.A_TRACK_DAYS}日且已不在拉回"
                    break
            last = pos.get(cal[-1])
            out.append({"code": code, "name": s["name"], "market": s["market"], "signal_day": sd, "entry_price": c[i], "breakout_low": l[i],
                        "status": status, "end_day": end_day, "why": why, "age": len(later), "pullback_days": n_pb if status == "alive" else 0,
                        "last_close": c[last] if last is not None else None,
                        "end_close": c[pos[end_day]] if end_day in pos else None})
    return out, per_day


# =========================
# A2 觸發重播（日 K 近似）
# =========================
def replay_a2_triggers(series: dict, cal: list, a_all: list):
    """對每一筆 A1 收盤確認，在「還在追蹤池內」的每一天檢查 A2 觸發。回傳 (觸發列表, {日期: 檔數})。
    池內期間 = 訊號日之後、到移出當天為止（移出當天盤中仍在池內；alive 的到基準日）。
    整理天數用 radar.consolidation_run 以「前一天為止」的日 K 計算（與盤中用夜間資料的算法一致）。"""
    out, seen = [], set()
    per_day = {d: 0 for d in cal[-WINDOW:]}
    for r in sorted(a_all, key=lambda x: (x["signal_day"], x["code"])):
        s = series.get(r["code"])
        if s is None:
            continue
        pos = {d: i for i, d in enumerate(s["dates"])}
        c, l, v, val = s["c"], s["l"], s["v"], s["val"]
        floor = r["breakout_low"]
        for t in (d for d in cal if d > r["signal_day"]):
            if r["end_day"] is not None and t > r["end_day"]:
                break
            j = pos.get(t)
            if j is None or j < 20 or (r["code"], t) in seen:
                continue
            prev = slice_series(s, j)                                   # 只用到前一個交易日為止的資料
            cons = rd.consolidation_run(prev["dates"], prev["c"], prev["v"], r["signal_day"], floor)
            n, avg_vol = cons["n"], cons["avg_vol"]
            if not avg_vol or not (rd.A2_MIN_DAYS <= n <= rd.A2_MAX_DAYS):
                continue
            if l[j] < floor:
                continue
            ma5, ma10, ma20 = _mean(c[j - 4:j + 1]), _mean(c[j - 9:j + 1]), _mean(c[j - 19:j + 1])
            if c[j] <= ma5:
                continue
            if rd.A2_REQUIRE_ALL_MAS and not (c[j] > ma10 and c[j] > ma20):
                continue
            vw = rd.vwap_of(v[j], val[j])
            if rd.A2_REQUIRE_VWAP and (vw is None or c[j] <= vw):
                continue
            if v[j] < avg_vol * rd.A2_VOL_RATIO or val[j] < rd.A_MIN_EST_TURNOVER:
                continue
            seen.add((r["code"], t))
            if t in per_day:
                per_day[t] += 1
            out.append({"code": r["code"], "name": r["name"], "market": r["market"], "day": t, "a1_day": r["signal_day"], "floor": floor,
                        "close": c[j], "consol_days": n, "avg_vol": avg_vol, "vol_ratio": v[j] / avg_vol, "val": val[j], "ma5": ma5})
    return out, per_day


def main() -> int:
    print("=" * 55, flush=True)
    print("一次性補歷史 backfill_history.py v3（日 K 重播 → signal_history.json → 收盤後選池 → initial_pools.json）", flush=True)
    print("=" * 55, flush=True)
    force = _truthy("BACKFILL_FORCE")
    do_scan = not _truthy("BACKFILL_NO_SCAN")
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
    universe = rd.build_universe(snap, pools)                # B_FULL_MARKET = True → 全市場
    today = rd.now_tw().date()
    start = today - timedelta(days=rd.LOOKBACK_CALENDAR_DAYS)
    print(f"[補歷史] 範圍：{len(universe):,} 檔（全市場快照 {len(snap['rows']):,} 檔），抓日 K {start} ～ {today}，約 {len(universe) * rd.BASELINE_CALL_INTERVAL / 60:.0f} 分鐘", flush=True)
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
    merged_today = False
    now = rd.now_tw()
    if rd._is_weekday(now) and now >= rd._close_confirm_today(now) and asof < today:      # 收盤後但富邦歷史還沒有今天（16:30 才更新）→ 用最終快照合成今天的 K 棒
        snap2 = feed.snapshot_all()
        dates = {str(m.get("date", "")).replace("-", "") for m in snap2["meta"].values()}
        if today.strftime("%Y%m%d") in dates:
            bars = rd.bars_from_snapshot(snap2, today)
            note = rd.unit_sanity(feed, series, bars, today)
            n_merged = rd.merge_today_bar(series, bars, today)
            asof, merged_today, snap = today, True, snap2
            print(f"[補歷史] 富邦歷史日 K 還沒有 {today}，用最終快照合成 {n_merged} 檔的今天 K 棒" + (f"｜{note}" if note else ""), flush=True)
    series = {c: s for c, s in series.items() if s["dates"][-1] == asof}
    if not merged_today:
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
    t_all, t_per_day = replay_a2_triggers(series, cal, a_all)
    b_by_day = {}
    for r in b_all:
        b_by_day[r["signal_day"]] = b_by_day.get(r["signal_day"], 0) + 1
    win = cal[-WINDOW:]
    fmt_days = lambda m: "｜".join(f"{d.strftime('%m/%d')} {m.get(d, 0)}" for d in win)
    print("[B 訊號] 每天新出現檔數：" + fmt_days(b_by_day), flush=True)
    print("[A1 近似] 每天出現檔數：" + fmt_days(a_per_day), flush=True)
    print("[A2 觸發] 每天出現檔數：" + fmt_days(t_per_day), flush=True)
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
    trig_dates = {}
    for t in t_all:
        trig_dates.setdefault(t["code"], []).append(t["day"].isoformat())
    stage_of = {}
    for code, r in sorted(latest_a.items()):
        if code in pools["a"]:
            continue
        s = series[code]
        cons = rd.consolidation_run(s["dates"], s["c"], s["v"], r["signal_day"], r["breakout_low"])      # 目前連續拉回天數與整理期均量（與 radar 夜間重算同一個函式）
        n_pb = int(cons["n"] or 0)
        stage = rd.a_stage_from_days(n_pb)
        stage_of[code] = stage
        mine = [d for d in sorted(trig_dates.get(code, [])) if d > r["signal_day"].isoformat()]
        pools["a"][code] = {"name": r["name"], "industry": "", "market": r["market"], "entry_date": r["signal_day"].isoformat(), "entry_time": "",
                            "entry_price": r["entry_price"], "breakout_low": r["breakout_low"], "status": "Pullback" if n_pb >= 1 else "Active",
                            "stage": stage, "pullback_days": n_pb, "a2": cons,
                            "origin": "A1_CLOSE_CONFIRMED", "last_close": r["last_close"], "last_date": asof_str, "miss_days": 0, "a2_dates": mine,
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
        stage = {"A2": "A2池", "A1": "A1追蹤"}.get(stage_of.get(r["code"], ""), "") if r["status"] == "alive" else ""
        rows.append({"類型": TYPE_A_MEMBER, "代號": r["code"], "名稱": r["name"], "市場": r["market"], "訊號日": r["signal_day"].isoformat(),
                     "狀態": r["status"], "目前階段": stage, "結束日": r["end_day"].isoformat() if r["end_day"] else "", "原因": r["why"],
                     "突破日低點/B防線": round(r["breakout_low"], 2), "價格": round(r["entry_price"], 2), "5日均量>=1000張": "是",
                     "備註": "A1 當日收盤確認（收盤價當通知價）；這一列是進入 A1 追蹤，不是 A2 觸發"})
    for t in sorted(t_all, key=lambda x: (x["day"], x["code"])):
        rows.append({"類型": TYPE_A_TRIGGER, "代號": t["code"], "名稱": t["name"], "市場": t["market"], "訊號日": t["day"].isoformat(),
                     "狀態": "觸發", "目前階段": "", "結束日": "", "原因": f"拉回整理 {t['consol_days']} 天後再發動",
                     "突破日低點/B防線": round(t["floor"], 2), "價格": round(t["close"], 2), "5日均量>=1000張": "是",
                     "備註": f"A1 確認日 {t['a1_day'].isoformat()}｜整理期均量 {t['avg_vol']:,.0f} 張，當日量 {t['vol_ratio']:.2f}x｜成交額 {t['val'] / 1e8:.1f} 億｜MA5 {t['ma5']:.2f}｜"
                             f"推估：用實際全日量代替盤中預估量，會比實盤寬鬆"})
    for r in sorted(b_all, key=lambda x: (x["signal_day"], x["code"])):
        rows.append({"類型": TYPE_B, "代號": r["code"], "名稱": r["name"], "市場": r["market"], "訊號日": r["signal_day"].isoformat(),
                     "狀態": r["status"], "目前階段": "", "結束日": r["end_day"].isoformat() if r["end_day"] else "",
                     "原因": {"dead": "跌破B低點防線", "expired": "等滿5日未發動", "alive": ""}[r["status"]],
                     "突破日低點/B防線": round(r["hit"]["burst_low"], 2), "價格": round(r["hit"]["burst_close"], 2),
                     "5日均量>=1000張": "是" if r["liquid"] else "否", "備註": f"爆量 {r['hit']['burst_ratio']}x｜K {r['hit']['kd_k']}/D {r['hit']['kd_d']}｜{r['hit']['trigger']}"})
    fields = ["類型", "代號", "名稱", "市場", "訊號日", "狀態", "目前階段", "結束日", "原因", "突破日低點/B防線", "價格", "5日均量>=1000張", "備註"]
    with REPORT_FILE.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    n_b_illiquid = sum(1 for r in latest_b.values() if not r["liquid"])
    n_a2 = sum(1 for st in stage_of.values() if st == "A2")
    lines = [f"🧾 <b>【補歷史完成】</b> 基準日 {asof_str}｜重播最近 {WINDOW} 個交易日｜全市場 {len(series):,} 檔" + ("｜今天 K 棒由最終快照合成" if merged_today else ""),
             f"🔵 B：期間共出現 {len(b_all)} 次訊號 → 仍存活（Watching）{len(latest_b)} 檔，新增進 B 池 {added_b} 檔"
             + (f"（其中 5 日均量 < 1,000 張 {n_b_illiquid} 檔）" if n_b_illiquid else ""),
             "　每天新出現：" + fmt_days(b_by_day),
             f"🟢 A1 收盤確認（日K推估）：期間共 {len(a_all)} 次 → 仍在追蹤 {len(latest_a)} 檔（A1 追蹤 {len(stage_of) - n_a2}、A2 池 {n_a2}），新增 {added_a} 檔",
             "　每天 A1 確認：" + fmt_days(a_per_day),
             f"🟡 A2 觸發（二次加碼，日K推估）：期間共 {len(t_all)} 次（整理 {rd.A2_MIN_DAYS}～{rd.A2_MAX_DAYS} 天後再發動）",
             "　每天觸發：" + fmt_days(t_per_day),
             "以上 A1 / A2 都是日 K 推估，不是實際盤中通知；A2 觸發用實際全日量，比實盤寬鬆。明細見 backfill_report.csv。"]
    rd.send_telegram("\n".join(lines))
    print("\n".join(l.replace("<b>", "").replace("</b>", "") for l in lines), flush=True)
    if not do_scan:
        print("[補歷史] BACKFILL_NO_SCAN=1：不做收盤後選池，明天盤中沒有 initial_pools.json 可用（請自己補跑 radar 收盤後選池）", flush=True)
        return 0
    # ---- 收盤後選池：直接產生明天要用的 initial_pools.json ----
    try:
        industry = feed.industry_map()
    except Exception as exc:
        print(f"[補歷史] 產業別查詢失敗：{type(exc).__name__}: {exc}", flush=True)
        industry = {}
    try:
        init, bases, index_ctx, info = rd.scan_core(feed, asof, series, universe, pools, industry=industry, snap=snap,
                                                    notes=["補歷史後選池"], ic_budget=rd.SCAN_IC_BUDGET_SECONDS,
                                                    max_new_curves=None)         # 補歷史沒有背景預抓：所有候選股的 1 分 K 都要現場抓（約 9 分鐘），不能套 150 檔上限
    except rd.ScanError as exc:
        rd.send_telegram(f"❌ <b>【補歷史】選池失敗</b>：{exc}。signal_history.json 已補好，請稍後重跑一次（BACKFILL_FORCE=1 只會新增不存在的代號）。")
        return 1
    rd.send_telegram(rd.build_night_message(init, bases, pools, asof_str, index_ctx, market_total=info["market_total"], n_scanned=info["n_scanned"],
                                            n_liquid=info["n_liquid"], n_b_new=info["n_b_new"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
