#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""合成数据生成器 —— 生成与真实结构一致、但内容全部虚构的财务数据。

真实项目里这些数据来自网银导出与金蝶账套；公开仓库一律用合成数据，
既能跑通全流程，又不会泄露任何真实客户、金额、银行或科目。
"""
import json
import random
from pathlib import Path

random.seed(20261017)

OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)

# 虚构主体与账户（真实项目里是实际公司名与银行账号）
ENTITIES = [
    {"code": "E01", "name": "示例贸易有限公司", "bank": "示例银行·城东支行", "acct": "6222********0001"},
    {"code": "E02", "name": "示例供应链有限公司", "bank": "示例银行·城南支行", "acct": "6222********0002"},
]
# 虚构客户（真实项目里是真实往来单位）
CUSTOMERS = ["甲方实业", "乙方科技", "丙方物流", "丁方材料", "戊方电子"]
# 虚构科目（结构与金蝶一致，名称通用）
SUBJECTS = [
    ("1002", "银行存款"), ("1122", "应收账款"), ("1123", "预付账款"),
    ("2202", "应付账款"), ("2203", "预收账款"), ("6001", "主营业务收入"),
    ("6401", "主营业务成本"), ("6602", "管理费用"), ("2221", "应交税费"),
]
MONTH = "2026-08"


def gen_bank_statements(n=5):
    """网银对账单：5 张，模拟多账户多银行"""
    rows = []
    for i in range(n):
        ent = ENTITIES[i % len(ENTITIES)]
        for d in range(1, 9):
            amt = round(random.uniform(1000, 90000), 2)
            is_debit = random.random() > 0.5   # 一次抛硬币决定方向，避免借贷同时为 0
            rows.append({
                "主体": ent["name"],
                "账户": ent["acct"],
                "开户行": ent["bank"],
                "交易日期": f"{MONTH}-{d:02d}",
                "摘要": random.choice(["货款", "服务费", "运费", "退款", "手续费"]),
                "对方户名": random.choice(CUSTOMERS),
                "借方发生额": amt if is_debit else 0,
                "贷方发生额": 0 if is_debit else amt,
                "余额": round(random.uniform(10000, 500000), 2),
                "流水号": f"SN{i:02d}{d:04d}",
            })
    return rows


def gen_trial_balance(statements):
    """科目余额表：2 张（对应两个主体）

    银行存款（1002）的本期发生额由对账单汇总而来 —— 这样合并后能真正勾稽上，
    勾稽检查才有意义（真实项目里余额表从账套导出，也应与流水对得上）。
    """
    agg = {}
    for st in statements:
        a = agg.setdefault(st["主体"], {"dr": 0.0, "cr": 0.0})
        a["dr"] += float(st["借方发生额"] or 0)
        a["cr"] += float(st["贷方发生额"] or 0)

    rows = []
    for ent in ENTITIES:
        a = agg.get(ent["name"], {"dr": 0.0, "cr": 0.0})
        for code, name in SUBJECTS:
            if code == "1002":
                dr, cr = round(a["dr"], 2), round(a["cr"], 2)
            else:
                dr, cr = round(random.uniform(0, 150000), 2), round(random.uniform(0, 150000), 2)
            rows.append({
                "主体": ent["name"],
                "科目编码": code,
                "科目名称": name,
                "期初余额": round(random.uniform(0, 200000), 2),
                "本期借方": dr,
                "本期贷方": cr,
                "期末余额": round(random.uniform(0, 300000), 2),
            })
    return rows


def gen_ledger_detail(n=2):
    """往来明细：2 张"""
    rows = []
    for i in range(n):
        ent = ENTITIES[i]
        for c in CUSTOMERS:
            rows.append({
                "主体": ent["name"],
                "往来单位": c,
                "期初": round(random.uniform(0, 80000), 2),
                "本期增加": round(random.uniform(0, 120000), 2),
                "本期减少": round(random.uniform(0, 120000), 2),
                "期末": round(random.uniform(0, 100000), 2),
            })
    return rows


def inject_anomalies(vouchers):
    """故意注入 3 类异常，用于演示 fail-closed 拦截。"""
    # 1) 借贷不平
    vouchers[3]["借方金额"] = round(vouchers[3]["借方金额"] + 100, 2)
    # 2) 缺少税额口径（应被阻断）
    vouchers[7]["税率"] = None
    # 3) 科目不存在（应被阻断）
    vouchers[11]["科目编码"] = "9999"
    return vouchers


def gen_vouchers():
    """凭证：按业务规则从对账单派生（一借一贷，两张分录行组成一张凭证）"""
    vouchers = []
    seq = 0
    for st in gen_bank_statements():
        seq += 1
        is_in = st["借方发生额"] > 0          # 银行借方 = 钱进来 = 应收减少 / 收入确认
        amt = st["借方发生额"] or st["贷方发生额"]
        vid = f"记-{seq:04d}"
        common = {
            "凭证号": vid,
            "日期": st["交易日期"],
            "摘要": f"{st['摘要']}-{st['对方户名']}",
            "来源流水号": st["流水号"],
        }
        # 分录 1：往来科目
        vouchers.append({
            **common,
            "分录序": 1,
            "科目编码": "1122" if is_in else "2202",
            "科目名称": "应收账款" if is_in else "应付账款",
            "核算项目": st["对方户名"],
            "借方金额": amt if is_in else 0,
            "贷方金额": 0 if is_in else amt,
            "税率": 0.13,
            "税额": round(amt * 0.13 / 1.13, 2),
        })
        # 分录 2：收入 / 成本（与分录 1 反向，保证整张凭证借贷平衡）
        vouchers.append({
            **common,
            "分录序": 2,
            "科目编码": "6001" if is_in else "6401",
            "科目名称": "主营业务收入" if is_in else "主营业务成本",
            "核算项目": "",
            "借方金额": 0 if is_in else amt,
            "贷方金额": amt if is_in else 0,
            "税率": 0.13,
            "税额": round(amt * 0.13 / 1.13, 2),
        })
    return inject_anomalies(vouchers)


if __name__ == "__main__":
    statements = gen_bank_statements(5)
    data = {
        "bank_statements": statements,
        "trial_balance": gen_trial_balance(statements),
        "ledger_detail": gen_ledger_detail(2),
        "vouchers_raw": gen_vouchers(),
    }
    for k, v in data.items():
        p = OUT / f"{k}.json"
        p.write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[gen] {k}: {len(v)} 行 -> {p.name}")
    print("[gen] 已注入 3 类异常（借贷不平 / 缺税率口径 / 科目不存在）")
