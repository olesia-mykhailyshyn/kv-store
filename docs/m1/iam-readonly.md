# M1, завдання 2–3: політика «лише перегляд» і роль для іншого principal

> **2.** Create an IAM policy that allows ONLY to view resources (no write access). In the sandbox you cannot attach it, so deliver it as an IaC template and explain what it would do.
> **3.** Create a Role that has this policy attached and could be assumed by another principal. Same rule: describe it in IaC, explain why the sandbox refuses it.

- Шаблон: [`infra/iam-readonly.yaml`](../../infra/iam-readonly.yaml) (стек `kv-iam`, у пісочниці **не деплоїться**)
- Рішення: [ADR-004](../adr/0004-readonly-access-design.md)
- Вимоги: [`specs/iam-readonly-access/spec.md`](../../openspec/changes/add-m1-infrastructure/specs/iam-readonly-access/spec.md)
- Перевірка: [`scripts/check-iam-readonly.sh`](../../scripts/check-iam-readonly.sh)
- Діаграми: [`architecture-uk.md`](architecture-uk.md), розділи 8, 12–14

## Коротко

Шаблон описує два ресурси:

| Ресурс | Тип | Що робить |
|---|---|---|
| політика `kv-readonly` | `AWS::IAM::ManagedPolicy` | **ЩО** можна: переглядати інфраструктуру і налаштування; нічого не змінювати; не читати дані й секрети |
| роль `kv-readonly` | `AWS::IAM::Role` | **ХТО** може: аудитор з **іншого** AWS-акаунта, лише з MFA, сесія ≤ 1 год; до ролі прикріплено лише `kv-readonly` |

Сценарій — **«спостерігач»**: викладач чи аудитор (людина) має **бачити** систему, але **не може** її змінити чи прочитати дані клієнтів kv-сховища. Принцип — least privilege.

## Що дозволяла б політика (завдання 2)

### Правило 1 — `ViewInfrastructure` (Allow, 49 дій)

| Сервіс | Що видно |
|---|---|
| EC2 / VPC (16 явних `Describe…`) | мережі, підмережі, route tables, IGW, NAT, endpoints, network ACL, security groups, мережеві інтерфейси, адреси, сервери, диски, теги, AZ |
| CloudFormation | стеки, їх ресурси, події, шаблони |
| DynamoDB | які таблиці є і як налаштовані (TTL, бекапи) |
| S3 | список бакетів і їх налаштування (шифрування, версіонування, блокування публічного доступу, теги) |
| Lambda | список функцій |
| CloudWatch | метрики, алерти, які групи логів існують |
| ELB, Auto Scaling | балансувальники, групи масштабування |
| IAM | ролі й політики та їх вміст |
| Service Quotas | ліміти акаунта |

`Resource: "*"` — навмисно: більшість дій list/describe не підтримують обмеження до конкретного ресурсу і працюють лише з `*`; аудитор має бачити всю інфраструктуру.

### Чого політика НЕ дозволяє

- **Будь-які зміни** (створити, змінити, видалити) — їх просто нема в Allow → **implicit deny**.
- **Вміст даних і секрети** — правило 2, **explicit deny**:

### Правило 2 — `DenyDataAndSecrets` (Deny, 25 дій)

| Що закрито | Дії | Чому |
|---|---|---|
| файли S3 і **навіть список їх імен** | `s3:GetObject*`, `s3:ListBucket*` | у kv-сховищі **ключі самі є даними** (`user:42:email`) |
| записи DynamoDB | `GetItem`, `BatchGetItem`, `Query`, `Scan`, `PartiQLSelect`, `GetRecords` | дані клієнтів |
| секрети | `secretsmanager:GetSecretValue`, `kms:Decrypt`, `ssm:GetParameter(s/ByPath)`, `ssm:GetParameterHistory` | паролі й ключі |
| вміст логів | `logs:GetLogEvents`, `FilterLogEvents`, `StartQuery`, `GetLogRecord` | у логах можуть бути дані |
| код і налаштування з секретами | `lambda:GetFunction`, `lambda:GetFunctionConfiguration`, `ec2:DescribeInstanceAttribute`, `ec2:DescribeLaunchTemplateVersions`, `autoscaling:DescribeLaunchConfigurations`, `ec2:GetLaunchTemplateData`, `ec2:GetConsoleOutput` | код функцій; **змінні середовища** Lambda і **user data** серверів, де бувають секрети |

