#!/usr/bin/env bash
# Recreates the kv-network stack from infra/network.yaml alone (M1, task 8):
# deletes it if it exists, creates it again, checks it against the network spec,
# and prints how long each step took.
# Usage: ./scripts/recreate-network.sh   (needs a valid kse-lab profile: ./scripts/lab-login.sh)
set -euo pipefail

cd "$(dirname "$0")/.."

PROFILE="kse-lab"
STACK="kv-network"
TEMPLATE="infra/network.yaml"

cfn() { aws cloudformation "$@" --profile "$PROFILE"; }
ec2() { aws ec2 "$@" --profile "$PROFILE"; }

stack_status() {
  cfn describe-stacks --stack-name "$STACK" --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "NONE"
}

echo "== 0. Validate template"
cfn validate-template --template-body "file://$TEMPLATE" > /dev/null
echo "ok"

echo "== 1. Tear down"
delete_seconds=0
status=$(stack_status)
if [ "$status" = "NONE" ]; then
  echo "stack $STACK does not exist, nothing to delete"
else
  echo "stack $STACK is $status, deleting"
  start=$(date +%s)
  cfn delete-stack --stack-name "$STACK"
  cfn wait stack-delete-complete --stack-name "$STACK"
  delete_seconds=$(( $(date +%s) - start ))
  echo "deleted in ${delete_seconds}s"
fi

echo "== 2. Bring back from IaC"
start=$(date +%s)
cfn deploy --template-file "$TEMPLATE" --stack-name "$STACK" \
  --tags Project=kv-store Milestone=m1 --no-fail-on-empty-changeset
create_seconds=$(( $(date +%s) - start ))
echo "created in ${create_seconds}s, status $(stack_status)"

echo "== 3. Check against the network spec"
failures=0
check() {   # check <description> <expected> <actual>
  if [ "$2" = "$3" ]; then mark="ok"; else mark="FAIL"; failures=$((failures + 1)); fi
  printf '%-4s %-62s expected %-6s got %s\n' "$mark" "$1" "$2" "$3"
}

vpc=$(cfn describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='VpcId'].OutputValue" --output text)
rt_routes() {   # rt_routes <route table Name tag> -> routes to 0.0.0.0/0 through an IGW
  ec2 describe-route-tables --filters "Name=vpc-id,Values=$vpc" "Name=tag:Name,Values=$1" \
    --query "length(RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0' && starts_with(GatewayId,'igw-')])" --output text
}

rt_subnets() {   # rt_subnets <route table Name tag> <subnet Name prefix> -> associated subnets of that tier
  local ids
  ids=$(ec2 describe-route-tables --filters "Name=vpc-id,Values=$vpc" "Name=tag:Name,Values=$1" \
    --query "RouteTables[0].Associations[?SubnetId].SubnetId" --output text)
  ec2 describe-subnets --subnet-ids $ids \
    --query "length(Subnets[?starts_with(Tags[?Key=='Name']|[0].Value,'$2')])" --output text
}

check "stack status" "CREATE_COMPLETE" "$(stack_status)"
check "public route table: 0.0.0.0/0 -> igw" "1" "$(rt_routes "$STACK-public-rt")"
check "private route table: no 0.0.0.0/0 -> igw" "0" "$(rt_routes "$STACK-private-rt")"
check "main route table: no route to igw" "0" "$(ec2 describe-route-tables \
  --filters "Name=vpc-id,Values=$vpc" Name=association.main,Values=true \
  --query "length(RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'])" --output text)"
check "main route table: no subnets associated" "0" "$(ec2 describe-route-tables \
  --filters "Name=vpc-id,Values=$vpc" Name=association.main,Values=true \
  --query "length(RouteTables[0].Associations[?SubnetId])" --output text)"
check "public route table: public subnets associated" "3" "$(rt_subnets "$STACK-public-rt" "$STACK-public-")"
check "private route table: private subnets associated" "3" "$(rt_subnets "$STACK-private-rt" "$STACK-private-")"
check "subnets in VPC" "6" "$(ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc" \
  --query 'length(Subnets)' --output text)"
check "subnets with automatic public IP" "0" "$(ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc" \
  --query 'length(Subnets[?MapPublicIpOnLaunch])' --output text)"
check "hourly-billed resources (NAT, VPC endpoints)" "0" "$(cfn list-stack-resources --stack-name "$STACK" \
  --query "length(StackResourceSummaries[?ResourceType=='AWS::EC2::NatGateway' || ResourceType=='AWS::EC2::VPCEndpoint'])" --output text)"
check "exports $STACK-*" "10" "$(cfn list-exports \
  --query "length(Exports[?starts_with(Name,'$STACK-')])" --output text)"

echo "== 4. Summary"
echo "teardown: ${delete_seconds}s   bring back: ${create_seconds}s   total: $((delete_seconds + create_seconds))s"
cfn describe-stacks --stack-name "$STACK" --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output text | sort

if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed." >&2
  exit 1
fi
echo "All checks passed."
