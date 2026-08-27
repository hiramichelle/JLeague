"""
J-League データサイト SFRT08 (チーム別集計結果) から
シュート数・得失点などのチーム集計データを取得し、Supabaseにupsertするスクリプト。

対象ページ例:
https://data.j-league.or.jp/SFRT08/search?competitionYearEx=2026&competitionIdEx=3&...

取得対象:
  ヘッダー: /html/body/div[1]/div[2]/div/form/div[3]/div[1]/table/thead
  データ  : /html/body/div[1]/div[2]/div/form/div[3]/div[1]/table/tbody[1]

列構成 (thead基準、18列):
  チーム名, 試合, 試合時間, 得点, 1試合平均得点, PK得点, PK,
  失点, 1試合平均失点, PK失点, 被PK, シュート, 被シュート,
  FK, CK, 反則, 警告, 退場

注意:
  「リーグ計」の合計行はチームではないため取得時に除外する。
  team列はSFRT08表記の正式名称のまま格納し、team_masterとの突合は
  Supabase側のVIEW (team_season_stats_joined) に任せる。

必要な環境変数:
    SUPABASE_URL
    SUPABASE_KEY   (service_role key。書き込み権限が必要)

必要なライブラリ:
    pip install requests lxml supabase
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, asdict
from typing import Optional

import requests
from lxml import html as lxml_html

# ─────────────────────────────────────────
# 設定
# ─────────────────────────────────────────

BASE_URL = "https://data.j-league.or.jp/SFRT08/search"

HEADERS_XPATH = "/html/body/div[1]/div[2]/div/form/div[3]/div[1]/table/thead"
BODY_XPATH = "/html/body/div[1]/div[2]/div/form/div[3]/div[1]/table/tbody[1]"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

SEASON_LABEL = "2026/27"

# 大会ごとの検索パラメータ。J3fetch.py のTARGETSと同じ考え方で、
# 増やしたい大会があればここに1行足すだけでよい。
TARGETS = [
    {
        "competition": "Ｊ１",
        "params": {
            "competitionYearEx": 2026,
            "competitionIdEx": 1,
            "selectedCompetitionName": "明治安田Ｊ１リーグ",
            "selectedCompetitionYear": "2026/27",
            "competitionYear": 2026,
            "competitionId": 1,
        },
    },
    {
        "competition": "Ｊ２",
        "params": {
            "competitionYearEx": 2026,
            "competitionIdEx": 2,
            "selectedCompetitionName": "明治安田Ｊ２リーグ",
            "selectedCompetitionYear": "2026/27",
            "competitionYear": 2026,
            "competitionId": 2,
        },
    },
    {
        "competition": "Ｊ３",
        "params": {
            "competitionYearEx": 2026,
            "competitionIdEx": 3,
            "selectedCompetitionName": "明治安田Ｊ３リーグ",
            "selectedCompetitionYear": "2026/27",
            "competitionYear": 2026,
            "competitionId": 3,
        },
    },
]

EXCLUDED_ROW_LABELS = {"リーグ計", ""}


@dataclass
class TeamStatsRow:
    stats_key: str = ""
    season: str = SEASON_LABEL
    competition: Optional[str] = None
    official_name: Optional[str] = None
    played: Optional[int] = None
    minutes_played: Optional[int] = None
    goals: Optional[int] = None
    avg_goals_per_match: Optional[float] = None
    pk_goals: Optional[int] = None
    pk_attempts: Optional[int] = None
    goals_against: Optional[int] = None
    avg_goals_against_per_match: Optional[float] = None
    pk_against: Optional[int] = None
    pk_saved: Optional[int] = None
    shots: Optional[int] = None
    shots_against: Optional[int] = None
    fk: Optional[int] = None
    ck: Optional[int] = None
    fouls: Optional[int] = None
    yellow_cards: Optional[int] = None
    red_cards: Optional[int] = None


def make_stats_key(season: str, competition: str, official_name: str) -> str:
    raw = f"{season}|{competition}|{official_name}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def to_int(text: str) -> Optional[int]:
    text = text.strip()
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def to_float(text: str) -> Optional[float]:
    text = text.strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ─────────────────────────────────────────
# 取得処理
# ─────────────────────────────────────────

def fetch_team_stats(
    competition_label: str,
    params: dict,
    session: Optional[requests.Session] = None,
) -> list[dict]:
    sess = session or requests
    resp = sess.get(BASE_URL, params=params, headers=REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    tree = lxml_html.fromstring(resp.text)

    thead = tree.xpath(HEADERS_XPATH)
    tbody = tree.xpath(BODY_XPATH)

    if not thead or not tbody:
        print(f"{competition_label}: テーブルが見つかりません。スキップします。")
        return []

    rows: list[dict] = []
    for tr in tbody[0].xpath(".//tr"):
        tds = tr.xpath(".//td")
        if len(tds) < 18:
            continue

        def cell_text(i: int) -> str:
            return tds[i].text_content().strip()

        official_name = cell_text(0)
        if official_name in EXCLUDED_ROW_LABELS:
            continue

        row = TeamStatsRow(
            stats_key=make_stats_key(SEASON_LABEL, competition_label, official_name),
            competition=competition_label,
            official_name=official_name,
            played=to_int(cell_text(1)),
            minutes_played=to_int(cell_text(2)),
            goals=to_int(cell_text(3)),
            avg_goals_per_match=to_float(cell_text(4)),
            pk_goals=to_int(cell_text(5)),
            pk_attempts=to_int(cell_text(6)),
            goals_against=to_int(cell_text(7)),
            avg_goals_against_per_match=to_float(cell_text(8)),
            pk_against=to_int(cell_text(9)),
            pk_saved=to_int(cell_text(10)),
            shots=to_int(cell_text(11)),
            shots_against=to_int(cell_text(12)),
            fk=to_int(cell_text(13)),
            ck=to_int(cell_text(14)),
            fouls=to_int(cell_text(15)),
            yellow_cards=to_int(cell_text(16)),
            red_cards=to_int(cell_text(17)),
        )
        rows.append(asdict(row))

    return rows


def fetch_all_targets() -> list[dict]:
    all_rows: list[dict] = []
    with requests.Session() as sess:
        for target in TARGETS:
            rows = fetch_team_stats(target["competition"], target["params"], session=sess)
            print(f"{target['competition']}: {len(rows)}件 取得")
            all_rows.extend(rows)
    return all_rows


# ─────────────────────────────────────────
# Supabase投入
# ─────────────────────────────────────────

def upsert_to_supabase(rows: list[dict]) -> None:
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    client = create_client(url, key)

    chunk_size = 100
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        client.table("team_season_stats").upsert(chunk, on_conflict="stats_key").execute()
        print(f"Supabaseへupsert: {i + len(chunk)}/{len(rows)}件")


# ─────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────

if __name__ == "__main__":
    data = fetch_all_targets()
    print(f"\n合計 {len(data)} チーム分のデータを取得しました。")

    if not data:
        print("取得件数が0件のため、Supabaseへの投入はスキップします。")
    else:
        upsert_to_supabase(data)
        print("Supabaseへの投入が完了しました。")