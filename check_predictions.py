#!/usr/bin/env python3
"""
Supabase に保存された最新の試合予測 (match_predictions) を直接確認するスクリプト
"""
import os
import sys

try:
    from supabase import create_client
except ImportError:
    print("supabase パッケージをインストールしています...")
    os.system("pip install supabase python-dotenv")
    from supabase import create_client

SUPABASE_URL = "https://cpybpnbqnbtnotlsiqbk.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("VITE_SUPABASE_ANON_KEY")

if not SUPABASE_KEY or "YOUR_SUPABASE" in SUPABASE_KEY:
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                if line.startswith("SUPABASE_KEY=") or line.startswith("VITE_SUPABASE_ANON_KEY="):
                    SUPABASE_KEY = line.strip().split("=", 1)[1].strip('"\'')
                if line.startswith("SUPABASE_URL=") or line.startswith("VITE_SUPABASE_URL="):
                    SUPABASE_URL = line.strip().split("=", 1)[1].strip('"\'')

if not SUPABASE_KEY:
    print("エラー: SUPABASE_KEY が見つかりません。.env ファイルを確認してください。")
    sys.exit(1)

client = create_client(SUPABASE_URL, SUPABASE_KEY)

print(f"=== Supabase 接続先: {SUPABASE_URL} ===")
print("テーブル 'match_predictions' から予測データを取得中...\n")

try:
    res = client.from_("match_predictions").select("*").limit(15).execute()
    data = res.data

    if not data:
        print("データが0件です。")
        sys.exit(0)

    print(f"取得成功！全 {len(data)} 件の予測サンプル（松本山雅以外の対戦カード）:\n")
    print(f"{'試合カード (home vs away)':<30} | {'節':<10} | {'ホーム勝率':<10} | {'ドロー':<8} | {'アウェイ勝率':<10} | {'AI予測結果'}")
    print("-" * 90)

    for row in data:
        home = row.get("home_team", "Home")
        away = row.get("away_team", "Away")
        card = f"{home} vs {away}"
        sec = row.get("section", "N/A")
        
        p_home = row.get("pred_home_prob") or row.get("prob_home_win") or 0.0
        p_draw = row.get("pred_draw_prob") or row.get("prob_draw") or 0.0
        p_away = row.get("pred_away_prob") or row.get("prob_away_win") or 0.0
        outcome = row.get("predicted_outcome") or "未定"

        p_h_pct = f"{p_home*100:.1f}%" if p_home <= 1.0 else f"{p_home:.1f}%"
        p_d_pct = f"{p_draw*100:.1f}%" if p_draw <= 1.0 else f"{p_draw:.1f}%"
        p_a_pct = f"{p_away*100:.1f}%" if p_away <= 1.0 else f"{p_away:.1f}%"

        print(f"{card:<30} | {sec:<10} | {p_h_pct:<10} | {p_d_pct:<8} | {p_a_pct:<10} | {outcome}")

    print("\n※ Supabase との連携、および別試合の予測データ保存が確認できました！")

except Exception as e:
    print(f"取得エラー: {e}")
