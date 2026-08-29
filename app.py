"""
J-League 勝敗予想ツール (Streamlit)

前提:
  Supabase に以下のVIEW/テーブルが作成済みであること
    - standings      (season, competition, team, played, wins, draws, losses,
                       goals_for, goals_against, goal_diff, points, position)
    - recent_form     (team, season, competition, games_counted, form_string,
                       recent_points, recent_goals_for, recent_goals_against)

必要な環境変数 / secrets:
    SUPABASE_URL
    SUPABASE_KEY   (anon key推奨。読み取り専用アプリなのでservice_roleは避ける)

ローカル実行:
    pip install streamlit supabase pandas
    streamlit run app.py

Secretsの置き方 (ローカル):
    .streamlit/secrets.toml に以下を記載
        SUPABASE_URL = "https://xxxxx.supabase.co"
        SUPABASE_KEY = "xxxxxxxxxxxx"
"""

from __future__ import annotations

import math

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit_shadcn_ui as ui
from supabase import create_client

# ─────────────────────────────────────────
# 初期設定
# ─────────────────────────────────────────

st.set_page_config(page_title="J-League 勝敗予想ツール", layout="wide")


@st.cache_resource
def get_client():
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY")
    if not url or not key:
        st.error("SUPABASE_URL / SUPABASE_KEY が設定されていません。.streamlit/secrets.toml を確認してください。")
        st.stop()
    return create_client(url, key)


@st.cache_data(ttl=300)
def load_standings() -> pd.DataFrame:
    client = get_client()
    res = client.table("standings").select("*").execute()
    return pd.DataFrame(res.data)


@st.cache_data(ttl=300)
def load_recent_form() -> pd.DataFrame:
    client = get_client()
    res = client.table("recent_form").select("*").execute()
    return pd.DataFrame(res.data)


@st.cache_data(ttl=300)
def load_home_away_splits() -> pd.DataFrame:
    client = get_client()
    res = client.table("home_away_splits").select("*").execute()
    return pd.DataFrame(res.data)


@st.cache_data(ttl=300)
def load_team_season_stats() -> pd.DataFrame:
    client = get_client()
    res = client.table("team_season_stats_joined").select("*").execute()
    return pd.DataFrame(res.data)


@st.cache_data(ttl=300)
def load_jleague_matches() -> pd.DataFrame:
    client = get_client()
    res = client.table("jleague_matches").select("*").execute()
    df = pd.DataFrame(res.data)
    # デバッグ: 列名と最初の行を表示
    if not df.empty:
        pass  # 本来はここでデバッグ出力するが、本番環境では不要
    return df


@st.cache_data(ttl=300)
def load_section_standings() -> pd.DataFrame:
    client = get_client()
    res = client.table("team_section_standings").select("*").execute()
    return pd.DataFrame(res.data)


FORM_BADGE = {"W": "🟢", "D": "⚪", "L": "🔴"}


def render_form_badges(form_string: str | None) -> str:
    if not form_string:
        return "(データなし)"
    return " ".join(FORM_BADGE.get(c, "?") for c in form_string)


def minmax_normalize(series: pd.Series) -> pd.Series:
    lo, hi = series.min(), series.max()
    if hi == lo:
        return series.apply(lambda _: 0.5)
    return (series - lo) / (hi - lo)


# ─────────────────────────────────────────
# データロード
# ─────────────────────────────────────────

standings_df = load_standings()
form_df = load_recent_form()
home_away_df = load_home_away_splits()
stats_df = load_team_season_stats()
section_standings_df = load_section_standings()

# jleague_matchesの取得
try:
    jleague_matches = load_jleague_matches()
except Exception as e:
    st.error(f"試合データ取得エラー: {e}")
    st.stop()

# 列名の確認・修正
if jleague_matches is None:
    st.error("jleague_matchesがNoneです")
    st.stop()

