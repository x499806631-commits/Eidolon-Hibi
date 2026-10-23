# 实现票

`/implement`、`/implement-spec` 和它们派出的实现子代理都按这里做。这是本仓库对那两个技能的补充，和技能正文冲突时以这里为准。派子代理时，prompt 里指向本文件和要做的票，不复述内容。

## 测试

- 写代码时跑类型检查和单个测试文件。
- 每次提交前跑 `sh scripts/affected-tests.sh <基点>`，基点是这张票开工前的提交。全量 pytest 是 CI 的，本机只在用户要求、或要复现卡住时跑：输出整份写进文件，外面包 `timeout 550`，加 `PYTHONUNBUFFERED=1`，卡住时把调用栈报出来。
- 本地后端开着时，`uv run` 可能因为要同步虚拟环境、`eidolon.exe` 被占而失败：先停后端，或者临时设 `UV_NO_SYNC=1`。
- 测试接缝写在规格的 Testing Decisions 里，已经和用户确认过。`tdd` 要你确认接缝时以它为准，直接开工。
- affected-tests 找的是导入了改动模块的测试，走 HTTP 断言接口完整返回的测试它找不到。改了某个接口的返回形状，就 grep 测试里断言这个返回的地方（比如返回里独有的键 `"max_draws"`），一起跑。
- 重活（affected-tests、`next build`、Playwright 验收、截图对拍）同一时间只有一个 agent 在跑。

## CI 和 draft PR

- `ci.yml` 只在 PR 和 main 上触发。功能分支的第一张票提交后就推到 origin，开 draft PR（`gh pr create --draft`）。用户说「先不开 PR」指的是正式 PR，draft 照开。
- 之后每张票（连同审查后的修复）推一次。推完不等 CI，接着做下一张。
- 下一次推送之前，先用 `gh run list --branch <分支> --limit 1` 看上一个 run：还在跑就等它跑完（新推送会取消 PR 上正在跑的 run，`cancel-in-progress`），红了用 `gh run view <run> --log-failed` 看失败，单独提交修复，提交说明写明修的是哪张票，然后再推这一张。
- 一波的最后一张推送后等 CI 跑完，绿了再列人工验证清单。
- 转正式 PR（`gh pr ready`）和合并，都要用户当次说。

## 排票

- 一张票派一个实现子代理，它从全新的上下文读票开工，代替手动 `/clear`。
- 默认串行：子代理不带 worktree，直接在集成分支上做，一次一个。本机建 worktree 常因符号链接权限失败（`.claude/skills/*` 是链接），并行省下的时间抵不过这个风险。
- 用户在场、frontier 上有两张以上能并行的票、而且用户要并行时，才开 worktree：prompt 里写明基点提交，让子代理先 `git log -1` 核对，不对就对齐；删 worktree 前，用 PowerShell 按绝对路径拆掉里面的 `frontend/node_modules` 联接和其他链接，确认主仓库的 `frontend/node_modules/next` 还在，再删。
- 默认每个会话只做一波：做完列出人工验证清单就停，下一波由用户新开会话。用户说无人值守、一路做完时，按依赖一路做下去，人工验证项攒进最后的报告。

## 每张票的收尾

1. 验收项逐条做到。能用 Playwright 或直接调接口验的自己验（`docs/agents/local-acceptance.md`），只把机器验不了的留给用户。
2. 调 `code-review`，固定点取这一波开始前的提交。两份报告都回来、汇总以后再动手：建议修的直接修，判断题和范围外的只报告，写进票的 `## Comments`。
3. 照 `docs/agents/issue-tracker.md` 关票。
4. 提交，推送。上一个 run 的结果在推送前看，这一波的最后一张推完等 CI（见「CI 和 draft PR」）。
