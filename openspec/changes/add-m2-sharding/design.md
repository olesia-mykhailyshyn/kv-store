# Design

## Context

Мотивація й обсяг — `proposal.md`; вимоги — `specs/*/spec.md`. Архітектурні рішення вже ухвалено в ADR-005…010; тут — як їх скласти разом.

Поточний стан: є мережевий стек `kv-network` (M1, `infra/network.yaml`) з експортами `kv-network-VpcId`, `…-PublicSubnet{A,B,C}Id`, `…-PrivateSubnet{A,B,C}Id`; коду застосунку нема. Обмеження:
- пісочниця: стеки зникають у кінці сесії, ключі ~3 год, ≤ 9 EC2, IAM лише на читання, готова роль EC2 `myS3Role` (`s3:*`);
- приватні підмережі без інтернету → на серверах лише Python 3 зі стандартною бібліотекою;
- бюджет: ≈ $2.66 за 40 год сесій.

## Goals / Non-Goals

**Goals:**
- Один код coordinator, що працює і в single-node, і в шардованому режимі.
- Один модуль зберігання (RAM + WAL), спільний для шарда, single-node coordinator і метаданих coordinator.
- Деплой «з порожнього акаунта» одним запуском CD за кілька хвилин.
- Кожен компонент перевіряється тестами локально, без AWS.

**Non-Goals:**
- Реплікація, кворуми — M3. Метрики, трасування, LWW — M4. Автомасштабування, ребаланс, прибирання мертвих шардів — M5.
- Автентифікація клієнтів і шардів — захист лише security groups.
- Compaction WAL, group commit.
- Бонуси (throttling, кеш) — під час M3.
- Збереження даних між сесіями пісочниці.

## Decisions

### 1. Структура репозиторію
```
api/openapi.yaml                 контракт (ADR-005)
src/kvstore/ring.py              кільце consistent hashing (ADR-006)
src/kvstore/storage.py           RAM + WAL (ADR-008)
src/kvstore/http_common.py       спільне: JSON-відповіді, помилки, розбір адрес
src/kvstore/coordinator.py       coordinator: API, метадані, маршрутизація, реєстрація
src/kvstore/shard.py             шард: внутрішнє API, heartbeat
tests/                           pytest, локальні процеси на localhost
infra/network.yaml               + S3 Gateway Endpoint
infra/artifacts.yaml             стек kv-artifacts: бакет коду, бакет результатів YCSB (versioning)
infra/app.yaml                   стек kv-app: 4 EC2, security groups
.github/workflows/ci.yml, deploy.yml
scripts/push-lab-secrets.sh      ключі з lab-credentials.ini → GitHub Secrets
scripts/ycsb-run.sh              прогін YCSB на клієнті
```
Один пакет `kvstore` для обох сервісів: спільні `storage` і `http_common`, один архів коду.

### 2. Single-node і шардований режим — один coordinator
Режим задається параметром запуску (`--mode single|sharded`).
- `single`: coordinator сам тримає записи через той самий `storage` (окремий WAL).
- `sharded`: записи пересилаються на шарди.

Відкинуто: окремий «single-node coordinator» — дублювання коду API.

### 3. Внутрішнє API шарда
Шард не знає визначень таблиць: coordinator передає явні поля `table`, `pk`, `sk` (для таблиці без sort key — `null`).
- `PUT /internal/items` `{table, pk, sk, value}`;
- `GET|HEAD|DELETE /internal/items?table=&pk=&sk=`;
- `GET /internal/query?table=&pk=&skFrom=&skTo=`;
- `GET /health`.

Описано в `api/openapi.yaml` з тегом `internal`. Відкинуто: дзеркальне публічне API на шарді — шарду довелося б знати схеми таблиць.

### 4. Зберігання (`storage`)
- Стан у RAM: `dict[(table, pk)] → dict[sk → value]`. Query сортує ключі SK при читанні; для обсягів M2 цього досить.
- WAL: файл JSON Lines; запис = `write` + `flush` + `os.fsync` під одним lock → лише потім RAM і відповідь. Записи в межах шарда серіалізуються lock-ом (один писач).
- Старт: читання WAL рядок за рядком; рядок, що не розбирається як JSON, допускається **лише останнім** — його відкидаємо і обрізаємо файл до попереднього рядка; пошкоджений рядок посередині → помилка старту (не мовчимо).
- Метадані coordinator: той самий клас, окремий файл, операції `create_table`.

### 5. HTTP
- `http.server.ThreadingHTTPServer`, `protocol_version = "HTTP/1.1"` (keep-alive для YCSB).
- Coordinator → шард: `http.client.HTTPConnection` з тайм-аутом 2 с, по з'єднанню на потік (без нового TCP на кожен запит). Помилка з'єднання або тайм-аут → `503`.
- Порт 8080 для coordinator і шардів.

### 6. Кільце і реєстрація
- `ring.py` за ADR-006: MD5, перші 8 байт, 500 міток `"{id}#{i}"`, `bisect`.
- Реєстрація: шард з `id` і `url` → coordinator під lock-ом перебудовує кільце лише при новому `id` (зміна адреси — лише запис у таблиці адрес).
- Heartbeat: окремий потік шарда, кожні 5 с; помилки реєстрації логуються, цикл не зупиняється.
- `id` шарда і адреса coordinator — з параметрів запуску (user data).

