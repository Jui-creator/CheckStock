"""夜間運算 baseline.py（v69）：每個交易日盤後執行，產出明天 radar.py 盤中唯一使用的靜態資料 initial_pools.json，並更新追蹤清單 signal_history.json。
價格與成交量全部來自富邦 Neo API；法人用官方資料（外資＋投信，只當參考）。建議 18:00 之後由 Google Apps Script 呼叫 baseline.yml（workflow_dispatch）觸發。
流程：
  0. 重複觸發保護：今天已經成功產出同一基準日的 initial_pools.json → 直接結束（要強制重跑：workflow 手動執行時勾選 force，或環境變數 FORCE=1）
  1. 富邦：全市場日 K（原始價格）→ MA5/10/20、5 日 / 20 日均量、KD(9,3,3)；追蹤清單內的股票即使沒達標也會抓
  2. 核對 / 補結算：radar 13:35 已結算 → 用官方日 K 核對當日 A1 收盤確認（close_provisional → official_reconciled）；
                    radar 當天沒結算成功 → 這裡用官方日 K 補結算（A1 收盤確認 / A2 移出 / B 等待日數與到期）
  3. B 池：KD 低檔金叉＋爆量＋收盤位置，通過者併入 B 池（等待最多 5 個交易日）
  4. 候選池：A1 候選池（5 日均量 ≥ 1,000 張 ＋ 昨收 ≤ 昨日最高均線）、A2 追蹤池（收盤確認的 A1，10 個交易日內）、B 候選池
  5. 分時量矩陣：只替候選股抓 1 分 K，預先算好「開盤後每分鐘的歷史同期累積佔比」（近 20 日中位數，與全市場曲線 70:30 混合）→ volume_time_matrix
  6. 官方法人（證交所 T86 ＋ 櫃買）近 5 日；沒出齊會重試；仍沒有 → 富邦補 B 候選。只記錄、不篩選
  7. 加權、櫃買指數最近 19 個完整交易日收盤（隔天算盤中即時月線）
  8. 原子寫入 initial_pools.json（暫存檔 → fsync → os.replace），再存 signal_history.json。任何一步資料不完整 → 不覆寫前一版 initial_pools.json
環境變數：TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS、FORCE（可選）
"""
import os
import sys
import asyncio
from datetime import date

import radar as rd


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y")


def already_done(feed):
    """同一個基準日、今天已經成功產出過 initial_pools.json → (True, 說明)。Apps Script 重試或重複觸發時不重跑。"""
    init, _ = rd.load_initial_pools()
    if init is None:
        return False, ""
    meta = init.get("metadata") or {}
    today = rd.now_tw().date()
    try:
        days = rd.trading_days(feed, today)
        latest = days[-1] if days else None
    except Exception as exc:
        print(f"[重複保護] 查詢最近交易日失敗（{type(exc).__name__}: {exc}），不略過", flush=True)
        return False, ""
    if latest and meta.get("baseline_date") == latest and str(meta.get("built_at", ""))[:10] == today.isoformat():
        return True, f"initial_pools.json 已在 {meta.get('built_at')} 產出（基準日 {latest}）"
    return False, ""


