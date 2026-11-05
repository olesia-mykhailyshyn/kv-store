#!/usr/bin/env bash
# Copies the sandbox session keys from lab-credentials.ini into GitHub Secrets (ADR-010, task 12.1),
# so the Deploy workflow can reach AWS. Run once at the start of every sandbox session.
# Values go to gh through stdin only: never in argv (visible in `ps`), never printed.
# Usage: ./scripts/push-lab-secrets.sh   (needs `gh auth login` and lab-credentials.ini)
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="lab-credentials.ini"

if [ ! -f "$SRC" ]; then
  echo "No $SRC. Copy lab-credentials.example.ini to $SRC and paste the keys from AWS Details." >&2
  exit 1
fi

# Value of "name=value" from the credentials file (same parsing as lab-login.sh).
value() { grep -E "^$1=" "$SRC" | cut -d= -f2- | tr -d '\r'; }

for key in aws_access_key_id aws_secret_access_key aws_session_token; do
  v="$(value "$key")"
  if [ -z "$v" ] || [ "$v" = "PASTE_HERE" ]; then
    echo "$key is missing in $SRC" >&2
    exit 1
  fi
  # aws_access_key_id -> AWS_ACCESS_KEY_ID (the secret names the workflow reads).
  name="$(echo "$key" | tr '[:lower:]' '[:upper:]')"
  # printf, not echo: no trailing newline inside the secret.
  printf '%s' "$v" | gh secret set "$name" > /dev/null
  echo "set $name"
done

echo "Secrets in the repository (names and dates only):"
gh secret list
