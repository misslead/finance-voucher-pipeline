#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""9 张月结台账 → 批量合并与透视。

真实场景：网银对账单 5 张 + 科目余额表 2 张 + 往来明细 2 张，分散在不同银行与主体，
手工合并一次要 10 分钟以上，且每次口径都要重新确认。

这里做的三件事：
1. 统一口径合并（补「来源文件」「主体」两列，保证可追溯）
2. 银行流水直接透成客户往来账（借方=应收增加，贷方=回款）
3. 合并结果做勾稽自检（流水合计 vs 余额表本期发生额）
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)


def load(name):
    p = DATA / f"{name}.json"
    if not p.exists():
        raise SystemExit(f"缺数据 {p.name}，先跑 gen_sample.py")
    return json.loads(p.read_text(encoding="utf-8"))


def write_csv(path, rows, fields):
    # utf-8-sig：Excel 直接双击打开不乱码
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"[merge] {path.name}: {len(rows)} 行")


def merge_bank(statements):
    """5 张对账单 → 一张合并表"""
    fields = ["主体", "账户", "开户行", "交易日期", "摘要", "对方户名",
              "借方发生额", "贷方发生额", "余额", "流水号", "来源文件"]
    rows = []
    for i, st in enumerate(statements):
        rows.append({**st, "来源文件": f"对账单_{i + 1:02d}.xlsx"})
    return rows, fields


def pivot_customer_ledger(bank_rows):
    """银行流水 → 客户往来账（按对方户名汇总）"""
    agg = defaultdict(lambda: {"应收增加": 0.0, "回款": 0.0, "笔数": 0})
    for r in bank_rows:
        key = (r["主体"], r["对方户名"])
        agg[key]["应收增加"] += float(r["借方发生额"] or 0)
        agg[key]["回款"] += float(r["贷方发生额"] or 0)
        agg[key]["笔数"] += 1

    rows = []
    for (ent, cust), v in sorted(agg.items()):
        rows.append({
            "主体": ent,
            "往来单位": cust,
            "应收增加": round(v["应收增加"], 2),
            "回款": round(v["回款"], 2),
            "净额": round(v["应收增加"] - v["回款"], 2),
            "笔数": v["笔数"],
        })
    return rows, ["主体", "往来单位", "应收增加", "回款", "净额", "笔数"]


def check_tie_out(bank_rows, trial_balance):
    """勾稽自检：流水合计 vs 余额表本期发生额合计

    不平就报出来 —— 不静默通过，这是财务数据最基本的底线。
    """
    # 只拿「银行存款」科目比对 —— 其他科目的发生额本来就不等于银行流水
    bank_dr = sum(float(r["借方发生额"] or 0) for r in bank_rows)
    bank_cr = sum(float(r["贷方发生额"] or 0) for r in bank_rows)
    bank_subject = [r for r in trial_balance if r["科目编码"] == "1002"]
    tb_dr = sum(float(r["本期借方"]) for r in bank_subject)
    tb_cr = sum(float(r["本期贷方"]) for r in bank_subject)
    return {
        "流水借方合计": round(bank_dr, 2),
        "流水贷方合计": round(bank_cr, 2),
        "余额表本期借方": round(tb_dr, 2),
        "余额表本期贷方": round(tb_cr, 2),
        "差异_借方": round(bank_dr - tb_dr, 2),
        "差异_贷方": round(bank_cr - tb_cr, 2),
        # 合成数据两边独立生成，必然不平；真实项目里这里应为 0
        "是否平": abs(bank_dr - tb_dr) < 0.01 and abs(bank_cr - tb_cr) < 0.01,
    }


def main():
    statements = load("bank_statements")
    trial = load("trial_balance")
    detail = load("ledger_detail")

    bank_rows, bank_fields = merge_bank(statements)
    write_csv(OUT / "merged_bank.csv", bank_rows, bank_fields)

    led_rows, led_fields = pivot_customer_ledger(bank_rows)
    write_csv(OUT / "customer_ledger.csv", led_rows, led_fields)

    tb_rows = [{**r, "来源文件": "科目余额表"} for r in trial]
    write_csv(OUT / "merged_trial_balance.csv", tb_rows, list(tb_rows[0].keys()))

    det_rows = [{**r, "来源文件": "往来明细"} for r in detail]
    write_csv(OUT / "merged_ledger_detail.csv", det_rows, list(det_rows[0].keys()))

    tie = check_tie_out(bank_rows, trial)
    (OUT / "tie_out.json").write_text(json.dumps(tie, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[check] 勾稽：{'平' if tie['是否平'] else '不平'}  差异借方 {tie['差异_借方']:,.2f}")
    print(f"[check] 9 张台账合并完成 → {OUT}")


if __name__ == "__main__":
    main()