### 7. Інфраструктура
- **`kv-network`**: + `AWS::EC2::VPCEndpoint` (Gateway, S3), прив'язаний до приватної route table.
- **`kv-artifacts`**: бакет коду (`code/<git-sha>.zip`) і бакет результатів YCSB з versioning. Окремий стек: бакет мусить існувати до серверів.
- **`kv-app`**: coordinator (приватна підмережа A), `shard-1` (B), `shard-2` (C), клієнт (публічна A, `AssociatePublicIpAddress: true`). AMI — останній Amazon Linux 2023 через публічний SSM-параметр (перевірити доступ). Instance profile `myS3Role`. User data: `aws s3 cp` архіву → розпакувати → systemd-сервіс з параметрами (`--mode`, `--id`, `--coordinator`). WAL — на кореневому EBS `gp2` 8 ГБ.
- **Security groups** — за spec `app-infrastructure`; SSH до клієнта з параметра `AdminCidr` і діапазону EC2 Instance Connect.

### 8. Деплой застосунку — заміна стека, не оновлення
CD видаляє `kv-app` (якщо є) і створює його заново з новою версією коду.
- Чому: зміна user data в `AWS::EC2::Instance` не перезапускає скрипт старту; заміна гарантує, що працює саме новий код.
- Дані губляться — прийнятно: між сесіями вони й так зникають, YCSB завантажує їх заново.

Відкинуто: оновлення на місці через SSM Run Command (потрібні Interface Endpoints, платні) або перезапуск через `cfn-hup` (потрібен доступ серверів до CloudFormation API — без інтернету нема).

### 9. Smoke test всередині VPC
Клієнт у user data чекає `/health` coordinator, виконує сценарій (таблиця → `PUT` → `GET` → `DELETE`) і надсилає `cfn-signal` з результатом. `CreationPolicy` на клієнті: без успішного сигналу за 10 хв стек `kv-app` падає → CD червоний. Клієнт має публічну IP → доступ до CloudFormation API.

### 10. CI
- GitHub Actions, `ubuntu-latest`, **Python 3.9** — та сама мінорна версія, що в Amazon Linux 2023 (перевірено: 3.9.25); код без синтаксису новіших версій.
- Кроки: `pytest` (юніт + інтеграційні тести на localhost-процесах), `openapi-spec-validator api/openapi.yaml`, перевірка відповідей сервісу на схеми з `openapi.yaml` у тестах, `cfn-lint infra/*.yaml`.

### 11. YCSB
- Клієнт `t3.small`: Java + YCSB з GitHub (у клієнта є інтернет).
- Binding: спершу перевірити, чи підходить вбудований REST-binding YCSB під наші адреси; якщо ні — власний мінімальний binding на Java (`bench/ycsb-binding/`).
- Таблиця `usertable`, PK = ключ YCSB, без SK; `value` = поля YCSB (`field0…field9`).
- **Workload A** (50 % читань / 50 % оновлень) як головний baseline + **workload C** (100 % читань) — **підтверджує Олеся**. Чому A: навантажує шлях запису (WAL + fsync), який M3 ускладнить реплікацією, тож різниця M2→M3 буде найпомітніша.
- Параметри стартово: `recordcount=100000`, `operationcount=200000`, `threadcount=8`; уточнюються пробним прогоном.
- Результати → `docs/m2/ycsb/` + бакет результатів.

### 12. Зміна вимоги `network` з M1
Вимога M1 «приватні підмережі — лише маршрут `local`» живе в незаархівованій зміні `add-m1-infrastructure`, тож тут вона додана як нова (ADDED). Після захисту й архівації M1 дельту `network` цієї зміни треба переписати як MODIFIED тієї вимоги, перш ніж архівувати M2.

## Risks / Trade-offs

- [Python 3.9 на сервері, новіший локально] → CI на 3.9; перевірити версію в AL2023 до коду.
- [fsync на кожен запис обмежує throughput записів] → чесно в baseline; group commit — лише якщо YCSB покаже вузьке місце.
- [`t3` burstable: CPU credits вичерпуються під тривалим YCSB] → короткі прогони; фіксувати тип і тривалість у звіті.
- [Один coordinator — bottleneck і SPOF] → ADR-008; відповідь на контрольне питання 7.
- [Ключі сесії спливають посеред деплою] → оновлювати Secrets на початку сесії; CD ідемпотентний, перезапускається.
- [Публічний SSM-параметр AMI може бути заборонений] → перевірити; запасний варіант — ID AMI параметром стека.
- [Реєстрація без автентифікації] → лише security groups; записано в ADR-009.
- [Заміна `kv-app` при кожному деплої — простій і втрата даних] → прийнятно в пісочниці; поза нею — rolling update.
- [YCSB REST-binding не підходить] → власний binding (запасний план у задачах).

## Migration Plan

1. Специфікація (`openapi.yaml`, OpenSpec, ADR) — коміт до коду.
2. Код і тести локально → CI зелений.
3. Сесія пісочниці: `push-lab-secrets.sh` → CD → smoke test зелений.
4. YCSB → результати в репозиторій.

Відкат: CD з попереднім git SHA; мережу не видаляємо (`cloudformation deploy`, не `recreate-network.sh`).

## Open Questions

- Конкретні параметри YCSB (потоки, кількість операцій) — після пробного прогону.
- Чи працює вбудований REST-binding YCSB з нашими адресами — перевіряється в задачах YCSB; вплив обмежений вибором binding.
