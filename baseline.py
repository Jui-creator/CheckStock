"""夜間運算 baseline.py（v69.10）：每個交易日盤後執行，產出明天 radar.py 盤中唯一使用的靜態資料 initial_pools.json，並更新追蹤清單 signal_history.json。
價格與成交量全部來自富邦 Neo API；法人用官方資料（外資＋投信，只當參考）。建議 18:00 之後由 Google Apps Script 呼叫 baseline.yml（workflow_dispatch）觸發。
流程：
  0. 重複觸發保護：今天已經成功產出同一基準日的 initial_pools.json → 直接結束（要強制重跑：workflow 手動執行時勾選 force，或環境變數 FORCE=1）
  1. 富邦：【全市場】日 K（原始價格）→ MA5/10/20、5 日 / 20 日均量、KD(9,3,3)；追蹤清單內的股票即使沒達標也會抓
     v69.2：B 池要全市場確認，不再用「快照成交量預篩」縮小範圍（約 1,900 檔，約 37 分鐘）；1,000 張流動性只限制 A1 候選池
  2. 核對 / 補結算：radar 13:35 已結算 → 用官方日 K 核對當日 A1 收盤確認（close_provisional → official_reconciled）；
                    radar 當天沒結算成功 → 這裡用官方日 K 補結算（A1 收盤確認 / A2 移出 / B 等待日數與到期）
  3. B 池：全市場掃描 KD 低檔金叉＋爆量＋收盤位置，通過者併入 B 池（等待最多 5 個交易日）；不篩 5 日均量
  4. 候選池：A1 候選池（5 日均量 >= 1,000 張 ＋ 昨收 <= 昨日最高均線）、B 候選池（全市場）
     A1 追蹤 / A2 池（v69.9）：A1 收盤確認後先在「A1 追蹤」；用日 K 重算連續拉回整理天數（收盤 < MA5、守住 MA10 與突破日低點），
     1～6 天 → 進「A2 池」（盤中重新站回才觸發 A2）；沒拉回 → 仍在 A1 追蹤（不會觸發 A2）；拉回連續超過 6 天 → 直接移出（不回 A1）。
     A1 追蹤滿 10 個交易日且不在拉回 → 移出（13:35 結算處理）；已在 A2 池的不受 10 天限制
  5. 分時量矩陣：只替候選股（A1 候選 ＋ A1 追蹤 ＋ A2 池 ＋ B 池）抓 1 分 K，預先算好「開盤後每分鐘的歷史同期累積佔比」→ volume_time_matrix
     低量 B 候選（5 日均量 < 1,000 張）分 K 完整日門檻放寬，且不參與全市場曲線；仍算不出矩陣的會在 Telegram 列出（盤中不會被 B 確認）
  6. 官方法人（證交所 T86 ＋ 櫃買）近 5 日；只記錄、不篩選。櫃買表頭沒有法人名稱 → 依固定位置解析並用「三大法人合計」驗算
  7. 加權、櫃買指數最近 19 個完整交易日收盤（存 closes19 / sum19，隔天盤中每 15 秒明確算即時 MA20）；指數代號用名稱精確比對（radar.py v69.2 起）
     v69.3：log 會印出前 19 日收盤明細、合計、穿越價 P，以及「看盤軟體基準日當天 MA20」供對帳（比 19 日窗口多含一天）
  8. 原子寫入 initial_pools.json（暫存檔 → fsync → os.replace），再存 signal_history.json。任何一步資料不完整 → 不覆寫前一版 initial_pools.json
  9. Telegram（v69.10）：只發一則【夜間運算】= 各池檔數＋兩個指數今收與趨勢，接著逐檔列出 A1 追蹤 / A2 池 / B 池（每區塊最後一行是移出條件）。
     只有異常才多一個「⚠️ 注意」區塊：富邦日 K 補結算、官方核對後移出、沒有指數基準、法人 T 日資料沒取得、A2 池標的沒有分時量矩陣。
     舊的第 2 則【A2 池 / B 池明細】（明天觸發 / 確認需要的價位與量能）保留在程式裡，SEND_POOL_DETAIL_MESSAGE = True 才會另外發。
需要同資料夾的 radar.py（v69.10：夜間／盤中共用函式都在那裡；本檔只用到 radar.py 已有的函式與常數）。
環境變數：TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS、FORCE（可選）
"""
import os
import sys
import time
import asyncio
from datetime import date

