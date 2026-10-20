"""法人資料 baseline.py（v70.2）：每個交易日傍晚（建議 18:00 之後，由 Google Apps Script 呼叫 baseline.yml）執行。
v70 起 baseline 只做一件事：算「官方外資、投信近 5 日買賣超」→ 寫 institutional.json；隔天 radar 盤前讀它，貼到 A1 通知的法人燈號。
A1 / A2 / B 選池、分時量矩陣、指數基準都已經搬到 radar 收盤後（13:35 結算完接著做），這裡不再碰。
v70.2：A1 追蹤改為無固定期限狀態機；baseline 只算法人，版本同步，不執行 A1/A2 階段邏輯。
為什麼法人要等傍晚：證交所三大法人買賣超日報、櫃買三大法人日報約傍晚才公布；收盤後 13:35 還沒有。
v70.1 修正（某天櫃買 10/13 沒有資料）：
  ① 舊版對「不是最新的那幾天」只試 1 次，遇到一次暫時性斷線（例如 ChunkedEncodingError）就放棄，該日整個市場變成 ➖。
     現在「連線失敗」類錯誤每一天都會重試（非最新日 3 次、每次間隔數秒；最新日沿用原本的等待重試）；「尚無資料」仍然不重試（休市日本來就沒有）。
  ② 重試後仍然缺的日子，改用「昨晚的 institutional.json」補回（同一天、同一檔的數字不會變），補不到才留 ➖。
  ③ Telegram 只在「重試與補回之後仍然缺」才警告，並寫明缺哪個市場、哪一天。
流程：
  0. 重複觸發保護：institutional.json 已經是這個交易日（T 日）而且上市、上櫃都取得、近 5 日也沒有缺 → 直接結束（要強制重跑：workflow 勾 force，或環境變數 FORCE=1）
  1. T 日 = 富邦 2330 日 K 的最後交易日，或 initial_pools.json 的基準日（收盤後選池已經用今天當基準日，但富邦日 K 16:30 才有今天）取較新的
  2. 官方法人（證交所 T86 ＋ 櫃買）近 5 個交易日；櫃買表頭沒有法人名稱 → 依固定位置解析並用「三大法人合計」驗算
  3. 仍缺的日子：用昨晚的 institutional.json 補回；B 池標的再不行才用富邦 institutional_trades 逐檔補
  4. 原子寫入 institutional.json（只含外資、投信，單位：張，最舊→最新），發一則 Telegram
需要同資料夾的 radar.py（v70.2：官方法人抓取與解析函式在那裡）。
環境變數：TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS、FORCE（可選）
"""
import os
import sys
import time
from datetime import date

import radar as rd

CONN_RETRIES = 3          # 「連線失敗」類錯誤，非最新日最多試幾次（最新日用 radar 的 INST_RETRIES）
CONN_RETRY_WAIT = 5       # 連線失敗後的等待秒數（第 n 次等 n × 此值）


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


def _is_conn_error(note: str) -> bool:
    return str(note).startswith("連線失敗")


def _fetch_inst_days_v2(days, retries=None, wait=None):
    """同 radar.fetch_inst_days，差別：
    ① 格式問題（欄位無法辨識 / 沒有資料表）不是「官方還沒公布」，立刻放棄，不再白等重試。
    ② 「連線失敗」（斷線、ChunkedEncodingError…）每一天都重試：最新日最多 retries 次、其他日最多 CONN_RETRIES 次；等待時間很短（第 n 次等 n × CONN_RETRY_WAIT 秒）。
    ③ 「尚無資料」只有最新一天才等（官方傍晚才公布）；其他日的「尚無資料」是真的沒有（例如休市），不重試。
    回傳 ({日期字串: {"tse": 表或 None, "otc": ...}}, [未取得說明])。"""
    retries = rd.INST_RETRIES if retries is None else retries
    wait = rd.INST_RETRY_WAIT if wait is None else wait
    out, notes = {}, []
    for idx, day in enumerate(days):
        latest = idx == len(days) - 1
        res = {}
        for mk in ("tse", "otc"):
            tbl, note = None, ""
            tries = retries if latest else CONN_RETRIES
            for attempt in range(1, tries + 1):
                tbl, note = rd.fetch_inst_market(mk, day)
                if tbl is not None:
                    break
                if note.startswith("欄位無法辨識") or note.startswith("回應沒有資料表"):
                    break
                if not latest and not _is_conn_error(note):
                    break                                                       # 非最新日的「尚無資料 / 日期不符」→ 不重試
                if attempt < tries:
                    pause = CONN_RETRY_WAIT * attempt if _is_conn_error(note) else wait
                    print(f"[法人] {mk} {day} 取得失敗（{note}），{pause:.0f} 秒後重試（{attempt}/{tries}）", flush=True)
                    time.sleep(pause)
            res[mk] = tbl
            if tbl is None:
                notes.append(f"{'上市' if mk == 'tse' else '上櫃'} {day}：{note}")
        out[day.isoformat()] = res
    return out, notes


