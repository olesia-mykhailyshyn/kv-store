# M1: архітектура в діаграмах

Наочна шпаргалка до M1: що збудовано, як воно пов'язане і чому так. Діаграми у форматі **Mermaid**: GitHub малює їх автоматично; у VS Code — у попередньому перегляді Markdown (Cmd+Shift+V) з розширенням «Markdown Preview Mermaid Support».

Детальні пояснення — у [журналі](journal-uk.md), рішення — в ADR (`docs/adr/`).

---

## 1. Загальна картина M1

Що лежить у репо, що з нього стає в AWS, а що лише перевіряється.

```mermaid
flowchart LR
    subgraph repo["Репозиторій (git)"]
        spec["openspec/changes/add-m1-infrastructure<br/>proposal · specs · design · tasks"]
        adr["docs/adr<br/>ADR-001…004"]
        net["infra/network.yaml"]
        iam["infra/iam-readonly.yaml"]
    end

    subgraph aws["AWS-пісочниця (us-east-1)"]
        stackNet["Стек kv-network<br/>VPC · IGW · 6 підмереж · 2 route tables"]
        stackIam["Стек kv-iam<br/>(не створюється: IAM заборонено)"]
    end

    spec -->|"spec before code"| net
    spec --> iam
    adr -.->|"рішення"| net
    adr -.-> iam
    net -->|"aws cloudformation deploy"| stackNet
    iam -->|"validate-template + policy simulator"| check["Перевірка без деплою"]
    iam -.->|"одна навмисна спроба → AccessDenied"| stackIam
```

- **Шаблон** (`.yaml`) — бланк замовлення; **стек** — виконане замовлення в AWS (журнал, крок 53).
- Мережу деплоїмо; IAM лише описуємо і перевіряємо, бо пісочниця не дає створювати ролі (ADR-002, розбір ролі).

---

## 2. Мережа: VPC, 3 AZ, 6 підмереж

```mermaid
flowchart TB
    internet(("Інтернет"))
    igw["Internet Gateway<br/>kv-network-igw"]
    internet <--> igw

    subgraph vpc["VPC kv-network-vpc · 10.0.0.0/16"]
        subgraph az1["AZ 1 (us-east-1a)"]
            pa["public-a<br/>10.0.0.0/24"]
            ra["private-a<br/>10.0.10.0/24"]
        end
        subgraph az2["AZ 2 (us-east-1b)"]
            pb["public-b<br/>10.0.1.0/24"]
            rb["private-b<br/>10.0.11.0/24"]
        end
        subgraph az3["AZ 3 (us-east-1c)"]
            pc["public-c<br/>10.0.2.0/24"]
            rc["private-c<br/>10.0.12.0/24"]
        end
    end

    igw <--> pa
    igw <--> pb
    igw <--> pc
```

- **VPC** — «ділянка ЖК з парканом»; **підмережі** — «будинки»; **IGW** — «ворота в місто» (кроки 13, 16).
- Адреса `10.0.X.Y`: `X` = номер підмережі (0–9 публічні, 10–19 приватні; остання цифра = AZ), `Y` = пристрій (ADR-003, крок 25).
- У кожній AZ — **пара** публічна + приватна: падіння однієї AZ не забирає ні вхід, ні дані (крок 57).
- Приватні підмережі **без зв'язку з IGW** — лише для внутрішнього доступу (isolated subnets, крок 23).

---

## 3. Маршрутизація: 2 таблиці, 6 прив'язок

