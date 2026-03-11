#!/usr/bin/env bash
# Deploy g3lobster to Google Cloud Run.
#
# Required environment variables:
#   GCP_PROJECT   — Google Cloud project ID
#
# Optional environment variables:
#   GCP_REGION    — Region (default: us-central1)
#   SERVICE_NAME  — Cloud Run service name (default: g3lobster)
#   AR_REPO       — Artifact Registry repo name (default: g3lobster)
#   IMAGE_TAG     — Image tag to deploy (default: latest)
#
# Usage:
#   GCP_PROJECT=my-project ./deploy/cloudrun.sh
#
set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT to your Google Cloud project ID}"

GCP_REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-g3lobster}"
AR_REPO="${AR_REPO:-g3lobster}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}/${SERVICE_NAME}:${IMAGE_TAG}"

echo "==> Building image via Cloud Build..."
gcloud builds submit \
  --project="${GCP_PROJECT}" \
  --config=cloudbuild.yaml \
  --substitutions="_REGION=${GCP_REGION},_REPO=${AR_REPO},_IMAGE=${SERVICE_NAME},SHORT_SHA=${IMAGE_TAG}"

echo "==> Deploying ${IMAGE} to Cloud Run (${SERVICE_NAME})..."
gcloud run deploy "${SERVICE_NAME}" \
  --project="${GCP_PROJECT}" \
  --region="${GCP_REGION}" \
  --image="${IMAGE}" \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080 \
  --set-env-vars="G3LOBSTER_CHAT_ENABLED=false" \
  --startup-probe-path=/health \
  --startup-probe-initial-delay=5

echo "==> Done. Service URL:"
gcloud run services describe "${SERVICE_NAME}" \
  --project="${GCP_PROJECT}" \
  --region="${GCP_REGION}" \
  --format='value(status.url)'
