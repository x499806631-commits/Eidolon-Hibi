# 01: 走通：回合和送礼记成就进度、解锁、到账，接口看得到

**What to build:** 成就从头走通。用户和一位角色有回合（打字的和通话里的都算）、送出礼物时，「和她的回合数」「送她的礼物件数」「来过几天」随之前进，「往来过几位角色」由按角色的进度推出来；过了门槛的阶段自动解锁，第 n 阶段到账 n 次抽奖；她按角色算的四个成就全部解锁时算集齐，另给 1 张重置卡，每位角色一辈子只给一次。回合 `done` 和送礼返回里的「这次到账」带上解锁了哪些阶段、集齐了哪位；她的成就和合起来的成就从接口看得到。这张票还建好前端两处都要用的「每一行写什么」纯函数。见规格「先对照 main」「八个成就（常量）」「记进度：接在往来记账上」「存什么」「到账了什么」「接口」，以及「她的对话页」里每一行的写法。

**Blocked by:** None (can start immediately)

**Status:** done

- [x] 开工先读 main 上每日任务做出的往来记账、发奖励入口、「这次到账」、`GET /api/gifts` 的 `rewarded_at`；和规格对不上的地方记进本票 `## Comments`
- [x] 八个成就的门槛和每阶段的奖励都写成常量（第 n 阶段 n 次抽奖），集齐给 1 张重置卡也是常量；阶段按序号认
- [x] 成就进度表（用户、成就、角色一行累计数，合起来的角色为空；「来过几天」带最后一天）和解锁的阶段表（用户、成就、角色、第几阶段唯一，带解锁时刻）；都登记进 `USER_DATA`，不进数据包
- [x] 往来记账里加成就一份：回合记下了加她的回合数，送礼了加她的件数，两处都在北京时间新的一天时给「来过几天」加一（按注入的时钟）；「往来过几位」由回合数至少 1 的角色数推出来，不另存
- [x] 每次记进度后按当前门槛解锁没解锁过的阶段、到账，再看这位角色是否集齐；进度写入、解锁、到账记录、余额修改在同一个事务里；到账键形如「成就、角色、第几阶段」和「集齐、角色」，同一阶段只到账一次、每位角色重置卡只有一次
- [x] 一次跨过几个阶段时几个都解锁、都到账；门槛调低后下一次相关动作时解锁；调高、删阶段不收回；加了阶段后再集齐不给第二张重置卡
- [x] 开关关着时什么也不记；出错只记日志、不让回合或送礼失败；删历史对话、导入数据包都不经过它
- [x] 「这次到账」加上解锁的阶段（成就、角色、第几阶段、给了几次抽奖）和集齐的角色（给了几张重置卡）；打字回合、通话里回合的 `done` 和送礼返回都带着
- [x] 新增 `GET /api/characters/{id}/achievements`：她的四个成就（累计进度、各阶段门槛和奖励、已解锁阶段的解锁时刻）和集齐那一行；档位只给档位名；开关关着时返回 null
- [x] `GET /api/gifts` 加 `achievements`：合起来的四个，形状同上；开关关着时为 null
- [x] 成就到账照常更新 `rewarded_at`（导航「礼物」上的点照旧亮）
- [x] 前端 `lib/` 纯函数：每一行写什么（名字、阶段点、下一个阶段的进度文字、全部解锁、下一阶段的奖励「抽奖 ×n」），各类进度文字（回合、时长「3 小时 12 分 / 5 小时」、件数、档位「下一个：亲密」、位、天、次），「集齐」一行；名字照原型 `?ac=B`；类型照接口的形状
- [x] 后端测试（走 HTTP 接口，`FakeClock`）：回合和送礼各自解锁的时机、第 n 阶段给 n 次；一次跨几个阶段；集齐给重置卡只一次（monkeypatch 降门槛）；门槛调低、调高；通话里的回合算；来过几天同一天只算一天、跨北京时间 0 点算两天、不要求连续；往来过几位推出来；隐藏的角色照样记；开关关着不记、接口 null；删历史对话不倒退；导入数据包不前进；两个并发回合只到账一次；删除用户后新表不剩行
- [x] 前端测试：行写法函数的各种情况

## Comments

### 拆票时的备注

- 发奖励入口 `Gifts.reward` 的 `Reward` 已经带 `reset_cards`（默认 0），到账时一并加进 `gift_balances`、记进 `gift_rewards`；规格说的「补上加 M 张重置卡」大概只需要接上，先核对。
- 看板三格和运行记录挪到了票 02；本票不写运行记录。
- 行写法的 `lib/` 纯函数放在本票，好让票 05、06 并行时不抢着建同一个模块。

