"""
株価自動ランキング - フェーズ3: Streamlitダッシュボード

実行方法:
    streamlit run app.py

やっていること:
    - data/ranking_jp.csv, data/ranking_us.csv (collector.pyが生成) を読み込んで表示するだけ
    - 銘柄を選ぶと、直近1ヶ月の株価チャートとスコア内訳を表示
    - サイドバーでウォッチリスト(config.yaml)の追加・削除ができる
"""

from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
import yfinance as yf

DATA_DIR = Path(__file__).parent / "data"
CONFIG_PATH = Path(__file__).parent / "config.yaml"

MARKETS = {"jp": "日本株", "us": "米国株"}

st.set_page_config(page_title="株式自動ランキング", layout="wide")


@st.cache_data(ttl=300)
def load_ranking(market_key: str) -> pd.DataFrame:
    path = DATA_DIR / f"ranking_{market_key}.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def load_last_updated() -> str:
    path = DATA_DIR / "last_updated.txt"
    if not path.exists():
        return "不明"
    return path.read_text(encoding="utf-8").strip()


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


@st.cache_data(ttl=3600)
def load_price_history(ticker: str) -> pd.DataFrame:
    return yf.Ticker(ticker).history(period="1mo", interval="1d")


def render_ranking_table(df: pd.DataFrame, sort_col: str, label: str):
    if df.empty:
        st.warning("データがありません。collector.py を一度実行してください。")
        return

    sorted_df = df.sort_values(sort_col, ascending=False).reset_index(drop=True)
    sorted_df.insert(0, "順位", sorted_df.index + 1)

    display_cols = {
        "順位": "順位",
        "name": "銘柄名",
        "ticker": "コード",
        "current_price": "現在値",
        "change_pct": "前日比%",
        "volume_surge_ratio": "出来高急増率",
        "total_score": "総合スコア",
    }
    view = sorted_df[list(display_cols.keys())].rename(columns=display_cols)
    st.dataframe(view, width="stretch", hide_index=True)


def render_detail(df: pd.DataFrame):
    if df.empty:
        return

    st.subheader("銘柄詳細")
    options = df["ticker"] + " - " + df["name"]
    choice = st.selectbox("銘柄を選択", options)
    ticker = choice.split(" - ")[0]
    row = df[df["ticker"] == ticker].iloc[0]

    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown(f"**{row['name']} ({ticker})** の直近1ヶ月の株価推移")
        hist = load_price_history(ticker)
        if not hist.empty:
            st.line_chart(hist["Close"])
        else:
            st.info("株価チャートを取得できませんでした。")

    with col2:
        st.markdown("**指標の内訳**")
        st.metric("現在値", row["current_price"])
        st.metric("前日比", f"{row['change_pct']}%")
        st.metric("出来高急増率", f"{row['volume_surge_ratio']}倍")
        st.metric("52週高値 / 安値", f"{row['high_52w']} / {row['low_52w']}")
        st.metric("総合スコア", row["total_score"])

    st.markdown("**関連ニュース**")
    st.caption("ニュース収集機能はフェーズ4で追加予定です。")


def render_watchlist_editor(config: dict):
    st.sidebar.header("ウォッチリスト編集")
    st.sidebar.caption(
        "ここでの変更はローカルの config.yaml に保存されます。"
        "デプロイ版(Streamlit Community Cloud)に反映するには、"
        "GitHub上のconfig.yamlも編集してpushしてください。"
    )

    market_key = st.sidebar.selectbox(
        "対象市場", options=list(MARKETS.keys()), format_func=lambda k: MARKETS[k]
    )

    st.sidebar.markdown("**現在の銘柄一覧**")
    current_list = config["watchlist"].get(market_key, [])
    for item in current_list:
        st.sidebar.text(f"{item['ticker']} - {item['name']}")

    with st.sidebar.form("add_ticker_form"):
        st.markdown("**銘柄を追加**")
        new_ticker = st.text_input("ティッカー(例: 7203.T / AAPL)")
        new_name = st.text_input("銘柄名")
        submitted = st.form_submit_button("追加")
        if submitted and new_ticker and new_name:
            if any(item["ticker"] == new_ticker for item in current_list):
                st.sidebar.error("すでに登録されています。")
            else:
                current_list.append({"ticker": new_ticker, "name": new_name})
                config["watchlist"][market_key] = current_list
                save_config(config)
                st.sidebar.success(f"{new_ticker} を追加しました。次回のデータ収集から反映されます。")
                st.rerun()

    with st.sidebar.form("remove_ticker_form"):
        st.markdown("**銘柄を削除**")
        tickers = [item["ticker"] for item in current_list]
        to_remove = st.selectbox("削除する銘柄", options=["(選択してください)"] + tickers)
        removed = st.form_submit_button("削除")
        if removed and to_remove != "(選択してください)":
            config["watchlist"][market_key] = [
                item for item in current_list if item["ticker"] != to_remove
            ]
            save_config(config)
            st.sidebar.success(f"{to_remove} を削除しました。")
            st.rerun()


def main():
    st.title("株式自動ランキング")
    st.caption(f"最終更新時刻: {load_last_updated()}")
    st.info(
        "本アプリは情報提供・分析支援を目的としており、投資助言ではありません。"
        "投資判断はご自身の責任で行ってください。",
        icon="ℹ️",
    )

    config = load_config()
    render_watchlist_editor(config)

    market_key = st.radio(
        "市場を選択", options=list(MARKETS.keys()), format_func=lambda k: MARKETS[k], horizontal=True
    )
    df = load_ranking(market_key)

    tab1, tab2, tab3 = st.tabs(["総合スコアランキング", "値上がりランキング", "出来高急増ランキング"])
    with tab1:
        render_ranking_table(df, "total_score", "総合")
    with tab2:
        render_ranking_table(df, "change_pct", "値上がり")
    with tab3:
        render_ranking_table(df, "volume_surge_ratio", "出来高急増")

    st.divider()
    render_detail(df)


if __name__ == "__main__":
    main()