import radar as rd


# =========================
# 法人解析修正（放在 baseline.py，不需要改 radar.py；對 radar.py 已有修正的版本也無害）
# =========================
_TPEX_GROUPS = 7                      # 櫃買：外資(不含外資自營商)、外資自營商、外資合計、投信、自營商自行買賣、自營商避險、自營商合計


def _parse_tpex_positional(fields, rows):
    """櫃買三大法人日報：代號、名稱 + 7 組（買進股數/賣出股數/買賣超股數）+ 三大法人買賣超股數合計 = 24 欄，表頭沒有法人名稱 → 依固定位置解析。
    組別：0 外資及陸資(不含外資自營商)、1 外資自營商、2 外資合計、3 投信、4 自營商(自行買賣)、5 自營商(避險)、6 自營商合計；組 g 的買賣超在第 4+3g 欄。
    驗算：最後一欄（三大法人合計）= 組 2 買賣超 + 組 3 買賣超 + 組 6 買賣超；至少 95% 的列吻合才採用，否則回傳 None（寧可不用，也不寫錯的數字）。
    外資取組 0（不含外資自營商），與證交所 T86 的口徑一致；投信取組 3。"""
    if len(fields) != 2 + 3 * _TPEX_GROUPS + 1:
        return None
    stripped = [rd._strip_paren(f) for f in fields]
    if "代號" not in stripped[0] or "合計" not in stripped[-1]:
        return None
    for g in range(_TPEX_GROUPS):
        if stripped[2 + 3 * g:5 + 3 * g] != ["買進股數", "賣出股數", "買賣超股數"]:
            return None
    out, ok, total = {}, 0, 0
    for row in rows:
        try:
            code = str(row[0]).strip()
            if not rd.STOCK_CODE_RE.fullmatch(code) or len(row) < len(fields):
                continue
            total += 1
            if abs(rd._num(row[23]) - (rd._num(row[10]) + rd._num(row[13]) + rd._num(row[22]))) <= 1:
                ok += 1
            out[code] = (rd._num(row[4]), rd._num(row[13]))
        except (IndexError, TypeError):
            continue
    if total == 0 or ok / total < 0.95:
        print(f"[法人] 櫃買位置解析的合計驗算不過（{ok}/{total} 吻合），不採用", flush=True)
        return None
    return out, f"外資 = 第1組買賣超（不含外資自營商）｜投信 = 第4組買賣超（位置解析，合計驗算 {ok}/{total} 吻合）"


_orig_parse_inst_rows = rd.parse_inst_rows


def _parse_inst_rows_v2(fields, rows):
    """先用 radar.py 原本「依欄名找」的解析（證交所適用）；找不到欄位時，改試櫃買的固定位置解析。"""
    res = _orig_parse_inst_rows(fields, rows)
    if res[0] is not None:
        return res
    pos = _parse_tpex_positional(fields, rows)
    if pos is not None:
        return pos
    return res


def _fetch_inst_days_v2(days, retries=None, wait=None):
    """同 radar.fetch_inst_days，差別：格式問題（欄位無法辨識 / 沒有資料表）不是「官方還沒公布」，立刻放棄，不再白等重試。
    只有「尚無資料 / 連線失敗 / 回應日期不對」才會重試（最新一天最多 retries 次、每次等 wait 秒）。"""
    retries = rd.INST_RETRIES if retries is None else retries
    wait = rd.INST_RETRY_WAIT if wait is None else wait
    out, notes = {}, []
    for idx, day in enumerate(days):
        latest = idx == len(days) - 1
        res = {}
        for mk in ("tse", "otc"):
            tbl, note = None, ""
            tries = retries if latest else 1
            for attempt in range(1, tries + 1):
                tbl, note = rd.fetch_inst_market(mk, day)
                if tbl is not None:
                    break
                if note.startswith("欄位無法辨識") or note.startswith("回應沒有資料表"):
                    break
                if attempt < tries:
                    print(f"[法人] {mk} {day} 尚未取得（{note}），{wait:.0f} 秒後重試（{attempt}/{tries}）", flush=True)
                    time.sleep(wait)
            res[mk] = tbl
            if tbl is None:
                notes.append(f"{'上市' if mk == 'tse' else '上櫃'} {day}：{note}")
        out[day.isoformat()] = res
    return out, notes


