# J-League Data Viewer — プロジェクト概要

最終更新: 2026-08-29

## このプロジェクトが何か

J1/J2/J3の試合データ・チーム集計データを自動取得してSupabaseに蓄積し、
Streamlitのマルチページアプリで「チーム軸の勝敗予測」と「節軸の日程・結果閲覧」の
2つの視点から扱えるようにしたツール。

アプリ名: **J-League Data Viewer**
- Team Basis: チーム×対戦相手を選んで重み付け勝敗予測を行うページ
- Schedule Basis: シーズン・大会・節を選ぶとその節の全カード(結果/予定)が一覧できるページ

現状は「基本形が完成し、実運用しながらUI・精度を磨いているフェーズ」。

## 全体構成

```
data.j-league.or.jp (公式データサイト)
  ├─ SFMS01 (日程・結果)      → J3fetch.py
  └─ SFRT08 (チーム別集計)    → sfrt08fetch.py
        ↓ (GitHub Actions が定期実行)
Supabase:
  jleague_matches (RAW)
  team_season_stats (RAW)
  team_master (チーム名表記ゆれ吸収マスタ)
        ↓ SQL VIEW (自動集計、ETL不要)
  team_match_long / standings / recent_form / home_away_splits
  team_match_progress / team_section_standings (節ごとの順位変動)
  team_season_stats_joined (team_masterで名寄せ済み)
        ↓ 読み取り (anon key)
Streamlit Cloud: app.py (ルーター) → pages/team_basis.py, pages/schedule_basis.py
```

## リポジトリ / インフラ

- GitHub: `hiramichelle/JLeague` (Public)
- Supabase: RLS有効。`anon`キーで読み取り、`service_role`キーでActionsから書き込み
- Streamlit Cloud: デプロイ済み
- GitHub Actions: `.github/workflows/fetch.yml` で定期実行(平日1日2回、土日は毎時)

## ファイル構成

```
JLeague/
├── app.py                    # st.navigationによるルーター(エントリポイント)
├── pages/
│   ├── team_basis.py         # チーム軸: 勝敗予測ツール本体
│   └── schedule_basis.py     # 節軸: 日程・結果一覧(シンプルな表)
├── J3fetch.py                # 日程・結果取得(J1/J2/J3対応、TARGETSリスト方式)
├── sfrt08fetch.py            # チーム別集計取得(シュート数等)
├── requirements.txt          # streamlit / supabase / pandas / plotly / streamlit-shadcn-ui
├── .gitignore                # secrets.toml等を除外
├── .streamlit/
│   └── secrets.toml          # ローカル用シークレット (Git管理外)
└── .github/workflows/
    └── fetch.yml             # 定期実行ワークフロー(J3fetch.py → sfrt08fetch.py の順に実行)
```

Supabase側テーブル/VIEW一覧 (SQLはコード管理していないので、変更時はSQL Editorで実行した内容をメモ推奨):

| 名前 | 種別 | 役割 |
|---|---|---|
| `jleague_matches` | RAWテーブル | 日程・結果。主キー的役割は`match_key`(試合日+ホーム+アウェイのハッシュ) |
| `team_season_stats` | RAWテーブル | SFRT08由来のチーム集計(シュート数等)。`official_name`はSFRT08表記のまま格納 |
| `team_master` | マスタテーブル | `club_slug`(不変キー)に`short_name`(jleague_matches表記)と`official_name`(SFRT08表記)を紐付け |
| `team_match_long` | VIEW | 1試合をhome/away2行に展開 |
| `standings` | VIEW | 最終順位表(season, competitionごとにrank) |
| `recent_form` | VIEW | 直近5試合のフォーム |
| `home_away_splits` | VIEW | ホーム/アウェイ別の勝率・平均得失点 |
| `team_match_progress` | VIEW | 1試合ごとの累積成績(節番号付き、全角数字対応で節ラベルから抽出) |
| `team_section_standings` | VIEW | 節番号ごとの順位スナップショット(forward-fill方式で延期試合にも対応) |
| `team_season_stats_joined` | VIEW | `team_season_stats`を`team_master`経由で名寄せし、得点率/失点率を算出 |

## 主要な設計判断とその理由

### 1. RAWとVIEWを分離した
`jleague_matches`/`team_season_stats`には取得結果をそのまま保存し、集計・分析は
すべてSQLの`VIEW`で行う。RAWが更新されるたびに自動で最新化され、別途バッチ処理が不要。

### 2. J1/J2/J3対応は「1リクエスト/大会」で完結する
`competition_years` + `competition_frame_ids`の2パラメータだけでシーズン全節分を
1リクエストで取得できる(`competition_section_ids`のループは不要と判明)。
`competition_frame_ids`対応: J1=1, J2=2, J3=3。
大会追加は`J3fetch.py`内の`TARGETS`リストに1行足すだけでよい。

### 3. チーム名表記ゆれは`club_slug`を真のキーにして吸収する
`jleague_matches.home_team_url`から抽出した`club_slug`(例: `sagamihara`)を不変キーとし、
`short_name`(jleague_matches表記、例: 相模原)と`official_name`(SFRT08表記、例: ＳＣ相模原)を
`team_master`に紐付ける。表記ゆれの実例: 半角/全角アルファベット混在(FC今治 vs ＦＣ今治)、
まれに正式名称そのものが変わるケース(滋賀→レイラック滋賀ＦＣ)。

