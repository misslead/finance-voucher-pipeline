#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""凭证生成 + fail-closed 校验。

核心不是"把数据填进 36 列"，而是**校验不通过就绝不放行**。

真实财务场景里最危险的不是算错，是"看起来对了就过账"：
税率没确认却按 0 入账、科目编码写错却没人拦、借贷差 0.01 被四舍五入抹平。
所以这里的规则是 fail-closed —— 任何一条不确定，凭证就进 blocked，而不是进导入文件。
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

PERIOD = "2026-08"

# 金蝶凭证导入模板的 36 列（字段名与顺序需与导入模板完全一致，否则整批失败）
FIELDS_36 = [
    "凭证字号", "凭证号", "日期", "会计期间", "附件数",
    "摘要", "科目代码", "科目名称", "币别", "汇率",
    "原币金额", "借方金额", "贷方金额", "数量", "单价",
    "核算项目类别", "核算项目代码", "核算项目名称", "部门", "职员",
    "客户", "供应商", "现金流量项目", "业务类型", "结算方式",
    "结算号", "票据号", "税率", "税额", "价税合计",
    "制单人", "审核人", "过账状态", "来源单据", "来源流水号", "备注",
]

# 有效科目表（真实项目从账套导出；这里用通用科目）
VALID_SUBJECTS = {
    "1002": "银行存款", "1122": "应收账款", "1123": "预付账款",
    "2202": "应付账款", "2203": "预收账款", "6001": "主营业务收入",
    "6401": "主营业务成本", "6602": "管理费用", "2221": "应交税费",
}
# 必须带核算项目的科目
NEED_AUX = {"1122", "1123", "2202", "2203"}


def rule_balance(v, totals):
    """R1 借贷必须平衡（凭证级：整张凭证借方合计 == 贷方合计，容差 0.01）"""
    t = totals[v["凭证号"]]
    if abs(t["dr"] - t["cr"]) > 0.01:
        return f"借贷不平：借 {t['dr']:,.2f} / 贷 {t['cr']:,.2f}"
    return None


def rule_subject(v):
    """R2 科目必须在账套里存在"""
    if v["科目编码"] not in VALID_SUBJECTS:
        return f"科目不存在：{v['科目编码']}"
    return None


def rule_tax(v):
    """R3 税率口径必须已确认 —— 未确认一律阻断，绝不默认 0"""
    if v.get("税率") is None:
        return "税率口径未确认（fail-closed，禁止按 0 入账）"
    return None


def rule_amount(v):
    """R4 金额必须为正（分录行允许单边为 0，但借贷合计不能为 0）"""
    if float(v["借方金额"]) + float(v["贷方金额"]) <= 0:
        return "金额为 0"
    return None


def rule_period(v):
    """R5 日期必须落在本会计期间"""
    if not str(v["日期"]).startswith(PERIOD):
        return f"日期 {v['日期']} 不在期间 {PERIOD}"
    return None


def rule_summary(v):
    """R6 摘要不能为空（空摘要的凭证在审计时无法追溯）"""
    if not str(v.get("摘要", "")).strip():
        return "摘要为空"
    return None


def rule_aux(v):
    """R7 应收/应付类科目必须带核算项目"""
    if v["科目编码"] in NEED_AUX and not str(v.get("核算项目", "")).strip():
        return f"科目 {v['科目编码']} 缺核算项目"
    return None


RULES = [rule_balance, rule_subject, rule_tax, rule_amount, rule_period, rule_summary, rule_aux]