rd.parse_inst_rows = _parse_inst_rows_v2
rd.fetch_inst_days = _fetch_inst_days_v2


# =========================
# 第 2 則 Telegram：A2 池 / B 池明細（v69.5）
# =========================
SEND_POOL_DETAIL_MESSAGE = False         # True = 夜間運算之後，另外發第 2 則【A2 池 / B 池明細】（明天觸發 / 確認需要的價位與量能）
POOL_DETAIL_MAX = 25                  # 每個池最多列幾檔，其餘用「…另有 N 檔」帶過（完整名單在 initial_pools.json）
_MKT_LABEL = {"tse": "上市", "otc": "上櫃"}


def _regime_note(index_ctx) -> list:
    """明天起始趨勢提醒：BEAR 時 A2 禁止，要等指數站回即時 MA20 並維持 5 分鐘才會恢復。"""
    if not index_ctx:
        return ["ℹ️ 沒有取得指數基準，明天起始趨勢由盤前 radar 判定"]
    bear = [v.get("name", mk) for mk, v in index_ctx.items() if v.get("init_state") == "BEAR"]
    if not bear:
        return ["🌡️ 明天起始趨勢：加權／櫃買皆在月線上方（NORMAL），A2 開放"]
    return [f"🌡️ 明天起始趨勢：{'、'.join(bear)} 在月線下方（BEAR）。BEAR 時 A2 禁止、A1 量能門檻提高為 {rd.A1_VOL_RATIO_BEAR:g} 倍、B 照常；"
            f"要等指數站回即時 MA20 並連續維持 {rd.REGIME_EXIT_HOLD_SEC // 60} 分鐘才恢復 A2（實際起始狀態由明早盤前讀數決定）"]


