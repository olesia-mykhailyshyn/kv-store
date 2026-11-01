# M1: AI-критика архітектури

> **AI design review.** You bring an AI critique of your architecture and either accept or refute 2–3 of its points, with arguments.

Цей документ — для захисту M1. Чотири частини:
1. **промпт**, який отримав AI-рецензент (дослівно);
2. **як запускали** (який AI, коли, що йому дали);
3. **транскрипт** — повна відповідь рецензента, без змін;
4. **моє рішення** по 2–3 пунктах: приймаю чи спростовую — з аргументами.

## 1. Промпт для AI-рецензента

**Що рецензент знає і чого — ні (навмисно):**
- **знає факти:** текст завдання M1, обмеження середовища (пісочниця AWS Academy, $20, IAM лише на читання, ≤ 9 EC2, ресурси видаляються в кінці сесії), що буде в M2–M3, і **всі артефакти** (шаблони, ADR, design, specs, README);
- **не знає** нашого обговорення і журналу — щоб критика не підлаштовувалась під наші аргументи і не була упередженою;
- **роль — критик**, не помічник: шукати слабкі місця, а не хвалити.

Промпт (копіювати повністю):

```text
You are a senior cloud architect doing a critical design review of a student project.
Your job is to find weaknesses, risks and questionable decisions — not to praise.

PROJECT
Capstone for a Cloud Architectures course: a "light" serverless key-value store similar to
DynamoDB, built over 5 milestones. This review covers Milestone 1 (infrastructure setup) only.
Repository (public): https://github.com/olesia-mykhailyshyn/kv-store

MILESTONE 1 TASKS (from the assignment)
1. Dissect the sandbox role: what it allows, what it denies, and how you found out.
2. Create an IAM policy that allows ONLY to view resources (no write access). The sandbox
   cannot attach it, so deliver it as an IaC template and explain what it would do.
3. Create a role with this policy that could be assumed by another principal; describe it in
   IaC and explain why the sandbox refuses it.
4. Create a VPC with public and private subnets. Public has an Internet Gateway, private
   subnets are for internal access only.
5. Create an IaC stack holding the network resources.
6. Review the service quotas needed in Milestone 2 and write down the ones that can limit you.
7. Calculate the monthly budget for Milestone 2 (2 shards) and compare it with the sandbox
   credit ceiling.
8. Tear the whole stack down and bring it back from IaC alone; record time and what broke.
Mandatory ADRs: choice of IaC tool; stack layout; CIDR addressing scheme.

ENVIRONMENT CONSTRAINTS (facts, not decisions)
- AWS Academy Cloud Architecting sandbox, us-east-1. Total budget 20 USD for the whole course.
- We act as a session of the role "voclabs" (ReadOnlyAccess + a hidden inline policy + an
  organization SCP). IAM is read-only: we cannot create roles or policies.
- Max 9 EC2 instances (more suspends the account), only t2/t3 up to medium, EBS <= 35 GB gp2.
- Not available: API Gateway, SQS, ECS/ECR, Secrets Manager, Pricing API.
- Sessions last ~3 hours; at the end of each session the platform deletes our stacks, so
  everything is recreated from IaC every session.

WHAT COMES NEXT (context for your review)
- M2: coordinator node routing CREATE/READ/DELETE/EXISTS by consistent hashing to 2 shards that
  register on start; CI/CD; YCSB benchmark.
- M3: 3 replicas per shard, leader + log, quorum reads (R+W>N).
- M4–M5: monitoring, MCP server, autoscaling, ops agent.

FILES TO REVIEW (read them in the repository)
- infra/network.yaml, infra/iam-readonly.yaml
- docs/adr/0001-iac-tool-cloudformation.md ... docs/adr/0004-readonly-access-design.md
- openspec/changes/add-m1-infrastructure/ (proposal.md, design.md, specs/*/spec.md, tasks.md)
- docs/m1/role-dissection.md, docs/m1/iam-readonly.md, docs/m1/quotas.md,
  docs/m1/budget-m2.md, docs/m1/recreatability.md
- scripts/recreate-network.sh, scripts/check-iam-readonly.sh, scripts/m2_budget.py
- README.md

WHAT TO DO
Give 8–12 critique points. Cover: security, network design, IAM design, IaC quality,
cost, reliability/availability, operability (sessions, recreation, CI/CD readiness),
fitness for M2/M3, and whether the ADRs really justify their decisions.
Also challenge our assumptions if you think they are wrong.

FORMAT for each point:
- Title
- Severity: high / medium / low
- Evidence: the exact file and the part of it (quote it)
- Why it matters
- What you would do instead

Answer in Ukrainian; keep AWS and technical terms in English.
Do not rewrite the project; point out problems and explain them.
```

