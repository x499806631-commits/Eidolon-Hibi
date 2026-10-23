"""玩法仓库登记（.github/ISSUE_TEMPLATE/fork_showcase.yml）的检查和收录，.github/workflows/fork-showcase.yml 调用它。

check：登记 issue 打开或编辑时，查仓库地址能不能打开、是不是公开、改没改名。结果写进一条评论，
再编辑时更新同一条；没通过就打 needs-info，通过了就摘掉。
accept：维护者打上 accepted 标签时，再查一遍，通过就把这一条写进 docs/community-forks.md 对应的一节、
提交并推到 main，然后评论、关闭 issue；没通过就评论原因，摘掉 accepted。

    python3 scripts/fork_showcase.py check 171
    python3 scripts/fork_showcase.py accept 171
    python3 scripts/fork_showcase.py accept 171 --dry-run   # 只打印要写进文档的一行，不提交不评论

issue 和仓库用 gh 查，CI 上要设 GH_TOKEN。只用标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from release_notes import REPO, run

DOC = Path(__file__).resolve().parent.parent / "docs" / "community-forks.md"
DOC_URL = f"https://github.com/{REPO}/blob/main/docs/community-forks.md"
# 评论里的标记，用来认出检查结果那条评论
MARKER = "<!-- fork-showcase-check -->"
PLACEHOLDER = "还没有收录的仓库。"
UPSTREAM_NAME = REPO.split("/")[1].lower()
SECTIONS = {"全年龄": "## 全年龄", "含成人内容（NSFW）": "## 含成人内容（NSFW）"}
REPO_URL = re.compile(r"^https?://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")


def gh(*args: str) -> str:
    return run("gh", *args, "--repo", REPO)


def fields(body: str) -> dict[str, str]:
    """把表单生成的正文（「### 标签」加内容）拆成 标签 → 内容；没填的是空串。"""
    parts = re.split(r"^### +(.+?)\s*$", body or "", flags=re.M)
    found = {}
    for label, value in zip(parts[1::2], parts[2::2]):
        value = value.strip()
        found[label] = "" if value == "_No response_" else value
    return found


def check(form: dict[str, str]) -> tuple[list[str], str | None]:
    """返回 没通过的原因 和 仓库的规范地址（打不开时是 None）。"""
    url = form.get("仓库地址", "").strip()
    match = REPO_URL.match(url)
    if not match:
        return ["仓库地址要是 GitHub 仓库的链接，形如 `https://github.com/用户名/仓库名`。"], None
    try:
        repo = json.loads(run("gh", "api", f"repos/{match[1]}/{match[2]}"))
    except subprocess.CalledProcessError:
        return [f"打不开 `{match[1]}/{match[2]}`：仓库不存在，或者不是公开的。"], None
    problems = []
    if repo.get("private") or repo.get("visibility", "public") != "public":
        problems.append("仓库不是公开的。")
    if repo["name"].lower() == UPSTREAM_NAME:
        problems.append(
            f"仓库还叫 `{repo['name']}`，请在仓库的 Settings → General 里改个名字（旧地址会自动跳转），"
            "改完把这里的仓库地址也换成新的。"
        )
    for label in ("项目名", "署名", "玩法简介"):
        if not form.get(label):
            problems.append(f"「{label}」没有填。")
    if form.get("内容分级") not in SECTIONS:
        problems.append("「内容分级」没有选。")
    return problems, repo["html_url"]


