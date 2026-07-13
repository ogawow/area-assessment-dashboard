# エリア別 査定・成約ダッシュボード

中古車買取業務の週次統計ビューアー。BigQuery の集計テーブルを毎日自動取得し、
GitHub Pages 上の静的ダッシュボードとして公開する。

- 公開URL: https://ogawow.github.io/area-assessment-dashboard/
- データソース: `buddica-direct.rds_postgres_prod.weekly_valuation_summary_api`
  (BigQuery スケジュールクエリが毎日 18:30 JST に WRITE_TRUNCATE で更新)

## アーキテクチャ

```
BigQuery スケジュールクエリ (毎日 18:30 JST)
  └→ weekly_valuation_summary_api テーブル更新
GitHub Actions (毎日 19:00 JST, .github/workflows/update-data.yml)
  └→ Workload Identity 連携でキーレス認証 (サービスアカウントキー無し)
  └→ scripts/update_data.py がテーブルを SELECT し data/*.json を再生成
  └→ 変更があれば commit & push → GitHub Pages が自動再デプロイ
ブラウザ (index.html ほか)
  └→ fetch('data/weekly.json') 等で描画。BigQueryへの直接アクセスは一切無し
```

「Webサイト → BigQuery のライブAPI」ではなく「日次バッチで静的JSONを焼き込む」構成。
データ更新が1日1回(18:30)なので鮮度は同等で、以下の利点がある:

- **認証情報がブラウザに一切出ない**(最重要)。BigQueryに触るのはGitHub Actionsだけ
- サーバー/Cloud Run等の運用・課金が無い。BigQueryクエリも1日1回だけ
- サービスアカウントは「集計テーブル1本の読み取り」のみ。生データ(個人情報を含む
  `buddica_users` 等)には権限が無い
- キーレス(Workload Identity)なのでキー漏洩・ローテーションの心配が無い

## ファイル構成

| パス | 役割 |
|---|---|
| `index.html` | 週別 査定ダッシュボード (data/weekly.json を fetch) |
| `monthly-dashboard.html` | 月別ダッシュボード (data/monthly.json) |
| `seiyaku-dashboard.html` | 査定・成約ファネル (data/seiyaku.json) |
| `data/weekly_valuation_summary.json` | 集計テーブル全行のJSON。他システムからのAPI代わりに使える |
| `scripts/update_data.py` | BigQuery→JSON 変換スクリプト |
| `scripts/zip3_to_pref.json` | 郵便番号上3桁→都道府県 (日本郵便 ken_all から生成) |
| `.github/workflows/update-data.yml` | 日次更新ワークフロー |
| `setup/gcp-setup.sh` | GCP側の初期設定スクリプト (1回だけ) |

## セットアップ (残作業)

コード側は完成済み。GCP権限だけオーナー作業が必要:

1. **GCP**: `buddica-direct` のIAM管理権限があるアカウントで Cloud Shell を開き、
   `setup/gcp-setup.sh` の中身を貼り付けて実行
2. **GitHub**: スクリプトが最後に表示する `GCP_WIF_PROVIDER` と `GCP_SA_EMAIL` を
   リポジトリのシークレットに設定 (表示される `gh secret set` コマンドでも可)
3. **動作確認**: リポジトリの Actions タブ → "Update dashboard data" → Run workflow

シークレット未設定の間、ワークフローは何もせず正常終了する(エラー通知は来ない)。

## ローカル検証

```bash
# CSVフィクスチャから生成 (BigQuery不要)
python3 scripts/update_data.py --from-csv scripts/fixture_sample.csv --out data

# 実データから生成 (buddica-direct に読み取り権限のあるアカウントで)
gcloud auth application-default login
pip install google-cloud-bigquery
python3 scripts/update_data.py --out data

# 表示確認
python3 -m http.server 8000  # → http://localhost:8000
```

## 注意

- このリポジトリと GitHub Pages は**公開**。data/*.json も誰でも取得できる
  (従来のHTML埋め込みデータと同じ公開範囲)。非公開にしたくなったら
  Vercel + パスワード保護等への移設が必要
- 集計テーブルは WRITE_TRUNCATE (洗い替え) だがテーブル自体は再作成されないため、
  テーブル単位のIAMは維持される。スケジュールクエリを「テーブル削除→再作成」方式に
  変えた場合は権限が消えるので注意
- 月別ページの数値は「週の開始日(月曜)が属する月」への集約。日単位の月集計とは
  月境界の週で最大6日分ずれる
