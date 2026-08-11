"""
株価自動ランキング - フェーズ1〜4: 株価・ニュース・RSI取得 + ランキング計算

実行方法:
    python collector.py

やっていること:
    1. config.yaml からウォッチリスト(監視銘柄)を読み込む
    2. yfinance で各銘柄の株価データ・ニュース・RSIを取得する
    3. 値上がり率・出来高急増率・ニュース件数・RSIからスコアを計算する
    4. 日本株・米国株それぞれのランキングを data/ranking_jp.csv, data/ranking_us.csv に保存する
    5. 銘柄ごとの直近ニュース見出しを data/news_jp.json, data/news_us.json に保存する
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf

CONFIG_PATH = Path(__file__).parent / "config.yaml"
DATA_DIR = Path(__file__).parent / "data"

RSI_PERIOD = 14
NEWS_MAX_ITEMS = 5
NEWS_RECENT_HOURS = 48


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def calc_rsi(close: pd.Series, period: int = RSI_PERIOD) -> float:
    """直近のRSI(相対力指数)を計算する。データ不足時は中立値50を返す。"""
    if len(close) < period + 1:
        return 50.0

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def rsi_to_score(rsi: float) -> float:
    """
    RSIを0〜100のスコアに変換する。
    RSI 70前後(適度な上昇トレンド)をピークとし、
    30以下(勢い不足)や80超(加熱しすぎ)は減点する設計。
    """
    if rsi <= 30:
        return rsi
    if rsi <= 70:
        return 30 + (rsi - 30) * (70 / 40)
    if rsi <= 80:
        return 100 - (rsi - 70) * 4
    return max(0.0, 60 - (rsi - 80) * 3)


def fetch_news(ticker: str) -> list[dict]:
    """銘柄の直近ニュースを取得する(Yahoo Finance経由、取得失敗時は空リスト)。"""
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return []

    items = []
    for entry in raw:
        content = entry.get("content", {})
        items.append({
            "title": content.get("title", ""),
            "publisher": (content.get("provider") or {}).get("displayName", ""),
            "url": (content.get("canonicalUrl") or {}).get("url", ""),
            "published_at": content.get("pubDate", ""),
        })
    return items


def count_recent_news(news_items: list[dict], hours: int = NEWS_RECENT_HOURS) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    count = 0
    for item in news_items:
        pub = item.get("published_at")
        if not pub:
            continue
        try:
            pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except ValueError:
            continue
        if pub_dt >= cutoff:
            count += 1
    return count


def fetch_stock_row(ticker: str, name: str) -> tuple[dict, list[dict]] | tuple[None, None]:
    """1銘柄分の株価・ニュース・RSIを取得する。取得失敗時は (None, None) を返す。"""
    try:
        stock = yf.Ticker(ticker)
        # 52週高値/安値・出来高平均・RSIを計算するため1年分の日足を取得
        hist = stock.history(period="1y", interval="1d")

        if hist.empty or len(hist) < 2:
            print(f"  [警告] {ticker} ({name}): データが取得できませんでした。スキップします。")
            return None, None

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

        rsi = calc_rsi(hist["Close"])

        news_items = fetch_news(ticker)
        news_count = count_recent_news(news_items)

        row = {
            "ticker": ticker,
            "name": name,
            "current_price": round(current_price, 2),
            "change_pct": round(change_pct, 2),
            "volume": int(current_volume),
            "avg_volume_20d": int(avg_volume_20d),
            "volume_surge_ratio": round(volume_surge_ratio, 2),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "rsi": round(rsi, 2),
            "news_count": news_count,
        }
        return row, news_items[:NEWS_MAX_ITEMS]
    except Exception as e:
        print(f"  [警告] {ticker} ({name}): 取得中にエラーが発生しました ({e})。スキップします。")
        return None, None


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
    # ニュース件数はウォッチリスト内での相対的な多さでスコア化
    df["news_score"] = normalize(df["news_count"])
    # RSIは絶対水準(加熱・勢い不足)で評価するため正規化しない
    df["rsi_score"] = df["rsi"].apply(rsi_to_score)

    df["total_score"] = (
        df["price_change_score"] * weights["price_change"]
        + df["volume_surge_score"] * weights["volume_surge"]
        + df["news_score"] * weights["news_surge"]
        + df["rsi_score"] * weights["rsi"]
    ).round(2)

    df = df.sort_values("total_score", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    return df


def collect_market(market_key: str, market_label: str, config: dict) -> tuple[pd.DataFrame, dict]:
    tickers = config["watchlist"].get(market_key, [])
    print(f"\n[{market_label}] {len(tickers)}銘柄のデータを取得します...")

    rows = []
    news_by_ticker = {}
    for item in tickers:
        print(f"  取得中: {item['ticker']} ({item['name']})")
        row, news_items = fetch_stock_row(item["ticker"], item["name"])
        if row:
            rows.append(row)
            news_by_ticker[item["ticker"]] = news_items
        time.sleep(0.3)  # 連続リクエストによるレート制限を避けるための小休止

    ranking = build_ranking(rows, config["weights"])
    print(f"[{market_label}] {len(ranking)}銘柄のランキングを作成しました。")
    return ranking, news_by_ticker


def main():
    config = load_config()
    DATA_DIR.mkdir(exist_ok=True)

    jp_ranking, jp_news = collect_market("jp", "日本株", config)
    us_ranking, us_news = collect_market("us", "米国株", config)

    if not jp_ranking.empty:
        jp_ranking.to_csv(DATA_DIR / "ranking_jp.csv", index=False, encoding="utf-8-sig")
        with open(DATA_DIR / "news_jp.json", "w", encoding="utf-8") as f:
            json.dump(jp_news, f, ensure_ascii=False, indent=2)
        print(f"\n保存しました: {DATA_DIR / 'ranking_jp.csv'}")
    else:
        print("\n[エラー] 日本株のデータが1件も取得できませんでした。")

    if not us_ranking.empty:
        us_ranking.to_csv(DATA_DIR / "ranking_us.csv", index=False, encoding="utf-8-sig")
        with open(DATA_DIR / "news_us.json", "w", encoding="utf-8") as f:
            json.dump(us_news, f, ensure_ascii=False, indent=2)
        print(f"保存しました: {DATA_DIR / 'ranking_us.csv'}")
    else:
        print("[エラー] 米国株のデータが1件も取得できませんでした。")

    updated_at = datetime.now(timezone.utc).astimezone().isoformat()
    with open(DATA_DIR / "last_updated.txt", "w", encoding="utf-8") as f:
        f.write(updated_at)
    print(f"\n最終更新時刻: {updated_at}")


if __name__ == "__main__":
    sys.exit(main())
