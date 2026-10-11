"""同期量基準 baseline.py：每個交易日盤後執行，一次產生明天 09:00 雷達要用的兩個檔案。

產出：
  volume_baseline.json  同期量基準（B 條件用）
      1. 用官方資料挑出「5 日均量 ≥ 1,000 張」的股票，並算好昨收、昨高、MA、均量。
      2. 用富邦歷史行情的「1 分 K」抓每檔最近 5 個交易日的每分鐘成交量（張），算成「開盤後第 m 分鐘開始前的累積量」。
      3. 每檔存兩條曲線（取樣點見 radar.MARKS，點與點之間線性內插）：
           avg   前 5 日同時間累積量的平均（每個時間點排除最大的一天）
           yest  基準日（昨日）同時間累積量
      4. 與富邦快照的全日成交量對照一次，確認「分 K 加總 = 快照 tradeVolume」，結果寫進 Telegram。
  stock_bases.json      歷史基底快取（昨收、昨高、MA、均量、族群）
      雷達 09:00 啟動時直接讀它，省下 2～3 分鐘向官方抓資料的時間。兩個檔案一起成功才會寫 stock_bases.json，
      所以雷達讀到的快取一定和基準檔是同一個基準日。

實際的計算邏輯放在 radar.py 的 build_baseline()（雷達開盤時若發現基準檔缺漏或過期，也會呼叫同一個函式自動補算）。
時間：富邦歷史資料盤後 16:30 前更新；官方資料約 15:00 後齊全，建議 17:00 以後執行。
      富邦歷史行情限速 60 次/分，每檔 1 次，約 600～900 檔需要 14～20 分鐘。
環境變數：TELEGRAM_TOKEN、TELEGRAM_CHAT_ID、FUBON_ID、FUBON_API_KEY、FUBON_CERT_PASS
"""
import sys
import asyncio

import radar as rd

# 供測試與舊程式沿用的名稱（實作在 radar.py）
candle_minute = rd.candle_minute
day_curves = rd.day_curves
build_entry = rd.build_entry
trading_days = rd.trading_days
check_against_snapshot = rd.check_against_snapshot


def main() -> int:
    print("=" * 55, flush=True)
    print("同期量基準 baseline.py（富邦 1 分 K → volume_baseline.json / stock_bases.json）", flush=True)
    print("=" * 55, flush=True)

    all_bases = rd.load_all_stock_bases(include_today=None)
    if not all_bases:
        rd.send_telegram("❌ <b>【同期量基準】</b> 無法建立歷史基底，未更新 volume_baseline.json")
        return 1

    # 只留「5 日均量達標」且「歷史資料完整」的股票（雷達只會用到這些）
    bases = {c: b for c, b in all_bases.items() if b["liquid"] and b["market"] not in rd.UNRELIABLE_MARKETS}
    if not bases:
        rd.send_telegram("❌ <b>【同期量基準】</b> 沒有可用的股票（歷史資料不完整或均量不足），未更新檔案")
        return 1

    # 補產業鏈標籤（只查這幾百檔，結果存進 stock_bases.json，雷達開盤就不用再查）
    try:
        chain_map = asyncio.run(rd.refresh_ic_chains(sorted(bases)))
    except Exception as exc:
        print(f"[產業鏈] 查詢流程例外，改用官方產業別: {type(exc).__name__}: {exc!r}", flush=True)
        chain_map = {}
    rd.apply_ic_chains(bases, chain_map)

    asof = max(b["asof_date"] for b in bases.values())
    today = rd.now_tw()
    if rd._is_weekday(today) and today.hour >= 15 and asof < today.date():
        rd.send_telegram(f"ℹ️ <b>【同期量基準】</b> 官方資料基準日是 {asof}，不是今天（{today.date()}）。"
                         f"若今天是交易日，代表官方資料還沒公布，請稍後重跑；若今天休市可忽略。")

    feed = rd.FubonFeed()
    try:
        feed.login()
    except Exception as exc:
        rd.send_telegram(f"❌ <b>【同期量基準】</b> 富邦登入失敗：{type(exc).__name__}: {exc}")
        return 1

    if not rd.build_baseline(bases, feed, source="盤後計算"):
        return 1
    rd.save_bases_cache(bases)
    rd.send_telegram(f"📦 <b>【同期量基準】</b> 歷史基底快取已更新（基準日 {asof}，{len(bases)} 檔）。"
                     f"明天 09:00 雷達啟動後可直接使用，不必再向官方預載。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
