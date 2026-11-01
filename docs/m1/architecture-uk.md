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
