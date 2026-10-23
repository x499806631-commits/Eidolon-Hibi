# 04: 看板「每日任务」一小节

**What to build:** 管理者在看板「好感度」分组里看到「每日任务」一小节，三格写着「一个回合」「送一件礼」「十个回合」在所选范围里各做完几次，拿来对照活跃用户；原有的回合数、活跃用户和趋势不受影响。见规格「运行记录和看板」。

**Blocked by:** 01

**Status:** done

- [x] 运行记录加一种 `kind`：做完一项每日任务；哪一项放进 `result`，`character_id` 为空；到账成功后写，写失败只记日志
- [x] 沿用 `record_rollups` 现有的列，不加列、不加表、不改汇总的结构；不算进 `user_rollups`、`taken_rollups`；看板别的小节读运行记录时排除它（同 `reach_out`）
- [x] 看板接口和前端：「好感度」分组里加「每日任务」一小节三格；开关开着、或者所选范围里有这种记录时才显示
- [x] 后端测试：三格的次数、按范围；开关关着且没有记录时这一小节为空；回合数、活跃用户和趋势不变（照 `test_affinity_dashboard.py`、`test_activity_records.py`）
- [x] 前端测试：这一小节什么时候显示（照 `dashboard.test.ts`）
- [x] 本地构建版上用 Playwright 看一眼看板这一小节

## Comments

### 票 04 交接

- 运行记录在 `happenings.py` 的 `Happenings._happened` 里写最合适：`Gifts.reward` 返回了这次新发的 `Reward`，用 `done[r.key]` 能拿到任务 id，到账成功之后对每一项写一条。

### 票 04 审查（判断题，没改）

- `activity_records.result` 对每日任务装的是任务 id（规格这么定的），所以 `Recording.result`、`RecordGroup.result` 的类型放宽成 `Result | str`。
- 任务 id 前后端各写了一份：后端 `happenings.DAILY_TASKS`，前端 `lib/dashboard.ts` 的 `DAILY_TASK_IDS`、`DailyTasksSection`，还有 `zh.ts` 看板和礼物页两套名字。成就分支在看板加三格时，照这几处一起改。
- 好感度关着、但范围里有人做完任务时，「好感度」标签页只显示「每日任务」一小节（`dashboardTabs`）。
