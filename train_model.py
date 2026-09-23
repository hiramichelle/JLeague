#!/usr/bin/env python3
"""
J.League 勝敗予測 & バックテスト保存スクリプト
===================================================
1. Supabaseの `v_match_features` から特徴量を取得
2. モデルの学習と評価（正解率・特徴量重要度）
3. 過去全試合のバックテスト + 未来試合の勝率予測
4. `match_predictions` テーブルへ一括保存 (UPSERT)
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
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")

try:
    from supabase import create_client, Client
except ImportError:
    print("エラー: supabase パッケージがありません。pip install supabase を実行してください。")
    sys.exit(1)

from sklearn.metrics import accuracy_score, log_loss
from sklearn.inspection import permutation_importance

# LightGBM または Macネイティブ HistGradientBoosting の自動判定
USE_LGBM = False
try:
    import lightgbm as lgb
    test_clf = lgb.LGBMClassifier(n_estimators=1, verbose=-1)
    USE_LGBM = True
except Exception:
    from sklearn.ensemble import HistGradientBoostingClassifier
    USE_LGBM = False

MODEL_VERSION = "v1.0-lgbm" if USE_LGBM else "v1.0-histgrad"


def get_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError(".env ファイルに SUPABASE_URL または SUPABASE_KEY が設定されていません。")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_all_match_features(client: Client) -> pd.DataFrame:
    """Supabaseの v_match_features から全試合を取得（ページネーション対応）"""
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


def determine_actual_outcome(score: Any, is_finished: bool) -> str:
    """実際のスコアから勝敗文字列を判定"""
    if not is_finished or not isinstance(score, str) or '-' not in score:
        return "UNKNOWN"
    try:
        parts = score.split('-')
        h, a = int(parts[0].strip()), int(parts[1].strip())
        if h > a:
            return "HOME_WIN"
        elif h == a:
            return "DRAW"
        else:
            return "AWAY_WIN"
    except Exception:
        return "UNKNOWN"


def prepare_dataset(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """特徴量の前処理と数値変換"""
    df['match_date_clean'] = pd.to_datetime(df['match_date'], errors='coerce')
    df = df.sort_values(by=['match_date_clean', 'match_key']).reset_index(drop=True)

    outcome_map = {"HOME_WIN": 2, "DRAW": 1, "AWAY_WIN": 0}
    df['actual_outcome'] = [determine_actual_outcome(s, f) for s, f in zip(df['score'], df['is_finished'])]
    df['target'] = df['actual_outcome'].map(outcome_map)

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

    return df, feature_cols


def train_model(df: pd.DataFrame, feature_cols: List[str]):
    """モデル訓練と検証"""
    df_finished = df[df['target'].notna()].copy()
    X = df_finished[feature_cols]
    y = df_finished['target'].astype(int)

    split_idx = int(len(df_finished) * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    print(f"\n=======================================================")
    print(f"データセット概要:")
    print(f"  総終了試合数          : {len(df_finished)} 試合")
    print(f"  訓練データ（過去80%）  : {len(X_train)} 試合")
    print(f"  検証データ（直近20%）  : {len(X_test)} 試合")
    print(f"  使用エンジン          : {MODEL_VERSION}")
    print(f"=======================================================\n")

    if USE_LGBM:
        model = lgb.LGBMClassifier(
            objective='multiclass',
            num_class=3,
            n_estimators=100,
            learning_rate=0.05,
            max_depth=4,
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

    # 検証評価
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)
    acc = accuracy_score(y_test, y_pred)
    loss = log_loss(y_test, y_prob)

    print(f"モデル評価（未知の直近試合に対する精度）:")
    print(f"  正解率（Accuracy）: {acc * 100:.2f}%")
    print(f"  Log Loss          : {loss:.4f}\n")

    return model


def generate_predictions_and_save(client: Client, model, df: pd.DataFrame, feature_cols: List[str]):
    """全試合の予測を計算し、Supabaseの match_predictions に保存"""
    X_all = df[feature_cols]
    probs = model.predict_proba(X_all) # クラス: [0:AWAY_WIN, 1:DRAW, 2:HOME_WIN]

    outcome_labels = {0: "AWAY_WIN", 1: "DRAW", 2: "HOME_WIN"}

    records_to_upsert: List[Dict[str, Any]] = []

    for i in range(len(df)):
        row = df.iloc[i]
        p_away, p_draw, p_home = float(probs[i][0]), float(probs[i][1]), float(probs[i][2])
        
        best_class = int(np.argmax(probs[i]))
        predicted_outcome = outcome_labels[best_class]
        confidence = float(np.max(probs[i]))

        actual = row['actual_outcome']
        is_correct = None
        if actual in ["HOME_WIN", "DRAW", "AWAY_WIN"]:
            is_correct = (predicted_outcome == actual)

        records_to_upsert.append({
            "match_key": str(row['match_key']),
            "model_version": MODEL_VERSION,
            "pred_home_prob": round(p_home, 4),
            "pred_draw_prob": round(p_draw, 4),
            "pred_away_prob": round(p_away, 4),
            "predicted_outcome": predicted_outcome,
            "confidence": round(confidence, 4),
            "is_correct": is_correct,
        })

    print(f"\nSupabaseの `match_predictions` へ予測結果を保存中... (全 {len(records_to_upsert)} 件)")
    
    # バッチ100件ずつ upsert
    batch_size = 100
    saved_count = 0
    for b in range(0, len(records_to_upsert), batch_size):
        batch = records_to_upsert[b : b + batch_size]
        res = client.table("match_predictions").upsert(batch, on_conflict="match_key,model_version").execute()
        if res.data:
            saved_count += len(res.data)

    print(f"✔ 完了: {saved_count} 件の予測データを Supabase に正常保存しました！")


if __name__ == "__main__":
    client = get_client()
    df_raw = fetch_all_match_features(client)
    df_prepared, feature_cols = prepare_dataset(df_raw)
    model = train_model(df_prepared, feature_cols)
    generate_predictions_and_save(client, model, df_prepared, feature_cols)