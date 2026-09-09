"""
J-League データサイト (data.j-league.or.jp) から
「日程・結果」テーブルを取得し、Supabaseにupsertするスクリプト。

【改訂ポイント】
1. competition_years + competition_frame_ids で全節一括取得
2. team_aliases テーブルから名寄せ辞書をメモリにロードし、
   Unicode NFKC正規化（全角半角吸収）を通して
   home_club_slug / away_club_slug を自動付与してupsertする。

必要な環境変数:
    SUPABASE_URL
    SUPABASE_KEY   (service_role key または 書き込み権限のあるキー)

必要なライブラリ:
    pip install requests lxml supabase
"""

from __future__ import annotations

import hashlib
import os
import time
import unicodedata
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

# 大会一覧: J1/J2/J3
TARGETS = [
    {"label": "J1", "competition_frame_ids": 1},
    {"label": "J2", "competition_frame_ids": 2},
    {"label": "J3", "competition_frame_ids": 3},
]


# ─────────────────────────────────────────
# 名寄せ（エイリアス）キャッシュ & 正規化ロジック
# ─────────────────────────────────────────

def load_alias_dict() -> dict[str, str]:
    """Supabase上の team_aliases（約180件）をメモリに一括ロードする"""
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("⚠️ SUPABASE_URL または SUPABASE_KEY が設定されていません。")
        return {}

    try:
        client = create_client(url, key)
        res = client.table("team_aliases").select("alias, club_slug").execute()
        alias_map = {row["alias"]: row["club_slug"] for row in res.data}
        print(f"✅ team_aliases から {len(alias_map)} 件の名寄せ辞書をロードしました。")
        return alias_map
    except Exception as e:
        print(f"⚠️ team_aliases のロード中にエラーが発生しました: {e}")
        return {}


# スクリプト起動時に1度だけメモリに読み込む
ALIAS_DICT = load_alias_dict()


def resolve_club_slug(raw_name: Optional[str]) -> Optional[str]:
    """生テキストを全角半角正規化して club_slug に変換する"""
    if not raw_name:
        return None

    # Unicode NFKC正規化: 全角英数（「ＳＣ相模原」「松本山雅ＦＣ」等）を半角へ一発変換
    normalized = unicodedata.normalize("NFKC", str(raw_name)).strip()

    # 1. 完全一致チェック
    if normalized in ALIAS_DICT:
        return ALIAS_DICT[normalized]

    # 2. 部分一致フォールバック（例: 表記に余分なスペースや枝番が含まれている場合）
    for alias, slug in ALIAS_DICT.items():
        if alias and (alias == normalized or alias in normalized or normalized in alias):
            return slug

    # 見つからない場合はログを出して None を返す
    print(f"⚠️ [未登録のチーム表記] 元表記: '{raw_name}' (正規化後: '{normalized}')")
    return None


# ─────────────────────────────────────────
# データ構造定義
# ─────────────────────────────────────────

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
    home_club_slug: Optional[str] = None  # ★名寄せされたID (例: sagamihara)
    score: Optional[str] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    is_finished: bool = False
    match_card_id: Optional[str] = None
    match_url: Optional[str] = None
    away_team: Optional[str] = None
    away_team_url: Optional[str] = None
    away_club_slug: Optional[str] = None  # ★名寄せされたID (例: matsumoto)
    stadium: Optional[str] = None
    attendance: Optional[str] = None
    broadcast: Optional[str] = None


def make_match_key(match_date: str, home_team: str, away_team: str) -> str:
    """試合日+ホーム+アウェイから一意キーを生成する"""
    raw = f"{match_date}|{home_team}|{away_team}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def parse_score(score_text: str) -> tuple[Optional[int], Optional[int], bool]:
    """'1-0' のようなスコア文字列を (home_score, away_score, is_finished) に分解する。"""
    import re

    m = re.match(r"^(\d+)-(\d+)$", score_text.strip())
    if not m:
        return None, None, False
    return int(m.group(1)), int(m.group(2)), True


# ─────────────────────────────────────────
# スクレイピング取得処理
# ─────────────────────────────────────────

def fetch_competition(
    competition_frame_ids: int,
    competition_years: int = COMPETITION_YEARS,
    session: Optional[requests.Session] = None,
) -> list[dict]:
    """指定した大会(competition_frame_ids)のシーズン全節分を1リクエストで取得する。"""

    params = {
        "competition_years": competition_years,
        "competition_frame_ids": competition_frame_ids,
    }

    sess = session or requests
    resp = sess.get(BASE_URL, params=params, headers=REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    tree = lxml_html.fromstring(resp.text)

    thead = tree.xpath(HEADERS_XPATH)
    tbody = tree.xpath(BODY_XPATH)

    if not thead or not tbody:
        print(f"competition_frame_ids={competition_frame_ids}: テーブルが見つかりません。スキップします。")
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
        home_score, away_score, is_finished = parse_score(score_text)

        row = MatchRow(
            match_key=make_match_key(match_date, home_team, away_team),
            season=cell_text(0),
            competition=cell_text(1),
            section=cell_text(2),
            match_date=match_date,
            kickoff_time=cell_text(4),
            home_team=home_team,
            home_team_url=cell_link(5),
            home_club_slug=resolve_club_slug(home_team),  # ★ここで名寄せ実行
            score=score_text,
            home_score=home_score,
            away_score=away_score,
            is_finished=is_finished,
            match_card_id=match_card_id,
            match_url=match_url,
            away_team=away_team,
            away_team_url=cell_link(7),
            away_club_slug=resolve_club_slug(away_team),  # ★ここで名寄せ実行
            stadium=cell_text(8),
            attendance=cell_text(9),
            broadcast=cell_text(10) if len(tds) > 10 else None,
        )
        rows.append(asdict(row))

    return rows


def fetch_all_targets() -> list[dict]:
    all_rows: list[dict] = []
    with requests.Session() as sess:
        for i, target in enumerate(TARGETS):
            rows = fetch_competition(target["competition_frame_ids"], session=sess)
            print(f"{target['label']}: {len(rows)}件 取得")
            all_rows.extend(rows)
            if i < len(TARGETS) - 1:
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

    chunk_size = 200
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        client.table("jleague_matches").upsert(chunk, on_conflict="match_key").execute()
        print(f"Supabaseへupsert: {i + len(chunk)}/{len(rows)}件")

    # 節ごとの順位変動等のマテリアライズドビューをリフレッシュ
    try:
        print("team_section_standings 等をリフレッシュ中...")
        client.rpc("refresh_section_standings").execute()
        print("リフレッシュ完了。")
    except Exception as e:
        print(f"⚠️ RPC refresh_section_standings の実行をスキップしました (未定義の場合無視して問題ありません): {e}")


# ─────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────

if __name__ == "__main__":
    data = fetch_all_targets()
    print(f"\n合計 {len(data)} 試合を取得しました。")

    if not data:
        print("取得件数が0件のため、Supabaseへの投入はスキップします。")
    else:
        upsert_to_supabase(data)
        print("🎉 Supabaseへの投入と名寄せの反映が完了しました！")