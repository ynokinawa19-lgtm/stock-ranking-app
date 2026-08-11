"""
株価自動ランキング - フェーズ1: 株価取得 + 簡易ランキング(CSV出力)

実行方法:
    python collector.py

やっていること:
    1. config.yaml からウォッチリスト(監視銘柄)を読み込む
    2. yfinance で各銘柄の株価データを取得する
    3. 「前日比%」と「出来高急増率」からスコアを計算する
    4. 日本株・米国株それぞれのランキングを data/ranking_jp.csv, data/ranking_us.csv に保存する

注意:
    ニュース件数・RSI はフェーズ4で追加予定のため、現時点ではスコア0として扱う。
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf

CONFIG_PATH = Path(__file__).parent / "config.yaml"
DATA_DIR = Path(__file__).parent / "data"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_stock_row(ticker: str, name: str) -> dict | None:
    """1銘柄分の株価データを取得して指標を計算する。取得失敗時はNoneを返す。"""
    try:
        stock = yf.Ticker(ticker)
        # 52週高値/安値と出来高平均を計算するため1年分の日足を取得
        hist = stock.history(period="1y", interval="1d")

        if hist.empty or len(hist) < 2:
            print(f"  [警告] {ticker} ({name}): データが取得できませんでした。スキップします。")
            return None

        latest = hist.iloc[-1]
        prev = hist.iloc[-2]

        current_price = latest["Close"]
        prev_close = prev["Close"]
        change_pct = (current_price - prev_close) / prev_close * 100

        current_volume = latest["Volume"]
        avg_volume_20d = hist["Volume"].tail(20).mean()
        volume_surge_ratio = (
            current_volume / avg_volume_20d if avg_volume_20d > 0 else 0
        )

        high_52w = hist["Close"].max()
        low_52w = hist["Close"].min()

        return {
            "ticker": ticker,
            "name": name,
            "current_price": round(current_price, 2),
            "change_pct": round(change_pct, 2),
            "volume": int(current_volume),
            "avg_volume_20d": int(avg_volume_20d),
            "volume_surge_ratio": round(volume_surge_ratio, 2),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            # ニュース件数・RSIはフェーズ4で追加。それまでは0固定。
            "news_score": 0.0,
            "rsi_score": 0.0,
        }
    except Exception as e:
        print(f"  [警告] {ticker} ({name}): 取得中にエラーが発生しました ({e})。スキップします。")
        return None


def normalize(series: pd.Series) -> pd.Series:
    """0〜100の範囲にスケーリングする(全て同じ値の場合は50を返す)。"""
    min_v, max_v = series.min(), series.max()
    if max_v == min_v:
        return pd.Series([50.0] * len(series), index=series.index)
    return (series - min_v) / (max_v - min_v) * 100


def build_ranking(rows: list[dict], weights: dict) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["price_change_score"] = normalize(df["change_pct"])
    df["volume_surge_score"] = normalize(df["volume_surge_ratio"])

    df["total_score"] = (
        df["price_change_score"] * weights["price_change"]
        + df["volume_surge_score"] * weights["volume_surge"]
        + df["news_score"] * weights["news_surge"]
        + df["rsi_score"] * weights["rsi"]
    ).round(2)

    df = df.sort_values("total_score", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    return df


def collect_market(market_key: str, market_label: str, config: dict) -> pd.DataFrame:
    tickers = config["watchlist"].get(market_key, [])
    print(f"\n[{market_label}] {len(tickers)}銘柄のデータを取得します...")

    rows = []
    for item in tickers:
        print(f"  取得中: {item['ticker']} ({item['name']})")
        row = fetch_stock_row(item["ticker"], item["name"])
        if row:
            rows.append(row)

    ranking = build_ranking(rows, config["weights"])
    print(f"[{market_label}] {len(ranking)}銘柄のランキングを作成しました。")
    return ranking


def main():
    config = load_config()
    DATA_DIR.mkdir(exist_ok=True)

    jp_ranking = collect_market("jp", "日本株", config)
    us_ranking = collect_market("us", "米国株", config)

    if not jp_ranking.empty:
        jp_ranking.to_csv(DATA_DIR / "ranking_jp.csv", index=False, encoding="utf-8-sig")
        print(f"\n保存しました: {DATA_DIR / 'ranking_jp.csv'}")
    else:
        print("\n[エラー] 日本株のデータが1件も取得できませんでした。")

    if not us_ranking.empty:
        us_ranking.to_csv(DATA_DIR / "ranking_us.csv", index=False, encoding="utf-8-sig")
        print(f"保存しました: {DATA_DIR / 'ranking_us.csv'}")
    else:
        print("[エラー] 米国株のデータが1件も取得できませんでした。")

    updated_at = datetime.now(timezone.utc).astimezone().isoformat()
    with open(DATA_DIR / "last_updated.txt", "w", encoding="utf-8") as f:
        f.write(updated_at)
    print(f"\n最終更新時刻: {updated_at}")


if __name__ == "__main__":
    sys.exit(main())
