# kv-store

Capstone project for KSE SE470 Cloud Architectures (2026/27): a "light" serverless key-value store in the spirit of DynamoDB, built across 5 milestones.

## Assignment

- [Original (EN)](docs/assignment/capstone-original-en.txt)
- [Translation (UK)](docs/assignment/capstone-uk.md)

## Milestones

| # | Topic | Status |
|---|---|---|
| M1 | Infrastructure setup (LabRole, IAM in IaC, VPC, quotas, budget, recreatability) | in progress |
| M2 | Sharding: API, coordinator, consistent hashing, CI/CD, YCSB baseline | not started |
| M3 | Replication: replica sets, log, quorum reads | not started |
| M4 | Monitoring, conflict resolution, MCP server | not started |
| M5 | Autoscaling, rebalancing, ops agent, final benchmark | not started |

## M1 — коротко

**Що це:** фундамент для сховища — розбір прав пісочниці, мережа як код, доступ «лише на перегляд» як код, квоти й бюджет для M2, доказ відтворюваності. Самого kv-сховища ще нема (воно — у M2).

### Швидкий старт (на початку кожної сесії)

```bash
./scripts/lab-login.sh          # ключі нової сесії → профіль kse-lab
./scripts/recreate-network.sh   # підняти мережу з IaC (+ 9 перевірок), ≈ 100 с
./scripts/check-iam-readonly.sh # перевірити read-only політику симулятором (33 перевірки)
python3 scripts/m2_budget.py    # перерахувати бюджет M2 з публічних прайсів AWS
```

Ресурси пісочниці **видаляються в кінці кожної сесії** — тому мережу піднімаємо щоразу (див. [розбір ролі](docs/m1/role-dissection.md), розділ 6a).

### Що здаємо за завданнями

| # | Завдання | Результат | Де |
|---|---|---|---|
| 1 | Розібрати роль пісочниці | роль `voclabs` = `ReadOnlyAccess` + `Pvoclabs` + SCP; що дозволено / заборонено / як з'ясували | [`docs/m1/role-dissection.md`](docs/m1/role-dissection.md) |
| 2 | Політика «лише перегляд» як IaC | `kv-readonly`: Allow перегляд, explicit Deny дані й секрети; симулятор 33/33 | [`infra/iam-readonly.yaml`](infra/iam-readonly.yaml), [`docs/m1/iam-readonly.md`](docs/m1/iam-readonly.md) |
| 3 | Роль з цією політикою для іншого principal | роль для аудитора з іншого акаунта з MFA; чому пісочниця відмовляє | там само |
| 4 | Мережа: публічні й приватні підмережі | VPC `10.0.0.0/16`, 3 AZ, 6 підмереж, IGW лише для публічних | [`infra/network.yaml`](infra/network.yaml) |
| 5 | IaC-стек мережі | стек `kv-network` (CloudFormation), 18 ресурсів, 10 експортів | розділ «M1 — мережа» нижче |
| 6 | Квоти для M2 | квоти AWS із запасом; обмежують правила пісочниці (≤ 9 EC2) і бюджет | [`docs/m1/quotas.md`](docs/m1/quotas.md) |
| 7 | Бюджет M2 vs $20 | 4 варіанти; 24/7 — понад $20, у сесіях — $2.7–5.4 | [`docs/m1/budget-m2.md`](docs/m1/budget-m2.md) |
| 8 | Знести й підняти з IaC | 100 с (32 + 68), 9/9 перевірок; що зламалося | [`docs/m1/recreatability.md`](docs/m1/recreatability.md) |

### Рішення (ADR) і специфікація

- [ADR-001](docs/adr/0001-iac-tool-cloudformation.md) — IaC-інструмент: CloudFormation
- [ADR-002](docs/adr/0002-stack-layout.md) — структура стеків: мережа окремо від IAM і застосунку
- [ADR-003](docs/adr/0003-cidr-addressing.md) — CIDR-схема і 3 AZ
- [ADR-004](docs/adr/0004-readonly-access-design.md) — дизайн read-only доступу
- OpenSpec-зміна (написана **до коду**): [`openspec/changes/add-m1-infrastructure/`](openspec/changes/add-m1-infrastructure/) — proposal, specs (`network`, `iam-readonly-access`), design, tasks
- Діаграми: [`docs/m1/architecture-uk.md`](docs/m1/architecture-uk.md); журнал кожного кроку: [`docs/m1/journal-uk.md`](docs/m1/journal-uk.md)

