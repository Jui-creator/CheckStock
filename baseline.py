"""同期量基準 baseline.py：每個交易日盤後執行，一次產生明天 09:00 雷達要用的兩個檔案。價格與成交量全部來自富邦 Neo API。

產出：
  stock_bases.json      歷史基底快取（雷達 09:00 啟動時直接讀，省下 10～15 分鐘向富邦抓日 K 的時間）
      1. 富邦全市場快照（snapshot.quotes）取得股票清單，預篩後逐檔抓「日 K」（historical.candles，原始價格 adjusted=false）。
      2. 算好昨收、昨高、MA、均量、近 20 日高、20 日均量、KD 起算值；5 日均量 ≥ 1,000 張才算達標。
      3. 產業別來自富邦 intraday.tickers；族群標籤再用櫃買產業價值鏈覆蓋（非價格資料，抓不到就維持富邦產業別）。
  volume_baseline.json  同期量基準（股池 A 的量能條件、股池 B 的停損「帶量」判斷用）
      4. 用富邦歷史行情的「1 分 K」抓每檔最近 5 個交易日的每分鐘成交量（張），算成「開盤後第 m 分鐘開始前的累積量」。
      5. 每檔存兩條曲線（取樣點見 radar.MARKS，點與點之間線性內插）：
           avg   前 5 日同時間累積量的平均（每個時間點排除最大的一天）
           yest  基準日（昨日）同時間累積量
      6. 與富邦快照的全日成交量對照一次，確認「分 K 加總 = 快照 tradeVolume」，結果寫進 Telegram。
  兩個檔案一起成功才會寫 stock_bases.json，所以雷達讀到的快取一定和基準檔是同一個基準日。

Telegram 完成訊息的數字寫法：「全市場 N 檔，達標 X 檔，涵蓋 Y 檔」
  全市場 = 富邦快照的一般股票數（已排除 ETF、權證、債券）
  達標   = 5 日均量 ≥ 1,000 張的股票數（流動性門檻，只有這些會進基準）
  涵蓋   = 達標股票中，有完整 5 天分 K、成功算出同期量曲線的檔數

實際的計算邏輯放在 radar.py（load_all_stock_bases / build_baseline）。
時間：富邦歷史資料盤後 16:30 前更新，建議 17:00 以後執行。
      富邦歷史行情限速 60 次/分：日 K 約 700 檔 ≈ 12～15 分鐘，1 分 K 約 600 檔 ≈ 11～15 分鐘，合計約 30 分鐘。
環境變數：TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS
"""
import re
import sys
import asyncio

import radar as rd

# 供測試與舊程式沿用的名稱（實作在 radar.py）
candle_minute = rd.candle_minute
day_curves = rd.day_curves
build_entry = rd.build_entry
trading_days = rd.trading_days
check_against_snapshot = rd.check_against_snapshot

_COVER_RE = re.compile(r"涵蓋 (\d+)/(\d+) 檔")


def format_coverage(message: str, market_total: int) -> str:
    """把 radar.build_baseline 完成訊息裡的「涵蓋 Y/X 檔」改寫成「全市場 N 檔，達標 X 檔，涵蓋 Y 檔」。
    訊息裡找不到這段格式（例如失敗訊息）就原樣回傳。"""
    def repl(m):
        covered, qualified = m.group(1), m.group(2)
        prefix = f"全市場 {market_total:,} 檔，" if market_total else ""
        return f"{prefix}達標 {int(qualified):,} 檔，涵蓋 {int(covered):,} 檔"
    return _COVER_RE.sub(repl, message, count=1)


def install_message_formatter(market_total: int) -> None:
    """讓 radar.build_baseline 發出的 Telegram 訊息套用新的數字寫法（只改這一段文字，不動其他行為）。"""
    original = rd.send_telegram

    def patched(message, *args, **kwargs):
        return original(format_coverage(message, market_total), *args, **kwargs)

    rd.send_telegram = patched


def main() -> int:
    print("=" * 55, flush=True)
    print("同期量基準 baseline.py（富邦日 K / 1 分 K → stock_bases.json / volume_baseline.json）", flush=True)
    print("=" * 55, flush=True)

    feed = rd.FubonFeed()
    try:
        feed.login()
    except Exception as exc:
        rd.send_telegram(f"❌ <b>【同期量基準】</b> 富邦登入失敗：{type(exc).__name__}: {exc}")
        return 1

    all_bases = rd.load_all_stock_bases(feed)          # 富邦快照 → 預篩 → 日 K → 基底
    if not all_bases:
        rd.send_telegram("❌ <b>【同期量基準】</b> 無法建立歷史基底（富邦日 K 抓取失敗或成功率過低），未更新檔案。"
                         "富邦歷史資料盤後 16:30 才更新，請稍後重跑。")
        return 1
    market_total = rd.UNIVERSE_INFO.get("market") or len(all_bases)

    # 只留「5 日均量達標」且「歷史資料完整」的股票（雷達只會用到這些）
    bases = {c: b for c, b in all_bases.items() if b["liquid"] and b["market"] not in rd.UNRELIABLE_MARKETS}
    if not bases:
        rd.send_telegram("❌ <b>【同期量基準】</b> 沒有可用的股票（日 K 資料不完整或均量不足），未更新檔案")
        return 1
    print(f"全市場 {market_total:,} 檔，達標（5 日均量 ≥ {rd.MIN_VOL5_LOTS:,} 張）{len(bases):,} 檔", flush=True)
    install_message_formatter(market_total)

    # 補產業鏈標籤（只查達標的這幾百檔，結果存進 stock_bases.json，雷達開盤就不用再查）
    try:
        chain_map = asyncio.run(rd.refresh_ic_chains(sorted(bases)))
    except Exception as exc:
        print(f"[產業鏈] 查詢流程例外，改用富邦產業別: {type(exc).__name__}: {exc!r}", flush=True)
        chain_map = {}
    rd.apply_ic_chains(bases, chain_map)

    asof = max(b["asof_date"] for b in bases.values())
    today = rd.now_tw()
    if rd._is_weekday(today) and today.hour >= 17 and asof < today.date():
        rd.send_telegram(f"ℹ️ <b>【同期量基準】</b> 富邦日 K 的基準日是 {asof}，不是今天（{today.date()}）。"
                         f"若今天是交易日，代表富邦歷史資料還沒更新（盤後 16:30），請稍後重跑；若今天休市可忽略。")

    if not rd.build_baseline(bases, feed, source="盤後計算"):
        return 1
    rd.save_bases_cache(bases)
    rd.send_telegram(f"📦 <b>【同期量基準】</b> 歷史基底快取已更新（基準日 {asof}）：全市場 {market_total:,} 檔，達標 {len(bases):,} 檔。"
                     f"含近 {rd.A_HIGH_DAYS} 日高 / KD / {rd.B_AVG_DAYS} 日均量。明天 09:00 雷達啟動後可直接使用，不必再向富邦預載。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
