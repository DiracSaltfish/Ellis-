#!/usr/bin/env python3
"""从东财公告 JSON、招募书文本和 PCF 暂存文件生成批量验收台账。

核心口径：
1. 标题命中“更新招募说明书”才填 latest_updated_prospectus_date；否则保留初始招募书日期并标注。
2. 补券时间只取基金管理人代买/代卖被替代证券的处理时点，不把退补款交收日当成补券日。
3. 只要招募书或适用的交易所/中国结算规则出现日间 RTGS，rtgs_subscription 即 PASS。
4. 只有基金文件在申购赎回处理语境中明确写出按净申购/净赎回轧差，same_day_netting_manager 才 PASS。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = ROOT / "临时数据"
LEDGER_ROOT = ROOT / "验收台账"


def code_of(value: str) -> str:
    return re.sub(r"\D", "", value)


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_search_text(value: str) -> str:
    """压缩 PDF 提取产生的空格，尤其是中文逐字空格，便于正则检索。"""
    value = compact(value).replace("＋", "+")
    return re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", value)


def read_input(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_notice(code: str) -> dict:
    path = TEMP_ROOT / code / f"{code}_eastmoney_issuance_operation_notices.json"
    return json.loads(path.read_text(encoding="utf-8"))


def select_document(code: str, notice: dict) -> tuple[dict, Path, Path]:
    item = notice.get("latest_updated_prospectus") or notice.get("latest_prospectus_any")
    if not item:
        raise FileNotFoundError(f"{code}: no prospectus in notice JSON")
    date = str(item.get("notice_date") or "").replace("-", "")
    text_files = list((TEMP_ROOT / code).glob(f"*{date}*.txt"))
    pdf_files = list((TEMP_ROOT / code).glob(f"*{date}*.pdf"))
    if len(text_files) != 1 or len(pdf_files) != 1:
        raise FileNotFoundError(f"{code}: expected one PDF/TXT for {date}, got {text_files}, {pdf_files}")
    return item, pdf_files[0], text_files[0]


def pcf_info(code: str) -> dict[str, str]:
    directory = TEMP_ROOT / code
    components = sorted(directory.glob(f"{code}_pcf_*_components.csv"))
    basics = sorted(directory.glob(f"{code}_pcf_*_basic.csv"))
    if not components:
        return {"pcf_asof": "", "pcf_rows": "", "pcf_cash_flag": "", "pcf_has_159900": ""}
    component = components[-1]
    match = re.search(r"_pcf_(\d{4}-\d{2}-\d{2})_components", component.name)
    asof = match.group(1) if match else ""
    with component.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    flags = sorted({row[6] for row in rows if len(row) > 6 and row[6]})
    has_159900 = any(len(row) > 3 and row[3] == "159900" for row in rows)
    if basics:
        basic_match = re.search(r"_pcf_(\d{4}-\d{2}-\d{2})_basic", basics[-1].name)
        asof = asof or (basic_match.group(1) if basic_match else "")
    return {
        "pcf_asof": asof,
        "pcf_rows": str(len(rows)),
        "pcf_cash_flag": "/".join(flags),
        "pcf_has_159900": "YES" if has_159900 else "NO",
    }


def section(text: str, start_patterns: list[str], end_patterns: list[str], max_chars: int = 2500) -> str:
    candidates = []
    for pattern in start_patterns:
        candidates.extend(re.finditer(pattern, text, re.I))
    if not candidates:
        return ""
    # “替代金额的处理程序”会嵌套命中“申购替代金额的处理程序”或
    # “赎回替代金额的处理程序”。若不去掉嵌套短标题，后者可能被短标题
    # 抢先选中，导致整段只剩“【1】/【2】”而没有实际处理内容。
    candidates = [
        match
        for match in candidates
        if not any(
            other is not match
            and other.start() <= match.start()
            and other.end() >= match.end()
            and (other.end() - other.start()) > (match.end() - match.start())
            for other in candidates
        )
    ]
    # 目录或“未来可调整”条款也可能出现相同标题，优先选后面确实出现
    # “确认成功/T日/基金管理人”的正文候选。
    viable = [m for m in candidates if re.search(r"确认成功|基金管理人|T\s*日", text[m.end() : m.end() + 900], re.I)]
    chosen = min((viable or candidates), key=lambda match: match.start())
    start = chosen.start()
    search_start = chosen.end()
    end = len(text)
    for pattern in end_patterns:
        # 从所选标题结束处开始搜索，避免“申购现金替代保证金和赎回对应的
        # 替代金额的处理程序”这种合并标题被自己的“赎回...”子串截断。
        match = re.search(pattern, text[search_start:], re.I)
        if match:
            end = min(end, search_start + match.start())
    return compact(text[start : min(end, start + max_chars)])


def substitution_block(text: str, kind: str, max_chars: int = 6500) -> str:
    """定位申购或赎回现金替代处理程序正文。

    各基金招募书的标题不完全一致，不能只依赖一个固定标题；同时要尽量在
    申购块和赎回块之间截断，避免把另一侧的计价规则混入证据。
    """
    if kind == "subscription":
        starts = [
            r"申购替代金额的处理程序",
            r"申购现金替代保证金的处理程序",
            r"申购现金替代保证金和赎回对应的替代金额的处理程序",
            r"申购对应的替代金额的处理程序",
            r"替代金额的处理程序",
        ]
        ends = [
            r"赎回替代金额的处理程序",
            r"赎回现金替代金额的处理程序",
            r"赎回对应的替代金额的处理程序",
            r"赎回现金替代款的处理程序",
        ]
    else:
        starts = [
            r"赎回替代金额的处理程序",
            r"赎回现金替代金额的处理程序",
            r"赎回对应的替代金额的处理程序",
            r"申购现金替代保证金和赎回对应的替代金额的处理程序",
            r"赎回现金替代款的处理程序",
            r"替代金额的处理程序",
        ]
        ends = [
            r"申购替代金额的处理程序",
            r"申购现金替代保证金的处理程序",
            r"申购对应的替代金额的处理程序",
        ]
    # 若存在方向明确的标题，不让通用的“替代金额的处理程序”把另一侧
    # 的正文提前截走；部分 PDF 会在申购/赎回标题前使用“③替代金额...”总标题。
    generic = r"替代金额的处理程序"
    specific_matches = [
        pattern for pattern in starts
        if pattern != generic and re.search(pattern, text, re.I)
    ]
    if specific_matches:
        starts = [pattern for pattern in starts if pattern != generic]
    return section(text, starts, ends, max_chars)


def cash_substitution_overview(text: str) -> str:
    """提取现金替代定义、金额公式和固定替代金额条款。"""
    return section(
        text,
        [r"现金替代相关内容", r"现金替代的有关内容", r"现金替代相关规定"],
        [r"预估现金部分相关内容", r"预估现金差额", r"申购赎回清单差错"],
        7000,
    ) or substitution_block(text, "subscription", 7000)


def source_snippet(text: str, patterns: list[str], limit: int = 900) -> str:
    """返回命中条款所在的一个可读句段，优先保留完整中文句子。"""
    if not text:
        return ""
    clauses = timing_clauses(text)
    for clause in clauses:
        if any(re.search(pattern, clause, re.I) for pattern in patterns):
            return compact(clause)[:limit]
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return compact(text[max(0, match.start() - 80) : match.start() + limit])[:limit]
    return ""


def fixed_replacement_amount_rule(text: str) -> str:
    overview = cash_substitution_overview(text)
    rule = source_snippet(overview, [r"必须替代金额", r"固定替代金额"], 900)
    if rule:
        return rule
    return ""


def _unbought_or_unsold_price_rule(block: str, kind: str) -> str:
    if not block:
        return ""
    if kind == "subscription":
        anchors = [r"未买入", r"未购入", r"未实际买入", r"未实际购入", r"未买到"]
    else:
        anchors = [r"未卖出", r"未实际卖出"]
    # 优先取同时出现“未买入/未卖出”和收盘价的整句，避免把 IOPV 或估值
    # 章节中的收盘价误判成现金替代的未成交计价。
    anchor_pattern = rf"(?:{'|'.join(anchors)})"
    for anchor_match in re.finditer(anchor_pattern, block, re.I):
        # PDF 常把“无交易取最近收盘价”用分号拆开，且“未买入部分”可能
        # 出现在收盘价规则之后。因此在锚点前后合并一个局部窗口再识别。
        window_start = max(0, anchor_match.start() - 420)
        window_end = min(len(block), anchor_match.end() + 900)
        window = block[window_start:window_end]
        price_matches = list(re.finditer(r"收盘价|收市价", window, re.I))
        if not price_matches:
            continue
        price_match = min(price_matches, key=lambda match: abs((window_start + match.start()) - anchor_match.start()))
        prefix = window[: price_match.start() + 1]
        date_matches = list(re.finditer(r"T\s*(?:\+\s*\d+)?\s*日", prefix, re.I))
        price_day = compact(date_matches[-1].group(0)) if date_matches else "相关 T 日"
        value = f"未{'买入' if kind == 'subscription' else '卖出'}部分按{price_day}收盘价折算为人民币"
        if re.search(r"无交易", window, re.I):
            value += "；当日无交易取最近交易日收盘价"
        if re.search(r"无收盘价", window, re.I):
            value += "；当日无收盘价取最后成交价"
        return value
    # 没有“未买入/未卖出”锚点时，不使用现金差额、IOPV 或估值章节中的
    # 普通收盘价，避免把无关的价格口径误写成未成交证券的结算价格。
    return ""


def unpurchased_price_rule(text: str, kind: str) -> str:
    block = substitution_block(text, kind)
    value = _unbought_or_unsold_price_rule(block, kind)
    if value:
        return value
    fixed = fixed_replacement_amount_rule(text)
    if fixed:
        label = "未补券" if kind == "subscription" else "未卖出"
        return f"{label}/固定现金替代：{fixed}"
    if re.search(r"全现金替代", text, re.I):
        return "全现金替代模式：招募书未单列未成交证券的补券/代卖计价价格"
    return "招募书未直接说明"


def suspension_handling(text: str) -> str:
    """将“停牌专门条款”和“停牌导致无交易时的价格兜底”分开记录。"""
    values = []
    if re.search(r"停牌的成份证券|处于停牌的成份证券|停牌成份证券", text, re.I):
        values.append("停牌成份证券适用必须现金替代/固定现金替代")

    # 风险章节也常出现“成分股长期停牌”，只有同时出现结算价调整/复牌
    # 办理的处理条款才计入本字段，避免把风险提示误当成操作规则。
    if re.search(r"长期停牌[^。；]{0,500}(?:参照[^。；]{0,100}估值价格|调整结算价格|调整结算价)", text, re.I):
        values.append("长期停牌/流动性不足导致价格不公允时，可参照估值价格调整结算价")
    if re.search(r"复牌后[^。；]{0,180}(?:实际交易成本|合理的价格)", text, re.I):
        values.append("复牌后按实际交易成本或基金管理人认为合理的价格办理")

    sub_price = unpurchased_price_rule(text, "subscription")
    red_price = unpurchased_price_rule(text, "redemption")
    no_trade_values = []
    for value in (sub_price, red_price):
        if re.search(r"无交易", value, re.I):
            no_trade_values.append("停牌导致当日无交易时，按对应处理程序的无交易价格规则计值")
            break
    if no_trade_values:
        values.extend(no_trade_values)

    if not values:
        return "未检出停牌专门条款；未成交计价规则亦未直接检出"
    return "；".join(dict.fromkeys(values))


def subscription_consideration_calculation(text: str) -> str:
    overview = cash_substitution_overview(text)
    buy_block = substitution_block(text, "subscription")
    values = []
    formula = source_snippet(
        overview,
        [r"(?:申购替代金额|申购现金替代保证金)\s*(?:[＝=：:]|为)", r"替代金额\s*[＝=]"],
        1000,
    )
    if formula:
        # 少数招募书把申购、赎回两个公式排在同一行；申购字段只保留
        # 申购侧，赎回侧的完整组成由 redemption_consideration_calculation 记录。
        formula = re.split(r"赎回替代金额\s*[＝=：:]?", formula, maxsplit=1)[0].rstrip()
        values.append(f"申购替代金额/保证金：{formula}")
    fixed = fixed_replacement_amount_rule(text)
    if fixed:
        values.append(f"必须现金替代：{fixed}")
    if re.search(r"实际(?:单位)?(?:买入|购入)成本[^。；]{0,220}(?:买入价格|购入价格)[^。；]{0,80}(?:相关费用|交易费用)|实际(?:购入|买入)的相关费用", buy_block, re.I):
        values.append("T日日终按已买入部分实际买入成本（含买入价格及相关交易费用）与未买入部分计值结果结算")
    elif re.search(r"实际(?:单位)?(?:买入|购入)成本", buy_block, re.I):
        values.append("T日日终按已买入部分实际买入成本与未买入部分计值结果结算")
    if re.search(r"预先收取[^。；]{0,220}(?:高于|低于)|退还多收取|收取欠缺的差额|多退少补", overview + buy_block, re.I):
        values.append("预收金额高于实际结算成本退还差额，低于实际结算成本由投资者补交差额")
    if not values:
        return "招募书未直接说明"
    return "；".join(dict.fromkeys(values))[:2200]


def redemption_consideration_calculation(text: str) -> str:
    sell_block = substitution_block(text, "redemption")
    values = []
    if re.search(r"全现金替代", text, re.I) and not re.search(r"实际(?:单位)?卖出所得|实际(?:单位)?卖出金额|未卖出", sell_block, re.I):
        values.append("本基金采用全现金替代，赎回对价包括现金替代、现金差额及其他对价；本 PDF 未单列底层代卖计算式，按相关业务规则及协议处理")
    if re.search(r"实际(?:单位)?卖出所得|实际(?:单位)?卖出金额", sell_block, re.I):
        if re.search(r"卖出价格扣减相应的交易费用|扣除相关费用|扣减相应的交易费用", sell_block, re.I):
            values.append("全部卖出：实际卖出所得/金额扣除相应交易费用后按汇率折算")
        else:
            values.append("全部卖出：按实际卖出所得/金额并按汇率折算")
    unsold_price = unpurchased_price_rule(text, "redemption")
    if re.search(r"未(?:能)?卖出", sell_block, re.I) and unsold_price not in {"招募书未直接说明", "全现金替代模式：招募书未单列未成交证券的补券/代卖计价价格"}:
        values.append("未全部卖出：加上未卖出部分按赎回未卖出价格规则计值后按汇率折算，确定赎回现金替代金额")
    fixed = fixed_replacement_amount_rule(text)
    if fixed:
        values.append(f"必须现金替代：{fixed}")
    if not values:
        return "招募书未直接说明"
    return "；".join(dict.fromkeys(values))[:2200]


def replacement_purchase_rule(text: str, deadline_value: str) -> str:
    block = substitution_block(text, "subscription")
    values = []
    if re.search(r"基金管理人[^。；]{0,350}(?:买入|购入)", block, re.I):
        values.append(f"基金管理人代投资者买入被替代成份证券，最迟时点：{deadline_value}")
    elif re.search(r"(?:买入|购入)(?:被替代成份证券|被替代证券|组合证券)|组合证券的代理买入", block, re.I):
        values.append(f"买入被替代证券，最迟时点：{deadline_value}")
    elif deadline_value not in {"未检出", "待人工复核"} and re.search(r"已买入|未能买入|实际买入成本", block, re.I):
        values.append(f"原文未单列代理买入动作，但以已买入/未买入结果结算，处理截止：{deadline_value}")
    if re.search(r"有权[^。；]{0,120}(?:不买入|不购入)|可[^。；]{0,80}(?:不买入|不购入)|不买入部分或全部|不购入部分或全部", block, re.I):
        values.append("基金管理人可不买入部分或全部被替代证券")
    if re.search(r"申购赎回轧差|轧差后的净额|净申购[^。；]{0,80}买入", block, re.I):
        values.append("未买入数量可包含同日申赎轧差后的未下单部分")
    return "；".join(dict.fromkeys(values)) if values else "招募书未直接说明"


def replacement_sale_rule(text: str, deadline_value: str) -> str:
    block = substitution_block(text, "redemption")
    values = []
    if deadline_value in {"未检出", "待人工复核"} and re.search(r"全现金替代", text, re.I):
        return "全现金替代/代买代卖模式；PDF未单列赎回代卖时点，按相关业务规则及协议处理"
    if re.search(r"基金管理人[^。；]{0,350}卖出", block, re.I):
        values.append(f"基金管理人代投资者卖出被替代成份证券，最迟时点：{deadline_value}")
    elif re.search(r"卖出相应的成份证券|卖出被替代证券|组合证券的代理卖出", block, re.I):
        values.append(f"卖出被替代证券，最迟时点：{deadline_value}")
    elif deadline_value not in {"未检出", "待人工复核"} and re.search(r"已卖出|未能卖出|实际卖出金额", block, re.I):
        values.append(f"原文未单列代理卖出动作，但以已卖出/未卖出结果结算，处理截止：{deadline_value}")
    if re.search(r"有权[^。；]{0,120}(?:不卖出)|可[^。；]{0,80}(?:不卖出)|不卖出部分或全部", block, re.I):
        values.append("基金管理人可不卖出部分或全部被替代证券")
    if re.search(r"申购赎回轧差|轧差后的净额|净赎回[^。；]{0,80}卖出", block, re.I):
        values.append("未卖出数量可包含同日申赎轧差后的未下单部分")
    return "；".join(dict.fromkeys(values)) if values else "招募书未直接说明"


def deadline(block: str, action: str) -> str:
    """取最靠近首个买入/卖出动作的时点。"""
    if not block:
        return "未检出"
    actions = list(re.finditer(r"买入|购入|卖出", block))
    if not actions:
        return "未检出"
    action_match = next((m for m in actions if (action == "buy" and m.group() in {"买入", "购入"}) or (action == "sell" and m.group() == "卖出")), actions[0])
    window_start = max(0, action_match.start() - 180)
    window_end = min(len(block), action_match.end() + 180)
    window = block[window_start:window_end]
    tplus = re.search(r"T\s*\+\s*(\d+)", window, re.I)
    if tplus:
        return f"T+{tplus.group(1)}（原文上下文）"
    if re.search(r"T\s*日(?:内|（[^）]*）内)?|在T\s*日", window, re.I):
        return "T日内"
    return "待人工复核"


def action_deadline(text: str, action: str, block: str = "") -> str:
    """提取实际买券/卖券动作的时点。

    优先在“申购/赎回替代金额的处理程序”正文块内识别，避免把风险提示中的
    “港股 T+0 回转交易”或现金替代款 T+N 误当成补券时间。只有在正文块没有
    命中时，才回退到全文，并要求同一语句同时处于替代证券处理语境。
    """
    source = compact(block or text).replace("＋", "+")
    clauses = re.split(r"(?<=[。；])", source)
    action_pattern = r"买入|购入" if action == "buy" else r"卖出"
    time_pattern = r"T\s*\+\s*(\d+)|T\s*日(?:内|当天)?"

    def scan(parts: list[str], require_context: bool) -> str:
        for clause in parts:
            if not re.search(action_pattern, clause):
                continue
            if require_context and not (
                "替代" in clause
                and ("被替代" in clause or "代投资者" in clause or "组合证券" in clause)
            ):
                continue
            actions = list(re.finditer(action_pattern, clause))
            times = list(re.finditer(time_pattern, clause, re.I))
            if not times:
                continue
            target = actions[0]
            nearest = min(times, key=lambda m: abs(m.start() - target.start()))
            if nearest.group(1):
                return f"T+{nearest.group(1)}"
            return "T日内"
        return ""

    result = scan(clauses, require_context=False if block else True)
    if result:
        return result
    if block:
        # 有些招募书把“基金管理人将买入/卖出证券”单独成句，上一句才写替代金额。
        # 这里仅在已定位的处理程序块内放宽语境，不再接触全文风险提示。
        result = scan(clauses, require_context=False)
        if result:
            return result
    return "未检出"


def nearby(text: str, patterns: list[str], limit: int = 420) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            start = max(0, match.start() - 80)
            return compact(text[start : min(len(text), match.end() + limit)])
    return ""


def same_day_netting(text: str) -> tuple[str, str, str]:
    """返回状态、证据类型、原文附近摘录。"""
    for match in re.finditer(r"轧差", text):
        window = text[max(0, match.start() - 250) : min(len(text), match.end() + 500)]
        if (
            re.search(r"轧差\s*处理", window)
            and re.search(r"净申购", window)
            and re.search(r"净赎回", window)
            and re.search(r"有权", window)
        ):
            return "PASS", "fund_specific_explicit", compact(window)
    replacement = section(
        text,
        [r"申购替代金额的处理程序", r"替代金额的处理程序"],
        [r"赎回替代金额的处理程序", r"现金差额", r"申购赎回清单"],
        6000,
    )
    if "申购赎回轧差" in replacement or "申购赎回轧差" in text:
        return "PENDING", "risk_or_non_operational", nearby(text, [r"申购赎回轧差"], 300)
    return "PENDING", "not_found", ""


def cash_difference(text: str) -> str:
    block = section(text, [r"清算交收与登记", r"申购与赎回的清算交收"], [], 5000)
    if not block:
        block = text
    found = {"清算": [], "交收": []}
    clauses = re.split(r"(?<=[。；])", compact(block).replace("＋", "+"))
    for clause in clauses:
        if "现金差额" not in clause:
            continue
        times = list(re.finditer(r"T\s*\+\s*(\d+)", clause, re.I))
        for action in found:
            action_matches = list(re.finditer(action, clause))
            if action_matches and times:
                nearest = min(times, key=lambda m: abs(m.start() - action_matches[0].start()))
                found[action].append(nearest.group(1))
    if found["清算"] or found["交收"]:
        c = found["清算"][0] if found["清算"] else "?"
        s = found["交收"][0] if found["交收"] else "?"
        return f"T+{c}清算；T+{s}交收"
    return "待人工复核"


def timing_clauses(text: str) -> list[str]:
    """按中文句号/分号切分时序条款，保留可读的原文片段。"""
    return re.split(r"(?<=[。；])", compact(text).replace("＋", "+"))


def timing_expression(clause: str, anchor: str | re.Match = "") -> str:
    """从一条处理语句中提取靠近动作的 T 日/T+N 或工作日时限。"""
    patterns = [
        r"T\s*\+\s*\d+\s*日(?:\s*[（(][^）)]{0,35}[）)])?\s*后的[^。；，,]{0,80}内",
        r"T\s*日(?:\s*[（(][^）)]{0,35}[）)])?\s*后的[^。；，,]{0,80}内",
        r"T\s*\+\s*\d+\s*日(?:\s*[（(][^）)]{0,25}[）)])?\s*内",
        r"T\s*\+\s*\d+\s*日(?:\s*[（(][^）)]{0,25}[）)])?",
        r"T\s*日(?:间|终|内|收市后|后)?",
        r"自有效赎回申请之日起\s*\d+\s*个(?:工作日|开放日)\s*内",
        r"数据发送后的第\s*\d+\s*个工作日\s*内",
    ]
    matches = []
    for pattern in patterns:
        matches.extend(re.finditer(pattern, clause, re.I))
    if not matches:
        return ""
    # 处理程序条款可能在同一句同时写基金份额、现金差额等多个时点。
    # 以动作锚点为准，避免把同句后面的 T+N 现金差额时点误当成基金份额交收时点。
    anchor_match = anchor if hasattr(anchor, "start") else (re.search(anchor, clause, re.I) if anchor else None)
    if anchor_match:
        before = [item for item in matches if item.start() <= anchor_match.start()]
        match = max(before, key=lambda item: item.start()) if before else min(matches, key=lambda item: item.start())
    else:
        match = min(matches, key=lambda item: item.start())
    return compact(match.group(0)).replace("＋", "+")


def settlement_timing(text: str, subject_patterns: list[str], action_patterns: list[str]) -> str:
    """在清算交收条款中提取一个主题对应的动作时点。"""
    block = section(text, [r"清算交收与登记", r"申购与赎回的清算交收", r"申购和赎回的清算交收"], [], 6500) or text
    for clause in timing_clauses(block):
        subject_matches = [match for subject in subject_patterns for match in re.finditer(subject, clause, re.I)]
        if not subject_matches:
            continue
        action_matches = [match for action in action_patterns for match in re.finditer(action, clause, re.I)]
        if not action_matches:
            continue
        candidates = [
            (abs(subject.start() - action.start()), subject, action)
            for subject in subject_matches
            for action in action_matches
        ]
        _, _, action_match = min(candidates, key=lambda item: item[0])
        # 排除“涉及现金差额……清算交收适用业务规则”这类总括性句子，
        # 要求主题和具体动作在同一处理语句内相邻出现。
        if min(abs(match.start() - action_match.start()) for match in subject_matches) > 140:
            continue
        if "适用" in clause and not re.search(r"办理|需按|正常情况下", clause, re.I):
            continue
        timing = timing_expression(clause, action_match)
        if timing:
            return timing
    return "待人工复核"


def first_clause_with(text: str, subject_patterns: list[str], action_patterns: list[str], limit: int = 160) -> str:
    """返回最靠前的主题/动作条款，用于记录复杂的跨市场工作日时限。"""
    for clause in timing_clauses(text):
        if any(re.search(subject, clause, re.I) for subject in subject_patterns) and any(re.search(action, clause, re.I) for action in action_patterns):
            return compact(clause)[:limit]
    return ""


def subscription_cash_substitution_timing(text: str) -> str:
    block = section(text, [r"清算交收与登记", r"申购与赎回的清算交收", r"申购和赎回的清算交收"], [], 6500) or text
    if re.search(r"日间[^。；]{0,80}(?:RTGS|实时逐笔全额)[^。；]{0,100}交收|(?:RTGS|实时逐笔全额)[^。；]{0,100}日间[^。；]{0,100}交收", block, re.I):
        return "T日日间 RTGS/实时逐笔全额交收；日间未完成时 T日日终交收"
    value = settlement_timing(text, [r"现金替代"], [r"交收", r"清算交收"])
    return value if value != "待人工复核" else "待人工复核"


def subscription_units_available_time(text: str) -> str:
    text = compact(text).replace("＋", "+")
    match = re.search(
        r"日间完成\s*RTGS[^。；]{0,100}?T\s*日(?:可卖出|可以卖出)[^。；]{0,80}?日终完成[^。；]{0,100}?T\s*\+\s*1\s*日(?:方可|可以)",
        text,
        re.I,
    )
    if match:
        return "T日（T日日间完成 RTGS）；T+1日（T日日终逐笔全额非担保交收）"
    block = section(text, [r"清算交收与登记", r"申购与赎回的清算交收", r"申购和赎回的清算交收"], [], 4000) or text
    if re.search(r"T\s*日[^。；]{0,80}基金份额[^。；]{0,80}交收", block, re.I):
        return "T日（基金份额交收完成后；招募书未另行说明可卖出时点）"
    return "招募书未直接说明"


def redemption_cash_substitution_arrival(text: str) -> str:
    text = compact(text).replace("＋", "+")
    settlement_block = section(text, [r"清算交收与登记", r"申购与赎回的清算交收", r"申购和赎回的清算交收"], [], 6500)
    clauses = timing_clauses(settlement_block or text)
    preferred = [
        r"赎回现金替代(?:金额|款)",
        r"赎回替代金额",
        r"赎回替代款",
    ]
    for clause in clauses:
        if not any(re.search(subject, clause, re.I) for subject in preferred):
            continue
        if not re.search(r"(?:清算交收|划往|支付|交收|到账)", clause, re.I):
            continue
        timing = timing_expression(clause, r"(?:清算交收|划往|支付|交收|到账)")
        if timing:
            return timing
        continue
    # 复杂时限有时在“赎回的清算交收”段落中与主题分成相邻两句。
    block = settlement_block or text
    for clause in timing_clauses(block):
        if re.search(r"现金替代", clause, re.I) and re.search(r"清算交收|支付|划往", clause, re.I):
            timing = timing_expression(clause, r"清算交收|支付|划往")
            if timing:
                return timing
    return "待人工复核"


def cash_difference_announcement(text: str) -> str:
    text = normalize_search_text(text)
    if re.search(r"T\s*日现金差额[^。；]{0,120}T\s*日后的第一个上海证券交易所交易日[^。；]{0,60}(?:公告|公布|发布)", text, re.I):
        return "T日后的第1个上海证券交易所交易日"
    if re.search(r"T\s*日申购赎回清单中公告\s*T-1\s*日现金差额", text, re.I):
        return "T日公告（T-1日现金差额）"
    direct = re.search(r"T\s*日现金差额[^。；]{0,100}T\s*\+\s*1\s*日[^。；]{0,60}(?:公告|公布|发布)", text, re.I)
    if direct:
        return "T+1日"
    block = section(text, [r"现金差额相关内容", r"清算交收与登记", r"申购与赎回的清算交收"], [], 6500) or text
    for clause in timing_clauses(block):
        if re.search(r"现金差额[^。；]{0,80}T\s*\+\s*1\s*日[^。；]{0,60}(?:公告|公布|发布)", clause, re.I):
            return "T+1日"
        if "现金差额" in clause and "预估现金差额" not in clause and re.search(r"公告|公布|发布", clause, re.I):
            timing = timing_expression(clause, r"公告|公布|发布")
            if timing:
                return timing
    return "待人工复核"


def cash_difference_clearing_time(text: str) -> str:
    """提取现金差额的清算时点，排除总括性规则句和仅描述公告的句子。"""
    text = normalize_search_text(text)
    block = section(text, [r"清算交收与登记", r"申购与赎回的清算交收", r"申购和赎回的清算交收"], [], 6500) or text
    for clause in timing_clauses(block):
        if "现金差额" not in clause or "预估现金差额" in clause or "适用" in clause:
            continue
        if not re.search(r"(?:办理|进行|完成)[^。；]{0,50}现金差额[^。；]{0,50}清算|现金差额[^。；]{0,80}(?:办理|进行|完成)[^。；]{0,30}清算", clause, re.I):
            continue
        timing = timing_expression(clause, r"清算")
        if timing:
            return timing
    # 招募书若只写“按 T+1 日公告的现金差额进行资金清算交收”，
    # 用公告时点作为清算时点的保守记录，并在字段定义中保留该限制。
    announcement = cash_difference_announcement(text)
    return announcement if announcement != "待人工复核" else "待人工复核"


def valuation_block(text: str) -> str:
    """定位基金合同/招募书中的“基金资产估值”正文，避免目录和风险提示干扰。"""
    # 不同 PDF 的章节标题格式并不统一，例如“第十四部分基金资产的估值”、
    # “§13 基金资产估值”和“十四、基金资产的估值”。先锁定带正式章节编号的
    # 标题，再要求其后确实出现估值日/估值对象/估值方法，排除目录、风险提示
    # 和正文中偶然出现的“暂停基金资产估值”。
    heading_pattern = (
        r"(?:^|\s|页)(?:第[一二三四五六七八九十百零〇0-9]+(?:部分|节)|§\s*\d+|"
        r"[一二三四五六七八九十百零〇]+、)\s*基金资产(?:的)?估值"
    )
    viable = []
    for match in re.finditer(heading_pattern, text, re.I):
        tail = text[match.end() : match.end() + 3200]
        if not re.search(r"估值日", tail, re.I):
            continue
        if not re.search(r"估值对象|估值方法|估值原则", tail, re.I):
            continue
        score = 0
        if re.search(r"估值对象", tail, re.I):
            score += 1
        if re.search(r"估值方法", tail, re.I):
            score += 1
        if re.search(r"外汇汇率|外币证券资产估值|主要货币对人民币汇率", tail, re.I):
            score += 1
        viable.append((score, match))
    if viable:
        _, match = max(viable, key=lambda item: (item[0], item[1].start()))
        start = match.start()
        confirmation = re.search(r"基金净值的确认", text[start + 100 :], re.I)
        end = start + 20000
        if confirmation:
            end = min(end, start + 100 + confirmation.start())
        # 有些招募书没有“基金净值的确认”小标题，固定长度会把后面的
        # “收益与分配/风险揭示”章节一并带入，进而误识别“汇率风险”为估值汇率。
        next_chapter = re.search(
            r"(?:第[一二三四五六七八九十百零〇0-9]+(?:部分|节)|[一二三四五六七八九十百零〇]+、)\s*"
            r"(?:基金的收益与分配|基金的费用与税收|基金的会计与审计|基金的会计和审计|基金的信息披露|风险揭示|基金财产清算)",
            text[start + 100 :],
            re.I,
        )
        if next_chapter:
            end = min(end, start + 100 + next_chapter.start())
        return text[start:end]
    return text


def first_context(text: str, patterns: list[str], before: int = 120, after: int = 520) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            start = max(0, match.start() - before)
            return compact(text[start : min(len(text), match.end() + after)])
    return ""


def nav_calculation_time(text: str) -> str:
    patterns = [
        r"T\s*日的基金份额净值在(?:当天|当日)收市后计算",
        r"基金管理人应于每个开放日交易结束后计算当日的基金资产净值和基金份额净值",
        r"每个估值日(?:闭市|交易结束)后计算基金(?:资产净值|份额净值)",
        r"每个估值日对前一估值日的基金资产估值",
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.I):
            if "T" in pattern:
                return "T日收市后计算"
            if "开放日" in pattern:
                return "每个开放日交易结束后计算"
            if "前一估值日" in pattern:
                return "每个估值日计算前一估值日的基金资产估值"
            return "每个估值日交易结束后计算"
    return "待人工复核"


def nav_announcement_time(text: str) -> str:
    match = re.search(r"T\s*日的基金份额净值[^。；]{0,120}(?:在|并在|按照)[^。；]{0,40}公告", text, re.I)
    if match:
        value = compact(match.group(0))
        if re.search(r"T\s*\+\s*1\s*日", value, re.I):
            return "T+1日内公告"
        if "收市后" in value:
            return "收市后；公告时点按基金合同/招募书约定"
        return value[:160]
    if re.search(r"T\s*日的基金份额净值[^。；]{0,100}公告", text, re.I):
        return "公告时点已写明，但未抽取出明确 T+N"
    return "待人工复核"


def nav_precision(text: str) -> str:
    patterns = [
        r"基金份额净值[^。；]{0,100}保留到小数点后\s*(\d+)\s*位[^。；]{0,80}小数点后第\s*(\d+)\s*位四舍五入",
        r"基金份额净值[^。；]{0,100}保留到小数点后\s*(\d+)\s*位",
        r"基金份额净值[^。；]{0,100}小数点后\s*(\d+)\s*位",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            digits = match.group(1)
            return f"小数点后{digits}位；第{match.group(2) if match.lastindex and match.lastindex >= 2 else int(digits) + 1}位四舍五入"
    return "待人工复核"


def nav_price_basis(text: str) -> str:
    block = valuation_block(text)
    value = first_context(
        block,
        [
            r"交易所上市的有价证券",
            r"境外证券市场上市",
            r"证券交易所挂牌的市价",
        ],
        before=0,
        after=620,
    )
    if value and re.search(r"收盘价|市价|估值", value, re.I):
        return value[:700]
    return "待人工复核"


def nav_fx_clause(text: str) -> str:
    block = valuation_block(text)
    heading = re.search(
        r"(?:^|\s)(?:\d+\s*[、.)）]|[（(]\s*\d+\s*[）)])\s*(?:(?:外汇)?汇率(?:选取原则)?(?!风险)|估值中的汇率选取原则)",
        block,
        re.I,
    )
    if heading:
        tail = block[heading.start() : heading.start() + 1100]
        # 只截断同层级的“13、税收”等条目，保留汇率项内部的“（1）（2）”分项。
        next_item = re.search(r"\s+\d{1,2}\s*[、.]\s*", tail[15:], re.I)
        if next_item:
            tail = tail[: 15 + next_item.start()]
        return compact(tail.split("税收", 1)[0])[:1000]
    value = first_context(
        block,
        [r"估值计算中涉及", r"外币证券资产估值", r"主要货币对人民币汇率", r"估值汇率", r"汇率公允价", r"中国外汇交易中心", r"彭博（伦敦时间）"],
        before=80,
        after=700,
    )
    # 没有独立“汇率”编号时，按估值条款中的最近编号截取，避免把前一个
    # 证券估值项目、页眉页脚或后续项目拼进汇率字段。
    for pattern in [r"估值计算中涉及", r"外币证券资产估值", r"主要货币对人民币汇率", r"估值汇率", r"汇率公允价", r"中国外汇交易中心", r"彭博（伦敦时间）"]:
        match = re.search(pattern, block, re.I)
        if not match:
            continue
        headings = list(re.finditer(r"(?:^|\s)\d{1,2}\s*[、.]\s*", block[:match.start()], re.I))
        start = headings[-1].start() if headings else max(0, match.start() - 80)
        next_item = re.search(r"\s+\d{1,2}\s*[、.]\s*", block[match.end() :], re.I)
        end = match.end() + (next_item.start() if next_item else 900)
        candidate = compact(block[start:min(len(block), end)])
        if candidate:
            return candidate[:1000]
    return value[:1000] if value else ""


def nav_fx_normal_source(clause: str) -> str:
    """仅返回正常港币/港元估值路径的来源摘录，不把备用小币种并入正式台账。"""
    if not clause:
        return ""
    midpoint = re.compile(
        r"人民币汇率[^。；]{0,20}中间价"
        r"|人民币(?:汇率)?中间价"
        r"|人民币与(?:主要货币|港币|港元)的中间价"
        r"|人民币对(?:港币|港元|主要货币)[^。；]{0,40}中间价"
        r"|(?:港币|港元)对人民币[^。；]{0,40}(?:中间价|估值)",
        re.I,
    )
    segments = [compact(item) for item in re.split(r"(?<=[。；])", clause) if compact(item)]
    for segment in segments:
        if re.search(r"中国人民银行|授权机构", segment, re.I) and midpoint.search(segment):
            # “或其他可以反映公允价值的汇率”是泛化/备用安排，主台账截断在正常中间价。
            normal_match = re.search(
                r"(?:估值日|当日|当天)[^。；]{0,180}(?:中国人民银行|其授权机构|授权机构)[^。；]{0,180}(?:中间价|人民币汇率)",
                segment,
                re.I,
            )
            if not normal_match:
                normal_match = re.search(
                    r"(?:中国人民银行|其授权机构|授权机构)[^。；]{0,180}(?:中间价|人民币汇率)",
                    segment,
                    re.I,
                )
            if normal_match:
                return compact(normal_match.group(0))[:700]
            return re.split(r"[，,]\s*(?:或其他|如有|若)", segment, maxsplit=1)[0][:700]
    for segment in segments:
        if re.search(r"(?:港股通结算汇兑比率|汇率公允价)", segment, re.I):
            return segment[:700]
    for segment in segments:
        if re.search(r"法律法规|监管机构|管理人与基金托管人协商|估值汇率", segment, re.I):
            return segment[:700]
    return compact(clause)[:700]


def nav_fx_normal_method(clause: str) -> str:
    """只提取本批港股通标的正常港币估值汇率，不展开小币种备用路径。"""
    if not clause:
        return "招募书未直接说明"
    midpoint = re.search(
        r"人民币汇率[^。；]{0,20}中间价"
        r"|人民币(?:汇率)?中间价"
        r"|人民币与(?:主要货币|港币|港元)的中间价"
        r"|人民币对(?:港币|港元|主要货币)[^。；]{0,40}中间价"
        r"|(?:港币|港元)对人民币[^。；]{0,40}(?:中间价|估值)",
        clause,
        re.I,
    )
    if re.search(r"中国人民银行|授权机构", clause, re.I) and midpoint:
        day = fx_normal_reference_day(clause)
        if not day and re.search(r"最新公布", clause, re.I):
            day = "最新公布的"
        if day == "最新公布的":
            return "正常：中国人民银行或其授权机构最新公布的人民币对港币汇率中间价"
        day_prefix = f"{day}" if day else ""
        return f"正常：{day_prefix}中国人民银行或授权机构公布的人民币对港币汇率中间价"
    if re.search(r"(?:港币|港元)对人民币[^。；]{0,40}(?:中间价|估值)", clause, re.I):
        return "正常：人民币对港币汇率中间价"
    if re.search(r"港股通结算汇兑比率", clause, re.I):
        return "正常：港股通结算汇兑比率（招募书估值条款明确如此）"
    if re.search(r"汇率公允价", clause, re.I):
        return "正常：汇率公允价（具体来源以招募书条款为准）"
    if re.search(r"根据相关法律法规及监管机构的要求确定|根据届时相关法律法规和监管机构的要求确定|根据届时相关法律法规及监管机构的要求确定", clause, re.I):
        if re.search(r"无相关规定[^。；]{0,120}管理人与基金托管人协商|管理人与基金托管人协商一致后确定", clause, re.I):
            return "正常：按法律法规/监管机构要求确定；未规定时由管理人与托管人协商（具体汇率未指定）"
        return "正常：按法律法规/监管机构要求确定（具体汇率未指定）"
    if re.search(r"估值汇率", clause, re.I):
        return "正常：采用估值汇率（具体来源未直接说明）"
    return "未明确：本次招募书未直接给出正常港币/港元→人民币估值汇率"


def nav_fx_hk_currency_scope(clause: str) -> str:
    """本批均为港股通标的，主台账只展示港币对人民币方向。"""
    return "港币/港元→人民币（港股通）" if clause else "原文未明确币种"


def fx_normal_reference_day(clause: str) -> str:
    """只识别正常港币估值路径的日期标签，不误取小币种备用路径时点。"""
    if not clause:
        return ""
    patterns = [
        r"(?P<day>估值日|当日|当天)[^。；]{0,160}(?:中国人民银行|其授权机构|授权机构)[^。；]{0,160}(?:中间价|人民币汇率)",
        r"(?:中国人民银行|其授权机构|授权机构)[^。；]{0,160}(?P<day>估值日|当日|当天)[^。；]{0,160}(?:中间价|人民币汇率)",
    ]
    for pattern in patterns:
        match = re.search(pattern, clause, re.I)
        if match:
            return match.group("day")
    return ""


def fx_reference_time(clause: str) -> str:
    if not clause:
        return "待人工复核"
    # 本批只关注港股通正常路径。若同一条款还包含“其他货币/彭博/伦敦时间
    # 16:00”等备用安排，必须先返回人民币中间价对应的估值日/当日。
    normal_day = fx_normal_reference_day(clause)
    if normal_day:
        return normal_day
    if re.search(r"中国人民银行|授权机构", clause, re.I) and re.search(
        r"人民币汇率[^。；]{0,20}中间价|人民币(?:汇率)?中间价|人民币与(?:主要货币|港币|港元)的中间价|人民币对(?:港币|港元|主要货币)[^。；]{0,40}中间价|(?:港币|港元)对人民币[^。；]{0,40}(?:中间价|估值)",
        clause,
        re.I,
    ):
        return "最新公布" if re.search(r"最新公布", clause, re.I) else "原文未明确取价时点"
    exact_matches = list(re.finditer(r"(?:北京时间|伦敦时间)[^0-9]{0,10}16[:：]00", clause, re.I))
    if exact_matches:
        values = []
        for match in exact_matches:
            value = compact(match.group(0)).replace("）", "").replace(")", "")
            if value not in values:
                values.append(value)
        return "；".join(values)
    for pattern in [
        r"T\s*[-＋+]\s*\d+\s*日(?:估值)?汇率",
        r"T\s*日(?:估值)?汇率",
        r"估值日(?:下午)?四点(?:（[^）]{0,20}）)?",
        r"下午四点(?:（[^）]{0,20}）)?",
        r"四点(?:（[^）]{0,20}）)?",
        r"前一估值日",
        r"估值日",
        r"当日(?:汇率|估值汇率)?",
        r"当天(?:汇率|估值汇率)?",
        r"实时汇率",
    ]:
        match = re.search(pattern, clause, re.I)
        if match:
            return compact(match.group(0))
    return "原文未明确取价时点"


def fx_currency_scope(clause: str) -> str:
    if not clause:
        return "原文未明确币种"
    foreign = []
    for pattern, label in [
        (r"美元", "美元"),
        (r"港币|港元", "港币/港元"),
        (r"日元", "日元"),
        (r"欧元", "欧元"),
        (r"英镑", "英镑"),
        (r"澳元", "澳元"),
        (r"新加坡元", "新加坡元"),
    ]:
        if re.search(pattern, clause, re.I) and label not in foreign:
            foreign.append(label)
    if foreign:
        return "、".join(foreign) + "对人民币"
    if re.search(r"港股通", clause, re.I) and "人民币" in clause:
        return "港币/港元折算为人民币"
    if re.search(r"折算为人民币|结算为人民币", clause, re.I):
        return "折算为人民币（具体外币币种未明确）"
    if re.search(r"主要货币|相关货币对人民币|外币.*人民币", clause, re.I):
        return "主要外币对人民币（具体币种未逐一列明）"
    if "人民币" in clause:
        return "人民币（外币币种未明确）"
    return "原文未明确币种"


def fx_fallback(clause: str) -> str:
    if not clause:
        return "待人工复核"
    for pattern in [r"若[^。；]{0,220}(?:未公布|无法取得|无相关规定|发生重大变更)[^。；]{0,220}", r"如[^。；]{0,220}(?:未公布|无法取得|无相关规定)[^。；]{0,220}"]:
        match = re.search(pattern, clause, re.I)
        if match:
            return compact(match.group(0))[:500]
    if re.search(r"与基金托管人协商一致后确定", clause, re.I):
        return "监管未规定时由基金管理人与基金托管人协商确定"
    return "原文未明确备用汇率处理"


def replacement_fx_context(text: str, kind: str) -> str:
    if kind == "creation":
        actual_patterns = [r"实际(?:单位)?(?:买入|购入)成本", r"实际购入的相关费用", r"未买入|未购入"]
    else:
        actual_patterns = [r"实际(?:单位)?卖出金额", r"未卖出", r"实际卖出"]
    # 优先锁定实际买入/卖出成本的处理语句，避免误取前面的替代金额公式。
    for pattern in actual_patterns:
        for match in re.finditer(pattern, text, re.I):
            window = text[max(0, match.start() - 420) : min(len(text), match.end() + 620)]
            if re.search(r"折算为人民币|折算汇率|估值汇率|结算汇兑比率|汇率公允价", window, re.I):
                return compact(window)[:1600]
    for match in re.finditer(r"折算汇率|估值汇率|汇率公允价", text, re.I):
        window = text[max(0, match.start() - 360) : min(len(text), match.end() + 900)]
        if re.search(r"买入|购入|卖出|未买入|未卖出|结算成本|结算金额", window, re.I):
            return compact(window)[:1600]
    return ""


def replacement_fx_fields(context: str) -> tuple[str, str, str, str]:
    if not context:
        return "招募书未直接说明", "招募书未直接说明", "原文未明确取价时点", "原文未明确币种"
    source_match = re.search(r"(?:折算汇率|估值汇率|汇率公允价)(?:采用|为|包括|按照)[^。；]{0,360}", context, re.I)
    if not source_match:
        source_match = re.search(r"(?:实际购入|实际买入|实际卖出)[^。；]{0,220}(?:结算汇兑比率|汇率)[^。；]{0,220}", context, re.I)
    source = compact(source_match.group(0))[:600] if source_match else "原文提到折算汇率，但未抽取出具体来源"
    # 只有来源条款本身明确出现“当日/估值日/16:00”等时点时才填入时点；
    # 不能把“ T 日内买入/卖出”误当成汇率取价时点。
    reference_time = fx_reference_time(source_match.group(0)) if source_match else "原文未明确取价时点"
    return context[:1000], source, reference_time, fx_currency_scope(context)


def replacement_fee_rule(text: str) -> str:
    values = []
    if re.search(r"实际(?:单位)?(?:买入|购入)成本[^。；]{0,80}(?:买入价格|购入价格)[^。；]{0,50}(?:相关费用|交易费用)", text, re.I) or re.search(r"买入价格与相关费用", text, re.I):
        values.append("申购：实际买入/购入成本包括买入价格与相关费用")
    if re.search(r"实际(?:单位)?卖出金额[^。；]{0,80}扣除相关费用", text, re.I) or re.search(r"卖出金额（扣除相关费用", text, re.I):
        values.append("赎回：实际卖出金额扣除相关费用")
    if not values and re.search(r"交易费用|相关费用", text, re.I):
        return "原文涉及相关费用，具体计入/扣除方式待人工复核"
    return "；".join(values) if values else "原文未明确费用处理"


def iopv_context(text: str) -> str:
    formula = re.search(r"基金份额参考净值\s*[＝=]", text, re.I)
    if formula:
        return compact(text[max(0, formula.start() - 600) : min(len(text), formula.start() + 1300)])
    heading = re.search(r"基金份额参考净值[^。；\n]{0,30}(?:计算与公告|计算方法)", text, re.I)
    if heading:
        return compact(text[heading.start() : heading.start() + 2600])
    return ""


def iopv_formula(text: str) -> str:
    context = iopv_context(text)
    match = re.search(r"基金份额参考净值\s*[＝=][^。；]{0,900}", context, re.I)
    return compact(match.group(0))[:900] if match else "招募书未直接说明"


def iopv_price_basis(text: str) -> str:
    context = iopv_context(text)
    values = []
    if re.search(r"实时成交数据", context, re.I):
        values.append("组合证券实时成交数据")
    if re.search(r"最新成交价", context, re.I):
        values.append("最新成交价")
    if re.search(r"收盘价", context, re.I):
        values.append("收盘价")
    return "；".join(values) if values else "原文未明确证券价格口径"


def iopv_fx_source(text: str) -> str:
    context = iopv_context(text)
    match = re.search(r"汇率公允价包括[^。；]{0,550}", context, re.I)
    if match:
        return compact(match.group(0))[:650]
    value = first_context(context, [r"汇率数据"], before=0, after=240)
    if value:
        return value[:320]
    return "招募书未直接说明"


def iopv_fx_time(text: str) -> str:
    context = iopv_context(text)
    if re.search(r"实时成交数据.*汇率数据|汇率数据.*实时成交数据", context, re.I):
        return "实时汇率数据"
    return fx_reference_time(context)


def iopv_publisher(text: str) -> str:
    context = iopv_context(text)
    calculator = "基金管理人或其委托机构计算" if re.search(r"基金管理人[^。；]{0,80}(?:委托|计算)|委托[^。；]{0,80}计算", context, re.I) else "计算机构原文未明确"
    if re.search(r"深圳证券交易所", context, re.I):
        publisher = "深圳证券交易所发布"
    elif re.search(r"上海证券交易所", context, re.I):
        publisher = "上海证券交易所发布"
    else:
        publisher = "发布机构原文未明确"
    return f"{calculator}；{publisher}"


def iopv_frequency_precision(text: str) -> str:
    context = iopv_context(text)
    values = []
    for pattern in [r"开市后", r"交易时间内", r"每\s*\d+\s*秒", r"每\s*\d+\s*分钟"]:
        match = re.search(pattern, context, re.I)
        if match:
            values.append(compact(match.group(0)))
    precision = re.search(r"(?:四舍五入)?[^。；]{0,20}小数点后\s*(\d+)\s*位", context, re.I)
    if precision:
        values.append(f"四舍五入保留小数点后{precision.group(1)}位")
    return "；".join(dict.fromkeys(values)) if values else "原文未明确发布频率与精度"


def same_day_netting_basis(status: str, context: str) -> str:
    if status != "PASS":
        return "招募书未直接说明"
    match = re.search(
        r"(?:在系统支持的情况下|对于)[^。；]{0,100}轧差处理[^。；]{0,420}",
        context,
        re.I,
    )
    value = compact(match.group(0) if match else context)
    return value[:700] if value else "已明确轧差处理，但原文摘录未取得"


def cash_substitution_mode(text: str, kind: str) -> str:
    block = section(text, [r"清算交收与登记", r"申购与赎回的清算交收", r"申购和赎回的清算交收"], [], 6500) or text
    if kind == "creation":
        if re.search(r"现金申购[^。；]{0,80}现金替代[^。；]{0,100}(?:RTGS|实时逐笔全额)|现金替代[^。；]{0,100}(?:RTGS|实时逐笔全额)", block, re.I):
            return "日间 RTGS/实时逐笔全额；日间未完成时日终逐笔全额非担保交收"
        if re.search(r"申购[^。；]{0,50}现金替代[^。；]{0,80}逐笔全额", block, re.I):
            return "逐笔全额交收"
    else:
        if re.search(r"赎回业务涉及的现金替代[^。；]{0,40}代收代付|现金赎回业务中的现金替代[^。；]{0,40}代收代付", block, re.I):
            return "代收代付"
        if re.search(r"赎回[^。；]{0,80}现金替代[^。；]{0,80}代收代付", block, re.I):
            return "代收代付"
    return "招募书未直接说明"


def subscription_cash_substitution_refund(text: str) -> str:
    block = section(
        text,
        [r"申购替代金额的处理程序", r"申购现金替代保证金的处理程序", r"申购现金替代保证金和赎回对应的替代金额的处理程序", r"替代金额的处理程序"],
        [r"赎回替代金额的处理程序", r"赎回对应的替代金额的处理程序"],
        5000,
    ) or text
    for clause in timing_clauses(block):
        if re.search(r"(?:退款|补款|退补款|多退少补)", clause) and re.search(r"(?:发送|清算|交收|办理)", clause):
            timing = timing_expression(clause, r"(?:发送|清算|交收|办理)")
            if timing:
                return timing
    return "待人工复核"


def adjustment_schedule(block: str, kind: str) -> str:
    if not block:
        return "待人工复核"
    if kind == "creation":
        pattern = r"(?:退款|补款|退还投资者|投资者应补交|应退款)"
    else:
        pattern = r"(?:赎回替代金额|赎回替代款)"
    matches = list(re.finditer(pattern, block))
    if not matches:
        return "待人工复核"
    for m in matches:
        window = block[max(0, m.start() - 100) : min(len(block), m.end() + 260)]
        time_match = re.search(r"T\s*(?:\+\s*\d+|日)[^。；]{0,120}", window, re.I)
        if time_match:
            return compact(time_match.group(0))
    return "见招募书处理程序"


def cross_border(text: str, title: str) -> bool:
    sample = f"{title} {text[:18000]}"
    return bool(re.search(r"跨境ETF|QDII|港股通|香港交易所|香港联合交易所|恒生", sample, re.I))


def build_row(order: int, raw: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    code = code_of(raw["标的代码"])
    market = raw["标的代码"].split(".")[-1]
    notice = load_notice(code)
    item, pdf, txt = select_document(code, notice)
    text = txt.read_text(encoding="utf-8", errors="replace")
    normalized_text = normalize_search_text(text)
    title = str(item.get("TITLE") or "")
    fund_name = str(item.get("ShortTitle") or title)
    updated = notice.get("latest_updated_prospectus")
    any_doc = notice.get("latest_prospectus_any")
    pcf = pcf_info(code)

    buy_block = substitution_block(normalized_text, "subscription", 6500)
    sell_block = substitution_block(normalized_text, "redemption", 6500)
    buy_deadline = action_deadline(normalized_text, "buy", buy_block)
    sell_deadline = action_deadline(normalized_text, "sell", sell_block)
    net_status, net_type, net_context = same_day_netting(normalized_text)
    rtgs_document = bool(re.search(r"RTGS|实时逐笔全额", normalized_text, re.I))
    is_cross_border = cross_border(normalized_text, title)
    subscription_cash_mode = cash_substitution_mode(normalized_text, "creation")
    redemption_cash_mode = cash_substitution_mode(normalized_text, "redemption")
    subscription_cash_time = subscription_cash_substitution_timing(normalized_text)
    subscription_units_time = settlement_timing(normalized_text, [r"申购"], [r"基金份额[^。；]{0,30}(?:清算)?交收", r"基金份额的交收"])
    redemption_units_time = settlement_timing(normalized_text, [r"赎回"], [r"基金份额[^。；]{0,30}(?:清算)?交收", r"基金份额的交收"])
    units_available_time = subscription_units_available_time(normalized_text)
    cash_diff_announcement = cash_difference_announcement(normalized_text)
    cash_diff_clearing = cash_difference_clearing_time(normalized_text)
    cash_diff_settlement = settlement_timing(normalized_text, [r"现金差额"], [r"交收"])
    subscription_refund_time = subscription_cash_substitution_refund(normalized_text)
    redemption_cash_arrival = redemption_cash_substitution_arrival(normalized_text)
    valuation_fx = nav_fx_clause(normalized_text)
    creation_replacement_context = replacement_fx_context(buy_block or normalized_text, "creation")
    redemption_replacement_context = replacement_fx_context(sell_block or normalized_text, "redemption")
    creation_replacement_rule, creation_replacement_source, creation_replacement_time, creation_replacement_currency = replacement_fx_fields(creation_replacement_context)
    redemption_replacement_rule, redemption_replacement_source, redemption_replacement_time, redemption_replacement_currency = replacement_fx_fields(redemption_replacement_context)
    suspension_rule = suspension_handling(normalized_text)
    subscription_consideration = subscription_consideration_calculation(normalized_text)
    redemption_consideration = redemption_consideration_calculation(normalized_text)
    subscription_purchase_rule = replacement_purchase_rule(normalized_text, buy_deadline)
    redemption_sale_rule = replacement_sale_rule(normalized_text, sell_deadline)
    subscription_unpurchased_price = unpurchased_price_rule(normalized_text, "subscription")
    redemption_unsold_price = unpurchased_price_rule(normalized_text, "redemption")
    netting_basis = same_day_netting_basis(net_status, net_context)
    if rtgs_document:
        rtgs = "PASS"
        rtgs_basis = "fund_document"
        rtgs_note = nearby(normalized_text, [r"RTGS", r"实时逐笔全额"], 260)
    elif is_cross_border and market in {"SZ", "SH"}:
        rtgs = "PASS"
        rtgs_basis = f"applicable_{market}_cross_border_rule"
        rtgs_note = "招募书未直接出现 RTGS；按适用的交易所/中国结算跨境 ETF 规则，日间 RTGS 交收，按项目口径 PASS"
    else:
        rtgs = "PENDING"
        rtgs_basis = "not_found"
        rtgs_note = "未检出 RTGS 或实时逐笔全额表述"
    has_updated = bool(updated)
    status = "PASS" if has_updated and buy_deadline not in {"未检出", "待人工复核"} and sell_deadline not in {"未检出", "待人工复核"} and rtgs == "PASS" and net_status == "PASS" else "PENDING"
    questions = []
    if not has_updated:
        questions.append("东财暂未找到更新招募书，继续监测")
    if net_status != "PASS":
        questions.append("向基金管理人/托管运营确认同日申赎是否按证券维度净额下单")
    if rtgs_basis != "fund_document":
        questions.append("招募书未直接写 RTGS，保留适用规则来源")
    row = {
        "list_order": str(order),
        "code": code,
        "market": market,
        "fund_name": fund_name,
        "latest_updated_prospectus_date": (updated or {}).get("notice_date", "") if updated else "",
        "latest_prospectus_any_date": (any_doc or {}).get("notice_date", "") if any_doc else "",
        "latest_product_summary_date": (notice.get("latest_product_summary_update") or {}).get("notice_date", ""),
        "pcf_asof": pcf["pcf_asof"],
        "pcf_rows": pcf["pcf_rows"],
        "pcf_cash_flag": pcf["pcf_cash_flag"],
        "pcf_has_159900": pcf["pcf_has_159900"],
        "prospectus_basis": "updated" if has_updated else "initial_only",
        "creation_buy_deadline": buy_deadline,
        "redemption_sell_deadline": sell_deadline,
        "subscription_cash_substitution_mode": subscription_cash_mode,
        "subscription_cash_substitution_settlement_time": subscription_cash_time,
        "subscription_fund_units_settlement_time": subscription_units_time,
        "subscription_fund_units_available_time": units_available_time,
        "cash_difference_announcement_time": cash_diff_announcement,
        "cash_difference_clearing_time": cash_diff_clearing,
        "cash_difference_settlement_time": cash_diff_settlement,
        "subscription_cash_substitution_refund_time": subscription_refund_time,
        "redemption_fund_units_settlement_time": redemption_units_time,
        "redemption_cash_substitution_mode": redemption_cash_mode,
        "redemption_cash_substitution_arrival_time": redemption_cash_arrival,
        "same_day_netting_basis": netting_basis,
        "constituent_suspension_handling": suspension_rule,
        "subscription_consideration_calculation": subscription_consideration,
        "redemption_consideration_calculation": redemption_consideration,
        "subscription_replacement_purchase_rule": subscription_purchase_rule,
        "redemption_replacement_sale_rule": redemption_sale_rule,
        "subscription_unpurchased_price_rule": subscription_unpurchased_price,
        "redemption_unsold_price_rule": redemption_unsold_price,
        "nav_calculation_time": nav_calculation_time(normalized_text),
        "nav_announcement_time": nav_announcement_time(normalized_text),
        "nav_price_basis": nav_price_basis(normalized_text),
        "nav_precision": nav_precision(normalized_text),
        "nav_fx_source": nav_fx_normal_source(valuation_fx) or "招募书未直接说明",
        "nav_fx_normal_method": nav_fx_normal_method(valuation_fx),
        "nav_fx_reference_time": fx_reference_time(valuation_fx),
        "nav_fx_currency_scope": nav_fx_hk_currency_scope(valuation_fx),
        "nav_fx_fallback": "本批不纳入小币种/备用路径" if valuation_fx else "原文未明确正常港币估值汇率",
        "creation_replacement_fx_rule": creation_replacement_rule,
        "creation_replacement_fx_source": creation_replacement_source,
        "creation_replacement_fx_reference_time": creation_replacement_time,
        "creation_replacement_fx_currency": creation_replacement_currency,
        "redemption_replacement_fx_rule": redemption_replacement_rule,
        "redemption_replacement_fx_source": redemption_replacement_source,
        "redemption_replacement_fx_reference_time": redemption_replacement_time,
        "redemption_replacement_fx_currency": redemption_replacement_currency,
        "cash_substitution_fee_rule": replacement_fee_rule(normalized_text),
        "iopv_formula": iopv_formula(normalized_text),
        "iopv_price_basis": iopv_price_basis(normalized_text),
        "iopv_fx_source": iopv_fx_source(normalized_text),
        "iopv_fx_reference_time": iopv_fx_time(normalized_text),
        "iopv_publisher": iopv_publisher(normalized_text),
        "iopv_frequency_precision": iopv_frequency_precision(normalized_text),
        "rtgs_subscription": rtgs,
        "rtgs_basis": rtgs_basis,
        "rtgs_note": rtgs_note,
        "same_day_netting_manager": net_status,
        "same_day_netting_evidence": net_type,
        "cash_difference_schedule": cash_difference(normalized_text),
        "creation_cash_sub_schedule": adjustment_schedule(buy_block, "creation"),
        "redemption_cash_sub_schedule": adjustment_schedule(sell_block, "redemption"),
        "overall_status": status,
        "evidence_url": item.get("notice_url", ""),
        "prospectus_file": str(pdf.resolve()),
        "prospectus_text_file": str(txt.resolve()),
        "open_question": "；".join(questions),
    }
    evidence = {
        "list_order": str(order),
        "code": code,
        "fund_name": fund_name,
        "prospectus_title": title,
        "prospectus_date": item.get("notice_date", ""),
        "rtgs_context": rtgs_note,
        "netting_context": net_context,
        "buy_context": buy_block[:1200],
        "sell_context": sell_block[:1200],
        "suspension_context": nearby(normalized_text, [r"长期停牌", r"停牌的成份证券", r"停牌成份证券"], 1200),
        "subscription_consideration_context": subscription_consideration,
        "redemption_consideration_context": redemption_consideration,
        "subscription_price_context": subscription_unpurchased_price,
        "redemption_price_context": redemption_unsold_price,
        "settlement_context": nearby(normalized_text, [r"清算交收与登记", r"申购与赎回的清算交收"], 1600),
        "valuation_context": nearby(normalized_text, [r"基金资产(?:的)?估值", r"基金净值的确认"], 1800),
        "valuation_fx_context": valuation_fx[:1200],
        "creation_replacement_fx_context": creation_replacement_context[:1200],
        "redemption_replacement_fx_context": redemption_replacement_context[:1200],
        "iopv_context": iopv_context(normalized_text)[:1400],
        "cash_difference_context": nearby(normalized_text, [r"现金差额"], 600),
        "source_text_file": str(txt.resolve()),
    }
    return row, evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols-file", type=Path, default=Path("/Volumes/Upan/premiumWatchlistSymbols.csv"))
    parser.add_argument("--skip", type=int, default=0, help="跳过输入文件前 N 个标的，例如前 10 个已人工完成时使用 10")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少个标的；与 --skip 配合可生成指定批次")
    parser.add_argument("--output-prefix", default="全量", help="输出文件名前缀")
    args = parser.parse_args()
    rows = read_input(args.symbols_file)[args.skip :]
    if args.limit is not None:
        rows = rows[: args.limit]
    ledger_rows = []
    evidence_rows = []
    errors = []
    for order, raw in enumerate(rows, args.skip + 1):
        try:
            row, evidence = build_row(order, raw)
            ledger_rows.append(row)
            evidence_rows.append(evidence)
        except Exception as exc:
            errors.append({"order": order, "raw": raw, "error": repr(exc)})
    if ledger_rows:
        ledger_path = LEDGER_ROOT / f"{args.output_prefix}核心台账.csv"
        evidence_path = TEMP_ROOT / f"{args.output_prefix}自动摘录_20260909.csv"
        with ledger_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(ledger_rows[0]), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(ledger_rows)
        with evidence_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(evidence_rows[0]), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(evidence_rows)
        print(ledger_path)
        print(evidence_path)
    if errors:
        error_path = TEMP_ROOT / f"{args.output_prefix}台账生成错误_20260909.json"
        error_path.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"errors={len(errors)}; see {error_path}")
    print(f"rows={len(ledger_rows)} errors={len(errors)}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
