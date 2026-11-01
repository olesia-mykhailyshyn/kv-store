#!/usr/bin/env python3
"""M2 budget estimate (M1, task 7) from AWS public price list files.

The sandbox denies the Pricing API (pricing:GetProducts, explicit deny in an SCP), so this
script reads the public AWS Price List Bulk files instead (no credentials needed):
  https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<service>/current/us-east-1/index.json
The EC2 file is ~480 MB, so its CSV version is streamed and filtered line by line.

Usage: python3 scripts/m2_budget.py        (prints Markdown tables)
"""
import csv
import io
import json
import urllib.request

BASE = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{}/current/us-east-1/index.{}"
HOURS_MONTH = 730          # AWS convention: 24/7 month
HOURS_SESSIONS = 40        # assumption: M2 work in the sandbox ≈ 13 sessions × 3 h


def offer_json(service):
    with urllib.request.urlopen(BASE.format(service, "json")) as resp:
        return json.load(resp)


def ondemand_price(offer, usagetype, begin="0", description_has=""):
    """USD price of the first OnDemand dimension whose product has this usagetype."""
    for sku, terms in offer["terms"]["OnDemand"].items():
        attrs = offer["products"][sku].get("attributes", {})
        if attrs.get("usagetype") != usagetype:
            continue
        for term in terms.values():
            for dim in term["priceDimensions"].values():
                if dim.get("beginRange") == begin and description_has in dim["description"]:
                    return float(dim["pricePerUnit"]["USD"])
    raise LookupError(f"{usagetype} not found")


def ec2_prices():
    """Stream the EC2 CSV and keep only the rows we need."""
    wanted = {"BoxUsage:t3.micro", "BoxUsage:t3.small", "EBS:VolumeUsage.gp2",
              "NatGateway-Hours", "NatGateway-Bytes"}
    found = {}
    with urllib.request.urlopen(BASE.format("AmazonEC2", "csv")) as resp:
        rows = csv.reader(io.TextIOWrapper(resp, encoding="utf-8"))
        for _ in range(5):                      # metadata lines before the header
            next(rows)
        header = next(rows)
        ix = {name: i for i, name in enumerate(header)}
        for row in rows:
            usage = row[ix["usageType"]]
            if usage not in wanted or row[ix["TermType"]] != "OnDemand":
                continue
            if usage.startswith("BoxUsage") and not (
                    row[ix["Operating System"]] == "Linux" and row[ix["Tenancy"]] == "Shared"
                    and row[ix["Pre Installed S/W"]] == "NA" and row[ix["CapacityStatus"]] == "Used"):
                continue
            found[usage] = float(row[ix["PricePerUnit"]])
    return found