def to_36(v):
    """把业务字段映射到金蝶 36 列"""
    amt = float(v["借方金额"] or v["贷方金额"])
    tax_rate = v.get("税率")
    return {
        "凭证字号": "记",
        "凭证号": v["凭证号"],
        "日期": v["日期"],
        "会计期间": PERIOD,
        "附件数": 1,
        "摘要": v["摘要"],
        "科目代码": v["科目编码"],
        "科目名称": v["科目名称"],
        "币别": "RMB",
        "汇率": 1,
        "原币金额": amt,
        "借方金额": v["借方金额"],
        "贷方金额": v["贷方金额"],
        "数量": "", "单价": "",
        "核算项目类别": "客户" if v["科目编码"] == "1122" else ("供应商" if v["科目编码"] == "2202" else ""),
        "核算项目代码": "", "核算项目名称": v.get("核算项目", ""),
        "部门": "", "职员": "", "客户": "", "供应商": "",
        "现金流量项目": "", "业务类型": "采购" if v["科目编码"] == "2202" else "销售",
        "结算方式": "电汇", "结算号": "", "票据号": "",
        "税率": "" if tax_rate is None else tax_rate,
        "税额": v.get("税额", 0),
        "价税合计": round(amt + float(v.get("税额", 0)), 2),
        "制单人": "system", "审核人": "", "过账状态": "未过账",
        "来源单据": "网银流水", "来源流水号": v.get("来源流水号", ""), "备注": "",
    }


def main():
    src = DATA / "vouchers_raw.json"
    if not src.exists():
        raise SystemExit("缺 vouchers_raw.json，先跑 gen_sample.py")
    raw = json.loads(src.read_text(encoding="utf-8"))

    # 凭证级合计（借贷平衡必须在整张凭证上判断，不能看单行）
    totals = defaultdict(lambda: {"dr": 0.0, "cr": 0.0})
    for v in raw:
        totals[v["凭证号"]]["dr"] += float(v["借方金额"] or 0)
        totals[v["凭证号"]]["cr"] += float(v["贷方金额"] or 0)

    # 按凭证分组：借贷平衡与幂等都必须以「整张凭证」为单位判断
    grouped = {}
    for v in raw:
        grouped.setdefault(v["凭证号"], []).append(v)

    ok, blocked, seen = [], [], set()
    reasons = defaultdict(int)

    for vid, lines in grouped.items():
        if vid in seen:
            for v in lines:
                blocked.append({**to_36(v), "阻断原因": "重复凭证号（幂等拦截）"})
            reasons["重复凭证号"] += 1
            continue

        errs_by_line = []
        for v in lines:
            errs = []
            for r in RULES:
                e = r(v, totals) if r is rule_balance else r(v)
                if e:
                    errs.append(e)
            errs_by_line.append(errs)

        if any(errs_by_line):
            for v, errs in zip(lines, errs_by_line):
                if errs:
                    blocked.append({**to_36(v), "阻断原因": "；".join(errs)})
                    for e in errs:
                        reasons[e.split("：")[0]] += 1
                else:
                    blocked.append({**to_36(v), "阻断原因": "同凭证其他分录行未通过校验"})
                    reasons["同凭证连带阻断"] += 1
        else:
            for v in lines:
                ok.append(to_36(v))
            seen.add(vid)

    with open(OUT / "vouchers_ok.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS_36)
        w.writeheader()
        w.writerows(ok)

    with open(OUT / "vouchers_blocked.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS_36 + ["阻断原因"])
        w.writeheader()
        w.writerows(blocked)

    print(f"[voucher] 放行 {len(ok)} 张 / 阻断 {len(blocked)} 张")
    for k, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  - {k}: {n}")

    report = ["# 凭证校验报告", "", f"期间：{PERIOD}", "",
              f"- 输入分录行：{len(raw)} 行", f"- 凭证张数：{len(grouped)} 张",
              f"- 放行分录：{len(ok)} 行", f"- 阻断分录：{len(blocked)} 行", "",
              "## 阻断原因分布", ""]
    report += [f"| {k} | {n} |" for k, n in sorted(reasons.items(), key=lambda x: -x[1])]
    (OUT / "voucher_report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"[voucher] 报告 -> {(OUT / 'voucher_report.md').name}")


if __name__ == "__main__":
    main()
