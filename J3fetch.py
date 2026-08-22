"""
J-League データサイト (data.j-league.or.jp) から
「日程・結果」テーブルを取得し、Supabaseにupsertするスクリプト。

GitHub Actions等での定期実行を想定 (ローカル環境の起動状態に依存しない運用)。

必要な環境変数:
    SUPABASE_URL       : SupabaseプロジェクトのURL
    SUPABASE_KEY        : Supabase の service_role キー (RLSを使う場合はそれに応じたキー)

必要なライブラリ:
    pip install requests lxml supabase
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, asdict
from typing import Optional

import requests
from lxml import html as lxml_html

# ─────────────────────────────────────────
# 設定
# ─────────────────────────────────────────

BASE_URL = "https://data.j-league.or.jp/SFMS01/search"

HEADERS_XPATH = "/html/body/div[1]/div[2]/div/div[5]/div[2]/table/thead"
BODY_XPATH = "/html/body/div[1]/div[2]/div/div[5]/div[2]/table/tbody"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
REQUEST_INTERVAL_SEC = 1.5

COMPETITION_YEARS = 2026
COMPETITION_FRAME_IDS = 3
COMPETITION_IDS = 730

# J3 全節 (6500は欠番)
TARGET_SECTION_IDS = list(range(6468, 6500)) + list(range(6501, 6506))


@dataclass
class MatchRow:
    match_key: str = ""
    season: Optional[str] = None
    competition: Optional[str] = None
    section: Optional[str] = None
    match_date: Optional[str] = None
    kickoff_time: Optional[str] = None
    home_team: Optional[str] = None
    home_team_url: Optional[str] = None
    score: Optional[str] = None
    match_card_id: Optional[str] = None
    match_url: Optional[str] = None
    away_team: Optional[str] = None
    away_team_url: Optional[str] = None
    stadium: Optional[str] = None
    attendance: Optional[str] = None
    broadcast: Optional[str] = None


def make_match_key(match_date: str, home_team: str, away_team: str) -> str:
    """試合日+ホーム+アウェイから一意キーを生成する(match_card_idが未確定の試合でも一意に定まる)。"""
    raw = f"{match_date}|{home_team}|{away_team}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────
# 取得処理
# ─────────────────────────────────────────

def fetch_section(
    section_id: int,
    session: Optional[requests.Session] = None,
) -> list[dict]:
    """指定した節(competition_section_ids)1つ分のテーブルを取得して辞書リストで返す。"""

    params = {
        "competition_years": COMPETITION_YEARS,
        "competition_frame_ids": COMPETITION_FRAME_IDS,
        "competition_ids": COMPETITION_IDS,
        "competition_section_ids": section_id,
    }

    sess = session or requests
    resp = sess.get(BASE_URL, params=params, headers=REQUEST_HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    tree = lxml_html.fromstring(resp.text)

    thead = tree.xpath(HEADERS_XPATH)
    tbody = tree.xpath(BODY_XPATH)

    if not thead or not tbody:
        # 該当節がまだ存在しない/空のケースはエラーにせずスキップ
        print(f"section_id={section_id}: テーブルが見つかりません。スキップします。")
        return []

    rows: list[dict] = []
    for tr in tbody[0].xpath(".//tr"):
        tds = tr.xpath(".//td")
        if len(tds) < 10:
            continue

        def cell_text(i: int) -> str:
            return tds[i].text_content().strip()

        def cell_link(i: int) -> Optional[str]:
            links = tds[i].xpath(".//a/@href")
            return links[0] if links else None

        score_text = cell_text(6)
        match_url = cell_link(6)
        match_card_id = None
        if match_url and "match_card_id=" in match_url:
            match_card_id = match_url.split("match_card_id=")[-1]

        match_date = cell_text(3)
        home_team = cell_text(5)
        away_team = cell_text(7)

        row = MatchRow(
            match_key=make_match_key(match_date, home_team, away_team),
            season=cell_text(0),
            competition=cell_text(1),
            section=cell_text(2),
            match_date=match_date,
            kickoff_time=cell_text(4),
            home_team=home_team,
            home_team_url=cell_link(5),
            score=score_text,
            match_card_id=match_card_id,
            match_url=match_url,
            away_team=away_team,
            away_team_url=cell_link(7),
            stadium=cell_text(8),
            attendance=cell_text(9),
            broadcast=cell_text(10) if len(tds) > 10 else None,
        )
        rows.append(asdict(row))

    return rows


def fetch_sections(section_ids: list[int]) -> list[dict]:
    all_rows: list[dict] = []
    with requests.Session() as sess:
        for i, sid in enumerate(section_ids):
            rows = fetch_section(sid, session=sess)
            if rows:
                print(f"section_id={sid}: {len(rows)}件 取得")
            all_rows.extend(rows)
            if i < len(section_ids) - 1:
                time.sleep(REQUEST_INTERVAL_SEC)
    return all_rows


# ─────────────────────────────────────────
# Supabase投入
# ─────────────────────────────────────────

def upsert_to_supabase(rows: list[dict]) -> None:
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    client = create_client(url, key)

    # 大量件数を一度に投げず、チャンクに分けて安全側に倒す
    chunk_size = 200
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        client.table("jleague_matches").upsert(chunk, on_conflict="match_key").execute()
        print(f"Supabaseへupsert: {i + len(chunk)}/{len(rows)}件")


# ─────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────

if __name__ == "__main__":
    data = fetch_sections(TARGET_SECTION_IDS)
    print(f"\n合計 {len(data)} 試合を取得しました。")

    if not data:
        print("取得件数が0件のため、Supabaseへの投入はスキップします。")
    else:
        upsert_to_supabase(data)
        print("Supabaseへの投入が完了しました。")