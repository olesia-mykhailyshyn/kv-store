# Tasks

Правило виконання (як у M1): по одному завданню; пояснення → згода Олесі → код/команда з поясненням кожного рядка → Олеся запускає → разом читаємо результат → запис у `docs/m2/journal-uk.md`. Тести пишуться перед кодом, який вони перевіряють. Ключі доступу ніколи не потрапляють у git. Git-команди запускає Олеся.

## 1. Архітектурні рішення (ADR)

- [x] 1.1 ADR-005 «API: REST + JSON, OpenAPI» — `docs/adr/0005-api-protocol-and-format.md`, статус Accepted
- [x] 1.2 ADR-006 «MD5, 500 virtual nodes» з даними експерименту — `docs/adr/0006-hash-function-and-virtual-nodes.md`, статус Accepted
- [x] 1.3 ADR-007 «Стратегія ключів» — `docs/adr/0007-partition-key-strategy.md`, статус Accepted
- [x] 1.4 ADR-008 «Compute і зберігання» — `docs/adr/0008-compute-and-shard-storage.md`, статус Accepted
- [x] 1.5 ADR-009 «Точка входу, метадані, реєстрація» — `docs/adr/0009-entry-point-metadata-registration.md`, статус Accepted
- [x] 1.6 ADR-010 «CI/CD у пісочниці» — `docs/adr/0010-ci-cd-in-sandbox.md`, статус Accepted

## 2. Контракт API (завдання 1)

- [x] 2.1 Написати `api/openapi.yaml` (OpenAPI 3.1): таблиці, записи (CREATE/READ/EXISTS/DELETE), Query, `/shards/register`, `/health`, внутрішнє API шарда (тег `internal`), схема помилки; перевірка: `openapi-spec-validator api/openapi.yaml` без помилок і кожен сценарій `specs/kv-api` має відповідну операцію й код відповіді
- [x] 2.2 Підсумкова таблиця «операція → метод, адреса, коди» у `docs/m2/api-uk.md`; перевірка: Олеся пояснює кожен рядок без нотаток

## 3. Зафіксувати план у git до коду

- [ ] 3.1 `openspec validate add-m2-sharding --strict` проходить; лише з дозволу Олесі — коміт (OpenSpec-зміна, ADR-005…010, `api/openapi.yaml`, експеримент, журнал) і push; перевірка: `git log` показує коміт специфікації раніше за будь-який код у `src/`

## 4. Перевірки пісочниці перед кодом

- [x] 4.1 Версія `python3` в Amazon Linux 2023; перевірка: версія записана в журнал з джерелом, CI налаштовано на неї
- [x] 4.2 Доступ до публічного SSM-параметра AMI (`aws ssm get-parameters --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64`); перевірка: ID AMI або точний текст помилки → рішення (SSM-параметр чи ID AMI параметром стека)

## 5. Зберігання: RAM + WAL (`shard-storage`)

- [ ] 5.1 Тести `tests/test_storage.py` за сценаріями `shard-storage`: перезапис і видалення переживають перезапуск, обрізаний останній рядок відкидається, пошкоджений рядок посередині — помилка, порядок за SK; перевірка: тести запускаються і падають (коду ще нема)
- [ ] 5.2 `src/kvstore/storage.py` (write → flush → fsync → RAM; програвання WAL на старті); перевірка: `pytest tests/test_storage.py` зелений
- [ ] 5.3 Тест «`kill -9` після `201`» на окремому процесі; перевірка: запис читається після перезапуску

## 6. Кільце (`sharding`)

- [ ] 6.1 Тести `tests/test_ring.py`: детермінованість між процесами, баланс 2 шардів у межах 50 ± 5 % на 1 000 000 ключів, при 2→3 переїзд ≤ 1/3 + 5 pp і лише на новий шард, мітки залежать від `id`, не від адреси; перевірка: тести падають
- [ ] 6.2 `src/kvstore/ring.py` за ADR-006; перевірка: `pytest tests/test_ring.py` зелений

## 7. Coordinator у single-node режимі (`kv-api`, завдання 2)

- [ ] 7.1 Інтеграційні тести `tests/test_api_single.py`: усі сценарії `specs/kv-api` проти процесу coordinator `--mode single` на localhost, перевірка відповідей на схеми з `openapi.yaml`; перевірка: тести падають
- [ ] 7.2 `src/kvstore/http_common.py` і `src/kvstore/coordinator.py` (single-node): таблиці + метадані в WAL, CRUD, Query, формат помилок; перевірка: `pytest tests/test_api_single.py` зелений
- [ ] 7.3 Демо single-node `curl`-ами (створити таблицю, PUT, GET, HEAD, Query, DELETE) у `docs/m2/demo.md`; перевірка: команди з файлу дають описані відповіді

## 8. Шарди і шардований coordinator (завдання 3–4)

- [ ] 8.1 Тести `tests/test_api_sharded.py`: coordinator `--mode sharded` + 2 процеси шардів на localhost — сценарії `kv-api` проходять, записи одного PK на одному шарді, реєстрація і heartbeat, зміна адреси шарда не змінює розподіл, після перезапуску coordinator кільце відновлюється ≤ 10 с, зупинений шард → `503`; перевірка: тести падають
- [ ] 8.2 `src/kvstore/shard.py` (внутрішнє API, heartbeat) і маршрутизація в coordinator; перевірка: `pytest` усіх тестів зелений
- [ ] 8.3 Демо: на якому шарді лежить ключ (логи або `/health` шардів з кількістю записів) — у `docs/m2/demo.md`; перевірка: видно розподіл ключів між шардами

