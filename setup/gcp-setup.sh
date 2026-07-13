#!/usr/bin/env bash
# =============================================================================
# GCP側セットアップ (1回だけ実行)
#
# 実行者: buddica-direct プロジェクトの IAM 管理権限を持つアカウント
# 実行場所: Cloud Shell 推奨 (https://console.cloud.google.com/ 右上の >_ アイコン)
#           プロジェクトを buddica-direct に切り替えてから、このファイルの内容を
#           まるごと貼り付けて実行してください。
#
# やること:
#   1. ダッシュボード専用の読み取り用サービスアカウントを作成
#   2. 権限を最小限に付与 (クエリ実行 + 対象テーブル1本の読み取りのみ)
#   3. GitHub Actions からキーレスで認証できるように Workload Identity 連携を作成
#      (サービスアカウントキーを一切発行しない = 漏洩リスクを持たない構成)
#   4. 最後に GitHub リポジトリに設定する2つのシークレット値を表示
# =============================================================================
set -euo pipefail

PROJECT="buddica-direct"
REPO="ogawow/area-assessment-dashboard"   # GitHubリポジトリ (owner/name)
SA_NAME="dashboard-reader"
POOL="github-pool"
PROVIDER="github-provider"
DATASET="rds_postgres_prod"
TABLE="weekly_valuation_summary_api"

gcloud config set project "$PROJECT"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

# --- 1. サービスアカウント作成 -----------------------------------------------
gcloud iam service-accounts create "$SA_NAME" \
  --display-name="Weekly dashboard read-only (GitHub Actions)" || true

# --- 2. 最小権限付与 ----------------------------------------------------------
# クエリを実行する権限 (プロジェクトレベルで必要)
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/bigquery.jobUser" \
  --condition=None >/dev/null

# データ読み取りは「集計済みテーブル1本だけ」に限定 (生データには触れない)
bq add-iam-policy-binding \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/bigquery.dataViewer" \
  "${PROJECT}:${DATASET}.${TABLE}"

# --- 3. Workload Identity 連携 (キーレス) ------------------------------------
gcloud iam workload-identity-pools create "$POOL" \
  --location=global --display-name="GitHub Actions" || true

gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --location=global \
  --workload-identity-pool="$POOL" \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'" || true

# このリポジトリのActionsだけがSAを名乗れるようにする
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" >/dev/null

# --- 4. GitHub に設定する値を表示 ---------------------------------------------
WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo ""
echo "============================================================"
echo "セットアップ完了。以下の2つを GitHub リポジトリのシークレットに設定してください。"
echo "(リポジトリ → Settings → Secrets and variables → Actions → New repository secret)"
echo ""
echo "  GCP_WIF_PROVIDER = ${WIF_PROVIDER}"
echo "  GCP_SA_EMAIL     = ${SA_EMAIL}"
echo ""
echo "gh CLI を使う場合は、手元のターミナルで:"
echo "  gh secret set GCP_WIF_PROVIDER -R ${REPO} -b '${WIF_PROVIDER}'"
echo "  gh secret set GCP_SA_EMAIL -R ${REPO} -b '${SA_EMAIL}'"
echo "============================================================"
