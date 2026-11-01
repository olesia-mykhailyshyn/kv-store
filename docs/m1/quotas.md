# M1, завдання 6: квоти, що можуть обмежити M2

> Review the service quotas you will need in milestone 2. Write down the ones that can limit you.

Перевірено 2026-11-01 у пісочниці AWS Academy (Cloud Architecting Sandbox), регіон `us-east-1`, профіль `kse-lab`. Усі команди лише читають.

## Що таке квота і навіщо перевіряти зараз

**Квота** — ліміт, який AWS ставить на акаунт (зазвичай на регіон): скільки ресурсів можна мати або скільки запитів робити. Перевищення → AWS відмовляє. Ліміти захищають спільні ресурси AWS, клієнта від власних помилок (скрипт, що в циклі запускає сервери), і обмежують шкоду від украдених ключів. Квоти бувають **регульовані** (можна попросити підняти; у пісочниці, найімовірніше, ні) і **фіксовані**.

Архітектура M2 ще не обрана, але відома її **форма**: coordinator + 2 шарди + точка входу + клієнт YCSB + CI/CD. Тому перевіряємо квоти **сервісів-кандидатів** (EC2, Lambda, DynamoDB, ELB, VPC, CloudFormation), щоб у M2 обирати архітектуру, вже знаючи межі.

## Команди

```bash
aws ec2 describe-account-attributes --attribute-names max-instances max-elastic-ips vpc-max-elastic-ips --profile kse-lab
aws service-quotas list-service-quotas --service-code ec2 --profile kse-lab \
  --query "Quotas[?contains(QuotaName,'Standard') || contains(QuotaName,'Elastic IP')].[QuotaCode,QuotaName,Value,Adjustable]" --output text
aws lambda get-account-settings --profile kse-lab
aws service-quotas list-service-quotas --service-code lambda --profile kse-lab
aws service-quotas list-service-quotas --service-code dynamodb --profile kse-lab
aws dynamodb describe-limits --profile kse-lab
aws service-quotas list-service-quotas --service-code elasticloadbalancing --profile kse-lab
aws service-quotas list-service-quotas --service-code vpc --profile kse-lab
aws service-quotas list-service-quotas --service-code cloudformation --profile kse-lab
```

## Квоти AWS

| Сервіс | Квота | Значення | Регульована | Потреба M2 (оцінка) | Може обмежити M2? |
|---|---|---|---|---|---|
| EC2 | Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances, `L-1216C47A` | 256 vCPU | так | 4 × `t3` ≈ 8 vCPU | ні |
| EC2 | `max-instances` (атрибут акаунта) | 20 | — | ~4 | ні |
| EC2 | EC2-VPC Elastic IPs, `L-0263D0A3` | 5 | так | 0–1 | ні (але кожна публічна IPv4 платна) |
| Lambda | Concurrent executions, `L-B99A9384` (= `ConcurrentExecutions` у `get-account-settings`) | 400 | так | десятки–сотні паралельних запитів YCSB | ні |
| Lambda | Concurrency scaling rate, `L-A1AFA3CF` | 1000 | ні | — | ні |
| Lambda | Function timeout, `L-9FEEFFC0` | 900 с | ні | мілісекунди на запит | ні |
| Lambda | Synchronous payload, `L-5C4B2C97` | 6 МБ | ні | малі значення | ні; великі значення — не в Lambda |
| Lambda | Deployment package zip / unzipped | 50 / 250 МБ | ні | невеликий код | ні |
| Lambda | Environment variable size, `L-6581F036` | 4 КБ | ні | кілька змінних | ні |
| DynamoDB | Maximum number of tables | 2500 | так | кілька | ні |
| DynamoDB | Table-level read / write throughput | 40 000 RCU / 40 000 WCU | так | сотні–тисячі оп/с | ні |
| DynamoDB | Account-level read / write throughput (provisioned) | 80 000 / 80 000 | так | — | ні |
| DynamoDB | Global Secondary Indexes per table | 20 | так | 0–1 | ні |
| ELB | Application Load Balancers per Region | 50 | так | 1 | ні |
| ELB | Targets per ALB / per target group | 1000 / 1000 | так | кілька | ні |
| ELB | Listeners per ALB | 50 | так | 1–2 | ні |
| VPC | VPCs per Region | 5 | так | 2 (default + `kv-network`) | ні |
| VPC | Internet gateways per Region | 5 | так | 1 | ні |
| VPC | Subnets per VPC | 200 | так | 6 | ні |
| VPC | Inbound or outbound rules per security group | 60 | так | кілька | ні |
| VPC | Security groups per network interface | 5 | так | 1–2 | ні |
| CloudFormation | Stacks | 2000 | так | кілька | ні |
| CloudFormation | Template Resources | 500 | ні | десятки | ні |
| CloudFormation | Template Outputs / Parameters | 200 / 200 | ні | ~10 | ні |
| CloudFormation | Розмір шаблону при `--template-body` | 51 200 байт (через S3 — 1 МБ) | ні | `network.yaml` ≈ 8 КБ | ні |
| CloudFormation | Швидкість API (`CreateStack`, `ExecuteChangeSet`…) | 2 запити/с | ні | поодинокі | ні |