```mermaid
flowchart LR
    igw["Internet Gateway"]

    subgraph pubrt["PublicRouteTable (правила для публічних)"]
        p1["рядок 1: 10.0.0.0/16 → local<br/>адреса в нашій мережі → доставити всередині"]
        p2["рядок 2: 0.0.0.0/0 → IGW<br/>будь-яка інша адреса → через ворота в інтернет"]
    end
    subgraph privrt["PrivateRouteTable (правила для приватних)"]
        q1["рядок 1: 10.0.0.0/16 → local<br/>адреса в нашій мережі → доставити всередині<br/>(іншого рядка нема → в інтернет дороги нема)"]
    end
    subgraph mainrt["Main route table (AWS створює сам, ні до чого не прив'язана)"]
        m1["10.0.0.0/16 → local"]
    end

    p2 --> igw

    pa["public-a"] -->|прив'язка| pubrt
    pb["public-b"] -->|прив'язка| pubrt
    pc["public-c"] -->|прив'язка| pubrt
    ra["private-a"] -->|прив'язка| privrt
    rb["private-b"] -->|прив'язка| privrt
    rc["private-c"] -->|прив'язка| privrt
```

**Як читати:** зліва — підмережі; стрілка «прив'язка» = «ця підмережа користується цією таблицею»; сіра рамка — таблиця, всередині — її рядки (маршрути); стрілка з рядка 2 — куди він веде. Приклад: сервер у `public-b` шле пакет на `8.8.8.8` → дивиться в PublicRouteTable → адреса не в `10.0.0.0/16`, підходить рядок 2 → пакет іде в Internet Gateway → в інтернет. Той самий сервер шле на `10.0.11.5` → підходить рядок 1 → пакет лишається всередині VPC.

- **Route table = навігатор підмережі**: за адресою призначення каже, куди везти пакет (крок 62).
- Таблиць **дві**, прив'язок (`SubnetRouteTableAssociation`) — **шість**: по одній на підмережу (крок 61).
- Єдина різниця таблиць — рядок `0.0.0.0/0 → IGW`. Саме він робить підмережу **публічною**.
- `local` є в кожній таблиці автоматично → усі підмережі бачать одна одну.
- **Main route table** ні до чого не прив'язана і без маршруту до IGW — «забута» підмережа не стане публічною випадково (крок 12).

---

## 4. Куди йде пакет

```mermaid
flowchart TB
    start["Пакет з підмережі на адресу X"] --> q{"X усередині<br/>10.0.0.0/16?"}
    q -->|так| local["local: доставити всередині VPC ✅"]
    q -->|ні| t{"Яка таблиця<br/>в підмережі?"}
    t -->|PublicRouteTable| out["0.0.0.0/0 → IGW → інтернет ✅"]
    t -->|PrivateRouteTable| drop["маршруту нема → пакет відкинуто ❌"]
```

AWS обирає **найточніший** маршрут (більше число після `/`): для `10.0.11.5` спрацює `local`, а не `0.0.0.0/0` (крок 60).

---

## 5. Як CloudFormation створює ресурси (залежності)

Порядок визначається посиланнями `!Ref` і `DependsOn`, а не порядком у файлі (крок 55).

```mermaid
flowchart LR
    vpc["Vpc"] --> att["InternetGatewayAttachment"]
    igw["InternetGateway"] --> att
    vpc --> subnets["6 × Subnet"]
    vpc --> rts["PublicRouteTable<br/>PrivateRouteTable"]
    att -->|"DependsOn"| route["PublicDefaultRoute<br/>0.0.0.0/0 → IGW"]
    rts --> route
    subnets --> assoc["6 × SubnetRouteTableAssociation"]
    rts --> assoc
```

`DependsOn: InternetGatewayAttachment` — маршрут до воріт можна створити лише після того, як ворота **приєднано** до VPC (крок 57).

---

## 6. Права в пісочниці: три «замки»

```mermaid
flowchart LR
    me["Ми: сесія ролі voclabs"] --> l1["ReadOnlyAccess<br/>(AWS, Allow)<br/>читати майже все"]
    l1 --> l2["Pvoclabs<br/>(Academy, Allow + explicit Deny)<br/>створювати сервіси; не чіпати ролі платформи"]
    l2 --> l3["SCP організації<br/>(explicit Deny)<br/>не обійти зсередини акаунта"]
    l3 --> res{"Дія дозволена?"}
```

