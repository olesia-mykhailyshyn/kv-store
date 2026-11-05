# Proposal

## Why

M1 дав мережу, але сховища ще нема. M2 будує саме kv-сховище: одна точка входу (coordinator) і дані, поділені між кількома шардами горизонтальним шардингом через consistent hashing. Робимо зараз, бо від M2 залежить усе далі: M3 реплікує ці шарди, M4 моніторить їх, M5 автомасштабує; а бенчмарк YCSB з M2 — baseline, з яким порівнюємо M3 і M5.

## What Changes

- **API-специфікація** (завдання 1): `api/openapi.yaml` (OpenAPI 3.1, REST + JSON) — реєстрація визначення таблиці, CRUD над записами (EXISTS, CREATE, READ, DELETE), складений ключ partition key + sort key, Query за діапазоном sort key, реєстрація шардів. ADR-005, ADR-007.
- **Coordinator** (завдання 2): Python-сервіс, що реалізує API; спершу в single-node режимі (зберігає записи сам), потім маршрутизує на шарди. Визначення таблиць переживають перезапуск (RAM + WAL). ADR-008, ADR-009.
- **Шардинг записів і читань** (завдання 3–4): кільце consistent hashing (MD5, 500 virtual nodes), ключ на кільці `таблиця#partition key`; шарди реєструються при старті й підтверджують себе heartbeat-ом. ADR-006, ADR-009.
- **Шард**: Python-сервіс, що зберігає записи в RAM + append-only WAL на EBS з fsync на кожен запис; після перезапуску відновлюється з WAL. ADR-008.
- **IaC** (завдання 5): стек застосунку (coordinator, 2 шарди в різних AZ, клієнт, security groups), стек бакета для коду; S3 Gateway Endpoint у мережі. ADR-002, ADR-009.
- **CI/CD** (завдання 6): GitHub Actions — CI на кожен push (тести, `cfn-lint`, перевірка OpenAPI), CD кнопкою (мережа → код у S3 → стек застосунку → smoke test). ADR-010.
- **YCSB baseline** (завдання 7): прогін бенчмарку на шардованому сховищі; числа збережені в репозиторії і в S3-бакеті з versioning.
- **Обов'язкові ADR** вже написані: 005 (API), 006 (хеш і vnodes), 007 (partition key); додаткові 008–010.
- README, демо, контрольні питання і транскрипт AI-критики — наприкінці M2.
- Бонусні завдання (throttling, кеш) — **не в M2**, під час M3.

## Capabilities

### New Capabilities
- `kv-api`: публічний контракт сховища — таблиці, записи, складений ключ, Query, коди відповідей і формат помилок; збереження визначень таблиць.
- `sharding`: розподіл ключів між шардами через consistent hashing, маршрутизація записів і читань, реєстрація шардів і heartbeat.
- `shard-storage`: як шард зберігає записи — довговічність підтвердженого запису, відновлення після перезапуску, впорядкування за sort key.
- `app-infrastructure`: розгортання застосунку в AWS з IaC — розміщення серверів, мережевий доступ між компонентами, доставка коду, ліміти пісочниці.
- `delivery-pipeline`: CI/CD — автоматичні перевірки кожної зміни і деплой інфраструктури й застосунку з перевіркою працездатності.
- `benchmark-baseline`: відтворюваний бенчмарк YCSB і збереження результатів для порівняння в M3 і M5.

### Modified Capabilities
- `network`: приватні підмережі отримують безкоштовний маршрут до S3 (Gateway Endpoint) — змінюється вимога «приватні підмережі лише з маршрутом `local`». Вимога M1 знаходиться в незаархівованій зміні `add-m1-infrastructure`; див. design.

## Impact

- **Новий код:** `api/openapi.yaml`; `src/` (coordinator, шард, кільце, WAL — Python, лише стандартна бібліотека); `tests/`; `infra/app.yaml`, `infra/artifacts.yaml`; `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`; `scripts/` (оновлення GitHub Secrets, YCSB).
- **Змінений код:** `infra/network.yaml` (S3 Gateway Endpoint), `scripts/recreate-network.sh` (перевірки), README.
- **AWS (пісочниця, us-east-1):** 4 EC2 (3 × `t3.micro`, 1 × `t3.small`) з 9; EBS `gp2`; S3-бакети для коду і результатів YCSB; S3 Gateway Endpoint ($0). Оцінка ≈ $2.66 за 40 год сесій ([`docs/m1/budget-m2.md`](../../../docs/m1/budget-m2.md)).
- **Залежності:** на серверах — лише Python 3 зі стандартною бібліотекою; у CI — `pytest`, `cfn-lint`, валідатор OpenAPI; на клієнті — Java + YCSB.
- **GitHub:** Actions, Secrets (ключі сесії, оновлюються щосесії вручну).
- **Наступні майлстоуни:** WAL шарда стає логом реплікації M3; heartbeat — основа health-check M5; кільце — основа ребалансу M5.