## 2. Як запускали

- AI-рецензент: окрема сесія Claude Code, модель Opus 5.5 (Medium). Це та сама модель, що допомагала будувати проєкт, тому незалежність обмежена: спільна лише модель, а не історія розмови.
- Дата: 2026-11-01
- Що дали: промпт вище; рецензент переглянув локальну копію репозиторію на коміті `56c35c0`. За його словами, журнал він не читав, до живого AWS не звертався.
- Окремий новий чат, без історії цієї роботи.

## 3. Транскрипт (повна відповідь, без змін)

```text
Written for: Олеся і транскрипт у ai-critique.md, розділ 3 (захист M1).

Переглянуто локальну копію на коміті 56c35c0, журнал не читала. До живого AWS не зверталася, тому твердження про поведінку AWS, позначені (перевірити), потребують підтвердження в акаунті. У репозиторії нічого не змінено.

1. Очищення платформою узагальнено з одного випадку на безплатних ресурсах
Severity: high
Evidence: budget-m2.md:57: «між сесіями нічого не працює і не тарифікується — це реалістичний сценарій». В role-dissection.md, розділ 6a доказ зводиться до DeleteStack для стеків без EC2, EBS і даних.
Why it matters: на цьому припущенні тримається вся цифра $2.7–5.4. Невідомо, що станеться зі стеком, який імпортує exports, з непорожнім S3 bucket (DELETE_FAILED) і з ресурсами поза стеками. Якщо kv-network видалятиметься одночасно з імпортером, його видалення може впасти (перевірити). Тоді платні ресурси можуть працювати далі й витрачати $20.
What I would do instead: до вибору архітектури M2 провести експеримент на t3.nano: EBS, непорожній bucket, таблиця DynamoDB, стек з ImportValue. У наступній сесії перевірити, що лишилося. Додати власний teardown наприкінці сесії й не покладатися на платформу.
2. Ліміт 9 EC2 не захищений технічно, а ADR-003 заклав більше вузлів
Severity: high
Evidence: ADR-003: «2→~4 шарди × 3 репліки, coordinator». Це 12+ вузлів. quotas.md для M3 дає «≈ 8 — впритул», а max-instances = 20 помилково виглядає як запас.
Why it matters: перевищення ліміту блокує акаунт, а AWS API сам цього не зупинить. За 8 інстансів заміна двох вузлів через CloudFormation за схемою create-before-delete дає 10 (перевірити, чи рахується така спроба). Для другого coordinator або нового шарду в M5 місця вже немає.
What I would do instead: скласти таблицю інстансів по кожному milestone, включно з rolling update і клієнтом YCSB. Перед кожним deploy перевіряти кількість інстансів і задавати жорсткий MaxSize в ASG. Для M5 розглянути логічні шарди на фіксованому наборі вузлів.
3. «Гарантія, що дані й секрети закриті» не виконується
Severity: high
Evidence: iam-readonly.yaml забороняє lambda:GetFunctionConfiguration з коментарем «Return environment variables», але дозволяє lambda:ListFunctions. iam-readonly.md сам визнає: «lambda:ListFunctions і cloudformation:Describe* їх показують». Дозволений також cloudformation:GetTemplate. ADR-004 обіцяє «гарантію… що не зламається від майбутніх розширень».
Why it matters:
Env vars доступні в обхід Deny, а user data, заборонена в EC2 і launch templates, може читатися через GetTemplate.
Deny-список неповний. Якщо додати ReadOnlyAccess (сценарій з ADR), можуть відкритися ssm:GetParameterHistory, ec2:GetConsoleOutput, ec2:GetLaunchTemplateData, logs:GetLogRecord (перевірити).
What I would do instead: прибрати lambda:ListFunctions і GetTemplate або прямо задокументувати їх як прийнятий ризик. Формулювання «гарантія» замінити на «запобіжник для перелічених дій». Секрети в шаблонах і env vars заборонити окремою перевіркою в CI.
4. Автоперевірки можуть бути зеленими за непрацюючої системи
Severity: high
Evidence:
check-iam-readonly.sh: done < <(aws iam simulate-custom-policy ...). Я запустила скрипт зі stub-ом aws, що завершується з помилкою ExpiredToken. Результат: «All checks passed.», exit code 0. Помилка в process substitution не спрацьовує для set -e, цикл просто не виконується.
recreate-network.sh не перевіряє прив'язки підмереж до route tables, хоча їх вимагає network spec. Якщо всі шість підмереж прив'язати до public route table, логіка перевірок усе одно дасть 9/9. Маршрут через NAT теж пройде.
Why it matters: «33/33» і «9/9» є головними доказами в README. Покриття теж часткове: 11 із 49 Allow-дій і 12 із 21 Deny-дії. Trust policy лише прочитана очима, живий трафік не тестувався, сценарій drift detection не виконувався (tasks.md, 4.8), хоча 9.5 вимагає перевірки всіх сценаріїв.
What I would do instead: перевіряти кількість отриманих результатів (рівно 33) і статус aws. Перевіряти CIDR і route table кожної підмережі. Генерувати списки дій із самого шаблону. Додати cfn-lint та Access Analyzer validate-policy.
5. Мережа «без перебудови» не готова до private-вузлів M2
Severity: high
Evidence: design.md: «готова до M2… без перебудови». budget-m2.md: «Код і пакети… через S3 Gateway Endpoint». Водночас:
у network.yaml endpoint немає, а route tables не експортуються;
recreate-network.sh вважає будь-який AWS::EC2::VPCEndpoint порушенням, хоча spec забороняє лише Interface;
design називає VPC endpoints платними без винятку для Gateway;
у документах M1 немає перевірки ec2:CreateVpcEndpoint.
Why it matters: стек застосунку не зможе додати endpoint через ImportValue, а додавання в мережевий стек зламає перевірку. Без інтернету Session Manager і CloudWatch потребують Interface endpoints або NAT, тобто погодинних витрат, яких немає в бюджеті. Шлях доставки коду й операторського доступу не визначений.
What I would do instead: ще зараз додати Gateway endpoints для S3 і DynamoDB, виправити перевірку та експортувати route table. Перевірити на тестовому інстансі встановлення пакетів і доступ до коду. Зафіксувати в ADR доставку коду, доступ оператора та шлях логів.
6. CI/CD не має реалістичної моделі credentials
Severity: high
Evidence: ADR-001: «простий деплой з будь-якого місця (Mac, CI)». Водночас ключі «міняються щосесії», а IAM лише на читання. README радить запускати на старті сесії recreate-network.sh, який спершу видаляє наявний стек.
Why it matters: OIDC-провайдера чи окремої deploy-ролі створити, найімовірніше, не вийде (перевірити iam:CreateOpenIDConnectProvider). Без них секрети GitHub доведеться вручну оновлювати щосесії. Той самий скрипт посеред сесії без імпортерів знесе мережу, а з імпортерами може залишити стек у DELETE_FAILED.
What I would do instead: окремий ADR про CI. Lint і тести виконувати без AWS-ключів. Deploy запускати вручну (workflow_dispatch) із session credentials або з машини оператора. Створити ідемпотентний up, а recreate залишити лише для демонстрації завдання 8.
7. Залежність M2 від готових ролей не перевірена
Severity: medium
Evidence: role-dissection.md: «Що саме вони дозволяють, перевіримо, коли проєктуватимемо M2». Там само симулятор показує, що iam:GetRole для цих ролей дозволений.
Why it matters: myS3Role з політикою AccessS3Bucket може не дати доступу до потрібних bucket, логів чи метрик. Варіанти бюджету порівнюються без знання, чи вони взагалі реалізовні. Це найдешевше закрите питання, що може змінити архітектуру.
What I would do instead: прочитати політики зараз і скласти матрицю «роль → S3, DynamoDB, CloudWatch, SSM». Проблема зачіпає й варіант Б з Lambda.
8. ADR застаріли або обґрунтовують рішення слабкими аргументами
Severity: medium
Evidence:
ADR-002: «Мережа створюється раз і живе весь курс». Це спростовано знахідкою про видалення стеків, але ADR лишається Accepted.
ADR-002: «одна спроба деплою». Design: «Окрему спробу деплою… не робимо».
ADR-004: Lambda «їхня конфігурація». Шаблон цю дію забороняє.
ADR-001: «цього класу проблем нема», хоча стеки теж застрягають у DELETE_FAILED чи UPDATE_ROLLBACK_FAILED.
ADR-003: 10.0.11.37 → AZ 2, хоча схема визначає індекс як останню цифру, тобто 1.
ADR-003: Fn::GetAZs подано як рішення проблеми різного mapping назв AZ між акаунтами, але функція повертає ті самі назви.
ADR-003: 10.0.0.0/16 подано як захист від конфліктів, хоча це дуже поширений діапазон.
Why it matters: на захисті це легко атакувати: ADR не відображає реального контексту рішень. CDK відхилено як «надмірний для M1», хоча вибір IaC діє до M5. Сильніший аргумент не названо: cdk bootstrap створює IAM-ролі (перевірити).
What I would do instead: додати Amendment до ADR-001 і ADR-002 з урахуванням сесійного видалення. Переоцінити користь Export у такому режимі. Виправити розбіжності, а аргументи ADR-003 переписати чесно: «зручна схема», а не «уникнення конфліктів».
9. Бюджет недооцінює YCSB і не розподіляє $20 на курс
Severity: medium
Evidence: budget-m2.md: «5 запусків × 1 млн операцій», «2.5 млн записів». m2_budget.py рахує кожну операцію як 1 WRU.
Why it matters:
Фаза load не врахована, а дані зникають щосесії, тому її доведеться повторювати.
За дефолтним записом YCSB близько 1 КБ разом із ключем запис перевищує 1 КБ і коштує 2 WRU (перевірити на реальних записах). Один load на 1 млн записів дає близько $1.25 лише за записи, тож 5 завантажень додають понад $6, більше за весь поточний підсумок варіанта Б ($5.38).
Припущено режим standard, але дефолт для t3 це unlimited (перевірити), а шаблон для цього ще не заданий.
Немає розподілу $20 на M2–M5, журналу фактичних витрат і запасу на невдалі прогони.
What I would do instead: додати load, повторні прогони, логи й розмір запису до моделі. Вести таблицю «план / факт» по кожному milestone. Явно задавати CreditSpecification. Прайс-файл current зафіксувати знімком, бо інакше розрахунок не відтворюється.
10. Огляд квот не називає реальних обмежень продуктивності
Severity: medium
Evidence: quotas.md: «жодна з перевірених квот не обмежить ні архітектуру, ні бенчмарк». Бюджет закладає t3.micro і EBS 8 ГБ gp2.
Why it matters: t3.micro має baseline 10% на vCPU і 1 ГБ RAM, а 8 ГБ gp2 дають базово 100 IOPS. YCSB на 1 млн записів по 1 КБ може вимірювати вичерпання CPU credits, пам'ять чи burst диска, а не алгоритм. Тоді порівняння M2, M3 і M5 втрачають сенс. Завдання просило записати саме ті квоти, що обмежують, а таблиця майже вся складається з «ні».
What I would do instead: додати обмеження CPU credits, RAM, IOPS і мережі. Провести короткий калібрувальний прогін із CPUCreditBalance. Зафіксувати незмінні умови бенчмарка для всіх milestone.
11. Сценарій спостерігача суперечить trust policy і набору дозволів
Severity: medium
Evidence: iam-readonly.md: «а в M4–M5 — MCP-сервер / ops-агент», але trust policy вимагає MFA, а principal це arn:aws:iam::${TrustedAccountId}:root. Allow не містить cloudtrail:LookupEvents, s3:GetBucketPolicy і s3:GetBucketAcl.
Why it matters: агент не пройде MFA, а Deny на логи заважатиме ops-сценарію. :root делегує рішення всьому акаунту аудитора, хоча доступ обмежить ще його IAM. Аудитор не побачить CloudTrail, за допомогою якого знайдено головну поведінку платформи, і публічний доступ через bucket policy. Spec також згадує s3:GetBucketEncryption, тоді як шаблон і скрипт використовують s3:GetEncryptionConfiguration.
What I would do instead: описати аудитора й агента як різних principal з різними політиками. Трастити конкретну роль аудитора. Додати потрібні аудитору дії перегляду. Для зовнішнього аудитора оцінити sts:ExternalId. Виправити spec.
12. Публічний репозиторій містить ідентифікатори, хоча їх заявлено як очищені
Severity: low
Evidence: role-dissection.md:59-61 містить email і AROA… у UserId, а поруч ID організації та SCP. Рядок 6 заявляє, що номер акаунта замінено на <ACCOUNT>. Такі самі збіги є в docs/m1/journal-uk.md.
Why it matters: прямої компрометації це не дає, бо ключів не знайдено. Але це персональні дані та ідентифікатори для точкових спроб, а заявлене маскування неповне. Вони вже є в історії git.
What I would do instead: маскувати email, AROA, org ID і SCP ID, а в CI додати перевірку на витоки (наприклад, gitleaks). Переписувати історію лише за окремим рішенням, бо це незворотна зміна для публічного репозиторію.
```