| Тип заборони | Текст помилки AWS | Приклад |
|---|---|---|
| implicit deny | `no identity-based policy allows` | `iam:CreateRole` |
| explicit deny у ролі | `explicit deny in an identity-based policy` | `iam:GetRole` на `voclabs` |
| explicit deny від SCP | `explicit deny in a service control policy` | роль `vocareum` |

Детально — [role-dissection.md](role-dissection.md).

---

## 7. Як AWS вирішує «можна чи ні»

```mermaid
flowchart TB
    req["Запит: дія над ресурсом"] --> d{"Є explicit Deny<br/>(роль або SCP)?"}
    d -->|так| deny1["ЗАБОРОНЕНО ❌<br/>жоден Allow не перебʼє"]
    d -->|ні| a{"Є Allow?"}
    a -->|так| allow["ДОЗВОЛЕНО ✅"]
    a -->|ні| deny2["ЗАБОРОНЕНО ❌<br/>(implicit deny)"]
```

Лекція 2, слайди 14–17; контрольне питання 7 (крок 18).

---

## 8. Read-only роль для аудитора (завдання 2–3, не деплоїться)

```mermaid
flowchart LR
    auditor["Аудитор<br/>(інший AWS-акаунт)"] -->|"sts:AssumeRole<br/>лише з MFA"| role["Роль kv-readonly"]
    role --> pol["Політика kv-readonly"]
    pol --> allowS["Allow: перегляд<br/>VPC, стеки, таблиці, бакети, Lambda,<br/>метрики, ролі, квоти"]
    pol --> denyS["Explicit Deny: вміст даних і секрети<br/>s3:GetObject · dynamodb:GetItem/Query/Scan ·<br/>secretsmanager:GetSecretValue · kms:Decrypt"]
```

- **Trust policy** — хто може взяти роль (аудитор з іншого акаунта, з MFA); **permissions policy** — що можна, коли взяв (ADR-004, кроки 27–31).
- Explicit Deny — «запобіжник»: дані закриті, навіть якщо колись хтось розширить Allow (крок 31).

---

## 9. Як M2 підключиться до мережі (Exports)

```mermaid
flowchart LR
    subgraph net["Стек kv-network"]
        ex["Exports<br/>kv-network-VpcId<br/>kv-network-VpcCidr<br/>kv-network-PublicSubnetA/B/CId<br/>kv-network-PrivateSubnetA/B/CId<br/>kv-network-PublicSubnetIds<br/>kv-network-PrivateSubnetIds"]
    end
    subgraph app["Стеки M2 (kv-app-…)"]
        imp["Fn::ImportValue"]
    end
    ex -->|"за назвою, завжди актуальні ID"| imp
```

- Після перестворення мережі (завдання 8) ID підмереж нові — імпорт за назвою бере актуальні (крок 22).
- Поки M2 імпортує значення, CloudFormation не дасть видалити `kv-network` → видаляємо у зворотному порядку: спершу застосунок, потім мережа (ADR-002).

---

## 10. Як мережа потрапляє в AWS: change set → execute (завдання 4.3)

**Що зробили.** Не створювали ресурси «наосліп», а спершу попросили CloudFormation **показати план** — change set — і лише після перегляду його виконали.

```bash
# 1. Попередній перегляд: що буде створено (нічого не створює)
aws cloudformation create-change-set \
  --stack-name kv-network --change-set-name initial-create --change-set-type CREATE \
  --template-body file://infra/network.yaml \
  --tags Key=Project,Value=kv-store Key=Milestone,Value=m1 \
  --profile kse-lab
aws cloudformation describe-change-set --stack-name kv-network --change-set-name initial-create --profile kse-lab

# 2. Виконання: CloudFormation створює ресурси
aws cloudformation execute-change-set --stack-name kv-network --change-set-name initial-create --profile kse-lab
aws cloudformation wait stack-create-complete --stack-name kv-network --profile kse-lab
```