def main():
    ec2 = ec2_prices()
    vpc = offer_json("AmazonVPC")
    elb = offer_json("AWSELB")
    lam = offer_json("AWSLambda")
    ddb = offer_json("AmazonDynamoDB")
    s3 = offer_json("AmazonS3")
    dt = offer_json("AWSDataTransfer")

    p = {
        "t3.micro, год": ec2["BoxUsage:t3.micro"],
        "t3.small, год": ec2["BoxUsage:t3.small"],
        "EBS gp2, ГБ-міс": ec2["EBS:VolumeUsage.gp2"],
        "NAT Gateway, год": ec2["NatGateway-Hours"],
        "NAT Gateway, ГБ": ec2["NatGateway-Bytes"],
        "публічна IPv4, год": ondemand_price(vpc, "USE1-PublicIPv4:InUseAddress"),
        "Interface VPC Endpoint, год (на AZ)": ondemand_price(vpc, "USE1-VpcEndpoint-Hours"),
        "ALB, год": ondemand_price(elb, "LoadBalancerUsage", description_has="Application"),
        "ALB LCU, год": ondemand_price(elb, "LCUUsage", description_has="Application"),
        "Lambda, запит": ondemand_price(lam, "Request"),
        "Lambda, ГБ-с": ondemand_price(lam, "Lambda-GB-Second"),
        "DynamoDB on-demand, читання (RRU)": ondemand_price(ddb, "ReadRequestUnits"),
        "DynamoDB on-demand, запис (WRU)": ondemand_price(ddb, "WriteRequestUnits"),
        "S3 Standard, ГБ-міс": ondemand_price(s3, "TimedStorage-ByteHrs"),
        "Трафік між AZ, ГБ (кожен напрямок)": ondemand_price(dt, "DataTransfer-Regional-Bytes"),
    }

    print("## Ціни за одиницю (us-east-1, On-Demand)\n")
    print("| Що | USD |\n|---|---|")
    for name, price in p.items():
        print(f"| {name} | {price:.10g} |")

    # Usage assumptions for one month of M2 (written out in docs/m1/budget-m2.md).
    ycsb_ops = 5 * 1_000_000            # 5 YCSB runs × 1M operations
    reads, writes = ycsb_ops // 2, ycsb_ops // 2   # workload A: 50/50
    lambda_ms, lambda_gb = 20, 0.128     # per invocation
    inter_az_gb = 10                     # coordinator ↔ shards in other AZs
    nat_gb = 5                           # packages and code via NAT (comparison only)
    alb_lcu = 1                          # average LCU while running (upper bound)

    def ec2_cost(hours, micro, small, disks):
        return {
            "EC2 t3.micro": micro * hours * p["t3.micro, год"],
            "EC2 t3.small (клієнт YCSB)": small * hours * p["t3.small, год"],
            "EBS gp2 8 ГБ": disks * 8 * p["EBS gp2, ГБ-міс"] * hours / HOURS_MONTH,
            "публічна IPv4 клієнта": hours * p["публічна IPv4, год"],
        }

    def lambda_cost(invocations):
        return {
            "Lambda запити": invocations * p["Lambda, запит"],
            "Lambda обчислення": invocations * lambda_ms / 1000 * lambda_gb * p["Lambda, ГБ-с"],
        }

    def variants(hours):
        traffic = {"трафік між AZ": inter_az_gb * 2 * p["Трафік між AZ, ГБ (кожен напрямок)"]}
        a = {**ec2_cost(hours, micro=3, small=1, disks=4), **traffic}
        a_plus = {**a,
                  "ALB": hours * p["ALB, год"],
                  "ALB LCU": alb_lcu * hours * p["ALB LCU, год"],
                  "публічні IPv4 ALB (2 AZ)": 2 * hours * p["публічна IPv4, год"]}
        b = {**ec2_cost(hours, micro=0, small=1, disks=1),
             **lambda_cost(2 * ycsb_ops),             # coordinator + shard function per op
             "DynamoDB читання": reads * p["DynamoDB on-demand, читання (RRU)"],
             "DynamoDB запис": writes * p["DynamoDB on-demand, запис (WRU)"]}
        c = {**ec2_cost(hours, micro=2, small=1, disks=3), **lambda_cost(ycsb_ops), **traffic}
        nat3 = {"3 × NAT Gateway": 3 * hours * p["NAT Gateway, год"],
                "NAT трафік": nat_gb * p["NAT Gateway, ГБ"],
                "3 × публічна IPv4 NAT": 3 * hours * p["публічна IPv4, год"]}
        return {"А. EC2 без ALB": a, "А+. EC2 + ALB": a_plus, "Б. Serverless": b,
                "В. Змішаний": c, "(для порівняння) NAT у 3 AZ": nat3}

    for title, hours in (("24/7 місяць", HOURS_MONTH), (f"лише сесії ({HOURS_SESSIONS} год)", HOURS_SESSIONS)):
        print(f"\n## Варіанти: {title}\n")
        print("| Варіант | Разом, USD | З чого складається |\n|---|---|---|")
        for name, parts in variants(hours).items():
            detail = "; ".join(f"{k} {v:.2f}" for k, v in parts.items())
            print(f"| {name} | **{sum(parts.values()):.2f}** | {detail} |")


if __name__ == "__main__":
    main()