## 3a. Перевірка критики агентом, що будував проєкт (2026-11-01)

Це реакція автора, а не незалежна думка. Кожен пункт звірено з файлами або запитом до AWS, лише читанням.

| # | Пункт | Перевірка | Висновок |
|---|---|---|---|
| 1 | Очищення узагальнено | докази є лише для стеків без платних ресурсів | **Правда.** Цифра $2.7–5.4 тримається на неперевіреному припущенні для EC2, EBS, S3 |
| 2 | Ліміт 9 EC2 | ADR-003: «2→~4 шарди × 3 репліки» | **Правда.** Це 12+ вузлів, більше за ліміт. Стосується M3–M5 |
| 3 | «Гарантія» не виконується | симулятор: наша політика + `Allow *` → `ec2:GetConsoleOutput`, `ec2:GetLaunchTemplateData`, `ssm:GetParameterHistory`, `logs:GetLogRecord` = **allowed**; контроль `s3:GetObject` = explicitDeny | **Правда.** Deny-список неповний, слово «гарантія» завищене |
| 4 | Зелені перевірки при збої | `check-iam-readonly.sh` із `aws`, що падає: **«All checks passed.», exit 0** | **Правда, це баг.** Результат 33/33 від 2026-11-01 справжній (у виводі було 33 рядки), але скрипт міг збрехати. `recreate-network.sh` не перевіряє прив'язки підмереж — теж правда |
| 5 | Мережа не готова до M2 | `ec2:CreateVpcEndpoint` = allowed; route tables не експортуються; перевірка забороняє будь-який `VPCEndpoint`; design називає endpoints платними без винятку | **Частково.** Формулювання «без перебудови» завищене, але додати endpoint — мала зміна в M2, робити її зараз не обов'язково |
| 6 | CI/CD і credentials | `iam:CreateOpenIDConnectProvider` = implicitDeny; README радить `recreate-network.sh` на старті сесії | **Правда.** Рішення CI — у M2. Порада в README небезпечна вже зараз: повторний запуск знесе мережу |
| 7 | Готові ролі не перевірені | у `role-dissection.md` так і написано | **Правда.** Перевірка дешева, логічно зробити на початку M2 |
| 8 | ADR застарілі | знайдено всі рядки; `Fn::GetAZs` повертає ті самі назви AZ, тож проблему різних назв між акаунтами не вирішує | **Правда**, крім дрібниці про «AZ 2»: це друга AZ з індексом 1, питання лише у формулюванні |
| 9 | Бюджет YCSB | `get-default-credit-specification` для t3 = **unlimited**, а бюджет припускає standard | **Правда.** Фазу load і 2 WRU не враховано. Перевіряти на реальних записах |
| 10 | Обмеження продуктивності | розділу про CPU credits, RAM, IOPS у `quotas.md` немає | **Правда.** Важливо для бенчмарка M2 |
| 11 | Спостерігач суперечить trust policy | spec містить `s3:GetBucketEncryption`, шаблон — `s3:GetEncryptionConfiguration` | **Правда.** Агент не пройде MFA. Назву дії в spec треба виправити |
| 12 | Ідентифікатори в репозиторії | email, AROA, org ID або SCP ID є в `journal-uk.md` і `role-dissection.md`; номера акаунта немає | **Правда, ризик низький.** Заяву про очищення треба уточнити |

