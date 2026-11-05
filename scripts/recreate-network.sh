#!/usr/bin/env bash
# Recreates the kv-network stack from infra/network.yaml alone (M1, task 8):
# deletes it if it exists, creates it again, checks it against the network spec,
# and prints how long each step took.
# M2 (task 9.2): the network now has one S3 Gateway Endpoint on the private
# route table; the checks allow exactly that and nothing else leaving the VPC.
# Usage: ./scripts/recreate-network.sh   (needs a valid kse-lab profile: ./scripts/lab-login.sh)
set -euo pipefail

cd "$(dirname "$0")/.."

PROFILE="kse-lab"
STACK="kv-network"
TEMPLATE="infra/network.yaml"
REGION="us-east-1"
S3_SERVICE="com.amazonaws.$REGION.s3"

cfn() { aws cloudformation "$@" --profile "$PROFILE" --region "$REGION"; }
ec2() { aws ec2 "$@" --profile "$PROFILE" --region "$REGION"; }

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

# Values for the S3 endpoint checks. Plain assignments (not inside check "...")
# so that a failing aws command stops the script under set -e.
private_rt=$(ec2 describe-route-tables --filters "Name=vpc-id,Values=$vpc" "Name=tag:Name,Values=$STACK-private-rt" \
  --query "RouteTables[0].RouteTableId" --output text)
s3_pl=$(ec2 describe-prefix-lists --filters "Name=prefix-list-name,Values=$S3_SERVICE" \
  --query "PrefixLists[0].PrefixListId" --output text)
s3_vpce=$(ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=$vpc" "Name=service-name,Values=$S3_SERVICE" \
  --query "VpcEndpoints[0].VpcEndpointId" --output text)
private_count() {   # private_count [<filter like "[?...]">] -> routes in the private table (all if no filter)
  ec2 describe-route-tables --route-table-ids "$private_rt" \
    --query "length(RouteTables[0].Routes${1:-})" --output text
}
endpoint_count() {   # endpoint_count [<filter like "[?...]">] -> endpoints in the VPC (all if no filter)
  ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=$vpc" \
    --query "length(VpcEndpoints${1:-})" --output text
}
stack_status_now=$(stack_status)
public_igw=$(rt_routes "$STACK-public-rt")
private_any_default=$(private_count "[?DestinationCidrBlock=='0.0.0.0/0']")
private_local=$(private_count "[?GatewayId=='local']")
private_s3=$(private_count "[?DestinationPrefixListId=='$s3_pl' && GatewayId=='$s3_vpce']")
private_total=$(private_count)
endpoints_total=$(endpoint_count)
endpoints_s3_gateway=$(endpoint_count "[?VpcEndpointType=='Gateway' && ServiceName=='$S3_SERVICE']")
endpoints_interface=$(endpoint_count "[?VpcEndpointType=='Interface']")
s3_vpce_tables=$(ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=$vpc" "Name=service-name,Values=$S3_SERVICE" \
  --query "VpcEndpoints[].RouteTableIds[]" --output text)
nat_resources=$(cfn list-stack-resources --stack-name "$STACK" \
  --query "length(StackResourceSummaries[?ResourceType=='AWS::EC2::NatGateway'])" --output text)

check "stack status" "CREATE_COMPLETE" "$stack_status_now"
check "public route table: 0.0.0.0/0 -> igw" "1" "$public_igw"
check "private route table: no 0.0.0.0/0 (any target)" "0" "$private_any_default"
check "private route table: local route" "1" "$private_local"
check "private route table: S3 prefix list -> S3 vpce" "1" "$private_s3"
check "private route table: no other routes (local + S3 only)" "2" "$private_total"
check "VPC endpoints in VPC (only the S3 one)" "1" "$endpoints_total"
check "S3 endpoint is Gateway type" "1" "$endpoints_s3_gateway"
check "Interface endpoints (billed hourly)" "0" "$endpoints_interface"
check "S3 endpoint attached to private route table only" "$private_rt" "$s3_vpce_tables"
check "NAT gateways in stack (billed hourly)" "0" "$nat_resources"
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