def build_pool_detail_message(init: dict, bases: dict, pools: dict, asof_str: str, index_ctx) -> str:
    """【A2 池 / B 池明細】。資料來自剛寫好的 initial_pools.json（init）：A2_tracking_pool、B_candidates、volume_time_matrix；
    均線、昨收、5 日均量等基底用 bases（取不到再退回 init['stock_bases']）。
    「站上即時 MA5」的條件：即時 MA5 = (前 4 日收盤合計 + 現價) / 5，所以現價 > 即時 MA5 ⇔ 現價 > 前 4 日收盤平均（ma5_cut）。"""
    meta = init["metadata"]
    sb = init.get("stock_bases") or {}
    matrix = init.get("volume_time_matrix") or {}

    def base_of(code):
        return bases.get(code) or sb.get(code) or {}

    lines = [f"📋 <b>【A2 池 / B 池明細】</b>基準日 {asof_str}｜下一個交易日 {meta.get('trade_date')}"]
    lines += _regime_note(index_ctx)

    # ---------- A2 池 ----------
    a2 = init.get("A2_tracking_pool") or {}
    lines += ["", f"🟡 <b>【A2 池】{len(a2)} 檔</b>（A1 收盤確認後，某天收盤 < MA5、守住 MA10 與突破日低點，連續 {rd.A2_MIN_DAYS}～{rd.A2_MAX_DAYS} 天；"
                  f"明天盤中重新站回才觸發 A2）"]
    if not a2:
        lines.append("目前沒有標的。")
    items = sorted(a2.items(), key=lambda kv: (-int(kv[1].get("pullback_days") or 0), kv[0]))
    for code, e in items[:POOL_DETAIL_MAX]:
        b = base_of(code)
        n = int(e.get("pullback_days") or (e.get("a2") or {}).get("n") or rd.entry_pullback_days(e) or 0)
        avg_vol = (e.get("a2") or {}).get("avg_vol")
        need = f"現價 > {rd._p2(b.get('ma5_cut'))}"
        if rd.A2_REQUIRE_ALL_MAS:
            need += f"、> {rd._p2(b.get('ma10_cut'))}（MA10）、> {rd._p2(b.get('ma20_cut'))}（MA20）"
        warn = "" if code in matrix else "｜⚠️ 沒有分時量矩陣，盤中無法觸發"
        vol_txt = (f"預估全日量 ≥ {avg_vol * rd.A2_VOL_RATIO:,.0f} 張（整理期均量 {avg_vol:,.0f} × {rd.A2_VOL_RATIO:g}）"
                   if avg_vol else "整理期均量不明")
        lines += ["",
                  f"<b>{code} {e.get('name', code)}</b>（{_MKT_LABEL.get(e.get('market'), '')}）｜📉 拉回第 {n} 天｜"
                  f"A1 {rd._md(e.get('entry_date'))} @ {rd._p2(e.get('entry_price'))}{warn}",
                  f"   昨收 {rd._p2(b.get('yest_close'))}｜MA5 {rd._p2(b.get('true_ma5'))}｜MA10 {rd._p2(b.get('true_ma10'))}｜"
                  f"突破日低點 {rd._p2(e.get('breakout_low'))}",
                  f"   明天觸發需：今日最低 ≥ 突破日低點；{need}（即時 MA5）＋ 現價 > VWAP；{vol_txt}；預估成交額 ≥ {rd.A_MIN_EST_TURNOVER / 1e8:g} 億"]
    if len(items) > POOL_DETAIL_MAX:
        lines += ["", f"…另有 {len(items) - POOL_DETAIL_MAX} 檔（完整名單見 initial_pools.json 的 A2_tracking_pool）"]

    # ---------- B 池 ----------
    bc = init.get("B_candidates") or {}
    n_watch = sum(1 for e in bc.values() if e.get("status") == "Watching")
    lines += ["", f"🔵 <b>【B 池】{len(bc)} 檔</b>（全市場，不篩 5 日均量；Watching {n_watch}｜Active {len(bc) - n_watch}；"
                  f"B 訊號日後最多等 {rd.B_WAIT_DAYS} 個交易日，10:00 起確認）"]
    if not bc:
        lines.append("目前沒有標的。")

    def b_off(e):                                   # 明天是 B 訊號日後第幾個交易日（pools['days'] 已含基準日，所以 +1）
        return rd.days_since(pools, e["burst_date"], inclusive=False) + 1

    b_items = sorted(bc.items(), key=lambda kv: (0 if kv[1].get("status") == "Watching" else 1, -b_off(kv[1]), kv[0]))
    for code, e in b_items[:POOL_DETAIL_MAX]:
        b = base_of(code)
        low = e["burst_low"]
        yc = b.get("yest_close")
        gap = rd._pc((yc / low - 1) * 100.0) if yc else "—"
        avg5 = b.get("avg_5d_vol_simple")
        avg5_txt = f"5 日均量 {avg5:,.0f} 張" if avg5 is not None else "5 日均量 —"
        warn = "" if code in matrix else "｜⚠️ 沒有分時量矩陣，盤中不會被 B 確認"
        kd = f"KD {e['kd_k']:.1f}/{e['kd_d']:.1f}｜" if e.get("kd_k") is not None else ""
        head = f"<b>{code} {e.get('name', code)}</b>（{_MKT_LABEL.get(e.get('market'), '')}）"
        if e.get("status") == "Watching":
            off = b_off(e)
            wait_txt = f"👀 明天第 {off}/{rd.B_WAIT_DAYS} 天" + ("（最後一天）" if off >= rd.B_WAIT_DAYS else "")
        else:
            wait_txt = f"✅ 已發動 {rd._md(e.get('entry_date'))} @ {rd._p2(e.get('entry_price'))}"
        lines += ["",
                  f"{head}｜{wait_txt}｜B 訊號日 {rd._md(e['burst_date'])} 防線 <code>{rd._p2(low)}</code>｜昨收 {rd._p2(yc)}（距防線 {gap}）{warn}",
                  f"   {kd}爆量 {float(e.get('burst_ratio') or 0):.1f}x｜{avg5_txt}｜{e.get('trigger', '')}"]
        if e.get("status") == "Watching":
            cap = e.get("burst_vol")
            cap_txt = (f"預估全日量 ≤ {cap * rd.B_SHRINK_RATIO:,.0f} 張（B 訊號日 {cap:,.0f} 張 × {rd.B_SHRINK_RATIO:g}）" if cap else "量縮條件不明")
            lines.append(f"   明天確認需：今日最低 ≥ {rd._p2(low)}；現價 > {rd._p2(b.get('ma5_cut'))}（即時 MA5）＋ 現價 > VWAP；{cap_txt}；"
                         f"預估成交額 ≥ {rd.B_MIN_EST_TURNOVER / 1e8:g} 億")
    if len(b_items) > POOL_DETAIL_MAX:
        lines += ["", f"…另有 {len(b_items) - POOL_DETAIL_MAX} 檔（完整名單見 initial_pools.json 的 B_candidates）"]

    lines += ["", "ℹ️ 以上條件的價位只是依昨日收盤推算的門檻；盤中以即時價、VWAP、預估量為準，且需連續 2 輪符合才會通知。"]
    return "\n".join(lines)