RCU / WCU — одиниці пропускної здатності DynamoDB: 1 RCU = 1 строго узгоджене читання до 4 КБ за секунду; 1 WCU = 1 запис до 1 КБ за секунду.

**Висновок по квотах AWS:** для M2 запас великий; жодна з перевірених квот не обмежить ні архітектуру, ні бенчмарк. Найближчі до межі в майбутньому — Elastic IP (5) і VPC на регіон (5), але нам потрібно по 1–2.

## Що реально обмежує: правила пісочниці і бюджет

| # | Обмеження | Звідки знаємо | Наслідок для M2+ |
|---|---|---|---|
| 1 | **≤ 9 EC2-інстансів**; спроба запустити більше → блокування акаунта лаби | Readme пісочниці | M2: coordinator + 2 шарди + клієнт YCSB ≈ 4 — ок. M3: 2 шарди × 3 репліки + coordinator + клієнт ≈ 8 — впритул. M5 (автомасштабування) — потребує стелі в Auto Scaling або не-EC2 обчислень |
| 2 | EC2 лише `t2/t3` від `nano` до `medium`, EBS ≤ 35 ГБ `gp2`, лише On-Demand, лише AMI від Amazon | Readme | скромна продуктивність; YCSB-числа будуть невеликими — порівнюємо між майлстоунами, а не з продакшеном |
| 3 | **Нема** API Gateway, SQS, ECS/ECR, Secrets Manager | Readme; симулятор `apigateway:POST`, `sqs:CreateQueue` = implicitDeny | точка входу — ALB або Lambda (function URL); без черг і контейнерів; секрети — SSM `SecureString` |
| 4 | IAM — лише читання; свої ролі створити не можна | Readme; проба `create-role` → AccessDenied | сервіси M2 — лише з готовими ролями `lambda-run-role`, `myBasicLambdaRole`, `myS3Role` (`iam:PassRole` дозволено) |
| 5 | **Ресурси видаляються в кінці кожної сесії**: платформа (роль `vocareum`) видаляє наші стеки | CloudTrail: `DeleteStack … assumed-role/vocareum/resource` о кінці таймера; `list-stacks` | щосесії піднімати все з IaC; дані сховища зникають → YCSB і демо — в межах однієї сесії; CI/CD має розгортати з нуля |
| 6 | Публічні IPv4 платні (≈ $3.6/міс кожна) | ціни AWS (звірити в завданні 7) | мінімум публічних адрес (`MapPublicIpOnLaunch: false`) |
| 7 | Бюджет **$20** на весь курс | лаба | завдання 7 |

## Що це означає для дизайну M2

- Ліміт **9 EC2** — головна межа для варіанту «шарди на серверах»; рахувати сервери для M3 і M5 уже зараз.
- **Lambda** (400 одночасних виконань) — реальна альтернатива для coordinator і/або шардів, і вона не займає слотів з 9 EC2.
- **DynamoDB** як сховище шардів має величезний запас пропускної здатності.
- Через **очищення в кінці сесії** розгортання має бути швидким і повністю автоматичним (завдання 8 і CI/CD у M2).
