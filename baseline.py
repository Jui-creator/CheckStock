"""夜間運算 baseline.py（v68）：每個交易日盤後執行，一次產生明天 radar.py 要用的三個檔案。價格與成交量全部來自富邦 Neo API，法人用官方資料。

建議 18:00 之後執行（富邦歷史資料 16:30 後更新；官方法人 18:00 起較齊；耗時主要在 1 分 K，限速 60 次/分）。
流程：
  1. 富邦：全市場日 K（原始價格）→ MA5/10/20、5 日均量、KD；B 型態候選；A1 追蹤標的的 A2 整理期標記（讀 signal_history.json）
  2. 富邦：1 分 K → 分時累積佔比 R（近 20 日中位數，與全市場曲線 70:30 混合）→ volume_baseline.json
  3. 官方（證交所 T86 ＋ 櫃買）：外資＋投信買賣超，近 5 個交易日；T 日沒出來會重試（INST_RETRIES × INST_RETRY_WAIT 秒）；仍沒有 → 富邦補 B 候選
  4. B 候選：KD<40 黃金交叉 ＋ 爆量 ≥ 前 20 日均量 2 倍 ＋ 型態 ＋ 法人 T 日合計 ≥ 0 → initial_pools.json
  5. stock_bases.json（歷史基底，含法人近 5 日）
三個檔案一起成功才會寫 stock_bases.json，所以雷達讀到的快取一定和 R 矩陣、候選是同一個基準日。

Telegram 完成訊息的數字寫法：「全市場 N 檔，達標 X 檔，涵蓋 Y 檔」
環境變數：TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS
"""
import re
import sys
import asyncio
from datetime import date

import radar as rd

# 供測試與舊程式沿用的名稱（實作在 radar.py）
candle_minute = rd.candle_minute
day_curves = rd.day_curves
build_entry = rd.build_entry
trading_days = rd.trading_days
check_against_snapshot = rd.check_against_snapshot

_COVER_RE = re.compile(r"涵蓋 (\d+)/(\d+) 檔")


def format_coverage(message: str, market_total: int) -> str:
    """把 radar.build_baseline 完成訊息裡的「涵蓋 Y/X 檔」改寫成「全市場 N 檔，達標 X 檔，涵蓋 Y 檔」。找不到這段格式就原樣回傳。"""
    def repl(m):
        covered, qualified = m.group(1), m.group(2)
        prefix = f"全市場 {market_total:,} 檔，" if market_total else ""
        return f"{prefix}達標 {int(qualified):,} 檔，涵蓋 {int(covered):,} 檔"
    return _COVER_RE.sub(repl, message, count=1)


def install_message_formatter(market_total: int) -> None:
    original = rd.send_telegram

    def patched(message, *args, **kwargs):
        return original(format_coverage(message, market_total), *args, **kwargs)

    rd.send_telegram = patched