# =========================
# 夜間運算 Telegram（v69.10：單一一則）
# =========================
def _night_base_of(code, bases, init):
    return bases.get(code) or (init.get("stock_bases") or {}).get(code) or {}


def _night_a_rows(entries: dict, bases: dict, init: dict, pools: dict) -> list:
    """A1 追蹤 / A2 池 → 盤後總結同款的列（共用 radar._close_a1_lines / _close_a2_lines，文字與盤後一致）。
    剩餘天數 = A_TRACK_DAYS - 納入後已過的交易日（追蹤日曆已含基準日）；排序依昨收距 MA10。"""
    rows = []
    for code, e in entries.items():
        b = _night_base_of(code, bases, init)
        yc, ma10 = b.get("yest_close"), b.get("true_ma10")
        age = rd.days_since(pools, e["entry_date"], inclusive=False)
        rows.append({"code": code, "name": e.get("name", code), "entry_date": e.get("entry_date"),
                     "days_left": max(0, rd.A_TRACK_DAYS - age), "pullback_days": int(e.get("pullback_days") or 0),
                     "gap_ma10": ((yc / ma10 - 1) * 100.0) if (yc and ma10) else 0.0})
    return rows


def _night_b_lines(bc: dict, bases: dict, init: dict, pools: dict) -> list:
    """B 池：一檔一行。「明天第 N/5 天」= B 訊號日後第幾個交易日（追蹤日曆已含基準日，所以 +1）；收 = 基準日收盤。"""
    def off_of(e):
        return rd.days_since(pools, e["burst_date"], inclusive=False) + 1
    out = [f"🔵 <b>B 池：{len(bc)} 檔</b>"]
    items = sorted(bc.items(), key=lambda kv: (0 if kv[1].get("status") == "Watching" else 1, -off_of(kv[1]), kv[0]))
    for code, e in items[:POOL_DETAIL_MAX]:
        b = _night_base_of(code, bases, init)
        low, yc = e["burst_low"], b.get("yest_close")
        if e.get("status") == "Watching":
            status = f"👀 明天第 {off_of(e)}/{rd.B_WAIT_DAYS} 天"
        else:
            status = f"✅ 已發動 {rd._md(e.get('entry_date'))} @ {rd._p2(e.get('entry_price'))}"
        parts = [f" • {code} {e.get('name', code)}", status,
                 f"{rd._md(e['burst_date'])} 最低 {rd._p2(low)}",
                 f"收 {rd._p2(yc)}（距 {rd._pc((yc / low - 1) * 100.0) if yc else '—'}）"]
        if e.get("kd_k") is not None:
            parts.append(f"KD {e['kd_k']:.1f}/{e['kd_d']:.1f}")
        parts.append(f"爆量 {float(e.get('burst_ratio') or 0):.1f}x")
        if e.get("trigger"):
            parts.append(str(e["trigger"]))
        out.append("｜".join(parts))
    if len(items) > POOL_DETAIL_MAX:
        out.append(f" …另有 {len(items) - POOL_DETAIL_MAX} 檔（完整名單見 initial_pools.json 的 B_candidates）")
    out.append(rd.exit_rule_b())
    return out


