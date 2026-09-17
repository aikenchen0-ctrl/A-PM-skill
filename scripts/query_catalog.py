"""检索知识条目，并校验原文覆盖和去重引用。"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARTS = (
    "a-comparison-ownership.json",
    "b-value-reward.json",
    "c-self-social-learning.json",
    "d-persuasion-action.json",
)
TAG1_INDEX = "tag1-term-index.json"
EFFECT_DIFF = "effect-library-diff.json"
PRODUCT_EXPANSION = "product-expansion.json"
KINDS = {"机制", "模型", "调节条件", "设计方法", "证据警示"}
ROLES = {"条目陈述", "条件或解释", "设计建议", "案例或数据", "重复", "结构标题", "空节点", "待核材料"}
STATUSES = {"原文主张，未独立核验", "已有有限外部核查", "存在争议或原文疑点", "仅标题或证据不足"}
FIELDS = {"id", "name", "kind", "aliases", "source_nodes", "definition", "conditions", "boundaries", "alternatives", "applications", "test", "evidence_status", "evidence_note"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_catalog():
    parts = [read_json(ROOT / "references" / "catalog" / name) for name in PARTS]
    entries = [entry for part in parts for entry in part["entries"]]
    relations = read_json(ROOT / "references" / "catalog" / "relations.json")
    source = read_json(ROOT / "references" / "source-index.json")
    tag1 = read_json(ROOT / "references" / TAG1_INDEX) if (ROOT / "references" / TAG1_INDEX).exists() else {"entries": []}
    diff = read_json(ROOT / "references" / EFFECT_DIFF) if (ROOT / "references" / EFFECT_DIFF).exists() else {"effect_only": []}
    expansion = read_json(ROOT / "references" / PRODUCT_EXPANSION) if (ROOT / "references" / PRODUCT_EXPANSION).exists() else {"entries": []}
    return parts, entries, relations, source, tag1, diff, expansion


def canonical_map(entries, relations):
    result = {entry["id"]: entry["id"] for entry in entries}
    for group in relations["canonical_groups"]:
        for member in group["member_ids"]:
            result[member] = group["canonical_id"]
    return result


def validate(parts, entries, relations, source):
    errors = []
    ids = [entry.get("id") for entry in entries]
    if len(ids) != len(set(ids)):
        errors.append("存在重复条目标识")
    by_id = {entry["id"]: entry for entry in entries}
    nodes = {node["id"]: node for node in source["nodes"]}
    coverage = Counter()
    role_counts = Counter()
    entry_links = Counter()
    for part in parts:
        low, high = part["source_range"]
        for entry in part["entries"]:
            ident = entry["id"]
            if FIELDS - entry.keys():
                errors.append(f"{ident} 缺少必填字段")
                continue
            if entry["kind"] not in KINDS or entry["evidence_status"] not in STATUSES:
                errors.append(f"{ident} 类型或证据状态无效")
            for field in ("name", "definition", "test", "evidence_note"):
                if not isinstance(entry[field], str) or not entry[field].strip():
                    errors.append(f"{ident} 的 {field} 为空")
            for field in ("aliases", "source_nodes", "conditions", "boundaries", "alternatives"):
                if not isinstance(entry[field], list):
                    errors.append(f"{ident} 的 {field} 必须是数组")
            if not entry["source_nodes"] or any(node not in nodes or not low <= node <= high for node in entry["source_nodes"]):
                errors.append(f"{ident} 原文引用为空、越区或不存在")
            if set(entry["applications"]) != {"usage", "payment", "referral"} or any(not value.strip() for value in entry["applications"].values()):
                errors.append(f"{ident} 三目标应用不完整")
        expected = low
        for segment in part["coverage"]:
            start, end, role = segment["start"], segment["end"], segment["role"]
            if start != expected or end < start or start < low or end > high:
                errors.append(f"{part['part']} 覆盖区间 {start}-{end} 不连续或越界")
            expected = end + 1
            if role not in ROLES or not segment.get("note", "").strip():
                errors.append(f"节点 {start}-{end} 缺角色或说明")
            linked = segment["entry_ids"]
            if role not in {"空节点", "结构标题"} and not linked:
                errors.append(f"节点 {start}-{end} 没有关联条目")
            for ident in linked:
                if ident not in by_id:
                    errors.append(f"节点 {start}-{end} 引用了不存在的 {ident}")
                entry_links[ident] += 1
            for node_id in range(start, end + 1):
                coverage[node_id] += 1
                role_counts[role] += 1
                if node_id not in nodes:
                    errors.append(f"不存在节点 {node_id}")
                elif (not nodes[node_id]["text"].strip()) != (role == "空节点"):
                    errors.append(f"节点 {node_id} 的空文本分类不一致")
        if expected != high + 1:
            errors.append(f"{part['part']} 没有覆盖到分区结尾")
    if set(coverage) != set(nodes) or any(count != 1 for count in coverage.values()):
        errors.append("完整原文覆盖存在遗漏、重复或越界")
    for ident in by_id:
        if not entry_links[ident]:
            errors.append(f"{ident} 没有覆盖关联")
    grouped = set()
    for group in relations["canonical_groups"]:
        members = group["member_ids"]
        if group["canonical_id"] not in members or len(members) < 2 or len(members) != len(set(members)):
            errors.append("同义组的主条目或成员不合法")
        if any(member not in by_id for member in members):
            errors.append("同义组引用不存在的条目")
            continue
        if grouped.intersection(members):
            errors.append("同义组相互重叠")
        grouped.update(members)
        if len({by_id[member]["kind"] for member in members}) != 1:
            errors.append("同义组混合不同条目类型")
    for relation in relations["related_distinctions"]:
        if any(ident not in by_id for ident in relation["ids"]):
            errors.append("关联区分引用了不存在的条目")
    canonical = canonical_map(entries, relations)
    unique = [entry for entry in entries if canonical[entry["id"]] == entry["id"]]
    return {
        "校验通过": not errors,
        "错误": errors,
        "原文节点数": len(nodes),
        "已处置节点数": len(coverage),
        "分区记录数": len(entries),
        "归并后条目数": len(unique),
        "归并后按类型": dict(Counter(entry["kind"] for entry in unique)),
        "归并后按证据状态": dict(Counter(entry["evidence_status"] for entry in unique)),
        "覆盖角色按节点": dict(role_counts),
        "限制": "结构校验不证明语义无遗漏、机制相互独立或科学结论成立；应用是待验证假设。",
    }


def main():
    parser = argparse.ArgumentParser(description="检索行为产品知识目录；不修改文件。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate", action="store_true", help="校验字段、引用、覆盖和同义归并")
    mode.add_argument("--search", help="按名称、别名及定义检索，不区分英文字母大小写")
    mode.add_argument("--id", dest="entry_id", help="读取条目及同义组中的原始记录")
    mode.add_argument("--node", type=int, help="查询原文节点、祖先和覆盖处置")
    mode.add_argument("--tag1-term", help="检索tag1全量术语索引，不代表已有机制建模")
    mode.add_argument("--effect-only", action="store_true", help="读取清洗后效应库独有术语")
    mode.add_argument("--expansion", help="检索产品专用扩展卡")
    args = parser.parse_args()
    parts, entries, relations, source, tag1, diff, expansion = load_catalog()
    by_id = {entry["id"]: entry for entry in entries}
    canonical = canonical_map(entries, relations)
    if args.validate:
        result = validate(parts, entries, relations, source)
    elif args.search is not None:
        query = args.search.strip().casefold()
        if not query:
            raise ValueError("检索词不能为空")
        result = [{"id": entry["id"], "name": entry["name"], "kind": entry["kind"], "canonical_id": canonical[entry["id"]], "source_nodes": entry["source_nodes"], "evidence_status": entry["evidence_status"]} for entry in entries if query in " ".join([entry["name"], *entry["aliases"], entry["definition"]]).casefold()]
    elif args.entry_id:
        if args.entry_id not in by_id:
            raise ValueError("没有找到该条目标识")
        ident = canonical[args.entry_id]
        result = {"主条目标识": ident, "相关记录": [entry for entry in entries if canonical[entry["id"]] == ident]}
    elif args.tag1_term is not None:
        query = args.tag1_term.strip().casefold()
        if not query:
            raise ValueError("tag1检索词不能为空")
        result = [entry for entry in tag1["entries"] if query in entry["term"].casefold() or query in entry["short_name"].casefold()]
    elif args.effect_only:
        result = {"数量": len(diff.get("effect_only", [])), "术语": diff.get("effect_only", [])}
    elif args.expansion is not None:
        query = args.expansion.strip().casefold()
        if not query:
            raise ValueError("扩展卡检索词不能为空")
        result = [entry for entry in expansion["entries"] if query in " ".join([entry["id"], entry["name"], *entry.get("source_terms", []), entry["definition"]]).casefold()]
    else:
        nodes = {node["id"]: node for node in source["nodes"]}
        if args.node not in nodes:
            raise ValueError("没有找到该原文节点")
        ancestors = []
        parent = nodes[args.node]["parent_id"]
        while parent:
            ancestors.append(nodes[parent])
            parent = nodes[parent]["parent_id"]
        dispositions = [segment for part in parts for segment in part["coverage"] if segment["start"] <= args.node <= segment["end"]]
        result = {"节点": nodes[args.node], "祖先": list(reversed(ancestors)), "处置": dispositions}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if args.validate and not result["校验通过"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"知识目录读取失败：{exc}", file=sys.stderr)
        sys.exit(2)