### 4. 節ラベルは全角数字・特殊表記に対応する正規表現で処理
J-Leagueサイトの節表記は全角数字(`第４節`)かつ`第３６節第１日`のような特殊表記もあるため、
`translate()`で全角→半角変換してから`第(\d+)節`で抽出する。

### 5. 節ごとの順位変動はforward-fill方式で計算
延期試合等でチームごとに消化試合数がズレていても、「その節番号までに消化した直近の試合」を
参照する(`team_section_standings`)ことで対応。

### 6. マルチページ化はst.navigationを採用
`app.py`をルーター専用にし、`pages/`配下の各ページが独立したSidebarを持つ設計。
Team Basis(チーム選択・重み設定)とSchedule Basis(節選択のみ)で必要な入力項目が
全く異なるため、Sidebarが1つに混在しないようにした。
**注意**: `pages/`ディレクトリ内に`app.py`という名前のファイルを重複配置すると
StreamlitAPIExceptionが発生する(実際に発生した詰まりどころ)。`pages/`配下は
ページファイルのみを置くこと。

### 7. Sidebarは入力専用、結果表示はメインエリアに統一
「試合情報(対戦済み結果/対戦予定)」は当初Sidebarに表示していたが、
「Sidebarは入力、結果はメインエリア」という一貫ルールに統一するため
メインエリア最上部に移動した。

### 8. 百年構想リーグ等の非対応方針
「data.j-league.or.jpと同じテーブル構造(thead/tbody)で取れるか」を唯一の判断基準とする。
取れないソースを無理に統合しない。

### 9. Secrets管理
- Supabaseキーは`anon` `public`キーを使用(RLS有効前提、`service_role`はアプリ側では使わない)
- `.streamlit/secrets.toml`は`.gitignore`で除外
- GitHub Actions側はSecrets(`SUPABASE_URL`/`SUPABASE_KEY`)に登録し、書き込み権限が
  必要なため`service_role`キーを使用

## 既知の詰まりどころ (再発したら参照)

- **VSCode統合ターミナルでの`git push`認証失敗**: `GIT_ASKPASS`がVSCode拡張のソケットを
  指してしまい`ECONNREFUSED`になることがある。Macの標準ターミナルアプリから実行すると回避できる。
- **`.github/workflows/*.yml`のpush拒否**: Personal Access Tokenに`workflow`スコープが
  無いと拒否される。トークン設定で追加が必要。
- **文字化け**: `resp.encoding = resp.apparent_encoding`は誤判定することがある。
  `resp.encoding = "utf-8"`で固定する。文字化けした行は`match_key`が別物になるため、
  直しても自動上書きされない。`truncate`して取り直すこと。
- **チーム名の半角/全角ズレ**: `team_master.official_name`とSFRT08の実際の表記が
  半角/全角で食い違うとJOINが失敗し、`short_name`がNULLになる(エラーにはならず
  「統計データなし」として静かに失敗するので気づきにくい)。
- **RLS未設定によるempty DataFrame**: VIEW経由のアクセスはRLSを素通りするが、
  テーブル本体(`jleague_matches`等)へ直接クエリすると、`anon`向けSELECTポリシーが
  無い場合は0件になる(エラーにならず空DataFrameが返るだけなので気づきにくい)。
- **`pages/`ディレクトリ内のファイル名重複**: `app.py`を`pages/`配下にも誤って
  置いてしまうと`StreamlitAPIException`が発生する。
- **streamlit-shadcn-ui 1.x系のAPI変更**: 旧バージョンの`ui.metric_card(...)`は
  廃止され、`ui.card(...)`(戻り値はNoneで、それ自体が描画を完了させる。
  `.render()`を追加で呼ぶ必要はない)に変わった。
- **macOS Gatekeeperの隔離属性**: ダウンロードしたスクリプトをホームディレクトリ直下で
  実行すると書き込み権限エラーになることがある(`xattr -d com.apple.quarantine`)。
- **Streamlit非推奨引数**: `use_container_width=True`は将来的に廃止予定。
  `width='stretch'`に置き換え済み。
- **Supabase PostgRESTのデフォルト1000件制限**: `client.table(...).select("*").execute()`は
  1リクエストあたり最大1000件しか返さない。J1+J2+J3合計(約1,140試合)のように
  1000件を超えるテーブルを一括取得すると、後方のデータが静かに切り捨てられる
  (エラーにはならないため気づきにくい)。SQL Editor(管理者権限で直接クエリ)では
  この制限を受けないため、「SQLでは正常なのにアプリ側だけ一部データが欠ける」という
  形で発覚した。対処は`range()`によるページネーションで全件取得すること
  (`jleague_matches`関連のload関数で対応済み)。件数が増え続ける可能性のある
  RAWテーブルを扱う際は常に要注意。

## 今後のアイデア置き場

- 予測モデルの精度検証(どの特徴量・重みが効くか、シーズン序盤はサンプル数不足に注意)
- 過去シーズンデータの追加取り込み
- Head-to-head(直接対決成績)の特徴量化
- ホームアドバンテージの数値をチームごとに変える(スタジアム別など)
- Schedule Basisページの機能拡充(節送り/戻りナビゲーション等)

## 会話ログ的な補足

このCLAUDE.mdは、Claudeとの対話でゼロから本プロジェクトを構築した過程のまとめとして
作成された。過去の詳細な試行錯誤(エラー対応など)はClaude.aiの会話履歴側に残っているため、
細部の再現手順が必要な場合はそちらを参照すると早い。