def build_night_message(init: dict, bases: dict, pools: dict, asof_str: str, index_ctx, *, market_total: int, n_scanned: int,
                        n_liquid: int, n_b_new: int, warns=None) -> str:
    """夜間運算（一則）：摘要（檔數＋指數今收與趨勢）→ [異常才有的 ⚠️ 注意] → A1 追蹤 → A2 池 → B 池。區塊之間空一行（太長時 send_telegram 依空行拆則）。"""
    meta, c = init["metadata"], init["metadata"]["counts"]
    bc = init.get("B_candidates") or {}
    n_b_low = sum(1 for cd in bc if cd in bases and not bases[cd]["liquid"])
    head = [f"📦 <b>【夜間運算】</b>基準日 {asof_str}，下一個交易日 {meta['trade_date']}",
            f"全市場 {market_total:,} 檔，掃描 {n_scanned:,} 檔｜5 日均量 ≥ {rd.MIN_VOL5_LOTS:,} 張：{n_liquid:,} 檔",
            f"🟢 A1 候選池 {c['a1']} 檔（5 日均量 ≥ {rd.MIN_VOL5_LOTS:,} 張＋昨收 ≤ 昨日最高均線）",
            f"🟢 A1 追蹤 {c.get('a1_track', 0)} 檔（A1 收盤確認 {rd.A_TRACK_DAYS} 日內，還沒拉回，不會觸發 A2）",
            f"🟡 A2 池 {c['a2']} 檔（拉回整理 {rd.A2_MIN_DAYS}～{rd.A2_MAX_DAYS} 天，明天盤中重新站回才觸發 A2）",
            f"🔵 B 池 {c['b']} 檔（全市場，不篩 5 日均量；今晚新增 {n_b_new}｜其中 5 日均量 < {rd.MIN_VOL5_LOTS:,} 張 {n_b_low} 檔）"]
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
    sections = [head]
    if warns:
        sections.append(["⚠️ <b>注意</b>"] + [f" • {w}" for w in warns])
    sections.append(rd._close_a1_lines(_night_a_rows(init.get("A1_tracking_pool") or {}, bases, init, pools), hint="initial_pools.json"))
    sections.append(rd._close_a2_lines(_night_a_rows(init.get("A2_tracking_pool") or {}, bases, init, pools), hint="initial_pools.json"))
    sections.append(_night_b_lines(bc, bases, init, pools))
    return "\n\n".join("\n".join(sec) for sec in sections)