def main() -> int:
    print("=" * 55, flush=True)
    print("夜間運算 baseline.py v69（富邦日 K / 1 分 K ＋ 官方法人 → initial_pools.json ＋ signal_history.json）", flush=True)
    print("=" * 55, flush=True)
    feed = rd.FubonFeed()
    try:
        feed.login()
    except Exception as exc:
        rd.send_telegram(f"❌ <b>【夜間運算】</b> 富邦登入失敗：{type(exc).__name__}: {exc}")
        return 1
    if not _truthy("FORCE"):
        skip, why = already_done(feed)
        if skip:
            print(f"[重複保護] {why}，重複觸發直接忽略（要強制重跑請勾選 force 或設 FORCE=1）", flush=True)
            return 0

    pools, pool_note = rd.load_pools()
    print(f"[追蹤清單] {pool_note}", flush=True)
    tracked = rd.tracked_from_pools(pools)                 # A2 追蹤池：標記拉回整理
    extra = rd.extra_codes_from_pools(pools)               # 追蹤清單內的代號都要有日 K（收盤結算要用均線）
    all_bases = rd.load_all_stock_bases(feed, tracked, extra)
    if not all_bases:
        rd.send_telegram("❌ <b>【夜間運算】</b> 無法建立歷史基底（富邦日 K 抓取失敗或成功率過低），未更新 initial_pools.json。"
                         "富邦歷史資料盤後 16:30 才更新，請稍後重跑。")
        return 1
    market_total = rd.UNIVERSE_INFO.get("market") or len(all_bases)
    bases = {c: b for c, b in all_bases.items() if (b["liquid"] or c in extra) and b["market"] not in rd.UNRELIABLE_MARKETS}
    liquid = {c: b for c, b in bases.items() if b["liquid"]}
    if not liquid:
        rd.send_telegram("❌ <b>【夜間運算】</b> 沒有可用的股票（日 K 資料不完整或均量不足），未更新 initial_pools.json")
        return 1
    print(f"全市場 {market_total:,} 檔，達標（5 日均量 ≥ {rd.MIN_VOL5_LOTS:,} 張）{len(liquid):,} 檔", flush=True)
    try:
        chain_map = asyncio.run(rd.refresh_ic_chains(sorted(liquid)))
    except Exception as exc:
        print(f"[產業鏈] 查詢流程例外，改用富邦產業別: {type(exc).__name__}: {exc!r}", flush=True)
        chain_map = {}
    rd.apply_ic_chains(bases, chain_map)
    asof = max(b["asof_date"] for b in bases.values())
    asof_str = asof.isoformat()
    today = rd.now_tw()
    if rd._is_weekday(today) and today.hour >= 17 and asof < today.date():
        rd.send_telegram(f"ℹ️ <b>【夜間運算】</b> 富邦日 K 的基準日是 {asof}，不是今天（{today.date()}）。"
                         f"若今天是交易日，代表富邦歷史資料還沒更新（盤後 16:30），請稍後重跑；若今天休市可忽略。")

    # ---- 核對 / 補結算 ----
    sbases = rd.settlement_bases(bases)
    if pools.get("asof") == asof_str:
        rec = rd.reconcile_a1_confirm(pools, bases, asof_str)
        settle_note = f"13:35 結算已完成；官方日 K 核對 A1 收盤確認 {rec['checked']} 檔，核對後移出 {rec['removed']} 檔"
    elif pools.get("asof", "") > asof_str:
        settle_note = f"追蹤清單的結算日（{pools.get('asof')}）比基準日新，略過補結算"
    elif not pools.get("asof"):
        rd.settle_close(pools, sbases, rd.official_closes(bases), asof_str)
        settle_note = f"首次建立追蹤清單（v69），追蹤日曆從基準日 {asof_str} 開始"
    else:
        res = rd.settle_close(pools, sbases, rd.official_closes(bases), asof_str)
        settle_note = (f"⚠️ radar 當日沒有完成 13:35 結算，已用官方日 K 補結算：A1 收盤確認 {len(res['a1_confirmed'])} 檔、未確認 {len(res['a1_failed'])} 檔，"
                       f"A2 追蹤池移出 {len(res['a_exits'])} 檔，B 池移出 {len(res['b_exits'])} 檔")
    print(f"[結算] {settle_note}", flush=True)

    # ---- B 池（夜間掃描）----
    merge = rd.merge_b_candidates(pools, bases)
    print(f"[B 池] 夜間新增 {merge['added']} 檔、略過 {merge['skipped']} 檔（已在池中或同一訊號日已移出）", flush=True)

    # ---- 候選池 → 只替候選股抓 1 分 K，預先算好分時量矩陣 ----
    a1_codes = [c for c, b in bases.items() if b["liquid"] and b["yest_close"] <= b["prev_ma_max"]]
    candidate_codes = set(a1_codes) | set(pools["a"]) | set(pools["b"])
    matrix_codes = sorted(c for c in candidate_codes if c in bases and bases[c]["liquid"])
    print(f"[候選池] A1 候選 {len(a1_codes)} 檔、A2 追蹤池 {len(pools['a'])} 檔、B 池 {len(pools['b'])} 檔 → 分時量矩陣 {len(matrix_codes)} 檔", flush=True)
    matrix, matrix_days, matrix_note = rd.build_volume_matrix(matrix_codes, asof, feed)
    if matrix is None:
        print("[分時量矩陣] 失敗，未更新 initial_pools.json（保留前一版）", flush=True)
        return 1

    # ---- 官方法人（放最後：1 分 K 跑完時官方資料比較可能已出齊）----
    day_list = [date.fromisoformat(d) for d in rd.trading_days(feed, asof)[-rd.INST_DAYS:]]
    inst_by_day, notes = rd.fetch_inst_days(day_list)
    rd.attach_inst(bases, inst_by_day, day_list)
    t = inst_by_day.get(asof_str) or {}
    inst_meta = {"t_date": asof_str, "tse_ok": t.get("tse") is not None, "otc_ok": t.get("otc") is not None, "notes": notes[:6], "fubon_filled": 0,
                 "days": [d.isoformat() for d in day_list], "note": "外資＋投信買賣超，只當參考，不是篩選條件"}
    missing = sorted(c for c in pools["b"] if c in bases and bases[c].get("inst_t") is None)
    if missing and rd.INST_FALLBACK_FUBON:
        print(f"[法人] 官方沒拿到 {len(missing)} 檔 B 池標的的 T 日資料，改用富邦逐檔補", flush=True)
        inst_meta["fubon_filled"] = rd.fubon_inst_fill(feed, bases, missing, asof)
    for code, e in pools["b"].items():                      # 追蹤清單裡的 B 標的也更新法人參考
        b = bases.get(code)
        if b and b.get("inst5"):
            e["inst5"], e["inst_t"] = b["inst5"], b.get("inst_t")

    index_ctx = rd.index_regime_snapshot(feed, asof)
    if not index_ctx:
        print("[指數] 沒有取得任何指數基準，隔天 radar 會自己補抓（盤前）", flush=True)

    init = rd.assemble_initial_pools(bases, pools, asof_str, index_ctx, inst_meta, matrix, matrix_days, matrix_note,
                                     notes=[settle_note, pool_note])
    rd.save_initial_pools(init)                             # 先存 initial_pools.json（原子寫入），再存追蹤清單
    rd.save_pools(pools)

    meta, c = init["metadata"], init["metadata"]["counts"]
    n_a2 = sum(1 for e in init["A2_tracking_pool"].values() if rd.A2_MIN_DAYS <= ((e.get("a2") or {}).get("n") or 0) <= rd.A2_MAX_DAYS)
    nw = sum(1 for e in init["B_candidates"].values() if e.get("status") == "Watching")
    lines = [f"📦 <b>【夜間運算 v69】</b> initial_pools.json 已更新（基準日 {asof_str}，下一個交易日 {meta['trade_date']}）",
             f"全市場 {market_total:,} 檔，達標（5 日均量 ≥ {rd.MIN_VOL5_LOTS:,} 張）{len(liquid):,} 檔",
             f"🟢 A1 候選池 {c['a1']} 檔（昨收 ≤ 昨日最高均線）",
             f"🟢 A2 追蹤池 {c['a2']} 檔（A1 收盤確認 {rd.A_TRACK_DAYS} 日內），其中拉回整理 {rd.A2_MIN_DAYS}～{rd.A2_MAX_DAYS} 天 {n_a2} 檔",
             f"🔵 B 池 {c['b']} 檔（Watching {nw}｜今晚新增 {merge['added']}）",
             f"📊 分時量矩陣 {c['matrix']} 檔（{matrix_note}）",
             f"法人 T 日：上市 {'✅' if inst_meta['tse_ok'] else '❌'} 上櫃 {'✅' if inst_meta['otc_ok'] else '❌'}"
             + (f"｜富邦補 {inst_meta['fubon_filled']} 檔" if inst_meta["fubon_filled"] else "") + "（僅供參考）"]
    for n in notes[:3]:
        lines.append(f"　　{n}")
    if index_ctx:
        lines.append("🌡️ 趨勢基準：" + "｜".join(f"{v['name']} 月線穿越價 {v['p']:,.0f}（昨收 {v['last_close']:,.0f}，{v['init_state']}）" for v in index_ctx.values()))
    else:
        lines.append("⚠️ 沒有取得指數基準，明天盤前 radar 會自己補抓")
    lines.append(f"🔧 {settle_note}")
    lines.append("明天 radar 啟動後直接讀 initial_pools.json，盤中不會再抓歷史資料。")
    rd.send_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