**Навіщо explicit Deny, якщо implicit deny і так блокує?** Як **запобіжник**: якщо колись хтось розширить Allow (наприклад, `s3:Get*`) або прикріпить до ролі ще одну політику (`ReadOnlyAccess`), Deny все одно переможе — він сильніший за будь-який Allow. Це запобіжник лише для **перелічених** дій, а не гарантія: AWS постійно додає нові дії, тому повного списку не буде. `Resource: "*"`, щоб покривати й таблиці/бакети, створені пізніше.

**Свідомий ризик:** `lambda:ListFunctions` і `cloudformation:GetTemplate` дозволені, бо аудитор має бачити функції й стеки, хоча показують змінні середовища й вміст шаблонів.

**Правило для M2+:** жодних секретів у змінних середовища Lambda, у user data серверів і в Outputs стеків — `lambda:ListFunctions` і `cloudformation:Describe*` їх показують. Секрети — лише в SSM Parameter Store (`SecureString`), читання якого закрите Deny.

## Хто і як брав би роль (завдання 3)

```yaml
AssumeRolePolicyDocument:                                # trust policy — ХТО
  Statement:
    - Effect: Allow
      Principal:
        AWS: !Sub arn:aws:iam::${TrustedAccountId}:root  # інший акаунт (аудитор)
      Action: sts:AssumeRole
      Condition:
        Bool:
          aws:MultiFactorAuthPresent: "true"             # лише з MFA
ManagedPolicyArns:
  - !Ref ReadOnlyPolicy                                  # permissions — ЩО (лише kv-readonly)
MaxSessionDuration: 3600                                 # сесія ≤ 1 год
```

| | Trust policy | Permissions policy |
|---|---|---|
| Питання | **ХТО** може взяти роль | **ЩО** можна, коли взяв |
| Де | усередині ролі | окрема політика `kv-readonly`, прикріплена до ролі |
| У нас | акаунт `TrustedAccountId` (параметр, рівно 12 цифр), лише з MFA | Allow перегляд + Deny дані/секрети |

- `…:root` = **акаунт загалом**, не root-користувач. Потрібна **згода обох сторін**: наша trust policy довіряє акаунту аудитора, а акаунт аудитора своєю політикою дозволяє своїй людині `sts:AssumeRole` на нашу роль.
- **Чому інший акаунт (cross-account):** це те, що вміє роль і не вміє група (лекція 2, слайд 13); реалістичний сценарій зовнішнього аудиту.
- **MFA:** украденого пароля недостатньо.
- **Сесія ≤ 1 год:** після неї ключі недійсні (`ExpiredToken`), роль треба брати знову з MFA.
- Сервіс (Lambda) як principal свідомо **не додано** (YAGNI). MCP-сервер чи ops-агент (M4–M5) цю роль узяти **не зможуть**: програма не введе MFA-код. Їм потрібен окремий principal і окреме рішення в M4–M5.

### Як це працювало б у справжньому акаунті

```bash
# адміністратор нашого акаунта
aws cloudformation deploy --template-file infra/iam-readonly.yaml --stack-name kv-iam \
  --parameter-overrides TrustedAccountId=<12 цифр> --capabilities CAPABILITY_NAMED_IAM

# аудитор у своєму акаунті (з MFA)
aws sts assume-role --role-arn arn:aws:iam::<наш акаунт>:role/kv-readonly --role-session-name audit \
  --serial-number <його MFA-пристрій> --token-code <код>
# → тимчасові ключі на ≤ 1 год:
#   ec2 describe-vpcs ✅ · ec2 create-vpc ❌ (implicit deny) · s3 GetObject ❌ (explicit deny)
```

`CAPABILITY_NAMED_IAM` — явна згода на деплой шаблону, що створює IAM-ресурси з власними назвами (захист від того, щоб шаблон непомітно змінив доступ).

## Чому пісочниця відмовляє

Пісочниця AWS Academy дає **будувати інфраструктуру**, але **не керувати доступом**. Докази ([розбір ролі](role-dissection.md)):

| Джерело | Що показало |
|---|---|
| Readme пісочниці | «AWS Identity and Access Management (IAM) — **Read-only access**» |
| Проба `aws iam create-role --role-name kv-probe-should-fail …` | `AccessDenied … not authorized to perform: iam:CreateRole … because no identity-based policy allows the iam:CreateRole action` |
| Симулятор для нашої ролі `voclabs` | `iam:CreateRole` = `implicitDeny`, `iam:CreatePolicy` = `implicitDeny` |

