#!/usr/bin/env bash
# The same deploy sequence as .github/workflows/deploy.yml, run from the Mac (task 10.3:
# the first manual deploy, before CD is trusted). Uses the kse-lab profile (./scripts/lab-login.sh).
#   1. kv-network (no change -> "No changes")  2. kv-artifacts
#   3. code archive -> s3://<code bucket>/code/<version>.zip
#   4. delete kv-app if it exists, create it again; create waits for the smoke-test signal
# Usage: ./scripts/deploy-local.sh [ADMIN_CIDR]
#   ADMIN_CIDR defaults to this Mac's public IP /32 (SSH to the client EC2).
set -euo pipefail

cd "$(dirname "$0")/.."

export AWS_PROFILE="${AWS_PROFILE:-kse-lab}"
export AWS_REGION="us-east-1"

ADMIN_CIDR="${1:-$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')/32}"

# Code version = git commit, like in CD. Uncommitted changes get a "-local-<time>" suffix,
# so an archive never pretends to be a commit it is not.
if CODE_VERSION="$(git rev-parse HEAD 2>/dev/null)"; then
  if [ -n "$(git status --porcelain -- src)" ]; then
    CODE_VERSION="$CODE_VERSION-local-$(date +%Y%m%d%H%M%S)"
  fi
else
  CODE_VERSION="local-$(date +%Y%m%d%H%M%S)"
fi

echo "== profile $AWS_PROFILE, code version $CODE_VERSION, admin CIDR $ADMIN_CIDR"
aws sts get-caller-identity --query '[Account, Arn]' --output text

echo "== 1. kv-network"
aws cloudformation deploy --stack-name kv-network \
  --template-file infra/network.yaml --no-fail-on-empty-changeset

echo "== 2. kv-artifacts"
aws cloudformation deploy --stack-name kv-artifacts \
  --template-file infra/artifacts.yaml --no-fail-on-empty-changeset

echo "== 3. code archive"
bucket="$(aws cloudformation list-exports \
  --query "Exports[?Name=='kv-artifacts-CodeBucket'].Value" --output text)"
if [ -z "$bucket" ] || [ "$bucket" = "None" ]; then
  echo "Export kv-artifacts-CodeBucket not found" >&2
  exit 1
fi
mkdir -p build
rm -f build/code.zip
# Top level of the zip is the package dir kvstore/.
(cd src && zip -q -r ../build/code.zip kvstore -x '*__pycache__*' '*.pyc')
aws s3 cp build/code.zip "s3://$bucket/code/$CODE_VERSION.zip"

echo "== 4. replace kv-app"
if aws cloudformation describe-stacks --stack-name kv-app > /dev/null 2>&1; then
  echo "kv-app exists, deleting"
  aws cloudformation delete-stack --stack-name kv-app
  aws cloudformation wait stack-delete-complete --stack-name kv-app
fi

if ! aws cloudformation deploy --stack-name kv-app --template-file infra/app.yaml \
  --parameter-overrides "CodeVersion=$CODE_VERSION" "AdminCidr=$ADMIN_CIDR"; then
  echo "kv-app failed. Failed events:" >&2
  aws cloudformation describe-stack-events --stack-name kv-app \
    --query "StackEvents[?contains(ResourceStatus, 'FAILED')].[LogicalResourceId, ResourceStatus, ResourceStatusReason]" \
    --output table >&2 || true
  exit 1
fi

echo "== outputs"
aws cloudformation describe-stacks --stack-name kv-app --query 'Stacks[0].Outputs' --output table
