# 02: 运行记录和看板「每日任务和成就」三格

**What to build:** 管理者在看板上看得到成就发出了多少东西。每解锁一个成就阶段、每集齐一位角色，运行记录里留一条；看板「每日任务」那一小节改名「每日任务和成就」，多出三格：解锁的阶段（个）、发出的抽奖次数、重置卡（张）。这些记录不算进回合数、活跃用户和趋势。见规格「运行记录和看板」。

**Blocked by:** 01

**Status:** done

- [x] 运行记录加一种 `kind`：解锁一个成就阶段；哪个成就第几阶段放进 `result`，角色放进 `character_id`（合起来的为空）；集齐也记一条同一种 `kind`、`result` 标成集齐；沿用 `record_rollups` 现有的列
- [x] 到账成功后写，写失败只记日志；同一阶段只写一条（只在真到账时写）；上线补算不写（补算的路径在票 04，这里留好不写的口子）
- [x] 看板别的小节排除这种记录：回合数、活跃用户、趋势不变（同每日任务那种）
- [x] 看板那一小节标题改「每日任务和成就」，加三格：解锁的阶段、发出的抽奖次数（按阶段序号加起来）、重置卡（集齐的条数）；小节什么时候出现照每日任务现在的规则
- [x] 管理者只看得到合计，看不到某一位用户的成就
- [x] 后端测试：三格的数、集齐的条数、别的小节的数不变（照 `test_affinity_dashboard.py`、`test_activity_records.py`）
- [x] 前端测试：看板那一小节（`dashboard.test.ts`）
- [x] 本地构建版上用 Playwright 看一眼看板那一小节

## Comments

### 票 01 交接

- 解锁的阶段和集齐在 `Happenings._happened` 里已经拿到：`given` 里的奖励按键映射成 `achievements.Unlocked` / `Completed`（`done` 字典），到账以后写运行记录就接在每日任务那段 `daily_task_done` 循环旁边。
- 到账键：`achievement:{成就}:{角色或空}:{第几阶段}`、`achievement_complete:{角色}`；看板「发出的抽奖次数」可按 `Unlocked.draws` 记进 `result`。

### 票 02 做完

- 运行记录 `kind` 是 `achievement`：`result` 写 `{成就}:{第几阶段}`（`activity.stage_unlocked`），集齐写 `complete`（`activity.COMPLETE`）；合起来的成就 `character_id` 为空串。只在 `Happenings._happened` 到账以后写（`_record_achievements`）；票 04 的补算不走这条路，自然不写。
- 看板接口多了 `achievements: {stages, draws, reset_cards}`，和 `daily_tasks` 同进同出：好感度开着，或者范围里有每日任务、成就的记录时才有，否则两个都是 null。
- 审查里的判断题（没改，留给用户定）：
  - 「发出的抽奖次数」按阶段序号加起来（规格这么定），重置卡按集齐的条数算，没有记下当时实际给的 `Unlocked.draws`、`COMPLETE_RESET_CARDS`。以后第 n 阶段不再给 n 次、或者集齐给的不止一张时，看板的数会和实际到账的对不上；到时候要在 `result` 里另记实际给的数。
  - `result` 是编码字符串，看板解析出阶段号再算数（沿用每日任务把哪一项写进 `result` 的做法）。
  - 每日任务、成就阶段、集齐三处写运行记录都是「try，失败只记日志」，没抽成共用函数。