if jleague_matches.empty:
    st.error(f"jleague_matchesが空です。Supabaseのjleague_matchesテーブルにデータがあるか確認してください。")
    st.write(f"取得されたデータ: {jleague_matches}")
    st.stop()

# 必要な列が存在するか確認
required_cols = ["season", "competition", "section", "home_team", "away_team"]
missing_cols = [col for col in required_cols if col not in jleague_matches.columns]
if missing_cols:
    st.error(f"jleague_matchesテーブルに必要な列が不足しています: {missing_cols}")
    st.write(f"実際の列: {jleague_matches.columns.tolist()}")
    st.stop()

if standings_df.empty:
    st.warning("standings データが空です。Supabase側のデータ取得・VIEW作成が完了しているか確認してください。")
    st.stop()

# ─────────────────────────────────────────
# サイドバー: シーズン/大会/チーム/重み設定
# ─────────────────────────────────────────

st.sidebar.header("設定")

season_options = sorted(standings_df["season"].unique(), reverse=True)
season = st.sidebar.selectbox("シーズン", season_options)

competition_options = sorted(standings_df[standings_df["season"] == season]["competition"].unique())
# デフォルトはＪ３をプリセット(存在すれば)
default_competition_index = competition_options.index("Ｊ３") if "Ｊ３" in competition_options else 0
competition = st.sidebar.selectbox("大会", competition_options, index=default_competition_index)

# 該当シーズン・大会の試合データを絞り込み
matches_scoped = jleague_matches[
    (jleague_matches["season"] == season) & 
    (jleague_matches["competition"] == competition)
]

if matches_scoped.empty:
    st.error(f"{season} {competition}の試合データがありません")
    st.stop()

# チーム選択(ホーム/アウェイ問わず、この大会に出場する全チームが対象)
all_teams = sorted(
    set(matches_scoped["home_team"].unique()) | set(matches_scoped["away_team"].unique())
)
# デフォルトは相模原をプリセット(存在すれば)
default_team_index = all_teams.index("相模原") if "相模原" in all_teams else 0
team = st.sidebar.selectbox("チーム", all_teams, index=default_team_index)

# 選択したチームが関わる全試合(ホーム/アウェイ両方)
team_matches = matches_scoped[
    (matches_scoped["home_team"] == team) | (matches_scoped["away_team"] == team)
]

if team_matches.empty:
    st.sidebar.warning("該当試合がありません")
    st.stop()

# 対戦相手の一覧(重複無し)
def _opponent_of(row):
    return row["away_team"] if row["home_team"] == team else row["home_team"]

opponents = sorted(team_matches.apply(_opponent_of, axis=1).unique())
opponent = st.sidebar.selectbox("対戦相手", opponents)

# 選択したチーム×対戦相手の組み合わせの試合(ホーム/アウェイ2試合あることが多い)
matches_between = team_matches[
    ((team_matches["home_team"] == team) & (team_matches["away_team"] == opponent))
    | ((team_matches["home_team"] == opponent) & (team_matches["away_team"] == team))
].copy()

if matches_between.empty:
    st.sidebar.error("該当試合が見つかりません")
    st.stop()

if len(matches_between) > 1:
    # 複数試合(ホーム/アウェイ2試合)ある場合は選択させる
    def _match_label(row):
        side = "ホーム" if row["home_team"] == team else "アウェイ"
        status = "消化済み" if row["is_finished"] else "未消化"
        return f"{row['section']} ({side}・{status})"

    matches_between["_label"] = matches_between.apply(_match_label, axis=1)
    selected_label = st.sidebar.selectbox("対戦カード", matches_between["_label"].tolist())
    match_row = matches_between[matches_between["_label"] == selected_label].iloc[0]
else:
    match_row = matches_between.iloc[0]

# 選択された試合から実際のホーム/アウェイを確定
home_team = match_row["home_team"]
away_team = match_row["away_team"]
match_section = match_row["section"]
match_is_finished = bool(match_row["is_finished"])