def one_line(text: str) -> str:
    """表单里填的文字压成一行，去掉会打乱列表的东西。"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("<", "&lt;").replace(">", "&gt;")


def entry(form: dict[str, str], url: str, number: int) -> str:
    name = one_line(form["项目名"]).replace("[", r"\[").replace("]", r"\]")
    return (
        f"- **[{name}]({url})** · {one_line(form['署名'])}：{one_line(form['玩法简介'])} "
        f"<!-- #{number} -->"
    )


def add_entry(doc: str, section: str, line: str) -> str:
    """把一条加到某一节的末尾；这一节还是占位文字时替换掉它。"""
    lines = doc.split("\n")
    start = lines.index(SECTIONS[section])
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = [item for item in lines[start + 1 : end] if item.strip() and item.strip() != PLACEHOLDER]
    return "\n".join([*lines[: start + 1], "", *body, line, "", *lines[end:]])


def report(number: int, comments: list[dict], text: str) -> None:
    """写检查结果：已经有那条评论就更新它，没有就新建。"""
    body = f"{MARKER}\n{text}"
    mine = [c for c in comments if MARKER in (c.get("body") or "")]
    if mine:
        comment_id = mine[-1]["url"].rsplit("-", 1)[-1]
        subprocess.run(
            ["gh", "api", "--method", "PATCH", f"repos/{REPO}/issues/comments/{comment_id}", "-F", "body=@-"],
            input=body, check=True, text=True, encoding="utf-8", capture_output=True,
        )
    else:
        subprocess.run(
            ["gh", "issue", "comment", str(number), "--repo", REPO, "--body-file", "-"],
            input=body, check=True, text=True, encoding="utf-8",
        )


def label(number: int, flag: str, name: str) -> None:
    gh("issue", "edit", str(number), flag, name)


def problems_text(problems: list[str]) -> str:
    return "\n".join(f"- {problem}" for problem in problems)


def do_check(number: int, issue: dict) -> None:
    problems, url = check(fields(issue["body"]))
    if problems:
        report(
            number, issue["comments"],
            f"自动检查没通过：\n\n{problems_text(problems)}\n\n改好后直接编辑这个 issue（不用重开），会自动再查一遍。",
        )
        label(number, "--add-label", "needs-info")
        print(f"#{number} 没通过：{problems}")
    else:
        report(number, issue["comments"], f"自动检查通过：{url} 能打开、是公开的、已经改名。等维护者看过后收录。")
        if "needs-info" in issue["labels"]:
            label(number, "--remove-label", "needs-info")
        print(f"#{number} 通过")


def do_accept(number: int, issue: dict, dry_run: bool) -> None:
    form = fields(issue["body"])
    problems, url = check(form)
    doc = DOC.read_text(encoding="utf-8")
    if url and url in doc:
        problems.append(f"{url} 已经收录过了。")
    if problems:
        print(f"#{number} 没通过：{problems}")
        if dry_run:
            return
        report(number, issue["comments"], f"收录前的检查没通过：\n\n{problems_text(problems)}")
        label(number, "--remove-label", "accepted")
        return
    line = entry(form, url, number)
    print(line)
    if dry_run:
        return
    DOC.write_text(add_entry(doc, form["内容分级"], line), encoding="utf-8", newline="\n")
    name = one_line(form["项目名"])
    subprocess.run(["git", "add", str(DOC)], check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"docs: 收录玩法仓库 {name}（#{number}）"], check=True)
    subprocess.run(["git", "pull", "-q", "--rebase"], check=True)
    subprocess.run(["git", "push", "-q"], check=True)
    if "needs-info" in issue["labels"]:
        label(number, "--remove-label", "needs-info")
    gh("issue", "comment", str(number), "--body", f"已收录进 [社区玩法仓库]({DOC_URL})，谢谢！")
    gh("issue", "close", str(number), "--reason", "completed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=["check", "accept"])
    parser.add_argument("number", type=int, help="登记 issue 的编号")
    parser.add_argument("--dry-run", action="store_true", help="accept 时只打印要写进文档的一行")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    issue = json.loads(gh("issue", "view", str(args.number), "--json", "body,state,labels,comments"))
    issue["labels"] = [item["name"] for item in issue["labels"]]
    if "fork-showcase" not in issue["labels"] or issue["state"] != "OPEN":
        print(f"#{args.number} 不是开着的登记 issue，跳过")
        return
    if args.action == "check":
        do_check(args.number, issue)
    else:
        do_accept(args.number, issue, args.dry_run)


if __name__ == "__main__":
    main()