### Що змінилось (порівняно з попереднім майлстоуном)

M1 — перший майлстоун, тому все нове:

| Що | Файли |
|---|---|
| IaC мережі і доступу | `infra/network.yaml`, `infra/iam-readonly.yaml` |
| Скрипти | `scripts/lab-login.sh`, `scripts/recreate-network.sh`, `scripts/check-iam-readonly.sh`, `scripts/m2_budget.py` |
| Рішення і план | `docs/adr/0001…0004`, `openspec/` (зміна `add-m1-infrastructure`) |
| Документи M1 | `docs/m1/*.md` (розбір ролі, IAM, квоти, бюджет, відтворюваність, діаграми, журнал) |
| В AWS (на час сесії) | стек `kv-network`; IAM-ресурси не створюються (пісочниця забороняє) |

## M1 — розбір ролі пісочниці (завдання 1)

Ми працюємо як **сесія ролі `voclabs`** (`LabRole` в акаунті нема). Права = `ReadOnlyAccess` (AWS, лише Allow) + `Pvoclabs` (Academy, Allow + explicit Deny; текст прихований) + **SCP** організації AWS Academy. Можна: читати майже все; створювати VPC, CloudFormation, EC2 (≤ 9, `t2/t3` до `medium`), Lambda, DynamoDB, S3; «вдягати» готові ролі `lambda-run-role`, `myBasicLambdaRole`, `myS3Role`. Не можна: створювати IAM, API Gateway, SQS, Organizations, Pricing API. Як з'ясували: Readme, CLI (`sts get-caller-identity`, `iam list-*`), policy simulator, проби з `--dry-run`, текст помилок, CloudTrail.

```bash
aws sts get-caller-identity --profile kse-lab
aws iam list-attached-role-policies --role-name voclabs --profile kse-lab
aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::<ACCOUNT>:role/voclabs \
  --action-names ec2:CreateVpc iam:CreateRole --profile kse-lab
```

Детально: [`docs/m1/role-dissection.md`](docs/m1/role-dissection.md).

## Куди рухаємось: форма системи в M2

M1 готує фундамент для M2, де з'являється саме kv-сховище:

```
клієнт / YCSB ──► точка входу ──► Coordinator ──hash(key)──► Шард 1 (частина ключів)
                                                    └──────► Шард 2 (інша частина ключів)
```

- **Coordinator** — маршрутизатор: приймає `CREATE` / `READ` / `DELETE` / `EXISTS`, рахує хеш ключа (consistent hashing) і обирає шард.
- **Шарди** — наші програми, кожна зберігає свою частину ключів; реєструються в coordinator при старті.
- **Точка входу** — куди клієнти шлють запити (кандидат — ALB; API Gateway у пісочниці нема).
- **YCSB** (Yahoo! Cloud Serving Benchmark) — стандартний навантажувальний тест для key-value/NoSQL сховищ; міряє пропускну здатність (операцій/с) і затримки (p95, p99). Його числа з M2 — базова лінія для M3 і M5.
- Плюс IaC і CI/CD для всього цього.

Код coordinator і шардів пишемо самі; AWS дає, **де** запускати (EC2 або Lambda), де зберігати і точку входу. Тому в M1 перевіряємо **квоти** сервісів-кандидатів (завдання 6) і **бюджет** (завдання 7) — щоб архітектуру M2 обирати, вже знаючи обмеження. Архітектура M2 буде рішенням (ADR) у M2.

## M1 — мережа `kv-network`