def main() -> int:
    print("=" * 55, flush=True)
    print("夜間運算 baseline.py（富邦日 K / 1 分 K ＋ 官方法人 → stock_bases.json / volume_baseline.json / initial_pools.json）", flush=True)
    print("=" * 55, flush=True)

    feed = rd.FubonFeed()
    try:
        feed.login()
    except Exception as exc:
        rd.send_telegram(f"❌ <b>【夜間運算】</b> 富邦登入失敗：{type(exc).__name__}: {exc}")
        return 1

    pools, _ = rd.load_pools()
    tracked = rd.tracked_from_pools(pools)                       # A1 通知過、追蹤中的標的 → 標記 A2 整理期
    all_bases = rd.load_all_stock_bases(feed, tracked)
    if not all_bases:
        rd.send_telegram("❌ <b>【夜間運算】</b> 無法建立歷史基底（富邦日 K 抓取失敗或成功率過低），未更新檔案。富邦歷史資料盤後 16:30 才更新，請稍後重跑。")
        return 1
    market_total = rd.UNIVERSE_INFO.get("market") or len(all_bases)
    bases = {c: b for c, b in all_bases.items() if (b["liquid"] or c in tracked) and b["market"] not in rd.UNRELIABLE_MARKETS}
    liquid = {c: b for c, b in bases.items() if b["liquid"]}
    if not liquid:
        rd.send_telegram("❌ <b>【夜間運算】</b> 沒有可用的股票（日 K 資料不完整或均量不足），未更新檔案")
        return 1
    print(f"全市場 {market_total:,} 檔，達標（5 日均量 ≥ {rd.MIN_VOL5_LOTS:,} 張）{len(liquid):,} 檔", flush=True)
    install_message_formatter(market_total)

    try:
        chain_map = asyncio.run(rd.refresh_ic_chains(sorted(liquid)))
    except Exception as exc:
        print(f"[產業鏈] 查詢流程例外，改用富邦產業別: {type(exc).__name__}: {exc!r}", flush=True)
        chain_map = {}
    rd.apply_ic_chains(bases, chain_map)

    asof = max(b["asof_date"] for b in bases.values())
    today = rd.now_tw()
    if rd._is_weekday(today) and today.hour >= 17 and asof < today.date():
        rd.send_telegram(f"ℹ️ <b>【夜間運算】</b> 富邦日 K 的基準日是 {asof}，不是今天（{today.date()}）。"
                         f"若今天是交易日，代表富邦歷史資料還沒更新（盤後 16:30），請稍後重跑；若今天休市可忽略。")

    # ---- R 矩陣（1 分 K，最耗時）----
    if not rd.build_baseline(liquid, feed, source="盤後計算"):
        return 1

    # ---- 法人（官方）：放在最後，1 分 K 跑完時官方資料比較可能已出齊 ----
    day_list = [date.fromisoformat(d) for d in rd.trading_days(feed, asof)[-rd.INST_DAYS:]]
    inst_meta = {"t_date": asof.isoformat(), "tse_ok": False, "otc_ok": False, "notes": [], "fubon_filled": 0}
    if rd.INST_FILTER:
        inst_by_day, notes = rd.fetch_inst_days(day_list)
        rd.attach_inst(liquid, inst_by_day, day_list)
        t = inst_by_day.get(asof.isoformat()) or {}
        inst_meta.update(tse_ok=t.get("tse") is not None, otc_ok=t.get("otc") is not None, notes=notes[:6])
        missing = sorted(c for c, b in liquid.items() if b.get("b_hit") and b.get("inst_t") is None)
        if missing and rd.INST_FALLBACK_FUBON:
            print(f"[法人] 官方沒拿到 {len(missing)} 檔 B 候選的 T 日資料，改用富邦逐檔補", flush=True)
            inst_meta["fubon_filled"] = rd.fubon_inst_fill(feed, liquid, missing, asof)
    init = rd.build_initial_pools(liquid, asof.isoformat(), inst_meta)
    unchecked = sum(1 for e in init["b"].values() if not e["inst"]["checked"])

    rd.save_bases_cache(bases)
    rd.save_initial_pools(init)
    n_a2 = sum(1 for b in bases.values() if (b.get("a2") or {}).get("n", 0) >= rd.A2_MIN_DAYS)
    lines = [f"📦 <b>【夜間運算】</b> 歷史基底、R 矩陣、B 候選已更新（基準日 {asof}）",
             f"全市場 {market_total:,} 檔，達標 {len(liquid):,} 檔",
             f"🔵 B 夜間候選 {len(init['b'])} 檔（法人淘汰 {len(init['b_rejected'])} 檔" + (f"｜⚠️ 法人未過濾 {unchecked} 檔" if unchecked else "") + "）",
             f"🟡 A1 追蹤 {len(tracked)} 檔，其中整理 {rd.A2_MIN_DAYS}～{rd.A2_MAX_DAYS} 天的 A2 候選 {n_a2} 檔"]
    if rd.INST_FILTER:
        lines.append(f"法人 T 日：上市 {'✅' if inst_meta['tse_ok'] else '❌'} 上櫃 {'✅' if inst_meta['otc_ok'] else '❌'}"
                     + (f"｜富邦補 {inst_meta['fubon_filled']} 檔" if inst_meta['fubon_filled'] else ""))
        for n in inst_meta["notes"][:3]:
            lines.append(f"　　{n}")
        if not (inst_meta["tse_ok"] and inst_meta["otc_ok"]):
            lines.append("⚠️ 官方法人資料不完整：明天盤前 radar 會再補查一次；若仍沒有，B 候選會標「法人未過濾」")
    lines.append("明天 radar 啟動後可直接使用，不必再向富邦預載。")
    rd.send_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
