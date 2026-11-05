# Spec Delta

## ADDED Requirements

### Requirement: Приватні підмережі мають безкоштовний доступ до S3
Приватні підмережі SHALL мати маршрут до S3 через S3 Gateway Endpoint (безкоштовний), щоб сервери без інтернету могли отримувати код. Інших маршрутів назовні приватні підмережі MUST NOT мати: дозволені лише `local` і маршрут на prefix list S3 через Gateway Endpoint.

#### Scenario: Маршрути приватної підмережі
- **WHEN** читаємо route table приватних підмереж
- **THEN** у ній є `local` і маршрут на `pl-…` (prefix list S3) через `vpce-…`, і нема маршруту `0.0.0.0/0`

#### Scenario: Endpoint безкоштовний
- **WHEN** переглядаємо ресурси мережевого стека
- **THEN** `AWS::EC2::VPCEndpoint` має тип `Gateway` для сервісу `com.amazonaws.us-east-1.s3`; Interface Endpoint нема