if match_is_finished:
    st.sidebar.markdown(f"**{match_section}** で対戦済み")
    if pd.notna(match_row.get("score")):
        st.sidebar.write(f"結果: {home_team} {match_row['score']} {away_team}")
else:
    st.sidebar.markdown(f"**{match_section}** で対戦予定")

st.sidebar.markdown(f"ホーム: **{home_team}** / アウェイ: **{away_team}**")

st.sidebar.divider()
st.sidebar.subheader("重み設定")
w_points = st.sidebar.slider("勝点(順位)の重み", 0.0, 1.0, 0.4, 0.05)
w_goal_diff = st.sidebar.slider("得失点差の重み", 0.0, 1.0, 0.25, 0.05)
w_form = st.sidebar.slider("直近5試合フォームの重み", 0.0, 1.0, 0.25, 0.05)
w_home_away = st.sidebar.slider("ホーム/アウェイ実績の重み", 0.0, 1.0, 0.2, 0.05)
home_advantage = st.sidebar.slider("ホームアドバンテージ(一律加点)", 0.0, 0.5, 0.1, 0.05)
draw_factor = st.sidebar.slider("引き分けの出やすさ", 0.0, 1.0, 0.35, 0.05)

st.sidebar.caption(
    "「ホーム/アウェイ実績の重み」はチームごとの実際のホーム勝率・アウェイ勝率を反映します。"
    "「ホームアドバンテージ」は全チーム共通の一律加点です。"
)

# ─────────────────────────────────────────
# メイン: 順位表
# ─────────────────────────────────────────

scoped = standings_df[
    (standings_df["season"] == season) & (standings_df["competition"] == competition)
].sort_values("position")

st.title("⚽ J-League 勝敗予想ツール")
st.caption(f"{season}シーズン {competition}")

st.subheader("順位表")
display_cols = [
    "position", "team", "played", "points", "wins", "draws", "losses",
    "goals_for", "goals_against", "goal_diff",
]
st.dataframe(
    scoped[display_cols].rename(columns={
        "position": "順位", "team": "チーム", "played": "試合数",
         "points": "勝点", "wins": "勝", "draws": "分", "losses": "負",
        "goals_for": "得点", "goals_against": "失点",
        "goal_diff": "得失点差",
    }),
    hide_index=True,
    width='stretch',
)

# ─────────────────────────────────────────
# メイン: チームサマリー (スコアカード)
# ─────────────────────────────────────────

st.subheader("チームサマリー")


def render_score_cards(team_name: str, label: str):
    row_standing = scoped[scoped["team"] == team_name]
    row_stats = stats_df[
        (stats_df["short_name"] == team_name)
        & (stats_df["season"] == season)
        & (stats_df["competition"] == competition)
    ]

    position_value = "―"
    goals_for_value = "―"
    goals_against_value = "―"
    if not row_standing.empty:
        r = row_standing.iloc[0]
        position_value = f"{int(r['position'])}位"
        goals_for_value = str(int(r["goals_for"]))
        goals_against_value = str(int(r["goals_against"]))

    scoring_rate_value = "―"
    conceding_rate_value = "―"
    if not row_stats.empty:
        s = row_stats.iloc[0]
        if pd.notna(s.get("shot_conversion_pct")):
            scoring_rate_value = f"{s['shot_conversion_pct']:.1f}%"
        if pd.notna(s.get("opponent_conversion_pct")):
            conceding_rate_value = f"{s['opponent_conversion_pct']:.1f}%"

    st.markdown(f"**{label}: {team_name}**")
    cols = st.columns(5)
    with cols[0]:
        ui.card(title="現在順位", content=position_value, key=f"card_position_{label}")
    with cols[1]:
        ui.card(title="総得点", content=goals_for_value, key=f"card_gf_{label}")
    with cols[2]:
        ui.card(title="総失点", content=goals_against_value, key=f"card_ga_{label}")
    with cols[3]:
        ui.card(title="得点率", content=scoring_rate_value, description="得点/シュート数", key=f"card_scoring_{label}")
    with cols[4]:
        ui.card(title="失点率", content=conceding_rate_value, description="失点/被シュート数", key=f"card_conceding_{label}")


