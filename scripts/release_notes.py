"""让 LLM 写一个版本的更新说明，.github/workflows/release.yml 调用它（见 docs/deploy.md「发版本」）。

材料是上一个版本标签到这次提交之间 main 的第一父链：合进来的 PR 取标题和描述，
直接提交到 main 的取提交信息。没有上一个版本时是首个版本，只给标题，让 LLM 写功能总览。

    python3 scripts/release_notes.py --version v0.2.0 --prev v0.1.0 --target <提交> --out notes.md
    python3 scripts/release_notes.py --version v0.1.0 --target HEAD --dry-run   # 只打印提示词

LLM 走 OpenAI 兼容的 /chat/completions：RELEASE_LLM_BASE_URL（如 https://api.openai.com/v1）、
RELEASE_LLM_API_KEY、RELEASE_LLM_MODEL。PR 用 gh 查，CI 上要设 GH_TOKEN。只用标准库。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "Sallyn0225/eidolon-sc")
IMAGES = ("ghcr.io/sallyn0225/eidolon-sc-backend", "ghcr.io/sallyn0225/eidolon-sc-frontend")

# 单个 PR 描述和整份材料的上限；材料超了就退回只给标题
BODY_LIMIT = 4000
MATERIAL_LIMIT = 150_000

PRODUCT = (
    "Eidolon-SC 是和《偶像大师 闪耀色彩》的偶像实时对话的 AI 分身：用户以 283 事务所制作人的身份"
    "和偶像打字聊天或打电话，她用日语原声回答、附中文字幕，能回忆原作剧情，记得用户说过的事。"
    "项目开源，可以自己部署到服务器上，任何人用 Google 或 GitHub 登录使用。"
)

# 版式：小节标题配 emoji、要点以加粗的短名称开头，方便扫读
FORMAT_RULES = """\
- 输出 Markdown 正文。不要写一级标题或版本号标题（Release 页面自己会显示），不要用代码块包住全文。
- 每条要点写成「- **短名称**：一句话说明」，短名称是几个字的功能名，方便扫读；说明说清改了什么。
- 要点之内不再嵌套列表。同一个功能的几处改动合成一条，不要拆成好几条。
- 正文里不用 emoji，emoji 只出现在小节标题前。"""

# 说法：摘自 .claude/skills/humanizer-zh 里适用于更新说明的几条
VOICE_RULES = """\
- 用简体中文，平实地陈述做了什么。不要开场白（「本次更新带来了……」「以下是……」），不要结束语和祝愿（「敬请期待」「欢迎体验」）。
- 不要宣传腔和拔高：不写「全新」「全面提升」「更流畅的体验」「标志着」「彰显」「赋能」「无缝」「极致」这类词，不在句尾补「让……更加……」式的赞美；不写「不只是……更是……」式的对比。
- 少用「进行＋动词」「对……进行优化」，直接说动作，如「调快了」「改成」「去掉了」。
- 只写材料里有的内容，不要编造数字、原因和效果。材料里的条件和限定要保留：「可选」「默认关闭」「要在后台打开」「只在通话时」不能丢；计划、未完成的部分不能写成已经上线。
- 讲使用者能感受到的变化，不讲实现细节、文件名、函数名和表名。
- 材料里的术语照用（如「好感度」「重置卡」「看板」「制作人」），不要自己换说法。
- 中文用全角标点，引用界面上的名字用「」。"""

UPDATE_PROMPT = f"""\
你在给开源项目 Eidolon-SC 写一个版本的 GitHub Release 更新说明，读者是使用者和自己部署的人。
{PRODUCT}

下面是从上一个版本到这个版本合进 main 的全部改动。第一行用一句话概括这一版主要做了什么（只陈述，不评价），
然后按下面的小节写，没有内容的小节整节省略：

### ✨ 新功能
### 🛠️ 改进
### 🐛 修复
### ⚠️ 升级注意
只写自己部署的人升级时要知道或要动手的事：数据库结构有变（升级前先备份）、新增或改名的环境变量、要手动执行的命令（比如重启 Caddy、跑同步）、默认行为的变化。没有就省略这一节。
### 📦 依赖
依赖升级合成一两条，只点出值得一提的（比如带安全修复的）。

版式：
{FORMAT_RULES}
- 小节标题照上面给的写，包括前面的 emoji，每一版都一样。
- 来自 PR 的条目末尾写上 PR 号，如「（#123）」；一条合并了几个 PR 时都写上。直接提交没有 PR 号，不写。

说法：
{VOICE_RULES}
- 只改测试、文档、开发流程、CI，对使用者没有影响的改动，不写。"""

FIRST_PROMPT = f"""\
你在给开源项目 Eidolon-SC 写第一个正式版本的 GitHub Release 说明，读者是第一次了解这个项目的使用者和想自己部署的人。
{PRODUCT}

下面是到这个版本为止合进 main 的全部 PR 和直接提交的标题。这是首个版本，不要写成逐条的更新记录，也不写修复和依赖升级。
第一段用一两句话说明这是第一个正式版本、项目是做什么的；然后按功能领域写一份「这个版本有什么」的总览。
领域自己归类，五六个为宜（如语音通话、文字对话、记忆与剧情、好感度与礼物、自己部署与后台），
每个领域一个 `### ` 小节，标题前配一个贴切的 emoji，下面三到六条要点。

