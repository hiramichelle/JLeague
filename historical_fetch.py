"""
過去シーズンのJ1/J2/J3データを一括取得する、一回限りのバックフィル用スクリプト。

J3fetch.py (GitHub Actionsで毎日回す定期実行用)とは別に用意している。
理由:
  ・過去データは一度取り込めば変化しないため、定期実行する必要がない
  ・対象年数分だけ手元で1回実行すればよい

J3fetch.pyのfetch_competition() / upsert_to_supabase()をそのまま再利用する。
(このファイルと同じディレクトリにJ3fetch.pyが必要)

必要な環境変数: SUPABASE_URL, SUPABASE_KEY (J3fetch.pyと共通。service_role key)
必要なライブラリ: requests lxml supabase (J3fetch.pyと共通)

実行方法:
    python historical_fetch.py
"""

from __future__ import annotations

import time

import requests

from J3fetch import TARGETS, REQUEST_INTERVAL_SEC, fetch_competition, upsert_to_supabase

# 取得したい過去シーズンの範囲。まずは1年だけ試してから広げることを推奨。
# (2026年は既存のJ3fetch.pyの定期実行でカバー済みなので含めない)
YEARS = [2025]  # 動作確認後に list(range(2020, 2026)) のように広げる


def fetch_all_years() -> list[dict]:
    all_rows: list[dict] = []
    with requests.Session() as sess:
        for year in YEARS:
            for target in TARGETS:
                rows = fetch_competition(
                    target["competition_frame_ids"],
                    competition_years=year,
                    session=sess,
                )
                print(f"{year}年 {target['label']}: {len(rows)}件 取得")
                all_rows.extend(rows)
                time.sleep(REQUEST_INTERVAL_SEC)
    return all_rows


if __name__ == "__main__":
    data = fetch_all_years()
    print(f"\n合計 {len(data)} 試合を取得しました。")

    if not data:
        print("取得件数が0件のため、Supabaseへの投入はスキップします。")
    else:
        upsert_to_supabase(data)
        print("Supabaseへの投入が完了しました。")