rd.parse_inst_rows = _parse_inst_rows_v2
rd.fetch_inst_days = _fetch_inst_days_v2


# =========================
# 主流程
# =========================
def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y")


def merge_previous(table: dict, old, day_keys: list) -> int:
    """重試後仍然是 None（= 該市場該日整張表沒抓到）的格子，用昨晚的 institutional.json 補回。
    同一天同一檔的外資、投信買賣超不會變，所以直接補；只補 None，不覆蓋這次新抓到的資料。回傳補回幾格（外資、投信各算一格）。"""
    if not old:
        return 0
    old_days = (old.get("meta") or {}).get("days") or []
    pos = {d: i for i, d in enumerate(old_days)}
    old_codes = old.get("codes") or {}
    n = 0
    for code, rec in table.items():
        orec = old_codes.get(code)
        if not orec:
            continue
        for i, k in enumerate(day_keys):
            j = pos.get(k)
            if j is None or rec["f"][i] is not None:
                continue
            if j < len(orec["f"]) and orec["f"][j] is not None and orec["t"][j] is not None:
                rec["f"][i], rec["t"][i] = orec["f"][j], orec["t"][j]
                n += 2
    return n


def still_missing(table: dict, inst_by_day: dict, day_keys: list) -> list:
    """補回之後仍然沒有資料的 (市場名, 日期)：官方那天沒抓到表，而且該市場沒有任何一檔被補回。"""
    out = []
    for i, k in enumerate(day_keys):
        for mk, label in (("tse", "上市"), ("otc", "上櫃")):
            if (inst_by_day.get(k) or {}).get(mk) is not None:
                continue
            recs = [r for r in table.values() if r["m"] == mk]
            if recs and all(r["f"][i] is None for r in recs):
                out.append((label, k))
    return out


def fubon_fill(feed, table: dict, targets: dict, day_list: list) -> int:
    """官方沒拿到的日子，改用富邦 ownership.institutional_trades 逐檔補（targets = {代號: 市場}，目前只補 B 池標的）。
    富邦只回傳「有法人進出」的日子，沒有那一筆的日子當 0（呼叫端的既有慣例）。回傳補到幾檔。"""
    keys = [d.isoformat() for d in day_list]
    n = 0
    for code, mk in targets.items():
        rec = table.get(code)
        if rec and all(x is not None for x in rec["f"]):
            continue
        d = feed.institutional(code, day_list[0], day_list[-1])
        if d is None:
            continue
        rec = rec or {"m": mk, "f": [None] * len(keys), "t": [None] * len(keys)}
        for i, k in enumerate(keys):
            if rec["f"][i] is None:
                f, t = d.get(k, (0.0, 0.0))
                rec["f"][i], rec["t"][i] = round(f / 1000.0, 1), round(t / 1000.0, 1)
        table[code] = rec
        n += 1
    return n


