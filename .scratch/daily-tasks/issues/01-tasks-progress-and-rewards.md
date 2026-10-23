# 01: 三项任务记进度、到账，礼物页显示

**What to build:** 每日任务从头走通。用户和任意角色有回合（打字的和通话里的都算）、送出礼物时，今天的三项任务（一个回合、送一件礼、十个回合）随之前进；做完一项自动加 1 次抽奖，同一项只加一次，不受 `MAX_DRAWS` 约束，北京时间 0 点换新、没做完的作废。礼物页抽奖台正下方出现「每日任务」一节，看得到三项做到哪了。这张票建好两样成就也要用的东西：回合和送礼两处只调用的「发生了什么」入口（规格里叫往来记账），以及幂等的「给用户发奖励」入口。见规格「三项任务」「记进度」「存什么」「发奖励」「`GET /api/gifts` 加两样」里的 `daily_tasks`、「礼物页那一节」。

**Blocked by:** None (can start immediately)

**Status:** done

- [x] 往来记账：回合记下以后（打字回合和通话里的回合走的同一处）、送礼成功以后各调一次；开关关着时什么也不记；出错只记日志，不让回合或送礼失败
- [x] 今日进度：每位用户一行（哪一天、回合数、送礼件数），按注入的时钟取北京时间的日期；不是今天就先清零再加，读的时候不是今天就当作 0
- [x] 到账记录和幂等的发奖励入口：按（用户、键）唯一，同一个键只加一次抽奖；进度写入、到账记录、`gift_balances` 加次数在同一个事务、同一把锁里；加之前先照常补上每天发的那份；没有余额行的先建一行；可以超过 7
- [x] 三项都写成常量：第 1 个回合、第 1 件礼物、累计第 10 个回合时各到账 1 次；之后再说话、再送礼不再加
- [x] `GET /api/gifts` 加 `daily_tasks`：三项的 id、是否做完、进度和目标、给几次抽奖，加上下次换新的时刻；开关关着时为 null
- [x] 两张新表登记进 `USER_DATA`，删除用户时一起删，不进数据包
- [x] 礼物页抽奖台下面一节「每日任务」：标题、做完几项「1/3」、右边「0 点换新」（按制作人时区写）；三行，空圈或勾，第三项没做完时带「3/10」，右边「抽奖 ×1」；做完的字变淡、划掉。怎么显示收进 `lib/` 纯函数；开关关着时礼物页照旧是「礼物暂时没有开放」
- [x] 后端测试（走 HTTP 接口，`FakeClock`）：三项各自做完的时机、只到账一次；几位角色的回合合起来算；通话里的回合算；冷却中送礼算；被拒、失败的回合不算；跨北京时间 0 点作废重来；已有 7 次时做完变成 8 次、第二天每天发的那份不把它压回去；开关关着不记、中途打开从那一刻起算、关了又打开已做完的还在；删历史对话后仍然做完；导入数据包不前进；删除用户后新表不剩行
- [x] 前端测试：那一节的显示函数（打勾、「3/10」、「1/3」）
- [x] 按 `docs/agents/local-acceptance.md` 在本地构建版上用 Playwright 看一眼礼物页那一节（宽屏、手机）

## Comments

### 实现说明（票 01）

- **往来记账**是新模块 `backend/src/eidolon/happenings.py` 的 `Happenings`：`turn_recorded(user_id, character_id)`、`gift_given(user_id, character_id)` 各返回 `Rewarded | None`（到账的抽奖次数、重置卡张数、做完的任务 id）；`daily_tasks(user_id)` 给 `GET /api/gifts` 用，关着时是 None。三项任务是 `DAILY_TASKS` 常量（`one_turn`、`one_gift`、`ten_turns`），按 `Counts`（回合数、送礼件数）判断。今日进度表 `daily_task_progress`（每位用户一行：day、turns、gifts）。
- **发奖励入口**是 `Gifts.reward(user_id, due)`：`due(db, today)` 在 Gifts 的锁和事务里调用，往来记账在里面写进度、返回该发的 `Reward(key, draws, reset_cards=0)`；同一个（用户、键）只发一次，记在 `gift_rewards`（user_id、key、draws、reset_cards、rewarded_at）。时钟只在锁里读一次，跨 0 点的那一刻进度、键和每天发的那份用的是同一天。没开放时抛 `GiftsClosed`。成就要发单份奖励就传 `lambda db, today: [Reward(key, n)]`。
- 每日任务的键是 `daily_task:<北京日期>:<任务 id>`。
- 调用处：`ConversationEngine` 在 `_record` 成功之后调 `turn_recorded`（打字和通话同一处）；送礼在 `gifts_api` 的路由里 `gifts.give` 成功之后调 `gift_given`。没放进 `Gifts.give`，是因为 `Happenings` 依赖 `Gifts`，反过来会成环。两处返回值暂时都丢掉。
- `assembly.py` 把 `Gifts` 挪到 `ConversationEngine` 之前建，`Backend` 多了 `happenings`。
- 出错只记日志；开关在两次检查之间被关掉（`GiftsClosed`）当作没开，不记日志。
- 前端：`lib/dailyTasks.ts` 的 `dailyTasksView`（「1/3」、「3/10」、「抽奖 ×1」、「{时刻} 换新」，换新时刻用 `nextDrawText`），`components/gifts/DailyTasksSection.tsx` 只管画；`GiftState` 加了 `daily_tasks`；新图标 `TaskOpenIcon`（Lucide `Circle`）。`DESIGN.md`「礼物页」加了「每日任务」一条，图标一节也补上了。
- 本地构建版上用 Playwright 看过宽屏和手机（造了 3 个回合的进度）：「1/3」、第一项打勾划掉、「3/10」、「明天 00:00 换新」都对。

审查后的判断题（没改）：
- `turn_recorded`、`gift_given` 的 `character_id` 现在没用到，按规格留给成就。
- `dailyTasksView` 遇到不认识的任务 id 时照样列出、名字用 id，规格没要求；留着是为了后端先加任务时前端不出错。
- 回合里同步拿 `Gifts` 的线程锁，和原来 `_record` 的同步写库一样，没改成放进线程池。
- `test_a_turn_does_the_first_and_gives_a_draw` 和后面几个测试有重叠，作为最短的那条路径留着。