render_score_cards(home_team, "ホーム")
render_score_cards(away_team, "アウェイ")

# ─────────────────────────────────────────
# メイン: 直近フォーム比較
# ─────────────────────────────────────────

st.subheader("直近5試合トレンド")

col_home, col_away = st.columns(2)


def render_team_form_panel(container, team_name: str, label: str, is_home: bool):
    row_form = form_df[
        (form_df["team"] == team_name)
        & (form_df["season"] == season)
        & (form_df["competition"] == competition)
    ]
    row_ha = home_away_df[
        (home_away_df["team"] == team_name)
        & (home_away_df["season"] == season)
        & (home_away_df["competition"] == competition)
        & (home_away_df["is_home"] == is_home)
    ]

    with container:
        st.markdown(f"**{label}: {team_name}**")
        if not row_form.empty:
            f = row_form.iloc[0]
            st.markdown(render_form_badges(f.get("form_string")))
            st.write(
                f"直近{int(f['games_counted'])}試合: 勝点{int(f['recent_points'])} "
                f"(得点{int(f['recent_goals_for'])} / 失点{int(f['recent_goals_against'])})"
            )
        else:
            st.write("直近試合データなし")

        ha_label = "ホーム" if is_home else "アウェイ"
        if not row_ha.empty and pd.notna(row_ha.iloc[0]["win_rate_pct"]):
            h = row_ha.iloc[0]
            st.write(
                f"{ha_label}成績: {int(h['played'])}試合 勝率{h['win_rate_pct']}% "
                f"(平均得点{h['avg_goals_for']} / 平均失点{h['avg_goals_against']})"
            )
        else:
            st.write(f"{ha_label}成績データなし")


render_team_form_panel(col_home, home_team, "ホーム", is_home=True)
render_team_form_panel(col_away, away_team, "アウェイ", is_home=False)

# ─────────────────────────────────────────
# メイン: 順位変動グラフ
# ─────────────────────────────────────────

st.subheader("順位変動")

progress_scoped = section_standings_df[
    (section_standings_df["season"] == season)
    & (section_standings_df["competition"] == competition)
    & (section_standings_df["team"].isin([home_team, away_team]))
].sort_values(["team", "section_no"])

if progress_scoped.empty:
    st.write("順位変動データがありません")
else:
    fig = px.line(
        progress_scoped,
        x="section_no",
        y="position",
        color="team",
        markers=True,
        labels={"section_no": "節", "position": "順位", "team": "チーム"},
    )
    # 順位は数値が小さいほど上位なので、Y軸を反転させて「上が1位」になるようにする
    max_position = int(progress_scoped["position"].max())
    fig.update_yaxes(autorange="reversed", dtick=1, range=[max_position + 0.5, 0.5])
    fig.update_xaxes(dtick=1)
    st.plotly_chart(fig, width='stretch')

# ─────────────────────────────────────────
# チーム別集計結果 (SFRT08データ)
# ─────────────────────────────────────────

st.subheader("チーム別集計結果")

col_home_stats, col_away_stats = st.columns(2)


def render_team_stats_panel(container, team_name: str, label: str):
    row_stats = stats_df[
        (stats_df["short_name"] == team_name)
        & (stats_df["season"] == season)
        & (stats_df["competition"] == competition)
    ]

    with container:
        st.markdown(f"**{label}: {team_name}**")
        if not row_stats.empty and pd.notna(row_stats.iloc[0]["played"]):
            s = row_stats.iloc[0]
            st.write(f"試合数: {int(s['played'])}")
            st.write(f"一試合平均得点: {s['avg_goals_per_match']:.2f}")
        else:
            st.write("統計データなし")


render_team_stats_panel(col_home_stats, home_team, "ホーム")
render_team_stats_panel(col_away_stats, away_team, "アウェイ")