## 4. Рішення по критиці

Статус на 2026-11-01. Рішення приймає Олеся.

### Таблиця пунктів, яку розглядали

**🔴 Виправити до здачі M1: це реальні помилки в уже зробленому**

| # | Проблема простими словами | Що зробити |
|---|---|---|
| 4 | **Скрипт перевірки може збрехати.** Я запустила його з AWS, що видає помилку, і він написав «All checks passed». Наш результат 33/33 справжній, але скрипт треба виправити. Інший скрипт також не перевіряє, до якої таблиці прив'язана кожна підмережа | перевіряти кількість відповідей і прив'язки |
| 3 | **Запобіжник має дірки.** Симулятор підтвердив: читання виводу консолі сервера, історії параметрів і записів логів не заборонені. Аудитор також бачить змінні середовища Lambda | доповнити Deny, а слово «гарантія» замінити чесним формулюванням |
| 6 | **Порада в README небезпечна.** `recreate-network.sh` на старті сесії: якщо запустити його вдруге, він знесе робочу мережу | окремий безпечний скрипт `up` |
| 8 | **ADR застаріли.** Наприклад, «мережа живе весь курс». І **я неправильно пояснила `Fn::GetAZs`**: він не вирішує різницю назв AZ між акаунтами, а лише не дає вписувати назви руками | додати поправки в ADR; пояснення виправити |
| 11 | **Помилка в spec:** там `s3:GetBucketEncryption`, а правильна дія `s3:GetEncryptionConfiguration`. Ще документ обіцяє роль для агента, хоча агент не пройде MFA | виправити spec і документ |
| 12 | **Заява про очищення неточна.** У публічних файлах є твоя пошта, ID ролі й організації. Номера акаунта та ключів немає, ризик низький | замаскувати надалі. Історію git не переписуємо без твого окремого рішення |