**Команди частина за частиною:**

| Частина | Що означає |
|---|---|
| `aws cloudformation` | звертаємось до сервісу CloudFormation |
| `create-change-set` | «підготуй план змін» (нічого не створює) |
| `--stack-name kv-network` | для якого стека план (стека ще нема — з'явиться оболонка `REVIEW_IN_PROGRESS`) |
| `--change-set-name initial-create` | назва плану — щоб потім виконати саме його |
| `--change-set-type CREATE` | стека ще нема → план **створення** (для наявного стека — `UPDATE`) |
| `--template-body file://infra/network.yaml` | шаблон з диска (`file://` = «прочитай файл») |
| `--tags Key=…,Value=…` | теги на весь стек → CloudFormation чепить їх на кожен ресурс |
| `--profile kse-lab` | які ключі використати (наш профіль пісочниці) |
| `describe-change-set` | показати план: список `Action` (`Add`/`Modify`/`Remove`) для кожного ресурсу |
| **`execute-change-set`** | «**виконуй** цей план»: CloudFormation починає реально створювати ресурси; стек переходить у `CREATE_IN_PROGRESS` |
| `--stack-name kv-network --change-set-name initial-create` | який саме план виконати (у стека може бути кілька планів) |
| `wait stack-create-complete` | чекати, поки стек стане `CREATE_COMPLETE` (або повідомити про помилку) |

Після `execute-change-set` план «використано»: щоб змінити стек ще раз, створюємо **новий** change set (тип `UPDATE`).

**Порівняння з `aws cloudformation deploy`:** `deploy` робить те саме однією командою (створює change set і одразу виконує). Ми розділили на два кроки, щоб **переглянути план до виконання**.

**Результат першого деплою (2026-11-01):** change set — 18 × `Add`; після `execute-change-set` стек `kv-network` = `CREATE_COMPLETE` за ~63 с від команди (перший ресурс `Vpc` готовий о 00:19:36 UTC, стек — о 00:20:03 UTC); теги стека `Project=kv-store`, `Milestone=m1`; 10 експортів `kv-network-…` на місці. VPC `vpc-0762773ace736d258`.

**Що означає change set.** «Список змін» між тим, що є в AWS зараз, і тим, що описано в шаблоні: які ресурси буде **додано** (`Add`), **змінено** (`Modify`, з позначкою, чи доведеться ресурс **замінити**) або **видалено** (`Remove`). Перегляд нічого не змінює; виконати його — окрема команда.

**Навіщо.** Побачити наслідки **до** того, як вони стались: що нічого зайвого (наприклад, платного NAT) не створиться і нічого потрібного не видалиться. Для першого створення це перевірка «шаблон = очікування» (у нас — 18 × `Add`); для майбутніх оновлень — захист від випадкового видалення чи заміни ресурсу. Change set — відповідь на питання «**що станеться?**» (до зміни); drift detection — «**чи змінив хтось руками?**» (після). Контрольне питання 9.

**Як CloudFormation створює ресурси.**

```mermaid
sequenceDiagram
    participant Me as Ми (Mac, AWS CLI)
    participant CFN as CloudFormation (в AWS)
    participant EC2 as Сервіс EC2/VPC (в AWS)

    Me->>CFN: create-change-set (шаблон network.yaml)
    CFN-->>Me: план: 18 × Add
    Me->>CFN: execute-change-set
    Note over CFN: будує граф залежностей з !Ref і DependsOn
    CFN->>EC2: CreateVpc, CreateInternetGateway (паралельно)
    EC2-->>CFN: vpc-…, igw-…
    CFN->>EC2: AttachInternetGateway, CreateSubnet ×6, CreateRouteTable ×2
    EC2-->>CFN: subnet-…, rtb-…
    CFN->>EC2: CreateRoute 0.0.0.0/0 → igw (після Attach), AssociateRouteTable ×6
    EC2-->>CFN: готово
    Note over CFN: записує: логічне ім'я → реальний ID; статус CREATE_COMPLETE
    Me->>CFN: describe-stacks / list-stack-resources
    CFN-->>Me: статус, Outputs, ресурси
```

1. Ми лише **надсилаємо шаблон** — уся робота відбувається **на боці AWS**. Тому, навіть якщо сесія лаби закінчиться посеред деплою, CloudFormation продовжить.
2. CloudFormation **будує граф залежностей**: з `!Ref Vpc` бачить, що підмережі чекають на VPC; з `DependsOn` — що маршрут чекає на приєднання воріт (діаграма 5). Незалежні ресурси створює **паралельно**.
3. Для кожного ресурсу **викликає API відповідного сервісу** (`ec2:CreateVpc`, `ec2:CreateSubnet`…) — **з нашими правами** (сесія ролі `voclabs`). Тому важливо, що симулятор показав `ec2:CreateVpc` = allowed (role-dissection, розділ 5).
4. **Запам'ятовує** відповідність «логічне ім'я в шаблоні → реальний ID» (`Vpc → vpc-0a1b…`) — це і є стек.
5. Якщо хоч один ресурс не створився — **rollback**: видаляє все, що встиг створити (стек атомарний, ADR-002).

---

## 11. Drift detection: чи збігається AWS з шаблоном (завдання 4.8)

**Що таке drift («дрейф»).** Стан, коли реальні ресурси в AWS **відпливли** від того, що описано в шаблоні: хтось змінив їх **руками** (у консолі, CLI) в обхід CloudFormation. **Drift detection** — перевірка, яку запускає CloudFormation: порівнює кожен ресурс стека з шаблоном і каже, що змінено.

**Аналогія.** Будинок збудовано за кресленням. Потім хтось перефарбував двері, а креслення не оновив — тепер креслення **бреше**. Drift detection — інспектор, який обходить будинок з кресленням у руках і записує розбіжності.

**Навіщо.** Головна обіцянка IaC: **код = правда**. Ручні зміни її ламають:
- при перестворенні з шаблону (завдання 8) ручна зміна **зникне** — і щось перестане працювати;
- наступне оновлення стека може **перезаписати** ручну зміну без попередження;
- на захисті не поясниш «чому так», якщо цього нема в коді.

```mermaid
flowchart LR
    tpl["Шаблон<br/>(очікуваний стан)"] --> cmp{"CloudFormation<br/>порівнює"}
    real["Ресурси в AWS<br/>(реальний стан)"] --> cmp
    cmp -->|"усе збігається"| sync["IN_SYNC ✅"]
    cmp -->|"є розбіжності"| drift["DRIFTED ⚠️<br/>+ які властивості змінено"]
```

**Статуси.** Стек: `IN_SYNC` (збігається) / `DRIFTED` (є розбіжності). Кожен ресурс: `IN_SYNC`, `MODIFIED` (змінено властивості), `DELETED` (видалено руками), `NOT_CHECKED` (тип ресурсу перевірку не підтримує).

**Команди:**
```bash
# запустити перевірку (повертає ID перевірки)
aws cloudformation detect-stack-drift --stack-name kv-network --profile kse-lab
# статус перевірки і загальний результат стека
aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id <ID> --profile kse-lab
# деталі по кожному ресурсу: що очікувалось, що насправді
aws cloudformation describe-stack-resource-drifts --stack-name kv-network --profile kse-lab
```

**Обмеження (важливо для захисту).**
- Бачить зміни **властивостей ресурсів, що вже є в стеку**. **Не бачить** нових ресурсів, доданих руками поза стеком (наприклад, зайвий маршрут у таблиці як окремий об'єкт).
- Не всі типи ресурсів підтримують перевірку (`NOT_CHECKED`).
- Перевірка лише **показує** розбіжності, сама нічого не виправляє.

**Change set vs drift detection** (контрольне питання 9):

| | Change set | Drift detection |
|---|---|---|
| Коли | **до** зміни | **після** |
| Порівнює | шаблон **новий** ↔ стек | шаблон **поточний** ↔ реальні ресурси |
| Питання | «що станеться, якщо застосую?» | «чи змінив хтось руками?» |

---

## 12. Як працює шаблон IAM (ресурси і зв'язки)

```mermaid
flowchart LR
    param["Parameters<br/>TrustedAccountId (12 цифр)"]
    subgraph tpl["infra/iam-readonly.yaml"]
        pol["ReadOnlyPolicy<br/>AWS::IAM::ManagedPolicy kv-readonly<br/>Allow: 49 дій перегляду<br/>Deny: 21 дія (дані, секрети)"]
        role["ReadOnlyRole<br/>AWS::IAM::Role kv-readonly<br/>MaxSessionDuration 1 год"]
        trust["Trust policy (усередині ролі)<br/>Principal: arn:aws:iam::ID:root<br/>Condition: MFA = true"]
    end
    out["Outputs<br/>ReadOnlyRoleArn · ReadOnlyPolicyArn"]

    param -->|"!Sub"| trust
    trust --- role
    pol -->|"!Ref → ManagedPolicyArns<br/>(прикріплено до ролі)"| role
    role -->|"!GetAtt .Arn"| out
    pol -->|"!Ref"| out
```

- **Політика** — ЩО можна; **роль** — картка, до якої політику прикріплено; **trust policy** — КОМУ видавати картку (README, розділ «M1 — IAM read-only»).
- Ролі можуть мати кілька політик; у `kv-readonly` — свідомо одна (spec).

## 13. Як аудитор користувався б роллю (у справжньому акаунті)

```mermaid
sequenceDiagram
    participant A as Аудитор (свій акаунт)
    participant STS as AWS STS
    participant AWS as Наш акаунт (сервіси)

    A->>A: вхід з MFA (код з телефона)
    A->>STS: sts:AssumeRole role/kv-readonly + MFA-код
    STS->>STS: trust policy: його акаунт? MFA? ✅
    STS-->>A: тимчасові ключі (≤ 1 год)
    A->>AWS: ec2:DescribeVpcs
    AWS-->>A: ✅ allowed (Allow)
    A->>AWS: ec2:CreateVpc
    AWS-->>A: ❌ AccessDenied (implicit deny — нема Allow)
    A->>AWS: s3:GetObject / dynamodb:Scan
    AWS-->>A: ❌ AccessDenied (explicit deny — запобіжник)
    Note over A,STS: через 1 год ключі недійсні (ExpiredToken) → знову AssumeRole з MFA
```

## 14. Як ми перевіряємо IAM без деплою (завдання 5.2)

```mermaid
flowchart LR
    yaml["infra/iam-readonly.yaml"] -->|"Ruby YAML → JSON"| json["документ політики<br/>kv-readonly (JSON)"]
    json --> sim["aws iam simulate-custom-policy<br/>(нічого не створює)"]
    broad["чужа політика<br/>Allow s3:*"] -.->|"лише в 4-й групі"| sim
    sim --> g1["перегляд → allowed ✅"]
    sim --> g2["зміни → implicitDeny ✅"]
    sim --> g3["дані/секрети → explicitDeny ✅"]
    sim --> g4["+ s3:* → explicitDeny ✅"]
    g1 & g2 & g3 & g4 --> res["scripts/check-iam-readonly.sh<br/>33/33 ok"]
```

Симулятор — «льотний тренажер»: проганяє той самий алгоритм, що й справжній AWS (explicit Deny → Allow → implicit deny, діаграма 7), але без реального запиту.