# ─────────────────────────────────────────
# 予測ロジック
# ─────────────────────────────────────────

st.subheader("勝敗予測")

if home_team == away_team:
    st.info("ホームとアウェイに同じチームが選択されています。異なるチームを選んでください。")
else:
    # 正規化された特徴量を作成 (リーグ内の全チームを母集団として min-max)
    norm_points = minmax_normalize(scoped.set_index("team")["points"])
    norm_goal_diff = minmax_normalize(scoped.set_index("team")["goal_diff"])

    form_indexed = form_df[
        (form_df["season"] == season) & (form_df["competition"] == competition)
    ].set_index("team")
    # フォームデータが無いチームは中央値で埋める
    recent_points_series = form_indexed["recent_points"].reindex(scoped["team"]).fillna(
        form_indexed["recent_points"].median() if not form_indexed.empty else 0
    )
    norm_form = minmax_normalize(recent_points_series)

    # ホーム/アウェイ別の勝率を正規化 (母集団は「ホームでの記録」「アウェイでの記録」それぞれ)
    ha_scoped = home_away_df[
        (home_away_df["season"] == season) & (home_away_df["competition"] == competition)
    ]
    home_win_rate = ha_scoped[ha_scoped["is_home"] == True].set_index("team")["win_rate_pct"]  # noqa: E712
    away_win_rate = ha_scoped[ha_scoped["is_home"] == False].set_index("team")["win_rate_pct"]  # noqa: E712
    norm_home_win_rate = minmax_normalize(home_win_rate.reindex(scoped["team"]).fillna(home_win_rate.median()))
    norm_away_win_rate = minmax_normalize(away_win_rate.reindex(scoped["team"]).fillna(away_win_rate.median()))

    def team_score(team_name: str, is_home: bool) -> float:
        score = (
            w_points * norm_points.get(team_name, 0.5)
            + w_goal_diff * norm_goal_diff.get(team_name, 0.5)
            + w_form * norm_form.get(team_name, 0.5)
        )
        if is_home:
            score += w_home_away * norm_home_win_rate.get(team_name, 0.5)
            score += home_advantage
        else:
            score += w_home_away * norm_away_win_rate.get(team_name, 0.5)
        return score

    score_home = team_score(home_team, is_home=True)
    score_away = team_score(away_team, is_home=False)

    # 3値(勝ち/分け/負け)への変換: strength を指数化してBradley-Terry風に配分
    sensitivity = 5.0
    strength_home = math.exp(score_home * sensitivity)
    strength_away = math.exp(score_away * sensitivity)
    strength_draw = draw_factor * math.sqrt(strength_home * strength_away)

    total = strength_home + strength_away + strength_draw
    p_home = strength_home / total
    p_away = strength_away / total
    p_draw = strength_draw / total

    c1, c2, c3 = st.columns(3)
    c1.metric(f"{home_team} 勝利", f"{p_home * 100:.1f}%")
    c2.metric("引き分け", f"{p_draw * 100:.1f}%")
    c3.metric(f"{away_team} 勝利", f"{p_away * 100:.1f}%")

    st.progress(p_home, text=f"{home_team} 優勢度")

    top = max(("home", p_home), ("draw", p_draw), ("away", p_away), key=lambda x: x[1])
    label_map = {"home": f"{home_team}の勝利", "draw": "引き分け", "away": f"{away_team}の勝利"}
    st.success(f"予測: **{label_map[top[0]]}** が最有力(確率 {top[1] * 100:.1f}%)")

    with st.expander("計算の内訳を見る"):
        st.write(f"{home_team} スコア: {score_home:.3f} (ホームアドバンテージ込み)")
        st.write(f"{away_team} スコア: {score_away:.3f}")
        st.caption(
            "スコアは「勝点・得失点差・直近フォーム・ホーム/アウェイでの実績」をリーグ内で0〜1に正規化し、"
            "サイドバーの重みで加重平均したものです。あくまで簡易的なヒューリスティックモデルです。"
        )