#!/usr/bin/env bash
# Checks the kv-readonly policy from infra/iam-readonly.yaml with the IAM policy simulator,
# without creating anything in AWS. Fails if any action gets an unexpected decision.
# Usage: ./scripts/check-iam-readonly.sh   (needs a valid kse-lab profile: ./scripts/lab-login.sh)
set -euo pipefail

cd "$(dirname "$0")/.."

PROFILE="kse-lab"
TEMPLATE="infra/iam-readonly.yaml"

# The policy document is plain YAML (no CloudFormation functions inside), so Ruby's YAML parser can read it.
POLICY=$(ruby -ryaml -rjson -e \
  'puts JSON.generate(YAML.safe_load(File.read(ARGV[0]))["Resources"]["ReadOnlyPolicy"]["Properties"]["PolicyDocument"])' \
  "$TEMPLATE")

# A second policy that allows everything in S3, to prove the Deny still wins over a broader Allow.
BROAD_S3='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"s3:*","Resource":"*"}]}'

failures=0

# check <expected decision> <policy JSON...> -- <action...>
check() {
  local expected="$1"; shift
  local policies=()
  while [ "$1" != "--" ]; do policies+=("$1"); shift; done
  shift
  local output answered=0
  # A failed call or a missing answer must fail the check, not look like "no errors".
  if ! output=$(aws iam simulate-custom-policy --profile "$PROFILE" \
                  --policy-input-list "${policies[@]}" --action-names "$@" \
                  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text); then
    echo "FAIL aws iam simulate-custom-policy failed"
    failures=$((failures + $#))
    return
  fi
  while read -r action decision; do
    [ -n "$action" ] || continue
    answered=$((answered + 1))
    if [ "$decision" = "$expected" ]; then mark="ok"; else mark="FAIL"; failures=$((failures + 1)); fi
    printf '%-4s %-45s %-13s (expected %s)\n' "$mark" "$action" "$decision" "$expected"
  done <<< "$output"
  if [ "$answered" -ne "$#" ]; then
    echo "FAIL asked about $# actions, got $answered answers"
    failures=$((failures + 1))
  fi
}

echo "== View infrastructure: allowed"
check allowed "$POLICY" -- \
  ec2:DescribeVpcs ec2:DescribeSubnets ec2:DescribeRouteTables cloudformation:DescribeStacks \
  dynamodb:DescribeTable s3:ListAllMyBuckets s3:GetEncryptionConfiguration lambda:ListFunctions \
  cloudwatch:GetMetricData iam:GetRole servicequotas:ListServiceQuotas

echo "== Change anything: implicitDeny (no Allow)"
check implicitDeny "$POLICY" -- \
  ec2:CreateVpc ec2:TerminateInstances cloudformation:DeleteStack s3:PutObject \
  dynamodb:PutItem lambda:UpdateFunctionCode iam:AttachRolePolicy iam:CreateRole

echo "== Read data or secrets: explicitDeny (guardrail)"
check explicitDeny "$POLICY" -- \
  s3:GetObject s3:ListBucket dynamodb:GetItem dynamodb:Query dynamodb:Scan \
  secretsmanager:GetSecretValue kms:Decrypt ssm:GetParameter logs:GetLogEvents \
  lambda:GetFunction lambda:GetFunctionConfiguration ec2:DescribeInstanceAttribute \
  ec2:GetConsoleOutput ec2:GetLaunchTemplateData ssm:GetParameterHistory logs:GetLogRecord

echo "== Our policy + a policy allowing s3:*: data still explicitDeny"
check explicitDeny "$POLICY" "$BROAD_S3" -- s3:GetObject s3:ListBucket

echo
if [ "$failures" -eq 0 ]; then
  echo "All checks passed."
else
  echo "$failures check(s) failed." >&2
  exit 1
fi
