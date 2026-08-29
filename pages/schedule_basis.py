"""
J-League Data Viewer - Schedule Basis

節を選ぶと、その節の全カード(結果 or 対戦予定)を一覧表示するページ。
Team Basisと異なり、チーム選択や重み設定は持たない。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from supabase import create_client


@st.cache_resource
def get_client():
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY")
    if not url or not key:
        st.error("SUPABASE_URL / SUPABASE_KEY が設定されていません。.streamlit/secrets.toml を確認してください。")
        st.stop()
    return create_client(url, key)


@st.cache_data(ttl=300)
def load_jleague_matches() -> pd.DataFrame:
    client = get_client()
    res = client.table("jleague_matches").select("*").execute()
    return pd.DataFrame(res.data)


# ─────────────────────────────────────────
# データロード
# ─────────────────────────────────────────

jleague_matches = load_jleague_matches()

if jleague_matches.empty:
    st.error("jleague_matchesが空です。Supabase側のデータ取得が完了しているか確認してください。")
    st.stop()

# ─────────────────────────────────────────
# サイドバー: シーズン/大会/節
# ─────────────────────────────────────────

st.sidebar.header("設定")

season_options = sorted(jleague_matches["season"].unique(), reverse=True)
season = st.sidebar.selectbox("シーズン", season_options)

competition_options = sorted(
    jleague_matches[jleague_matches["season"] == season]["competition"].unique()
)
default_competition_index = competition_options.index("Ｊ３") if "Ｊ３" in competition_options else 0
competition = st.sidebar.selectbox("大会", competition_options, index=default_competition_index)

matches_scoped = jleague_matches[
    (jleague_matches["season"] == season) & (jleague_matches["competition"] == competition)
]

if matches_scoped.empty:
    st.error(f"{season} {competition}の試合データがありません")
    st.stop()

section_options = sorted(matches_scoped["section"].unique())
section = st.sidebar.selectbox("節", section_options)

section_matches = matches_scoped[matches_scoped["section"] == section].copy()

# ─────────────────────────────────────────
# メイン: 節ごとの試合一覧
# ─────────────────────────────────────────

st.title("⚽ J-League Data Viewer")
st.caption(f"{season}シーズン {competition} ／ Schedule Basis")

st.subheader(f"{section} の対戦カード")

if section_matches.empty:
    st.warning("該当試合がありません")
else:
    def _status_and_score(row):
        if row["is_finished"] and pd.notna(row.get("score")):
            return "消化済み", row["score"]
        return "未消化", "vs"

    section_matches[["ステータス", "スコア"]] = section_matches.apply(
        lambda r: pd.Series(_status_and_score(r)), axis=1
    )

    display_cols = [
        "match_date", "kickoff_time", "home_team", "スコア", "away_team",
        "stadium", "ステータス",
    ]
    st.dataframe(
        section_matches[display_cols].rename(columns={
            "match_date": "試合日", "kickoff_time": "K/O時刻",
            "home_team": "ホーム", "away_team": "アウェイ",
            "stadium": "スタジアム",
        }),
        hide_index=True,
        width="stretch",
    )
