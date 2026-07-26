#!/usr/bin/env bash
# One-shot (not idempotent — like the other infra scripts): KMS keyring + key for BYOK
# credential encryption (core/byok.py), and the runtime SA grant. Run once by the
# operator. (Numbered 10, not 9 — 09-prod-secrets.sh already claims that slot.)
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

gcloud kms keyrings create librarian --location=us-central1 --project="${PROJECT_ID}"
gcloud kms keys create byok-credentials --location=us-central1 --keyring=librarian \
  --purpose=encryption --project="${PROJECT_ID}"
gcloud kms keys add-iam-policy-binding byok-credentials \
  --location=us-central1 --keyring=librarian --project="${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/cloudkms.cryptoKeyEncrypterDecrypter"

echo "KMS keyring 'librarian' / key 'byok-credentials' created; ${RUNTIME_SA} granted encrypt/decrypt."
echo "Set repo VARIABLE (or confirm) GCP_PROJECT_ID=${PROJECT_ID} — deploy.yml derives KMS_KEY_NAME from it."
