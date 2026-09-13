#!/usr/bin/env python3
"""
J.League 勝敗予測モデル (Mac完全対応版)
===================================================
Supabaseの特徴量ビュー `v_match_features` から
過密日程（中何日）と直近5試合調子（勝点・得失点）を取得し、
「ホーム勝 / 引き分け / アウェイ勝」の確率を予測します。
"""

import os
import sys
from typing import List, Dict, Any, Tuple
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# .env ファイルから環境変数を読み込み
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")

try:
    from supabase import create_client, Client
except ImportError:
    print("エラー: supabase パッケージがありません。pip install supabase を実行してください。")
    sys.exit(1)

from sklearn.metrics import accuracy_score, log_loss, classification_report

# LightGBMが使えるかチェックし、libompがないMac環境では互換のHistGradientBoostingに自動切り替え
USE_LGBM = False
try:
    import lightgbm as lgb
    # テスト的に小さなモデルを作って libomp エラーを検知
    test_clf = lgb.LGBMClassifier(n_estimators=1, verbose=-1)
    USE_LGBM = True
except Exception:
    from sklearn.ensemble import HistGradientBoostingClassifier
    USE_LGBM = False


def fetch_all_match_features() -> pd.DataFrame:
    """Supabaseの v_match_features から全試合を取得（ページネーション対応）"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError(".env ファイルに SUPABASE_URL または SUPABASE_KEY が設定されていません。")

    client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print(f"Supabaseに接続中 ({SUPABASE_URL})...")

    all_rows: List[Dict[str, Any]] = []
    page_size = 1000
    offset = 0

    while True:
        res = client.table("v_match_features") \
            .select("*") \
            .range(offset, offset + page_size - 1) \
            .execute()
        
        batch = res.data
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    print(f"v_match_features から {len(all_rows)} 件の試合データを取得しました。")
    return pd.DataFrame(all_rows)


def prepare_dataset(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """前処理：日付順ソート、正解ラベルの作成（2:ホーム勝, 1:分, 0:アウェイ勝）"""
    df['match_date_clean'] = pd.to_datetime(df['match_date'], errors='coerce')
    df = df.sort_values(by=['match_date_clean', 'match_key']).reset_index(drop=True)

    def determine_target(score: Any, is_finished: bool):
        if not is_finished or not isinstance(score, str) or '-' not in score:
            return np.nan
        try:
            parts = score.split('-')
            h = int(parts[0].strip())
            a = int(parts[1].strip())
            if h > a:
                return 2  # ホーム勝
            elif h == a:
                return 1  # 引き分け
            else:
                return 0  # アウェイ勝
        except Exception:
            return np.nan

    df['target'] = [determine_target(s, f) for s, f in zip(df['score'], df['is_finished'])]

    feature_cols = [
        'home_rest_days',
        'away_rest_days',
        'rest_advantage_days',
        'home_form_points',
        'away_form_points',
        'form_points_advantage',
        'home_form_gf',
        'away_form_gf',
        'home_form_ga',
        'away_form_ga',
        'home_form_gd',
        'away_form_gd',
    ]

    for col in feature_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # 終了済み試合（訓練用）と未消化試合（未来予測用）に分離
    df_train = df[df['target'].notna()].copy()
    df_train['target'] = df_train['target'].astype(int)

    df_upcoming = df[df['is_finished'] == False].copy()

    return df_train, df_upcoming


def train_and_evaluate(df_train: pd.DataFrame):
    """時系列分割（過去80%で学習、直近20%で検証）による機械学習モデルの訓練"""
    feature_cols = [
        'home_rest_days',
        'away_rest_days',
        'rest_advantage_days',
        'home_form_points',
        'away_form_points',
        'form_points_advantage',
        'home_form_gf',
        'away_form_gf',
        'home_form_ga',
        'away_form_ga',
        'home_form_gd',
        'away_form_gd',
    ]

    X = df_train[feature_cols]
    y = df_train['target']

    # 時系列分割（未来のリークを完全防止）
    split_idx = int(len(df_train) * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    print(f"\n=======================================================")
    print(f"データセット概要:")
    print(f"  総終了試合数          : {len(df_train)} 試合")
    print(f"  訓練データ（過去80%）  : {len(X_train)} 試合")
    print(f"  検証データ（直近20%）  : {len(X_test)} 試合")
    print(f"  使用エンジン          : {'LightGBM' if USE_LGBM else 'HistGradientBoosting (Macネイティブ高精度エンジン)'}")
    print(f"=======================================================\n")

    if USE_LGBM:
        model = lgb.LGBMClassifier(
            objective='multiclass',
            num_class=3,
            n_estimators=100,
            learning_rate=0.05,
            max_depth=4,
            num_leaves=15,
            random_state=42,
            verbose=-1
        )
    else:
        model = HistGradientBoostingClassifier(
            max_iter=100,
            learning_rate=0.05,
            max_depth=4,
            random_state=42
        )

    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)

    acc = accuracy_score(y_test, y_pred)
    loss = log_loss(y_test, y_prob)

    print(f"モデル評価（未知の直近試合に対する精度）:")
    print(f"  正解率（Accuracy）: {acc * 100:.2f}%")
    print(f"  Log Loss          : {loss:.4f}\n")

    # 特徴量の重要度（順列重要度）
    from sklearn.inspection import permutation_importance
    r = permutation_importance(model, X_test, y_test, n_repeats=5, random_state=42)
    
    print("特徴量の重要度ランキング（勝敗に効いている順）:")
    importance = pd.DataFrame({
        '特徴量': feature_cols,
        '重要度': np.maximum(0, r.importances_mean)
    }).sort_values(by='重要度', ascending=False).reset_index(drop=True)

    max_imp = max(importance['重要度']) if max(importance['重要度']) > 0 else 1
    for idx, row in importance.iterrows():
        bar = "█" * int(row['重要度'] / max_imp * 20)
        print(f"  {idx+1:2d}. {row['特徴量']:<24} {bar} ({row['重要度']:.4f})")

    return model, feature_cols


def predict_upcoming(model, feature_cols: List[str], df_upcoming: pd.DataFrame, top_n: int = 10):
    """今週末・未来の未消化試合に対する勝率確率の予測"""
    if df_upcoming.empty:
        print("\n未消化の試合が見つかりませんでした。")
        return

    X_future = df_upcoming[feature_cols].copy()
    probs = model.predict_proba(X_future)

    print(f"\n=======================================================")
    print(f"【今週末・直近の未来の試合】AI勝率予測（上位 {top_n} 試合）:")
    print(f"{'試合日':<12} {'節':<10} {'ホーム':<14} {'アウェイ':<14} {'ホーム勝率':<10} {'引分確率':<8} {'アウェイ勝率':<10}")
    print(f"-----------------------------------------------------------------------------------------")

    for i in range(min(top_n, len(df_upcoming))):
        m = df_upcoming.iloc[i]
        p_away, p_draw, p_home = probs[i][0], probs[i][1], probs[i][2]
        date_str = str(m['match_date'])[:10] if pd.notna(m['match_date']) else "未定"
        section_str = str(m['section'])[:8] if pd.notna(m['section']) else "-"
        print(f"{date_str:<12} {section_str:<10} {m['home_club_slug']:<14} {m['away_club_slug']:<14} {p_home*100:>8.1f}%  {p_draw*100:>6.1f}%  {p_away*100:>8.1f}%")
    print(f"=======================================================\n")


if __name__ == "__main__":
    df_raw = fetch_all_match_features()
    df_train, df_upcoming = prepare_dataset(df_raw)
    model, feature_cols = train_and_evaluate(df_train)
    predict_upcoming(model, feature_cols, df_upcoming, top_n=10)