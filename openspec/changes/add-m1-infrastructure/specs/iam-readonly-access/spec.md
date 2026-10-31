# Spec Delta

## Purpose

Доступ «лише на читання» до ресурсів AWS-акаунта: IAM-політика, яка дозволяє лише переглядати ресурси, і роль з цією політикою, яку може взяти інший principal. Описані в IaC; у пісочниці AWS Academy не деплояться, бо вона забороняє створювати IAM-ресурси.

## ADDED Requirements

### Requirement: Політика дозволяє лише перегляд
Read-only політика SHALL дозволяти лише дії, що переглядають ресурси (їхній список, опис, конфігурацію). Політика MUST NOT дозволяти жодної дії, яка створює, змінює або видаляє ресурси чи їхні налаштування доступу.

#### Scenario: Перегляд дозволено
- **WHEN** політику перевіряють policy simulator'ом на дії перегляду (наприклад, `ec2:DescribeVpcs`, `s3:ListAllMyBuckets`)
- **THEN** результат для цих дій — `allowed`

#### Scenario: Запис заборонено
- **WHEN** політику перевіряють policy simulator'ом на дії запису (наприклад, `ec2:CreateVpc`, `ec2:TerminateInstances`, `s3:PutObject`, `iam:AttachRolePolicy`)
- **THEN** результат для кожної з цих дій — `implicitDeny` або `explicitDeny`

### Requirement: Політика не відкриває секрети
Read-only політика MUST NOT дозволяти читати значення секретів і ключів: вміст секретів Secrets Manager, розшифровані параметри SSM, розшифрування через KMS.

#### Scenario: Секрет не читається
- **WHEN** політику перевіряють policy simulator'ом на `secretsmanager:GetSecretValue` і `kms:Decrypt`
- **THEN** результат для обох дій — не `allowed`

### Requirement: Політика не відкриває вміст даних
Read-only політика SHALL дозволяти бачити ресурси сховища даних (таблиці, бакети) та їхні налаштування, але MUST NOT дозволяти читати їхній вміст: записи таблиць DynamoDB і об'єкти S3.

#### Scenario: Метадані видно
- **WHEN** політику перевіряють policy simulator'ом на `dynamodb:DescribeTable` і `s3:GetBucketEncryption`
- **THEN** результат для обох дій — `allowed`

#### Scenario: Вміст не читається
- **WHEN** політику перевіряють policy simulator'ом на `dynamodb:GetItem`, `dynamodb:Scan` і `s3:GetObject`
- **THEN** результат для кожної з цих дій — не `allowed`

### Requirement: Роль з read-only політикою
SHALL існувати IAM-роль, до якої прикріплено read-only політику і жодної іншої політики з правами.

#### Scenario: Права ролі
- **WHEN** переглядаємо політики, прикріплені до ролі в шаблоні
- **THEN** серед них є лише read-only політика

### Requirement: Роль може взяти конкретний principal
Trust policy ролі SHALL дозволяти `sts:AssumeRole` явно названому principal'у. Trust policy MUST NOT дозволяти assume будь-кому (`"Principal": "*"` без умов).

#### Scenario: Довірений principal
- **WHEN** читаємо trust policy ролі в шаблоні
- **THEN** у ній `sts:AssumeRole` дозволено лише для конкретного principal'а (ARN акаунта, ролі або сервісу), а не для `*`

### Requirement: Роль береться лише з MFA
Trust policy ролі MUST дозволяти `sts:AssumeRole` людині з іншого акаунта лише тоді, коли сесія автентифікована з MFA.

#### Scenario: Умова MFA
- **WHEN** читаємо trust policy ролі в шаблоні
- **THEN** statement для довіреного акаунта має умову `aws:MultiFactorAuthPresent = true`

### Requirement: Дані закриті навіть при розширенні дозволів
Read-only політика SHALL містити explicit Deny на читання вмісту даних і секретів, щоб ці дії лишалися забороненими, навіть якщо до ролі колись додадуть ширший Allow.

#### Scenario: Deny перемагає ширший Allow
- **WHEN** policy simulator перевіряє `s3:GetObject` для набору з нашої політики і політики, що дозволяє `s3:*`
- **THEN** результат — `explicitDeny`

### Requirement: IAM описано в IaC і перевірено без деплою
Політику й роль SHALL описано в CloudFormation-шаблоні в репозиторії. Шаблон SHALL проходити перевірку синтаксису CloudFormation без створення ресурсів.

#### Scenario: Валідація шаблону
- **WHEN** запускаємо `aws cloudformation validate-template` для IAM-шаблону
- **THEN** команда завершується без помилок

#### Scenario: Документ пояснює дію
- **WHEN** читаємо документацію M1
- **THEN** у ній пояснено, що дозволяла б політика, хто й навіщо брав би роль і чому пісочниця не дає їх створити (з реальною помилкою як доказом)
