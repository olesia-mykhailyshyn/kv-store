#!/usr/bin/env bash
# Loads AWS sandbox credentials from lab-credentials.ini into the "kse-lab" AWS CLI profile.
# Usage: paste the block from "AWS Details -> AWS CLI -> Show" into lab-credentials.ini,
# then run ./scripts/lab-login.sh
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="lab-credentials.ini"
PROFILE="kse-lab"
REGION="us-east-1"

if [ ! -f "$SRC" ]; then
  echo "No $SRC. Copy lab-credentials.example.ini to $SRC and paste the keys from AWS Details." >&2
  exit 1
fi

# Value of "name=value" from the credentials file; everything after the first "=" is kept.
value() { grep -E "^$1=" "$SRC" | cut -d= -f2- | tr -d '\r'; }

for key in aws_access_key_id aws_secret_access_key aws_session_token; do
  v="$(value "$key")"
  if [ -z "$v" ] || [ "$v" = "PASTE_HERE" ]; then
    echo "$key is missing in $SRC" >&2
    exit 1
  fi
  aws configure set "$key" "$v" --profile "$PROFILE"
done

aws configure set region "$REGION" --profile "$PROFILE"
aws configure set output json --profile "$PROFILE"

echo "Saved profile $PROFILE. Checking who we are:"
aws sts get-caller-identity --profile "$PROFILE"