Мережа, у якій житимуть усі сервіси сховища: VPC `10.0.0.0/16` у 3 AZ, публічні підмережі з виходом в інтернет через Internet Gateway, приватні — лише для доступу всередині VPC. Шаблон: [`infra/network.yaml`](infra/network.yaml). Рішення: [ADR-002](docs/adr/0002-stack-layout.md) (стеки), [ADR-003](docs/adr/0003-cidr-addressing.md) (адреси). Діаграми: [`docs/m1/architecture-uk.md`](docs/m1/architecture-uk.md).

| Підмережа | CIDR | AZ | Route table |
|---|---|---|---|
| public-a / b / c | `10.0.0.0/24` / `10.0.1.0/24` / `10.0.2.0/24` | 1-ша / 2-га / 3-тя | public: `local` + `0.0.0.0/0 → IGW` |
| private-a / b / c | `10.0.10.0/24` / `10.0.11.0/24` / `10.0.12.0/24` | 1-ша / 2-га / 3-тя | private: лише `local` |

Вартість: $0 (VPC, підмережі, IGW, route tables безкоштовні; NAT Gateway свідомо нема).

> ⚠️ **Ресурси пісочниці не зберігаються між сесіями:** у кінці сесії AWS Academy видаляє наші стеки (перевірено через CloudTrail, див. [розбір ролі](docs/m1/role-dissection.md), розділ 6a). На початку кожної сесії мережу треба підняти заново кроками нижче.

### Як підняти

1. AWS Academy → Cloud Architecting → Sandbox → **Start Lab**; AWS Details → AWS CLI → Show → вставити блок у `lab-credentials.ini` (файл у `.gitignore`).
2. `./scripts/lab-login.sh` — записує ключі в профіль `kse-lab` і показує `Arn` (`assumed-role/voclabs/…`).
3. Перевірити шаблон і створити стек через change set (спершу переглянути, потім виконати):

```bash
aws cloudformation validate-template --template-body file://infra/network.yaml --profile kse-lab

aws cloudformation create-change-set \
  --stack-name kv-network --change-set-name initial-create --change-set-type CREATE \
  --template-body file://infra/network.yaml \
  --tags Key=Project,Value=kv-store Key=Milestone,Value=m1 \
  --profile kse-lab
aws cloudformation wait change-set-create-complete --stack-name kv-network --change-set-name initial-create --profile kse-lab
aws cloudformation describe-change-set --stack-name kv-network --change-set-name initial-create \
  --query 'Changes[].ResourceChange.[Action,LogicalResourceId]' --output table --profile kse-lab   # очікуємо 18 × Add

aws cloudformation execute-change-set --stack-name kv-network --change-set-name initial-create --profile kse-lab
aws cloudformation wait stack-create-complete --stack-name kv-network --profile kse-lab
```

Для оновлення наявного стека — те саме з `--change-set-type UPDATE` і новою назвою change set.

### Як перевірити

```bash
# статус стека: CREATE_COMPLETE
aws cloudformation describe-stacks --stack-name kv-network --query 'Stacks[0].StackStatus' --output text --profile kse-lab

# ID VPC з виходів стека
VPC=$(aws cloudformation describe-stacks --stack-name kv-network \
  --query "Stacks[0].Outputs[?OutputKey=='VpcId'].OutputValue" --output text --profile kse-lab)

# маршрути: public-rt має 0.0.0.0/0 → igw-…; private-rt і main — лише local; main ні до чого не прив'язана
aws ec2 describe-route-tables --filters Name=vpc-id,Values=$VPC --profile kse-lab \
  --query 'RouteTables[].{Name:Tags[?Key==`Name`]|[0].Value,Main:Associations[?Main]|[0].Main,Routes:Routes[].[DestinationCidrBlock,GatewayId],Subnets:Associations[?SubnetId].SubnetId}'

# підмережі: 6 штук, CIDR і AZ як у таблиці, MapPublicIpOnLaunch = False
aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC --profile kse-lab \
  --query 'Subnets[].[Tags[?Key==`Name`]|[0].Value,CidrBlock,AvailabilityZone,MapPublicIpOnLaunch]' --output table

# нема платних ресурсів (NatGateway, VPCEndpoint): 18 ресурсів 7 типів
aws cloudformation list-stack-resources --stack-name kv-network --profile kse-lab \
  --query 'StackResourceSummaries[].ResourceType' --output text | tr '\t' '\n' | sort | uniq -c

# 10 експортів kv-network-… для стеків M2
aws cloudformation list-exports --query 'Exports[?starts_with(Name,`kv-network-`)].[Name,Value]' --output table --profile kse-lab
```