**🟡 Записати в документи зараз, а зробити в M2**

| # | Проблема | Що зробити |
|---|---|---|
| 1 | Видалення бачили лише на безплатних ресурсах. Чи так само зникнуть сервери й диски, не перевірено, а від цього залежить оцінка $2.7–5.4 | у M2 зробити дешевий експеримент і власне прибирання в кінці сесії |
| 9 | Бюджет занижений: не враховано завантаження даних для YCSB, запис може коштувати вдвічі більше, а `t3` типово працює в режимі `unlimited` (я припустила `standard`) | уточнити припущення в документі |
| 10 | `t3.micro` слабкий: тест може показати межу сервера, а не нашого алгоритму | додати ці обмеження в `quotas.md` |
| 2 | Запас під 12+ вузлів в ADR-003 не вміщається в ліміт 9 серверів | таблиця серверів по майлстоунах |
| 5, 6, 7 | Доставка коду на приватні сервери, ключі для CI/CD (OIDC недоступний), права готових ролей | окремі рішення на початку M2 |

### Що вирішили

| # | Рішення | Чому | Що зроблено |
|---|---|---|---|
| 11 | ✅ **Прийнято, виправлено** | у spec була назва дії, якої немає в шаблоні; документ обіцяв роль агентові, який не може ввести MFA | spec: `s3:GetEncryptionConfiguration`; `iam-readonly.md`: спостерігач — людина, агентові в M4–M5 потрібне окреме рішення |
| 6 (порада в README) | ❌ **Відкинуто, лишаємо як є** | Олеся знає, що повторний запуск знесе мережу, і це її влаштовує | нічого |
| 12 | ❌ **Відкинуто, лишаємо як є** | рішення Олесі; ключів і номера акаунта у файлах немає, ризик низький | нічого, історію git не змінюємо |
| 1, 2, 5, 6 (CI/CD), 7, 9, 10 | ⏭ **Відкладено на M2** | стосуються рішень M2, зараз лише фіксуємо | записано тут і в `tasks.md`; бюджет, квоти й ADR поки не змінюємо |
| 4 | ✅ **Прийнято, виправлено** | критик знайшов справжній баг: при збої AWS скрипт писав «All checks passed», ми це відтворили | `check-iam-readonly.sh` завершується з помилкою, якщо AWS упав або відповідей замало (перевірено з підставним `aws`: exit 1; насправді 37/37). `recreate-network.sh` перевіряє прив'язки підмереж (11/11) |
| 3 | ✅ **Прийнято частково** | симулятор показав дірки; «гарантія» неправдива. `ListFunctions` і `GetTemplate` лишаємо: без них аудитор не бачить функцій і стеків | у Deny додано `ec2:GetConsoleOutput`, `ec2:GetLaunchTemplateData`, `ssm:GetParameterHistory`, `logs:GetLogRecord`; «гарантію» замінено на «запобіжник для перелічених дій»; ризик задокументовано |
| 8 | ✅ **Прийнято, виправлено** | ADR не відповідали реальності, а захист починається з них | додано поправки в ADR-001…004 (видалення стеків, `Fn::GetAZs`, аргументи CIDR, актуальна політика) |

### Для захисту: 2–3 пункти

Рішення вже є по кожному з цих пунктів. Пропозиція: № 4 (приймаю), № 11 (приймаю), № 6 (відхиляю). Остаточний вибір Олесі: _(підтвердити)_