def main() -> int:
    print("=" * 55, flush=True)
    print("法人資料 baseline.py v70.2（官方外資、投信近 5 日買賣超 → institutional.json）", flush=True)
    print("=" * 55, flush=True)
    feed = rd.FubonFeed()
    try:
        feed.login()
    except Exception as exc:
        rd.send_telegram(f"❌ <b>【法人資料】</b> 富邦登入失敗：{type(exc).__name__}: {exc}")
        return 1
    today = rd.now_tw().date()
    try:
        days_all = rd.trading_days(feed, today)
    except Exception as exc:
        rd.send_telegram(f"❌ <b>【法人資料】</b> 查詢最近交易日失敗：{type(exc).__name__}: {exc}")
        return 1
    init, _ = rd.load_initial_pools()
    base_date = ((init or {}).get("metadata") or {}).get("baseline_date") or ""
    t_day = max(days_all[-1] if days_all else "", base_date)
    if not t_day:
        rd.send_telegram("❌ <b>【法人資料】</b> 找不到最近的交易日")
        return 1
    days = (days_all + ([t_day] if t_day not in days_all else []))[-rd.INST_DAYS:]
    day_list = [date.fromisoformat(d) for d in days]
    old, _ = rd.load_institutional()
    if not _truthy("FORCE"):
        m = (old or {}).get("meta") or {}
        complete = old and not (m.get("missing") or [])
        if old and m.get("t_date") == t_day and m.get("tse_ok") and m.get("otc_ok") and complete:
            print(f"[重複保護] institutional.json 已經是 {t_day}（上市、上櫃都取得、近 {len(days)} 日沒有缺），直接忽略（要強制重跑請勾選 force 或設 FORCE=1）", flush=True)
            return 0
    print(f"[法人] T 日 {t_day}，取近 {len(days)} 個交易日：{days[0]} ～ {days[-1]}", flush=True)
    notes = []
    try:
        inst_by_day, notes = rd.fetch_inst_days(day_list)
    except Exception as exc:
        rd.send_telegram(f"❌ <b>【法人資料】</b> 官方法人抓取整段失敗：{type(exc).__name__}: {exc}")
        return 1
    t = inst_by_day.get(t_day) or {}
    tse_ok, otc_ok = t.get("tse") is not None, t.get("otc") is not None
    table = rd.build_inst_table(inst_by_day, day_list)
    restored = merge_previous(table, old, days)                      # 重試後仍缺的日子：用昨晚的檔案補回
    if restored:
        print(f"[法人] 用昨晚的 institutional.json 補回 {restored} 格（官方這幾天沒抓到的資料）", flush=True)
    filled = 0
    targets = {cd: (e.get("market") or "tse") for cd, e in ((init or {}).get("B_candidates") or {}).items()}
    gaps = still_missing(table, inst_by_day, days)
    if targets and rd.INST_FALLBACK_FUBON and gaps:
        filled = fubon_fill(feed, table, targets, day_list)
        print(f"[法人] 官方沒拿到的日子，B 池標的改用富邦補 {filled} 檔", flush=True)
    miss_txt = [f"{lb} {k[5:].replace('-', '/')}" for lb, k in gaps]
    obj = {"rule": rd.INST_RULE,
           "meta": {"t_date": t_day, "days": days, "built_at": rd.now_tw().isoformat(timespec="seconds"), "tse_ok": tse_ok, "otc_ok": otc_ok,
                    "fubon_filled": filled, "restored_cells": restored, "missing": miss_txt, "notes": notes[:6],
                    "unit": "張（外資＝不含外資自營商；投信）；最舊→最新；None＝該日沒有資料"},
           "codes": table}
    rd.save_institutional(obj)
    lines = [f"🏦 <b>【法人資料】</b>T 日 {t_day}｜上市 {'✅' if tse_ok else '❌'} 上櫃 {'✅' if otc_ok else '❌'}"
             f"｜外資、投信近 {len(days)} 日（{days[0][5:].replace('-', '/')}～{days[-1][5:].replace('-', '/')}）共 {len(table):,} 檔，明天 radar 盤前直接讀取"]
    if restored:
        lines.append(f"　　官方有幾天暫時抓不到，已用昨晚的資料補回 {restored // 2:,} 筆")
    if not (tse_ok and otc_ok):
        lines.append("⚠️ T 日有市場沒取得：A1 通知的法人燈號會顯示 ➖（只是參考，不影響選股）。官方資料較晚公布時，可再手動跑一次 baseline（勾 force）")
    older = [(lb, k) for lb, k in gaps if k != t_day]
    if older:
        lines.append("⚠️ 重試與補回後仍然缺：" + "、".join(f"{lb} {k[5:].replace('-', '/')}" for lb, k in older)
                     + "。A1 通知對應那一格會顯示 ➖（只是參考，不影響選股）；手動再跑一次 baseline（勾 force）通常就能補上")
    if base_date and base_date != t_day:
        lines.append(f"⚠️ 收盤後選池的基準日是 {base_date}，和法人 T 日 {t_day} 不同：明天 radar 會因為日期不符而不貼法人資料")
    shown = [n for n in notes if any(n.startswith(f"{lb} {k}") for lb, k in gaps)]
    for n in shown[:3]:
        lines.append(f"　　{n}")
    rd.send_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