### Як знести

```bash
aws cloudformation delete-stack --stack-name kv-network --profile kse-lab
aws cloudformation wait stack-delete-complete --stack-name kv-network --profile kse-lab
```

Якщо стек M2 імпортує експорти `kv-network-…`, спершу видалити його (ADR-002).

## M1 — IAM read-only (завдання 2–3, не деплоїться)

Політика «лише перегляд» і роль, яку може взяти аудитор з **іншого** AWS-акаунта (лише з MFA). Шаблон: [`infra/iam-readonly.yaml`](infra/iam-readonly.yaml). Рішення: [ADR-004](docs/adr/0004-readonly-access-design.md). Повне пояснення: [`docs/m1/iam-readonly.md`](docs/m1/iam-readonly.md). У пісочниці AWS Academy IAM лише на читання, тому шаблон **перевіряється без деплою** (див. [розбір ролі пісочниці](docs/m1/role-dissection.md)).

| | Trust policy (`AssumeRolePolicyDocument`) | Permissions policy (`kv-readonly`, через `ManagedPolicyArns`) |
|---|---|---|
| Питання | **ХТО** може взяти роль | **ЩО** можна робити, коли роль узяли |
| Де записана | всередині ролі | окремий об'єкт, прикріплений до ролі |
| У нас | акаунт аудитора (`TrustedAccountId`), лише з MFA | Allow: перегляд інфраструктури; Deny: вміст даних і секрети |
| Аналогія | список, кому видати картку | аркуш правил на картці: куди з нею пускають |

- Сесія ролі — не довше 1 год (`MaxSessionDuration: 3600`); далі ключі недійсні (`ExpiredToken`), роль треба брати знову з MFA.
- До ролі прикріплено **лише** `kv-readonly` (ролі можуть мати кілька політик; тут — свідомо одна).
- Explicit Deny на читання даних і секретів діє навіть якщо колись додадуть ширший Allow.

### Як перевірити (нічого не створює)

```bash
# 1. шаблон написаний правильно
aws cloudformation validate-template --template-body file://infra/iam-readonly.yaml --profile kse-lab

# 2. логіка політики — policy simulator (33 перевірки, виходить з помилкою, якщо хоч одна не збіглась)
./scripts/check-iam-readonly.sh
```

Скрипт [`scripts/check-iam-readonly.sh`](scripts/check-iam-readonly.sh):
1. витягує документ політики `kv-readonly` з шаблону в JSON (Ruby-парсер YAML);
2. для кожної групи дій викликає `aws iam simulate-custom-policy --policy-input-list <JSON> --action-names <дії>` — AWS проганяє справжній алгоритм оцінки прав, **нічого не виконуючи**;
3. порівнює відповідь з очікуваною:

| Група дій | Приклади | Очікуємо | Чому |
|---|---|---|---|
| перегляд інфраструктури | `ec2:DescribeVpcs`, `cloudformation:DescribeStacks`, `s3:ListAllMyBuckets` | `allowed` | є в Allow |
| будь-яка зміна | `ec2:CreateVpc`, `s3:PutObject`, `iam:AttachRolePolicy` | `implicitDeny` | Allow нема |
| дані й секрети | `s3:GetObject`, `dynamodb:Scan`, `secretsmanager:GetSecretValue`, `lambda:GetFunctionConfiguration` | `explicitDeny` | запобіжник Deny |
| наша політика **+** чужа з `s3:*` | `s3:GetObject`, `s3:ListBucket` | `explicitDeny` | Deny перемагає ширший Allow |

Результат (2026-11-01): **33/33 ok, «All checks passed.»**