版式：
{FORMAT_RULES}
- 不写 PR 号。

说法：
{VOICE_RULES}"""


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def merged_prs() -> dict[str, dict]:
    """合并提交的哈希 → PR（number、title、body、作者是不是机器人）。"""
    out = run(
        "gh", "pr", "list", "--repo", REPO, "--state", "merged", "--base", "main",
        "--limit", "1000", "--json", "number,title,body,author,mergeCommit",
    )
    return {pr["mergeCommit"]["oid"]: pr for pr in json.loads(out) if pr.get("mergeCommit")}


def first_parent_commits(prev: str | None, target: str) -> list[tuple[str, str, str]]:
    """(哈希, 标题, 正文)，从旧到新。"""
    span = f"{prev}..{target}" if prev else target
    out = run("git", "log", "--first-parent", "--reverse", "--format=%H%x1f%s%x1f%b%x1e", span)
    commits = []
    for record in out.split("\x1e"):
        if record.strip():
            sha, subject, body = record.strip("\n").split("\x1f")
            commits.append((sha, subject, body.strip()))
    return commits


def tidy_body(body: str) -> str:
    """去掉 PR 模板里的注释和 Evidence 一节（截图、测试结果），截到上限。"""
    body = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    body = re.sub(r"^## Evidence\b.*?(?=^## |\Z)", "", body, flags=re.S | re.M)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(body) > BODY_LIMIT:
        body = body[:BODY_LIMIT] + "\n……（后面略）"
    return body


def material(prev: str | None, target: str, with_bodies: bool) -> str:
    prs = merged_prs()
    entries = []
    for sha, subject, body in first_parent_commits(prev, target):
        pr = prs.get(sha)
        if pr:
            head = f"PR #{pr['number']}：{pr['title']}"
            # Dependabot 的描述是一大段更新日志，标题就够了
            text = "" if pr["author"].get("is_bot") else tidy_body(pr["body"])
        else:
            head = f"直接提交 {sha[:7]}：{subject}"
            text = body
        entries.append(f"## {head}\n\n{text}".strip() if with_bodies and text else f"## {head}")
    return "\n\n".join(entries)


def build_prompt(prev: str | None, target: str) -> tuple[str, str]:
    if prev is None:
        return FIRST_PROMPT, material(None, target, with_bodies=False)
    text = material(prev, target, with_bodies=True)
    if len(text) > MATERIAL_LIMIT:
        text = material(prev, target, with_bodies=False)
    return UPDATE_PROMPT, text


def chat(system: str, user: str) -> str:
    base = os.environ["RELEASE_LLM_BASE_URL"].rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    payload = json.dumps({
        "model": os.environ["RELEASE_LLM_MODEL"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    headers = {
        "Authorization": f"Bearer {os.environ['RELEASE_LLM_API_KEY']}",
        "Content-Type": "application/json",
        # 有的服务商挡 Python 默认的 User-Agent
        "User-Agent": "eidolon-sc-release-notes",
    }
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(request, timeout=600) as response:
                reply = json.load(response)
            return reply["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:1000]
            retryable = error.code in (408, 429) or error.code >= 500
            if not retryable or attempt == 2:
                sys.exit(f"LLM 返回 {error.code}：{detail}")
            print(f"LLM 返回 {error.code}，重试", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                sys.exit(f"连不上 LLM：{error}")
            print(f"连不上 LLM（{error}），重试", file=sys.stderr)
        time.sleep(10 * (attempt + 1))
    raise AssertionError


def clean(notes: str) -> str:
    """去掉推理模型的 <think> 段和包住全文的代码块。"""
    notes = re.sub(r"<think>.*?</think>", "", notes, flags=re.S).strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\n(.*)\n```", notes, flags=re.S)
    return (fenced.group(1) if fenced else notes).strip()


def footer(version: str, prev: str | None) -> str:
    tag = version.removeprefix("v")
    if prev:
        changes = f"**完整变更**：https://github.com/{REPO}/compare/{prev}...{version}"
    else:
        changes = f"**全部提交**：https://github.com/{REPO}/commits/{version}"
    images = "、".join(f"`{image}:{tag}`" for image in IMAGES)
    return f"---\n\n{changes}\n\n**镜像**：{images}（`.env` 里设 `EIDOLON_IMAGE_TAG={tag}` 固定在这个版本）"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--version", required=True, help="这次的版本，如 v0.2.0")
    parser.add_argument("--prev", default="", help="上一个版本的标签；不填是首个版本")
    parser.add_argument("--target", default="HEAD", help="这次发布的提交")
    parser.add_argument("--out", help="写到这个文件；不填打印出来")
    parser.add_argument("--dry-run", action="store_true", help="只打印提示词，不调 LLM")
    args = parser.parse_args()
    prev = args.prev or None
    # Windows 控制台默认 GBK，打不出日文假名
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    system, user = build_prompt(prev, args.target)
    if args.dry_run:
        print(f"{system}\n\n========\n\n{user}")
        print(f"\n（材料 {len(user)} 字）", file=sys.stderr)
        return

    notes = clean(chat(system, user))
    if not notes:
        sys.exit("LLM 没有写出内容")
    notes = f"{notes}\n\n{footer(args.version, prev)}\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as file:
            file.write(notes)
    else:
        sys.stdout.write(notes)


if __name__ == "__main__":
    main()
