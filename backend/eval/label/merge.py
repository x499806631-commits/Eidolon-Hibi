"""把在 `label.html` 里标好的例子并进判断模型的 eval 集 `eval/judge.toml`。

一批候选例子（JSON）先在 `label.html` 里盲标：选完级才亮出 AI 给的级和理由，和 AI 不一样的
最后集中再过一遍。导出的标注（`<name>.labels.json`）和候选例子一起交给这个脚本：

    cd backend
    uv run python eval/label/merge.py eval/label/affinity-plain.json affinity-plain.labels.json

候选例子是 `{ "name": …, "cases": [...] }`，每条是一个 `[[affinity]]` 用例，外加只给标注页看的
字段：`name`（角色中文名）、`category`、`said_zh`、上文里她那几句的 `zh`。并进去时这些字段去掉，
级、两道是非题按标注写，`reviewed = true`；级和 AI 的不一样时，id 里的级跟着改，note 写明。
删掉的、没标的不并。id 已经在 eval 集里的整批不并，免得重复跑一遍写进两份。

只用标准库。目前只认 `[[affinity]]`。
"""

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

HEADER = "# ---- 以下由 eval/label/merge.py 从标注页并入 ----"


def text(s: str) -> str:
    """A TOML basic string: JSON's escapes are TOML's too."""
    return json.dumps(s, ensure_ascii=False)


def block(case: dict, label: dict) -> str:
    ai = case["level"]
    level = label["level"]
    id_ = case["id"]
    note = case.get("note", "")
    if level != ai:
        id_ = re.sub(rf"-affinity-{ai}-", f"-affinity-{level}-", id_, count=1)
        note = f"人工标 {level}（AI 原标 {ai}：{note}）"
    if label.get("comment"):
        note = f"{label['comment']}（{note}）" if note else label["comment"]
    out_of_line = label.get("out_of_line", case.get("out_of_line", False))
    manipulation = label.get("manipulation", case.get("manipulation", False))
    lines = ["[[affinity]]", f"id = {text(id_)}", f"character = {text(case['character'])}"]
    history = case.get("history", [])
    if history:
        lines.append("history = [")
        for m in history:
            key = "user" if "user" in m else "character"
            lines.append(f"  {{ {key} = {text(m[key])} }},")
        lines.append("]")
    else:
        lines.append("history = []")
    lines += [f"said = {text(case['said'])}", f"level = {level}"]
    if out_of_line:
        lines.append("out_of_line = true")
    if manipulation:
        lines.append("manipulation = true")
    if case.get("group"):
        lines.append(f"group = {text(case['group'])}")
    if case.get("variant"):
        lines.append(f"variant = {text(case['variant'])}")
    lines += [f"note = {text(note)}", "reviewed = true"]
    return "\n".join(lines)


def merge(candidates: dict, labels: dict, eval_text: str) -> tuple[str, list[str], int]:
    """The eval set with the labelled cases appended, their ids, and how many were left out."""
    existing = {
        entry["id"]
        for table in tomllib.loads(eval_text).values()
        if isinstance(table, list)
        for entry in table
    }
    blocks, ids, left_out = [], [], 0
    for case in candidates["cases"]:
        label = labels.get(case["id"])
        if not label or label.get("skip") or label.get("level") is None:
            left_out += 1
            continue
        b = block(case, label)
        id_ = tomllib.loads(b)["affinity"][0]["id"]
        if id_ in existing or id_ in ids:
            raise SystemExit(f"用例 {id_} 已经在 eval 集里了，这一批没有并入")
        blocks.append(b)
        ids.append(id_)
    if not blocks:
        return eval_text, ids, left_out
    merged = eval_text.rstrip("\n") + "\n"
    if HEADER not in merged:
        merged += f"\n{HEADER}\n"
    merged += "".join(f"\n{b}\n" for b in blocks)
    tomllib.loads(merged)
    return merged, ids, left_out


def main() -> int:
    parser = argparse.ArgumentParser(description="把标注页标好的例子并进判断模型的 eval 集")
    parser.add_argument("candidates", type=Path, help="候选例子的 JSON")
    parser.add_argument("labels", type=Path, help="标注页导出的 <name>.labels.json")
    parser.add_argument("--into", type=Path, default=Path("eval/judge.toml"),
                        help="eval 集（默认 eval/judge.toml）")
    args = parser.parse_args()
    candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    if labels.get("name") != candidates.get("name"):
        print(f"标注是「{labels.get('name')}」的，候选例子是「{candidates.get('name')}」的",
              file=sys.stderr)
        return 1
    merged, ids, left_out = merge(candidates, labels["labels"],
                                  args.into.read_text(encoding="utf-8"))
    with open(args.into, "w", encoding="utf-8", newline="\n") as f:
        f.write(merged)
    print(f"并入 {len(ids)} 条，没并 {left_out} 条（删掉或没标）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
