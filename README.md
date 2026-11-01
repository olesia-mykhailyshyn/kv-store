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

## M1 — мережа `kv-network`

Мережа, у якій житимуть усі сервіси сховища: VPC `10.0.0.0/16` у 3 AZ, публічні підмережі з виходом в інтернет через Internet Gateway, приватні — лише для доступу всередині VPC. Шаблон: [`infra/network.yaml`](infra/network.yaml). Рішення: [ADR-002](docs/adr/0002-stack-layout.md) (стеки), [ADR-003](docs/adr/0003-cidr-addressing.md) (адреси). Діаграми: [`docs/m1/architecture-uk.md`](docs/m1/architecture-uk.md).

| Підмережа | CIDR | AZ | Route table |
|---|---|---|---|
| public-a / b / c | `10.0.0.0/24` / `10.0.1.0/24` / `10.0.2.0/24` | 1-ша / 2-га / 3-тя | public: `local` + `0.0.0.0/0 → IGW` |
| private-a / b / c | `10.0.10.0/24` / `10.0.11.0/24` / `10.0.12.0/24` | 1-ша / 2-га / 3-тя | private: лише `local` |

Вартість: $0 (VPC, підмережі, IGW, route tables безкоштовні; NAT Gateway свідомо нема).

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