## 9. Мережа: S3 Gateway Endpoint (`network`)

- [ ] 9.1 Додати Gateway Endpoint S3 у `infra/network.yaml` (приватна route table); перевірка: `aws cloudformation validate-template` і `cfn-lint` без помилок
- [ ] 9.2 Оновити перевірки `scripts/recreate-network.sh`: дозволити лише Gateway Endpoint S3, приватні маршрути — `local` + prefix list S3; перевірка: скрипт проходить на розгорнутій мережі
- [ ] 9.3 `aws cloudformation deploy` мережі (change set переглядаємо разом); перевірка: сценарії delta-spec `network` (маршрут `pl-…` через `vpce-…`, нема `0.0.0.0/0` у приватних)

## 10. Стеки застосунку (`app-infrastructure`, завдання 5)

- [ ] 10.1 `infra/artifacts.yaml`: бакет коду, бакет результатів з versioning, Outputs/Exports; перевірка: `cfn-lint`, деплой, `aws s3api get-bucket-versioning` = `Enabled`
- [ ] 10.2 `infra/app.yaml`: 4 EC2 за design (рішення 7), security groups за spec, user data (код з S3 → systemd), `CreationPolicy` і smoke test на клієнті; перевірка: `cfn-lint` без помилок
- [ ] 10.3 Ручний перший деплой у сесії (архів коду → S3 → `kv-app`); перевірка: стек `CREATE_COMPLETE`, smoke test надіслав успішний `cfn-signal`
- [ ] 10.4 Перевірити сценарії `app-infrastructure`: розміщення і AZ (`describe-instances`), нема `0.0.0.0/0` у SG coordinator, клієнт не дістає шард (`curl` з клієнта на IP шарда — тайм-аут), ≤ 4 EC2 і нема NAT/Interface Endpoint/LB; перевірка: вивід у журналі

## 11. CI (`delivery-pipeline`, завдання 6)

- [ ] 11.1 `.github/workflows/ci.yml`: Python з 4.1, `pytest`, `openapi-spec-validator`, `cfn-lint`; на push і pull request; перевірка: зелений запуск на GitHub
- [ ] 11.2 Навмисно зламати тест кільця в гілці → CI червоний → повернути; перевірка: посилання на обидва запуски в журналі

## 12. CD (`delivery-pipeline`, завдання 6)

- [ ] 12.1 `scripts/push-lab-secrets.sh`: ключі з `lab-credentials.ini` → `gh secret set` без виводу значень; перевірка: `gh secret list` показує 3 секрети з новою датою
- [ ] 12.2 `.github/workflows/deploy.yml` (`workflow_dispatch`): мережа (`cloudformation deploy`) → `kv-artifacts` → архів коду в S3 → заміна `kv-app` → очікування smoke test; перевірка: зелений запуск з порожнього акаунта
- [ ] 12.3 Повторний запуск без змін мережі; перевірка: стек `kv-network` «No changes», CD зелений
- [ ] 12.4 Лог CD не містить значень ключів; перевірка: пошук у лозі — лише `***`

## 13. YCSB baseline (`benchmark-baseline`, завдання 7)

- [ ] 13.1 Олеся підтверджує workload (пропозиція design: A + C) і стартові параметри; перевірка: рішення в журналі з обґрунтуванням (контрольне питання 8)
- [ ] 13.2 Binding: перевірити вбудований REST-binding YCSB; якщо не підходить — мінімальний власний у `bench/ycsb-binding/`; перевірка: `ycsb load` на 1000 записів проходить
- [ ] 13.3 `scripts/ycsb-run.sh`: load + run з параметрами з файлу, результат + конфігурація + дата → `docs/m2/ycsb/` і бакет результатів; перевірка: файли є в обох місцях
- [ ] 13.4 Повний прогін baseline; перевірка: звіт з throughput, avg/p95/p99 для читань і записів, конфігурацією; висновки в `docs/m2/ycsb/README.md`

## 14. Здача M2

- [ ] 14.1 README: як підняти M2, як перевірити, що змінилось відносно M1; перевірка: команди з README виконуються як написано
- [ ] 14.2 `docs/m2/demo.md` повністю (single-node, шардування, реєстрація, `503`, CI/CD, YCSB); перевірка: прогін демо підряд в одній сесії
- [ ] 14.3 AI-критика архітектури M2 (`docs/m2/ai-critique.md`: промпт, транскрипт, рішення по пунктах); перевірка: рішення прийнято щодо кожного пункту
- [ ] 14.4 `docs/m2/control-questions-uk.md` — 8 контрольних питань з поясненнями й матеріалом лекцій; перевірка: Олеся відповідає без нотаток
- [ ] 14.5 `openspec validate add-m2-sharding --strict`; коміти по логічних блоках з дозволу Олесі; перевірка: CI зелений на останньому коміті

## Workflow follow-up

- Після захисту M1: архівувати `add-m1-infrastructure`, потім переписати delta `specs/network` цієї зміни як MODIFIED вимоги «Приватні підмережі лише для внутрішнього доступу» (design, рішення 12).
- Після захисту M2: `/opsx:archive add-m2-sharding`.
- Бонуси (throttling, кеш) — окремі зміни під час M3.
