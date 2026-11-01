# M1: сценарій демо

Приблизно 5 хвилин. Перед початком: **Start Lab**, свіжі ключі в `lab-credentials.ini`, термінал у папці `kv-store`. Ключі на екрані не показувати.

| # | Що показати | Команда | Що має бути |
|---|---|---|---|
| 1 | Хто ми | `./scripts/lab-login.sh` | `assumed-role/voclabs/…` |
| 2 | Мережа зникає й повертається лише з коду | `./scripts/recreate-network.sh` | ≈ 100 с, 11 рядків `ok`, «All checks passed.» |
| 3 | Мережа в AWS | консоль: VPC → Your VPCs, Subnets, Route tables | `kv-network-vpc`, 6 підмереж; маршрут до IGW лише в `kv-network-public-rt` |
| 4 | Логіка read-only політики | `./scripts/check-iam-readonly.sh` | 37 рядків `ok`, «All checks passed.» |
| 5 | Пісочниця не дає створити IAM | `aws iam create-role --role-name kv-probe-should-fail --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' --profile kse-lab` | `AccessDenied … no identity-based policy allows the iam:CreateRole action` |
| 6 | Бюджет M2 | `python3 scripts/m2_budget.py` | таблиці кошторису, варіант А: $45.01 за 24/7 і $2.66 за 40 год (суми можуть змінитися разом із прайсами AWS) |

Кроки 1, 2, 4, 5 і 6 окремо запускали 2026-11-01. Весь сценарій підряд ще не проганяли, тому варто зробити це перед захистом.