CloudFormation створює ресурси **з правами того, хто деплоїть** (сесія ролі `voclabs`) → на `iam:CreatePolicy` / `iam:CreateRole` отримав би `AccessDenied` → стек відкотився б (rollback). Окрему спробу деплою не робили (рішення 2026-11-01): результат той самий, а невдалий стек довелося б прибирати.

**Чому Academy так робить:** IAM — «ключі від усього». З правом створювати ролі можна було б створити роль з `AdministratorAccess` і обійти всі обмеження пісочниці (privilege escalation), або IAM-користувача з постійними ключами (доступ після кінця сесії й курсу).

## Як перевірено без деплою

| # | Перевірка | Результат |
|---|---|---|
| 1 | `aws cloudformation validate-template --template-body file://infra/iam-readonly.yaml` | без помилок; вимагає `CAPABILITY_NAMED_IAM` |
| 2 | `./scripts/check-iam-readonly.sh` — `aws iam simulate-custom-policy` з JSON політики з шаблону | **37/37 ok** |
| 3 | читання trust policy в шаблоні | principal — `arn:aws:iam::${TrustedAccountId}:root` (не `*`; параметр приймає лише 12 цифр); умова MFA; лише `kv-readonly` в `ManagedPolicyArns` |

Результати симулятора (2026-11-01):

| Група | Дії | Очікували | Отримали |
|---|---|---|---|
| перегляд | `ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeRouteTables`, `cloudformation:DescribeStacks`, `dynamodb:DescribeTable`, `s3:ListAllMyBuckets`, `s3:GetEncryptionConfiguration`, `lambda:ListFunctions`, `cloudwatch:GetMetricData`, `iam:GetRole`, `servicequotas:ListServiceQuotas` | `allowed` | 11/11 ✅ |
| зміни | `ec2:CreateVpc`, `ec2:TerminateInstances`, `cloudformation:DeleteStack`, `s3:PutObject`, `dynamodb:PutItem`, `lambda:UpdateFunctionCode`, `iam:AttachRolePolicy`, `iam:CreateRole` | `implicitDeny` | 8/8 ✅ |
| дані й секрети | `s3:GetObject`, `s3:ListBucket`, `dynamodb:GetItem`, `dynamodb:Query`, `dynamodb:Scan`, `secretsmanager:GetSecretValue`, `kms:Decrypt`, `ssm:GetParameter`, `logs:GetLogEvents`, `lambda:GetFunction`, `lambda:GetFunctionConfiguration`, `ec2:DescribeInstanceAttribute`, `ec2:GetConsoleOutput`, `ec2:GetLaunchTemplateData`, `ssm:GetParameterHistory`, `logs:GetLogRecord` | `explicitDeny` | 16/16 ✅ |
| наша + чужа політика `Allow s3:*` | `s3:GetObject`, `s3:ListBucket` | `explicitDeny` | 2/2 ✅ |

## Що ми з'ясували по дорозі (для захисту)

- **«Read-only» ≠ `Describe*/List*/Get*`.** `Get*` включає читання даних (`s3:GetObject`); `ec2:Describe*` включає user data серверів; `lambda:GetFunctionConfiguration` — змінні середовища. Тому в Allow — явний список, а не широкі зірочки; знайшла це в тому числі автоматична security-перевірка.
- **AWS-керовані політики не підходять:** `ReadOnlyAccess` (саме вона в нашої ролі `voclabs`) дозволяє `s3:Get*` і `dynamodb:Scan` — тобто читати дані; і змінюється без нас (у пісочниці — версія v191).
- **Зірочки:** у Deny — добре (ширше = безпечніше); `Resource: "*"` в Allow — необхідно для list/describe; зірочки в **діях** Allow — лише якщо точно знаєш, що вони покривають.

## Контрольні питання

- **4. Ролі vs групи для видачі політик.** Група — постійне членство, лише для людей, сама нічого не робить, не може бути principal і не працює між акаунтами. Роль — «перевтілення» на час: тимчасові ключі, може бути principal, працює cross-account, її беруть і люди, і сервіси. Тому для аудитора з іншого акаунта — **роль**.
- **7. Коли allow, а коли deny.** За замовчуванням усе заборонено (implicit deny). **Allow** — видати потрібне (правило 1). **Explicit Deny** — гарантувати, що чогось не буде **ніколи**, хоч би які дозволи з'явилися (правило 2). Порядок оцінки: explicit Deny → Allow → implicit deny. Живі приклади всіх трьох — у [розборі ролі](role-dissection.md), розділ 6.
