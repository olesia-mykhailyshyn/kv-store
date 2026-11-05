# M2 — API коротко

Повний контракт: [`api/openapi.yaml`](../../api/openapi.yaml) (OpenAPI 3.1). Рішення: [ADR-005](../adr/0005-api-protocol-and-format.md), [ADR-007](../adr/0007-partition-key-strategy.md).

Приклад таблиці: `orders`, partition key `userId`, sort key `orderDate`.

## Публічне API (клієнт → coordinator)

| Операція | Метод і адреса | Успіх | Помилки |
|---|---|---|---|
| Створити таблицю | `POST /tables` `{"name":"orders","partitionKey":"userId","sortKey":"orderDate"}` | `201` | `400` (погана назва), `409` (вже є) |
| Опис таблиці | `GET /tables/orders` | `200` | `404` |
| **CREATE** | `PUT /tables/orders/items` `{"userId":"42","orderDate":"2026-11-05","value":{...}}` | `201` = на диску шарда | `400`, `404` (таблиці нема), `413` (> 64 КіБ), `503` |
| **READ** | `GET /tables/orders/items/42/2026-11-05` | `200` `{"key":{...},"value":{...}}` | `400`, `404`, `503` |
| **EXISTS** | `HEAD /tables/orders/items/42/2026-11-05` | `200` без тіла | `404`, `503` |
| **DELETE** | `DELETE /tables/orders/items/42/2026-11-05` | `204` | `404`, `503` |
| Query | `GET /tables/orders/items/42?skFrom=2026-11-01&skTo=2026-11-05` | `200` `{"items":[...]}` за зростанням SK | `400`, `503` |

**Таблиця без sort key** (наприклад, `usertable` для YCSB): адреса запису — `/tables/usertable/items/user42`, без `{sk}`. Для таблиці зі sort key та сама адреса без `{sk}` — це Query. Неправильна форма адреси → `400`.

## Службове API

| Хто → кому | Метод і адреса | Навіщо |
|---|---|---|
| шард → coordinator | `POST /shards/register` `{"id":"shard-1","url":"http://10.0.11.25:8080"}` | реєстрація при старті + heartbeat кожні 5 с |
| будь-хто → будь-кого | `GET /health` | живий чи ні (smoke test) |
| coordinator → шард | `PUT/GET/HEAD/DELETE /internal/items`, `GET /internal/query` | операції над записами на конкретному шарді; шард не знає схем таблиць — отримує `table`, `pk`, `sk` явно |

## Коди відповідей

| Код | Значення |
|---|---|
| `200` | є / прочитано |
| `201` | створено (таблицю або запис) |
| `204` | видалено |
| `400` | неправильний запит |
| `404` | нема таблиці або запису |
| `409` | таблиця вже існує |
| `413` | тіло більше за 64 КіБ |
| `503` | шард, що зберігає ключ, не відповів (2 с) — не `404`, бо дані можуть бути там |

**Помилки** завжди мають тіло `{"error":"<код>","message":"<опис>"}` (крім `HEAD`).
