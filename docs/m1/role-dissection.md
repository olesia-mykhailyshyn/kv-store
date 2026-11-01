# M1, завдання 1: розбір ролі пісочниці

> Dissect the LabRole. Write down what it allows, what it denies, and — most importantly — how you found out (console, CLI, policy simulator).

Середовище: AWS Academy Cloud Architecting 3 — Sandbox, регіон `us-east-1`, AWS CLI профіль `kse-lab`. Номер акаунта в усіх виводах замінено на `<ACCOUNT>`.

**Короткий підсумок — [розділ 7](#7-підсумок-дозволено--заборонено--як-зясували).**

Розбір іде від найменш до найбільш прямого джерела: **що обіцяють** (Readme) → **що записано** (політики ролі) → **що відбувається насправді** (simulator і проби).

## 1. Readme пісочниці (що обіцяє AWS Academy)

Джерело: права панель лаби «AWS Academy Cloud Architecting 3 - Sandbox», розділ *«You will be restricted to the following services and usage»*.

| Сервіс | Обмеження з Readme |
|---|---|
| AWS Cloud9 | інстанси nano, micro, small |
| Amazon CloudFormation | — |
| Amazon CloudFront | — |
| AWS CloudShell | — |
| AWS CloudTrail | — |
| Amazon CloudWatch | — |
| Amazon DynamoDB | — |
| Amazon EC2 | типи лише `t2.nano`–`t2.medium`, `t3.nano`–`t3.medium`; EBS до 35 ГБ, лише `gp2`; лише On-Demand; лише AMI від Amazon (Linux, Windows); **до 9 інстансів — спроба запустити більше призведе до блокування (suspend) акаунта лаби** |
| Amazon EC2 Auto Scaling | — |
| Elastic Load Balancing | — |
| AWS IAM | **лише читання (Read-only access)** |
| AWS KMS | лише перелік (List access) |
| AWS Lambda | — |
| Amazon RDS | `db.t3.micro`–`db.t3.medium`; EBS до 100 ГБ `gp2`; On-Demand; Aurora, MySQL, PostgreSQL, MariaDB; **Multi-AZ не підтримується**; Enhanced monitoring не підтримується |
| Amazon Route 53 | не можна реєструвати домени |
| Amazon S3 | — |
| Amazon S3 Glacier | — |
| Amazon SNS | — |
| AWS Systems Manager (SSM) | — |
| Tagging | — |

Інші факти з Readme:
- *«Do not change the lab Region unless specifically instructed to do so»*.
- Вбудований термінал лаби працює на сервері **поза** нашим AWS-акаунтом і вже має ключі для AWS CLI.
- Для SSH на EC2 треба брати готову пару ключів **`vockey`**.

### Що Readme означає для нашого проєкту

- **IAM — лише читання.** Створити політику і роль для завдань 2–3 не вийде (це очікувано за умовою). Ролей для майбутніх сервісів (Lambda, EC2) теж не створимо: треба знайти готові (див. розділ 4).
- **Ліміт 9 EC2-інстансів з блокуванням акаунта.** Критично для M3–M5: 2 шарди × 3 репліки + coordinator = 7 інстансів, а автомасштабування в M5 легко перевищить 9. Ліміт треба закласти в дизайн M2–M5 (або будувати на Lambda / DynamoDB, які в цей ліміт не входять).
- **У переліку нема** API Gateway, SQS, ECS/ECR, Secrets Manager. VPC окремо не названо, але мережеві ресурси VPC належать до API EC2.
- **Точка входу M2:** ALB є (Elastic Load Balancing), API Gateway — ні.
- **RDS без Multi-AZ** — реплікацію між AZ (M3) RDS нам не дасть; ми й так будуємо свою.

## 2. Хто ми (CLI)

```bash
aws sts get-caller-identity --profile kse-lab
```
```json
{
  "UserId": "AROAZLP3FKVQNG7TJEUY4:user5162300=omykhailyshyn@kse.org.ua",
  "Account": "<ACCOUNT>",
  "Arn": "arn:aws:sts::<ACCOUNT>:assumed-role/voclabs/user5162300=omykhailyshyn@kse.org.ua"
}
```
- `assumed-role/voclabs/…` — ми **сесія ролі `voclabs`**, а не IAM-користувач; префікс `AROA` в `UserId` — ID ролі.
- Завдання називає роль `LabRole`. У нашій пісочниці ми працюємо під `voclabs`; чи існує `LabRole`, перевіряємо в розділі 4.

## 3. Політики ролі `voclabs` (CLI)

| Команда | Результат |
|---|---|
| `aws iam list-attached-role-policies --role-name voclabs` | прикріплена AWS-керована **`ReadOnlyAccess`** (`arn:aws:iam::aws:policy/ReadOnlyAccess`) |
| `aws iam list-role-policies --role-name voclabs` | вбудована політика **`Pvoclabs`** |
| `aws iam get-role --role-name voclabs` | **AccessDenied** — `…not authorized to perform: iam:GetRole on resource: role voclabs with an explicit deny in an identity-based policy` |
| `aws iam get-role-policy --role-name voclabs --policy-name Pvoclabs` | **AccessDenied** — `…iam:GetRolePolicy … with an explicit deny in an identity-based policy` |
| `aws iam get-policy-version --policy-arn arn:aws:iam::aws:policy/ReadOnlyAccess --version-id v191` | текст `ReadOnlyAccess`: версія v191, 3 statement'и, **усі `Allow`, жодного `Deny`**, 3061 дія, зокрема `iam:Get*`, `iam:List*`, `ec2:Describe*`, `s3:Get*`, `dynamodb:Get*`, `dynamodb:Query`, `dynamodb:Scan` |

### Висновки
1. Права `voclabs` = **`ReadOnlyAccess` + `Pvoclabs`**.
2. Текст `Pvoclabs` AWS Academy **приховує** (explicit deny на читання власної ролі).
3. **Явний Deny сидить у `Pvoclabs`.** Помилка вказує на explicit deny в identity-based policy; таких політик у ролі дві; у `ReadOnlyAccess` Deny нема, і вона навіть дозволяє `iam:Get*`. Отже Deny — лише в `Pvoclabs`, і він перемагає Allow з `ReadOnlyAccess`.
4. IAM заборонено **вибірково**: `list-*` про власну роль проходять, `get-*` — ні.
5. `ReadOnlyAccess` дозволяє читати й **дані** (`s3:Get*` включає `GetObject`, `dynamodb:Scan`) — тому для нашого аудитора ми її не беремо (ADR-004).

## 4. Інші ролі в акаунті

```bash
aws iam list-roles --profile kse-lab --query 'Roles[].[RoleName,Path,CreateDate]' --output table
aws iam list-instance-profiles --profile kse-lab
# для кожної ролі: хто може брати (trust policy з list-roles) і які політики
aws iam list-attached-role-policies --role-name <роль> --profile kse-lab
aws iam list-role-policies --role-name <роль> --profile kse-lab
```

**`LabRole` в акаунті нема.** Ролі, створені не AWS-сервісами автоматично:

| Роль | Хто може брати (trust) | Політики | Що це для нас |
|---|---|---|---|
| `voclabs` | — (`get-role` заборонено) | `ReadOnlyAccess` + inline `Pvoclabs` | **наша роль** (розділи 2–3) |
| `vocareum` | **інший акаунт** (платформа Vocareum): `…:root` і `…:user/addmember` | `AdministratorAccess` | **службова роль платформи**, через яку Academy керує акаунтом; для нас недоступна |
| `vocareum-eventbridge` | — | — | службова роль платформи |
| `lambda-run-role` | сервіс `lambda.amazonaws.com` | inline `lambda-execution-policy` | **готова роль для Lambda** — кандидат для M2 |
| `myBasicLambdaRole` | сервіс `lambda.amazonaws.com` | `AWSLambdaBasicRole` | ще одна готова роль для Lambda (кандидат) |
| `myS3Role` | сервіс `ec2.amazonaws.com`; є instance profile `myS3Role` | `AccessS3Bucket` | **готова роль для EC2** (через instance profile) — кандидат для M2 |
| `EMR_DefaultRole`, `EMR_EC2_DefaultRole`, `EMR_AutoScaling_DefaultRole` | сервіси EMR / EC2 | керовані політики EMR | для Amazon EMR (не в Readme); нам не потрібні |
| `AWSServiceRoleFor…` (AutoScaling, Cloud9, CloudWatchEvents, ElastiCache, ElasticLoadBalancing, Organizations, Support, TrustedAdvisor) | відповідні сервіси AWS | — | **service-linked roles**: AWS створює їх сам для своїх сервісів |

**Знахідка: в акаунті є Service Control Policy (SCP).** Спроба `aws iam list-role-policies --role-name vocareum`:
```
AccessDenied … not authorized to perform: iam:ListRolePolicies on resource: role vocareum
with an explicit deny in a service control policy:
arn:aws:organizations::<ACCOUNT>:policy/o-saorexhpfm/service_control_policy/p-fmfaf9og
```
- Акаунт пісочниці **входить до AWS Organizations** (`o-saorexhpfm`), і над ним діє **SCP** — обмеження рівня організації, яке **не обійти зсередини акаунта**, хоч би які політики мала роль.
- Лекція 2 (слайд 24) каже «у пісочниці Org немає» — **для нас** це правда в сенсі «ми не можемо створювати організацію чи SCP», але сам акаунт **керується** організацією Academy/Vocareum, і її SCP на нас діє.
- Отже наші права обмежені **трьома шарами**: `ReadOnlyAccess` (Allow) → `Pvoclabs` (Allow + explicit Deny) → **SCP організації** (explicit Deny). Звідки саме прийшла заборона, видно з тексту помилки: «identity-based policy» або «service control policy».

**Висновок для M2:** свої ролі створити не можна, але в акаунті вже є **готові ролі для Lambda** (`lambda-run-role`, `myBasicLambdaRole`) і **для EC2** (`myS3Role` з instance profile). Що саме вони дозволяють, перевіримо, коли проєктуватимемо M2.

## 5. Policy simulator

Симулятор відповідає на питання «чи дозволено principal'у дію X над ресурсом Y?», **нічого не виконуючи**. Результат: `allowed` / `implicitDeny` (нема Allow) / `explicitDeny` (є Deny). Колонка `AllowedByOrganizations` показує, чи пропускає дію **SCP організації**.

**Обидва симулятори доступні:**
- `simulate-custom-policy` (перевірка довільного JSON-документа) — **працює**: тестова політика `Allow ec2:Describe*` → `ec2:DescribeVpcs` = `allowed`, `ec2:CreateVpc` = `implicitDeny`. Отже метод перевірки нашої read-only політики (design, рішення 6) робочий.
- `simulate-principal-policy` (перевірка реальної ролі `voclabs`) — **працює**.

### Ключові дії, ресурс `*` (за замовчуванням)
```bash
aws iam simulate-principal-policy --policy-source-arn "arn:aws:iam::<ACCOUNT>:role/voclabs" \
  --action-names ec2:DescribeVpcs ec2:CreateVpc cloudformation:CreateStack iam:CreateRole ... \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision,OrganizationsDecisionDetail.AllowedByOrganizations]' --output table
```
| Дія | Рішення | SCP пропускає | Що це для нас |
|---|---|---|---|
| `ec2:DescribeVpcs` | allowed | так | читати мережу |
| `ec2:CreateVpc` | **allowed** | так | **мережевий стек M1 задеплоїться** |
| `cloudformation:CreateStack` | **allowed** | так | CloudFormation доступний |
| `iam:CreateRole` | **implicitDeny** | так | роль для завдання 3 не створимо — **нема Allow** (не явна заборона) |
| `iam:CreatePolicy` | **implicitDeny** | так | політику для завдання 2 не створимо |
| `s3:CreateBucket` | allowed | так | |
| `dynamodb:CreateTable` | allowed | так | DynamoDB для M2+ |
| `lambda:CreateFunction` | allowed | так | Lambda для M2+ |
| `ec2:RunInstances` | allowed | так | сервери (з обмеженнями Readme) |
| `apigateway:POST` | **implicitDeny** | так | **API Gateway нема** (збігається з Readme) |
| `sqs:CreateQueue` | **implicitDeny** | так | **SQS нема** (збігається з Readme) |
| `organizations:CreateOrganization` | **explicitDeny** | **ні** | заборонено **SCP організації** |

### Уточнення для конкретних ролей (`--resource-arns`)
Симулятор із ресурсом `*` показав `iam:GetRole` = allowed, хоча справжній виклик на `voclabs` дав explicit deny, і `iam:PassRole` = implicitDeny. Тому перевірили ті самі дії на **конкретних** ролях:

| Дія | `voclabs` | `vocareum` | `lambda-run-role` | `myBasicLambdaRole` | `myS3Role` |
|---|---|---|---|---|---|
| `iam:GetRole` | **explicitDeny** | **explicitDeny** | allowed | allowed | allowed |
| `iam:PassRole` | implicitDeny | **explicitDeny** | **allowed** | **allowed** | **allowed** |

**Висновки:**
- **Заборони прив'язані до конкретних ресурсів.** `Pvoclabs` забороняє дивитися саме **на ролі платформи** (`voclabs`, `vocareum`), а на інші ролі — можна. Тому результат симулятора з `*` вводить в оману: перевіряти треба з конкретним ARN ресурсу.
- **Симулятор збігся з реальністю**, коли ресурс указано правильно (`get-role voclabs` → explicit deny в обох).
- **`iam:PassRole` дозволено для трьох готових ролей.** `PassRole` — дозвіл «вдягнути» роль на сервіс (наприклад, створюючи Lambda, вказати їй `lambda-run-role`). Отже в M2 **можна** запускати Lambda з `lambda-run-role`/`myBasicLambdaRole` і EC2 з `myS3Role`.
- Створення IAM — **implicitDeny** (просто нема Allow), а Organizations — **explicitDeny від SCP**: різні механізми заборони, і симулятор їх розрізняє.

## 6. Проби дій (справжні виклики)

Симулятор **передбачає**; проба показує, що відбувається **насправді**. Нічого не створено.

| # | Команда | Результат | Збігається з симулятором? |
|---|---|---|---|
| 1 | `aws ec2 describe-vpcs` | одна VPC: `vpc-01ac8f73c7cff0074`, `172.31.0.0/16`, `IsDefault = True` (default VPC) | так (`ec2:DescribeVpcs` = allowed) |
| 2 | `aws ec2 create-vpc --cidr-block 10.99.0.0/16 --dry-run` | `DryRunOperation: Request would have succeeded, but DryRun flag is set.` | так (`ec2:CreateVpc` = allowed) |
| 3 | `aws iam create-role --role-name kv-probe-should-fail --assume-role-policy-document '{…ec2…}'` | `AccessDenied … not authorized to perform: iam:CreateRole on resource: arn:aws:iam::<ACCOUNT>:role/kv-probe-should-fail because no identity-based policy allows the iam:CreateRole action` | так (`iam:CreateRole` = implicitDeny) |

Після проби 3 перевірено: ролі `kv-probe-should-fail` в акаунті нема (`aws iam list-roles --query "Roles[?RoleName=='kv-probe-should-fail']"` → порожньо).

**`--dry-run`** — прапорець EC2: AWS перевіряє права і параметри, але **нічого не створює**. Відповідь `DryRunOperation` = «дозволено», `UnauthorizedOperation` = «заборонено». В IAM такого прапорця нема, тому проба 3 — справжня спроба.

**Як за текстом помилки відрізнити тип заборони:**
| Текст помилки | Тип | Приклад |
|---|---|---|
| `because no identity-based policy allows the … action` | **implicit deny** — Allow просто нема | `iam:CreateRole` (проба 3) |
| `with an explicit deny in an identity-based policy` | **explicit deny** у політиці ролі (`Pvoclabs`) | `iam:GetRole` на `voclabs` (розділ 3) |
| `with an explicit deny in a service control policy: …` | **explicit deny** від SCP організації | `iam:ListRolePolicies` на `vocareum` (розділ 4) |

## 6a. CloudTrail: що платформа робить з нашими ресурсами

**CloudTrail** — журнал усіх викликів API в акаунті (хто, що, коли, звідки); є в Readme. Знайшли з його допомогою, що ресурси **не** зберігаються між сесіями:

```bash
aws cloudformation list-stacks --profile kse-lab \
  --query 'StackSummaries[].[StackName,StackStatus,CreationTime,DeletionTime]' --output text
aws cloudtrail lookup-events --profile kse-lab \
  --lookup-attributes AttributeKey=ResourceName,AttributeValue=kv-network \
  --start-time 2026-11-01T01:00:00Z --end-time 2026-11-01T03:00:00Z
```

| Що | Результат |
|---|---|
| `list-stacks` | `kv-network` — `DELETE_COMPLETE` о 02:09:48 UTC; у ту саму секунду видалено службовий стек платформи `c229528a…`, який створюється при кожному Start Lab (і створює ролі `lambda-run-role`, `myBasicLambdaRole`, `myS3Role`); старий `demo-vpc-stack` (жовтень) — так само |
| CloudTrail `DeleteStack` для `kv-network` | `userIdentity`: `arn:aws:sts::<ACCOUNT>:assumed-role/vocareum/resource`, IP `34.213.185.13`, `aws-sdk-php` |

**Висновок:** у кінці сесії (таймер ~3 год) платформа AWS Academy через свою роль `vocareum` (`AdministratorAccess`, розділ 4) **видаляє наші стеки**. Ресурси пісочниці **не переживають** сесію → усе піднімаємо з IaC щосесії.

## 7. Підсумок: дозволено / заборонено / як з'ясували

**Хто ми:** сесія ролі `voclabs` (не IAM-користувач і не `LabRole` — `LabRole` в акаунті нема).
**З чого складаються права — три шари:**
```
ReadOnlyAccess (AWS, Allow)  →  Pvoclabs (Academy, Allow + explicit Deny)  →  SCP організації (explicit Deny)
```

### Дозволено
| Що | Як з'ясували |
|---|---|
| Читати майже всі ресурси (`Describe*`, `List*`, `Get*`), зокрема й дані (`s3:Get*`, `dynamodb:Scan`) | текст `ReadOnlyAccess` (`get-policy-version`); проба `describe-vpcs` |
| Створювати мережі (VPC, підмережі, IGW) | симулятор `ec2:CreateVpc` = allowed; проба `create-vpc --dry-run` → `DryRunOperation` |
| CloudFormation, DynamoDB, Lambda, EC2, S3 | симулятор = allowed; Readme |
| EC2 — лише `t2`/`t3` nano–medium, EBS ≤ 35 ГБ `gp2`, **≤ 9 інстансів** | Readme |
| «Вдягати» на сервіси готові ролі `lambda-run-role`, `myBasicLambdaRole` (Lambda), `myS3Role` (EC2) | симулятор `iam:PassRole` з `--resource-arns` = allowed; `list-roles` |
| Користуватися policy simulator'ом (`iam:Simulate*`) | виклики повернули результати; `iam:Simulate*` у тексті `ReadOnlyAccess` |
| Бачити, які політики прикріплені до своєї ролі | `list-attached-role-policies`, `list-role-policies` пройшли |

### Заборонено
| Що | Тип заборони | Як з'ясували |
|---|---|---|
| Створювати IAM-ролі й політики | **implicit deny** (нема Allow) | проба `create-role` → `no identity-based policy allows`; симулятор = implicitDeny; Readme «IAM: Read-only» |
| Дивитися свою роль `voclabs` і текст `Pvoclabs` | **explicit deny** у `Pvoclabs` | `get-role`, `get-role-policy` → `explicit deny in an identity-based policy`; симулятор з `--resource-arns` = explicitDeny |
| Дивитися й «вдягати» службову роль платформи `vocareum` | **explicit deny** (`Pvoclabs` і SCP) | симулятор = explicitDeny; `list-role-policies vocareum` → `explicit deny in a service control policy` |
| API Gateway, SQS (і все, чого нема в Readme) | **implicit deny** | симулятор = implicitDeny; відсутні в Readme |
| Створювати AWS Organizations | **explicit deny від SCP** | симулятор: explicitDeny, `AllowedByOrganizations = False` |
| Pricing API (`pricing:GetProducts`) | **explicit deny від SCP** | `aws pricing get-products` → `AccessDeniedException … explicit deny in a service control policy` (2026-11-01, під час бюджету M2) |
| Понад 9 EC2-інстансів | правило платформи → **блокування акаунта** | Readme |
| RDS Multi-AZ, реєстрація доменів | правило платформи | Readme |

### Як з'ясовували (методи)
1. **Readme** — що Academy обіцяє.
2. **CLI**: `sts get-caller-identity` (хто ми), `iam list-*` / `get-*` (які політики), `get-policy-version` (текст публічної `ReadOnlyAccess`).
3. **Policy simulator**: `simulate-principal-policy` (наша роль), з `--resource-arns` для конкретних ресурсів; `simulate-custom-policy` (довільна політика).
4. **Проби**: справжні виклики, для EC2 — з `--dry-run`.
4a. **CloudTrail** (`lookup-events`) і `list-stacks` — хто і коли змінював ресурси акаунта.
5. **Текст помилки** як джерело: він називає шар заборони (identity-based policy / service control policy) і тип (explicit / «no policy allows»).

### Що це означає для проєкту
- **M1:** мережу деплоїмо по-справжньому; IAM-завдання 2–3 — лише як код, перевірка симулятором.
- **M2+:** сервіси отримують права через **готові ролі**; точка входу — з наявного (ALB або Lambda), API Gateway нема.
- **M3–M5:** ліміт **9 EC2** закладати в дизайн реплікації й автомасштабування.

### Поведінка платформи
- Наприкінці кожної сесії платформа (роль `vocareum`) видаляє наші стеки — CloudTrail (розділ 6a). Ресурси між сесіями не зберігаються.
- На кожному Start Lab платформа заново створює службовий стек і готові ролі для сервісів.

### Розбіжність з лекцією, яку ми знайшли
Лекція 2 (слайд 24): «у пісочниці Org немає». Насправді акаунт **входить** в AWS Organizations платформи і над ним діє **SCP** — ми лише не можемо створювати свою організацію.