# =========================
# 主流程
# =========================
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
    print("夜間運算 baseline.py v69.10（富邦全市場日 K / 候選股 1 分 K ＋ 官方法人 → initial_pools.json ＋ signal_history.json）", flush=True)
    print("=" * 55, flush=True)
    if not rd.B_FULL_MARKET:
        print("⚠️ rd.B_FULL_MARKET = False：夜間日 K 只抓快照成交量預篩後的股票，B 池不是全市場", flush=True)
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
    tracked = rd.tracked_from_pools(pools)                 # A1 追蹤 / A2 池：標記拉回整理
    extra = rd.extra_codes_from_pools(pools)               # 追蹤清單內的代號都要有日 K（收盤結算要用均線）
    all_bases = rd.load_all_stock_bases(feed, tracked, extra)
    if not all_bases:
        rd.send_telegram("❌ <b>【夜間運算】</b> 無法建立歷史基底（富邦日 K 抓取失敗或成功率過低），未更新 initial_pools.json。"
                         "富邦歷史資料盤後 16:30 才更新，請稍後重跑。")
        return 1
    market_total = rd.UNIVERSE_INFO.get("market") or len(all_bases)
    # 留下：A1 達標（5 日均量 >= 1,000 張）、追蹤清單內的、以及今晚 B 型態命中的（B 不篩流動性，全市場）
    bases = {c: b for c, b in all_bases.items()
             if (b["liquid"] or c in extra or b.get("b_hit")) and b["market"] not in rd.UNRELIABLE_MARKETS}
    liquid = {c: b for c, b in bases.items() if b["liquid"]}
    if not liquid:
        rd.send_telegram("❌ <b>【夜間運算】</b> 沒有可用的股票（日 K 資料不完整或均量不足），未更新 initial_pools.json")
        return 1
    n_scanned = len(all_bases)
    n_b_hit = sum(1 for b in all_bases.values() if b.get("b_hit"))
    n_b_hit_low = sum(1 for b in all_bases.values() if b.get("b_hit") and not b["liquid"])
    print(f"全市場 {market_total:,} 檔，抓到日 K 並掃描 {n_scanned:,} 檔｜達標（5 日均量 ≥ {rd.MIN_VOL5_LOTS:,} 張）{len(liquid):,} 檔｜"
          f"今晚 B 型態命中 {n_b_hit} 檔（其中 5 日均量不到 {rd.MIN_VOL5_LOTS:,} 張 {n_b_hit_low} 檔）", flush=True)
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
    warns = []                                              # 夜間 Telegram「⚠️ 注意」：只放異常，正常時整塊不出現
    sbases = rd.settlement_bases(bases)
    if pools.get("asof") == asof_str:
        rec = rd.reconcile_a1_confirm(pools, bases, asof_str)
        settle_note = f"13:35 結算已完成；官方日 K 核對 A1 收盤確認 {rec['checked']} 檔，核對後移出 {rec['removed']} 檔"
        if rec["removed"]:
            warns.append(f"夜間用官方日 K 核對後，移出 {rec['removed']} 檔 A1 收盤確認（官方收盤沒站穩三均線）")
    elif pools.get("asof", "") > asof_str:
        settle_note = f"追蹤清單的結算日（{pools.get('asof')}）比基準日新，略過補結算"
    elif not pools.get("asof"):
        rd.settle_close(pools, sbases, rd.official_closes(bases), asof_str)
        settle_note = f"首次建立追蹤清單（v69），追蹤日曆從基準日 {asof_str} 開始"
    else:
        res = rd.settle_close(pools, sbases, rd.official_closes(bases), asof_str)
        settle_note = (f"⚠️ radar 當日沒有完成 13:35 結算，已用官方日 K 補結算：A1 收盤確認 {len(res['a1_confirmed'])} 檔、未確認 {len(res['a1_failed'])} 檔，"
                       f"A1 追蹤 / A2 池移出 {len(res['a_exits'])} 檔，B 池移出 {len(res['b_exits'])} 檔")
        warns.append(settle_note.replace("⚠️ ", "", 1))
    print(f"[結算] {settle_note}", flush=True)

    # ---- A1 追蹤 / A2 池：用日 K 重算每檔追蹤標的的連續拉回整理天數，決定誰在「A1 追蹤」、誰進「A2 池」----
    stage_cnt = rd.apply_a_stages(pools, bases)
    print(f"[A 追蹤] A1 追蹤（收盤確認後還沒拉回）{stage_cnt['a1']} 檔｜A2 池（拉回整理 {rd.A2_MIN_DAYS}～{rd.A2_MAX_DAYS} 天）{stage_cnt['a2']} 檔｜拉回逾 {rd.A2_MAX_DAYS} 天移出 {stage_cnt.get('removed', 0)} 檔", flush=True)
    for cd, e in sorted(pools["a"].items()):
        print(f"    {cd} {e.get('name', '')}｜{e.get('stage')}｜A1 確認 {e.get('entry_date')}｜連續拉回 {e.get('pullback_days', 0)} 天", flush=True)

    # ---- B 池（夜間全市場掃描）----
    merge = rd.merge_b_candidates(pools, bases)
    print(f"[B 池] 夜間新增 {merge['added']} 檔、略過 {merge['skipped']} 檔（已在池中或同一訊號日已移出）", flush=True)

    # ---- 候選池 → 只替候選股抓 1 分 K，預先算好分時量矩陣（含 B 池全市場的低量股）----
    a1_codes = [c for c, b in bases.items() if b["liquid"] and b["yest_close"] <= b["prev_ma_max"]]
    candidate_codes = set(a1_codes) | set(pools["a"]) | set(pools["b"])
    matrix_codes = sorted(c for c in candidate_codes if c in bases)
    lowliq = {c for c in matrix_codes if not bases[c]["liquid"]}
    print(f"[候選池] A1 候選 {len(a1_codes)} 檔、A1 追蹤＋A2 池 {len(pools['a'])} 檔、B 池 {len(pools['b'])} 檔 → 分時量矩陣 {len(matrix_codes)} 檔"
          f"（其中低量股 {len(lowliq)} 檔，分 K 完整日門檻放寬為 {rd.B_LOWLIQ_MIN_CANDLES} 根）", flush=True)
    matrix, matrix_days, matrix_note = rd.build_volume_matrix(matrix_codes, asof, feed, lowliq)
    if matrix is None:
        print("[分時量矩陣] 失敗，未更新 initial_pools.json（保留前一版）", flush=True)
        return 1

    # ---- 官方法人（放最後：1 分 K 跑完時官方資料比較可能已出齊；只是參考，整段失敗也不影響夜間運算）----
    inst_meta = {"t_date": asof_str, "tse_ok": False, "otc_ok": False, "notes": [], "fubon_filled": 0, "days": [],
                 "note": "外資＋投信買賣超，只當參考，不是篩選條件"}
    notes = []
    try:
        day_list = [date.fromisoformat(d) for d in rd.trading_days(feed, asof)[-rd.INST_DAYS:]]
        inst_by_day, notes = rd.fetch_inst_days(day_list)
        rd.attach_inst(bases, inst_by_day, day_list)
        t = inst_by_day.get(asof_str) or {}
        inst_meta.update(tse_ok=t.get("tse") is not None, otc_ok=t.get("otc") is not None, notes=notes[:6],
                         days=[d.isoformat() for d in day_list])
        missing = sorted(c for c in pools["b"] if c in bases and bases[c].get("inst_t") is None)
        if missing and rd.INST_FALLBACK_FUBON:
            print(f"[法人] 官方沒拿到 {len(missing)} 檔 B 池標的的 T 日資料，改用富邦逐檔補", flush=True)
            inst_meta["fubon_filled"] = rd.fubon_inst_fill(feed, bases, missing, asof)
        for code, e in pools["b"].items():                  # 追蹤清單裡的 B 標的也更新法人參考
            b = bases.get(code)
            if b and b.get("inst5"):
                e["inst5"], e["inst_t"] = b["inst5"], b.get("inst_t")
    except Exception as exc:
        notes = [f"法人整段失敗：{type(exc).__name__}: {exc}"]
        inst_meta["notes"] = notes
        print(f"[法人] 整段失敗（不影響夜間運算，法人參考欄位為空）：{type(exc).__name__}: {exc}", flush=True)

    miss_inst = [lb for lb, ok in (("上市", inst_meta["tse_ok"]), ("上櫃", inst_meta["otc_ok"])) if not ok]
    if miss_inst:
        warns.append(f"法人 T 日資料沒取得：{'、'.join(miss_inst)}（只是參考，不影響選股；A1 通知的法人燈號會顯示 ➖）")
    index_ctx = rd.index_regime_snapshot(feed, asof)
    if not index_ctx:
        print("[指數] 沒有取得任何指數基準，隔天 radar 會自己補抓（盤前）", flush=True)

    init = rd.assemble_initial_pools(bases, pools, asof_str, index_ctx, inst_meta, matrix, matrix_days, matrix_note,
                                     notes=[settle_note, pool_note])
    rd.save_initial_pools(init)                             # 先存 initial_pools.json（原子寫入），再存追蹤清單
    rd.save_pools(pools)

    try:
        text = build_night_message(init, bases, pools, asof_str, index_ctx, market_total=market_total, n_scanned=n_scanned,
                                   n_liquid=len(liquid), n_b_new=merge["added"], warns=warns)
    except Exception as exc:                                # 訊息組裝失敗不能讓夜間運算失敗（檔案已經存好了）
        print(f"[Telegram] 夜間訊息組裝失敗（不影響夜間運算）：{type(exc).__name__}: {exc}", flush=True)
        text = f"📦 <b>【夜間運算】</b>基準日 {asof_str} 已更新（訊息組裝失敗：{type(exc).__name__}，請看 Actions log）"
    rd.send_telegram(text)

    if SEND_POOL_DETAIL_MESSAGE:                            # 選用：第 2 則【A2 池 / B 池明細】（失敗只記 log）
        try:
            rd.send_telegram(build_pool_detail_message(init, bases, pools, asof_str, index_ctx))
        except Exception as exc:
            print(f"[Telegram] A2 / B 明細訊息產生或發送失敗（不影響夜間運算）：{type(exc).__name__}: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