Trust policy (хто бере роль) перевіряється читанням шаблону: principal — `arn:aws:iam::${TrustedAccountId}:root` (не `*`), умова `aws:MultiFactorAuthPresent = "true"`. Що пісочниця не дає створити IAM, доведено пробою `aws iam create-role` → `AccessDenied` ([розбір ролі](docs/m1/role-dissection.md), розділ 6).

## M1 — квоти і обмеження для M2 (завдання 6)

Ліміти, які можуть вплинути на M2 (coordinator + 2 шарди + точка входу + YCSB). Перевірено 2026-11-01 у пісочниці, регіон `us-east-1`. Детально: [`docs/m1/quotas.md`](docs/m1/quotas.md).

### Квоти AWS (сервіс Service Quotas і API сервісів)

| Сервіс | Квота | Значення | Потреба M2 (оцінка) | Обмежує? |
|---|---|---|---|---|
| EC2 | Running On-Demand Standard instances (`L-1216C47A`) | 256 vCPU | 4 сервери `t3` ≈ 8 vCPU | ні |
| EC2 | `max-instances` (атрибут акаунта) | 20 | ~4 | ні |
| EC2 | EC2-VPC Elastic IPs (`L-0263D0A3`) | 5 | 0–1 | ні, але кожна платна |
| Lambda | Concurrent executions (`L-B99A9384`) | 400 | десятки–сотні при YCSB | ні |
| Lambda | Function timeout | 900 с (15 хв) | мілісекунди на запит | ні |
| Lambda | Synchronous payload | 6 МБ | малі ключі/значення | ні |
| Lambda | Deployment package (zip / unzipped) | 50 / 250 МБ | невеликий код | ні |
| Lambda | Environment variables | 4 КБ | кілька змінних | ні |
| DynamoDB | Maximum number of tables | 2500 | кілька | ні |
| DynamoDB | Table-level read / write throughput | 40 000 RCU / 40 000 WCU | сотні–тисячі оп/с | ні |
| DynamoDB | Account-level read / write throughput | 80 000 / 80 000 | — | ні |
| ELB | Application Load Balancers per Region | 50 | 1 | ні |
| ELB | Targets per ALB | 1000 | кілька | ні |
| VPC | VPCs per Region | 5 | 2 (default + наша) | ні |
| VPC | Internet gateways per Region | 5 | 1 | ні |
| VPC | Subnets per VPC | 200 | 6 | ні |
| VPC | Inbound/outbound rules per security group | 60 | кілька | ні |
| CloudFormation | Stacks | 2000 | кілька | ні |
| CloudFormation | Resources per template | 500 | десятки | ні |
| CloudFormation | Template body (передача напряму) | 51 200 байт | `network.yaml` ≈ 8 КБ | ні |

RCU / WCU — одиниці пропускної здатності DynamoDB: 1 RCU = 1 читання (до 4 КБ) за секунду, 1 WCU = 1 запис (до 1 КБ) за секунду.

### Що реально обмежує (правила пісочниці і бюджет)

| # | Обмеження | Джерело | Наслідок для M2+ |
|---|---|---|---|
| 1 | **≤ 9 EC2-інстансів**, спроба більше → блокування акаунта | Readme пісочниці | M2 (≈ 4) вміщається; M3 (2 × 3 репліки + coordinator ≈ 7+) і M5 — впритул |
| 2 | EC2 лише `t2`/`t3` від `nano` до `medium`; EBS ≤ 35 ГБ `gp2`; лише On-Demand | Readme | скромна продуктивність |
| 3 | **Нема** API Gateway, SQS, ECS/ECR, Secrets Manager | Readme, симулятор | точка входу — ALB або Lambda; без черг і контейнерів |
| 4 | Публічні IPv4 платні (≈ $3.6/міс кожна), Elastic IP ≤ 5 | ціни AWS, квота | мінімум публічних адрес |
| 5 | **Ресурси видаляються в кінці кожної сесії** (платформа видаляє стеки) | CloudTrail, `list-stacks` | щосесії піднімати все з IaC; дані зникають; YCSB і демо — в межах сесії |
| 6 | Бюджет **$20** на весь курс | лаба | розрахунок — завдання 7 |

