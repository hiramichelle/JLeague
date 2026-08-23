# J-League 勝敗予想ツール — プロジェクト概要

最終更新: 2026-08-23

## このプロジェクトが何か

J1/J2/J3の試合データを自動取得し、Supabaseに蓄積し、Streamlitアプリで
ホーム/アウェイチームを選んで重み付けの勝敗予測を行うツール。

現状は「基本形が完成し、実運用しながら精度検証をしているフェーズ」。

## 全体構成

```
data.j-league.or.jp (公式データサイト)
        ↓ J3fetch.py (GitHub Actions が定期実行)
Supabase: jleague_matches (RAWテーブル)
        ↓ SQL VIEW (自動集計、ETL不要)
Supabase: team_match_long / standings / recent_form
        ↓ 読み取り (anon key)
Streamlit Cloud: app.py (勝敗予測UI)
```

## リポジトリ / インフラ

- GitHub: `hiramichelle/JLeague` (Public)
- Supabase: プロジェクト作成済み、RLS有効
- Streamlit Cloud: デプロイ済み (`app.py` が起動ファイル)
- GitHub Actions: `.github/workflows/fetch.yml` で定期実行

## ファイル構成

```
JLeague/
├── J3fetch.py              # データ取得スクリプト (J1/J2/J3対応版)
├── app.py                  # Streamlit勝敗予測アプリ
├── requirements.txt        # streamlit / supabase / pandas (バージョン固定済み)
├── .gitignore              # secrets.toml等を除外
├── .streamlit/
│   └── secrets.toml        # ローカル用シークレット (Git管理外)
└── .github/workflows/
    └── fetch.yml           # 定期実行ワークフロー
```

Supabase側 (SQLはコード管理していないので、変更時はSQL Editorで実行した内容をメモ推奨):
- `jleague_matches` — RAWテーブル。主キー的役割は `match_key` (試合日+ホーム+アウェイのハッシュ)
- `team_match_long` — 1試合を home/away 2行に展開したVIEW
- `standings` — チーム別の順位表VIEW (season, competition ごとにrank)
- `recent_form` — 直近5試合のフォームVIEW

## 主要な設計判断とその理由

### 1. RAWとVIEWを分離した
`jleague_matches` には取得結果をそのまま保存し、順位表や直近フォームは
すべてSQLの `VIEW` で計算している。理由: RAWが更新されるたびに自動で
最新化され、別途バッチ処理が不要になるため。

### 2. J1/J2/J3対応は「1リクエスト/大会」で完結する
`competition_years` + `competition_frame_ids` の2パラメータだけで
シーズン全節分を1リクエストで取得できることが判明した (以前使っていた
`competition_section_ids` のループは不要だった)。
`competition_frame_ids` の対応: J1=1, J2=2, J3=3, 百年構想リーグ=11 (推定), その他=40

大会を追加したくなったら `J3fetch.py` 内の `TARGETS` リストに
`{"label": "...", "competition_frame_ids": N}` を1行足すだけでよい。

### 3. テーブル/VIEW名は大会名に依存させていない
`jleague_matches` はJ3専用ではなく、`season`/`competition`列で
大会を区別する汎用設計。そのため J1/J2 を追加した際もテーブル新設や
リネームは一切不要だった。`app.py` のサイドバーも
`standings_df["competition"].unique()` で動的に選択肢を作っているため
追加対応コードなしでJ1/J2が使えるようになっている。

### 4. 百年構想リーグ等の非対応方針
「data.j-league.or.jp と同じテーブル構造(thead/tbody)で取れるか」を
唯一の判断基準とする。取れないソース(別サイト等)を無理に統合しない。
将来的にどうしても必要になった場合は、別のRAWテーブルを追加し、
`team_match_long` 相当のVIEWをUNIONで束ねる設計にする
(app.py側の変更は不要になるよう設計する)。

### 5. Secrets管理
- Supabaseキーは `anon` `public` キーを使用 (RLS有効前提、`service_role` は使わない)
- `.streamlit/secrets.toml` は `.gitignore` で除外
- GitHub Actions側は Secrets (`SUPABASE_URL` / `SUPABASE_KEY`) に登録し、
  こちらは書き込み権限が必要なため `service_role` キーを使用している

## 既知の詰まりどころ (再発したら参照)

- **VSCode統合ターミナルでの `git push` 認証失敗**: `GIT_ASKPASS` が
  VSCode拡張のソケットを指してしまい `ECONNREFUSED` になることがある。
  Macの標準ターミナルアプリから実行すると回避できる。
- **`.github/workflows/*.yml` のpush拒否**: Personal Access Tokenに
  `workflow` スコープが無いと拒否される。トークン設定で追加が必要。
- **文字化け**: `resp.encoding = resp.apparent_encoding` は誤判定することがある。
  `resp.encoding = "utf-8"` で固定する。文字化けした行は `match_key` が
  別物になるため、直しても自動上書きされない。`truncate` して取り直すこと。
- **macOS Gatekeeperの隔離属性**: ダウンロードしたスクリプトをホームディレクトリ
  直下で実行すると書き込み権限エラーになることがある (`xattr -d com.apple.quarantine`)。

## 今後のアイデア置き場 (思いついたらここに追記していく想定)

- 予測モデルの精度検証 (どの特徴量・重みが効くか)
- 過去シーズンデータの追加取り込み
- Head-to-head (直接対決成績) の特徴量化
- ホームアドバンテージの数値をチームごとに変える (スタジアム別など)
- plotly等によるUIのリッチ化

## 会話ログ的な補足

このCLAUDE.mdは、Claudeとの対話でゼロから本プロジェクトを構築した過程の
まとめとして作成された。過去の詳細な試行錯誤 (エラー対応など) は
Claude.aiの会話履歴側に残っているため、細部の再現手順が必要な場合は
そちらを参照すると早い。