### 对照 main（开工时）

- 往来记账是 `happenings.Happenings`（`turn_recorded`、`gift_given`），发奖励入口是 `Gifts.reward`，`Reward` 已带 `reset_cards`、到账时一并加进 `gift_balances` 并记进 `gift_rewards`，不用补。和规格对得上，没有要改的说法。
- `Gifts.reward` 的回调原来收「今天」，改成收「现在」（`due(db, now)`），好让解锁时刻和到账时刻是同一个；每日任务自己用 `day_of(now)` 划天，行为不变。
- 「这次到账」就是 `happenings.Rewarded`，打字回合、通话里回合的 `done`（`TurnDone.rewarded`）和送礼返回都已经带着，加字段即可。
- 「隐藏的角色照样记」：隐藏只存在浏览器的 localStorage（`lib/listPreferences`），后端不知道谁被隐藏，天然照记，没有另写后端测试。

### 实现说明（票 01）

- 新模块 `backend/src/eidolon/achievements.py`：八个成就的常量（`PER_CHARACTER`、`ALL_CHARACTERS`，`Stage(goal, draws)`，第 n 阶段 n 次）、`COMPLETE_RESET_CARDS = 1`、两张表（`achievement_progress`、`achievement_stages`，合起来的角色存空字符串）、`keep`（记进度、按当前门槛解锁、返回该到账的奖励）、`hers`/`of_all`（读）。档位存到过的最高档位在 `TIERS_REACHED` 里的序号（1 亲近、2 亲密），给浏览器时换成档位名。
- 到账键：`achievement:{成就}:{角色或空}:{第几阶段}`、`achievement_complete:{角色}`。每次记进度都把所有已达到的阶段的键交给 `Gifts.reward`，靠键唯一只到账一次；集齐看的是「现在每个按角色的成就的每个阶段都有解锁行」，不看进度，所以门槛调高不影响、加了阶段后再集齐键已存在不给第二张。
- `Happenings._happened` 在同一个 `due` 回调里先算每日任务、再调 `achievements.keep`，同一把锁、同一个事务；`Rewarded` 加 `achievements`（解锁的阶段）和 `completed`（集齐的角色）。
- 接口：`GET /api/characters/{id}/achievements` 在 `gifts_api.py`，返回 `{achievements: [{id, progress, stages: [{goal, draws, unlocked_at}]}], complete: {completed_at, reset_cards}}`，没有这位角色 404，开关关着 null；`GET /api/gifts` 加 `achievements`（合起来的四个，同样的 `{id, progress, stages}`，关着 null）。通话的进度和门槛单位是秒。
- 判断：集齐那一行的 `completed_at` 是「现在的每个阶段都解锁了」时其中最晚的解锁时刻（删掉的阶段不算）；以后加了阶段、没再集齐时为 null。`reset_cards` 是「集齐（还）会给几张」：第一次集齐时在进度表记一行 `complete`，之后为 0，前端这时不写「重置卡 ×n」。
- 前端：`lib/achievements.ts` 的 `achievementRow`、`completeRow` 和类型（`Achievement`、`HerAchievements`；`UnlockedStage`、`CompletedCharacter` 在 `lib/rewards`），文案在 `copy.achievements`；`Rewarded`（`lib/rewards`）和 `GiftState.achievements`（`lib/gifts`）跟上了接口形状。下一个阶段取第一个没解锁的阶段，不按进度。

### 审查（票 01）

- 已修：集齐那一行重置卡给过以后为 0（规格「每位角色一辈子最多 1 张」）；`completed_at` 不算删掉的阶段；删了和 `test_daily_tasks.py` 重复的两处「这次到账」断言、前端两条不分支的用例；`Rewarded` 里的阶段和集齐改成具名类型；两处 docstring 折行。
- 只报告、没改（判断题）：成就 id 是裸字符串，后端 `Progress.shown`、`_of_all` 和前端 `progressText` 各按 id 分支（Repeated Switches）；`_happened` 里 `done` 混放任务 id、`Unlocked`、`Completed` 再用 `isinstance` 拆；合起来的角色库里是空字符串、出库是 None。等票 03 加通话、抽奖、档位时如果分支继续变多，再考虑把推算和展示方式挂到 `Achievement` 上。