```bash
# як перевірити (лише читання)
aws ec2 describe-account-attributes --attribute-names max-instances max-elastic-ips --profile kse-lab
aws lambda get-account-settings --profile kse-lab
aws dynamodb describe-limits --profile kse-lab
aws service-quotas list-service-quotas --service-code <ec2|lambda|dynamodb|elasticloadbalancing|vpc|cloudformation> --profile kse-lab
```

## M1 — бюджет M2 (завдання 7)

Скільки коштував би M2 (2 шарди) у різних варіантах, порівняно зі стелею пісочниці **$20**. Ціни — з публічних прайсів AWS (Pricing API у пісочниці заборонений SCP), розраховано скриптом [`scripts/m2_budget.py`](scripts/m2_budget.py) 2026-11-01. Детально, з припущеннями: [`docs/m1/budget-m2.md`](docs/m1/budget-m2.md).

> ⚠️ Це **оцінка**, а не рішення: архітектуру M2 оберемо в M2 (ADR).

| Варіант | Склад | 24/7 місяць | Лише сесії (40 год) |
|---|---|---|---|
| **А. EC2 без ALB** | coordinator + 2 шарди `t3.micro` (приватні), клієнт YCSB `t3.small` (публічний, 1 IPv4) | **$45.01** ❌ | **$2.66** ✅ |
| А+. EC2 + ALB | як А + ALB у 2 AZ | $74.58 ❌ | $4.28 ✅ |
| Б. Serverless | Lambda + DynamoDB, клієнт YCSB `t3.small` | $23.94 ❌ | $5.38 ✅ |
| В. Змішаний | coordinator у Lambda, 2 шарди `t3.micro`, клієнт `t3.small` | $37.83 ❌ | $3.41 ✅ |
| (порівняння) NAT у 3 AZ | лише NAT, додатково до варіанту | $109.72 ❌ | $6.22 |

Ключові ціни (us-east-1): `t3.micro` $0.0104/год, `t3.small` $0.0208/год, EBS `gp2` $0.10/ГБ-міс, публічна IPv4 $0.005/год, ALB $0.0225/год + $0.008/LCU-год, NAT $0.045/год + $0.045/ГБ, Lambda $0.20/млн + $0.0000166667/ГБ-с, DynamoDB on-demand $0.125/млн читань і $0.625/млн записів, S3 Gateway Endpoint — $0.

**Висновок:** 24/7 жоден варіант не вміщається в $20; у реальному режимі пісочниці (ресурси живуть лише під час сесій і видаляються в кінці) — усі вміщаються ($2.7–5.4 за M2). Найдорожче — сервери й публічні IPv4; ALB і NAT для M2 не потрібні.

```bash
python3 scripts/m2_budget.py   # перерахувати (без ключів AWS, нічого не створює)
```

## M1 — відтворюваність (завдання 8)

Усю мережу можна знести і підняти **лише з IaC** однією командою: [`scripts/recreate-network.sh`](scripts/recreate-network.sh) (знести, якщо є → `aws cloudformation deploy` → 9 автоматичних перевірок за spec → час). Детально: [`docs/m1/recreatability.md`](docs/m1/recreatability.md).

| Запуск (2026-11-01) | Знести | Підняти | Разом | Перевірки |
|---|---|---|---|---|
| Після очищення платформою (стека нема) | 0 с | 68 с | 68 с | 9/9 ✅ |
| Повний цикл: знести → підняти | 32 с | 68 с | **100 с** | 9/9 ✅ |

Що зламалося з першої спроби: у перестворенні — нічого; довкола — платформа видаляє стеки в кінці сесії (виявлено через CloudTrail), пастки zsh у командах (тому скрипти на bash). ID ресурсів після кожного перестворення нові → M2 бере їх через експорти `kv-network-…`.

```bash
./scripts/lab-login.sh && ./scripts/recreate-network.sh   # на початку кожної сесії
```
