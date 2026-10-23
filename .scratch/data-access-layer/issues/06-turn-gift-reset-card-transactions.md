# 06: 记录回合、送礼、用重置卡各合成一个事务

**What to build:** `Affinities`、`Gifts`、`Happenings`（连同成就）迁到数据访问层，几处跨模块的写入各合成一个事务：
- **记录回合**：消息、照片、对话事件、好感度变化、每日任务和成就；
- **送礼**；
- **用重置卡**；
- **通话结束**：记下通话，再结算每日任务和成就。

用户发的一条消息、送的一份礼物，要么完整记下，要么什么都没留下。记录回合不再在事件循环上写库。一个回合让她进入冷却时，正在进行的通话照样马上被她挂断。

**Blocked by:** 04, 05

**Status:** done

- [x] 三个模块不再自己开连接、不再持有锁；`Happenings` 不再借 `Gifts` 的连接写。
- [x] 上面四处各是一个写任务、一个事务：一个事务体调用别的模块的事务体，把同一条连接传过去。原来 `Affinities` 拿着锁去回调 `Store.add_turn`、两次提交的做法去掉。
- [x] 送礼、用重置卡的补偿写入删掉。
- [x] 删掉 `on_cooldown`：写入返回「这一步让她进入了冷却」和冷却到什么时候，由记录回合的一方在 await 之后、在循环上通知通话模块。通话模块读到的「正在进行的是不是这一回合」和现在一样。
- [x] 运行记录（`daily_task_done`、`achievement_unlocked` 等）仍然在事务之外单独写，失败只记日志。
- [x] 礼物余额、成就的「检查加写入」都在写任务里判断。
- [x] 新测试：记录回合、送礼、用重置卡在中途失败时（比如成就结算抛异常），什么都没留下；冷却照样挂断进行中的通话，结束原因和现在一样。
- [x] 受影响的测试通过，CI 绿。

## Comments

### 票 01 交接

- `LiveConfig.on_change` 的监听器已经是 async（`Callable[[], Awaitable[None]]`）。`assembly` 里暂时是 `config.on_change(lambda: asyncio.to_thread(gifts.notice))`，`Gifts` 迁完后把 `notice` 改成 async，直接 `config.on_change(gifts.notice)`，那句注释一起删。

### 票 03 交接

- `/api/admin/gifts` 的 `gifts.last_grant` 在 `run_in_threadpool` 里。`/api/stats` 的 `affinities.tier`、`happenings.tally` 在 `summed_up` 里，也在线程池里跑。

### 票 04 交接

- `Store.add_turn`、`add_gift`、`keep_call` 还是同步方法：SQL 已经搬进模块级事务体 `_add_turn`、`_add_gift`、`_keep_call`，经 `Database.prepare` 阻塞地交给写线程。合并事务时，直接在新的写任务里调这三个事务体，把同一条连接传进去；然后删掉这三个同步方法，`prepare` 文档里那半句也一起删。
- 回合开头的读取已经收成一个读任务 `ConversationEngine._read_before_asking`（回合和搭话共用）。里面的 `self._affinity(conversation)` 现在调的是 `Affinities.status`，用的还是它自己的连接。迁完后改成接收同一条读连接的事务体。
- `engine._reply` 里还有两处 `to_thread`：`_cools`（`Affinities.cools`）和 `happenings.turn_recorded`。
- `/api/stats` 的 `summed_up` 里只剩 `affinities.tier` 和 `happenings.tally` 还在线程池里读库。`Store` 那三项已经在 handler 里 await 了。
- `/api/ice-servers` 整个 `calls.ice_servers` 放在 `run_in_threadpool` 里，是因为 `refuse_while_cooling` 要读冷却。
- 注意：`add_turn`（`engine._record`）和 `keep_call`（`CallOutcome._settle`）现在是在事件循环上经 `prepare` 等写队列。层的检查只看交出连接的线程，查不到这种等待，所以这一张必须把它们改成 await。

### 票 05 交接

- `ActivityLog.daily_task_done`、`achievement_unlocked`、`character_complete` 还是同步方法，经 `Database.prepare` 阻塞地写，因为 `Happenings` 在线程里调它们。`Happenings` 改成 async 后，把它们改成 async（照 `Recording.finish` 走 `ActivityLog._write`，失败只记日志），删掉 `_written_now`。它们仍然在事务之外单独写。
- `Admissions.turn`、`call` 里 `refuse_while_cooling` 放在 `asyncio.to_thread` 里，因为它读冷却。`Affinities` 迁完后改成 await，`/api/ice-servers` 的 `run_in_threadpool` 也一起收掉。
- `CallOutcome._settle` 里的 `keep`（`keep_call` 加 `happenings.call_ended`）现在用 `asyncio.to_thread` 跑。合成一个写任务以后，把 `KeepCall` 改成 async，直接 await，去掉 `to_thread` 和那句注释。

### 实现说明（票 06）

**`Affinities(db, clock, is_on)`**（`affinity.py`）：

- 连接、锁、`close`、`on_cooldown` 都去掉了。建表经 `prepare(_open)`。
- 改变好感度的三个方法改成事务体，接收调用方写任务的连接，只返回结果，不调回调：
  - `change(db, …)` 返回 `(Change | None, Status)`。「这一步让她进入了冷却」就是 `Change.cooldown`，冷却到什么时候在 `Status.cooldown_until`。
  - `receive_gift(db, …)` 返回 `(Change | None, Status)`；`reset(db, …)` 返回带回的角色。
- 读的方法改成 async：`status`、`cooldown_until`、`cools`。`tier` 换成批量的 `tiers(user_id, character_ids)`，`/api/stats` 一次读完所有偶像。
- `read_status(db, …)` 是给别的读任务用的事务体（回合开头的 `_read_before_asking`、礼物页 `Gifts._state`），**不再看开关**：开关由调用方在循环上看（新加的 `Affinities.on`），再决定读不读。

**`Gifts(db, clock, is_open, affinities, characters, catalog, random)`**（`gifts.py`）：

- 不再收 `store`；连接、锁、`close` 去掉了。两处补偿写入都删了。
- `notice` 改成 async，先读，开关变了才交写任务。`assembly` 里改成 `config.on_change(gifts.notice)`，那句注释删了。
- `state` 先读：今天的抽奖次数已经给过，就只读不写；该给了，再交一个写任务，在里面给次数，再读一遍。
- `draw(db, …)`、`give(db, …)`、`reward(db, user_id, due)`、`reward_everyone(db, due)` 是事务体。
  - `give` 在同一个事务里扣礼物、`receive_gift`、`store.add_gift`。
  - `reward` 不再判断开关，也不再抛 `GiftsClosed`：由调用方在循环上判断，避免回合在事务中途因为开关被回滚。
  - `due(db, now)` 的形状保持不变。
- `use_reset_card` 是一个写任务：扣卡、`affinities.reset`、没人可带回就抛 `NoOneToReset`，三步在一个事务里，失败就整个回滚。`grant_reset_cards`、`last_grant` 改成 async。
- 新加两个守卫，都在写任务之前跑：
  - `refuse_unless_open()`：先 notice，再看开关，关着就抛 `GiftsClosed`。四处调用（送礼、抽奖、用卡、发卡）共用它。
  - `refuse_unknown(gift_id, character_id)`：没有这个角色或这件礼物就抛。

**`Happenings(db, clock, is_open, gifts, activity)`**（`happenings.py`）：

- 自己的连接去掉了，不再借 `Gifts` 的连接写。上线时的补发（`_launch`）在构造时经 `prepare(gifts.reward_everyone, due)` 跑。
- `turn_recorded(db, user_id, character_id, tier, open)` 是事务体，引擎记录回合时在同一个事务里调它。开关 `open` 由引擎在循环上看（新加的 `Happenings.open`）后传进来。
- 送礼（`give`）、抽奖（`draw`）、通话结束（`call_ended`）各是一个写任务，在这里组合：
  - 送礼：`gifts.give` 加任务和成就；
  - 抽奖：`gifts.draw` 加成就；
  - 通话结束：`store.keep_call` 加成就。
- `gifts_api` 改调 `happenings.give` / `draw`。`call_ended` 的签名改成 `(conversation_id, user_id, character_id, connected_at, seconds)`，也就是 `Calls` 的 `KeepCall`。
- `write_records(user_id, rewarded)` 在事务提交之后写运行记录，每条一个写任务，失败只记日志。
  - `ActivityLog.daily_task_done`、`achievement_unlocked`、`character_complete` 改成 async，经 `Recording.finish` 走 `_write`。`_written_now` 删了。
- 读的方法改成 async：`daily_tasks`、`hers`、`of_all`、`tally`。

**记录回合**（`engine.py`）：

- `_record(db, …)` 是一个事务体：好感度 `change`、`store.add_turn`（消息、照片、事件、`answered`）、`happenings.turn_recorded`。原来 `Affinities` 拿着锁回调 `Store.add_turn`、提交两次的做法去掉了。
- `Store.add_turn`、`add_gift`、`keep_call` 这三个同步方法删了，事务体改名成模块函数 `store.add_turn`、`add_gift`、`keep_call`。`Database.prepare` 文档里那半句一起删了。
- **冷却**：新加 `engine.on_cooled(listener)`，`assembly` 里接的是 `calls.cooled`。写入 await 完以后，`Change.cooldown` 为真，就在循环上告诉监听器。
  - 通知是在跑这一回合的 task 的 context 里发的，所以 `_taking_turn` 和原来一样。
- **取消**（审查后加的）：
  - 写入和写入之后要报告的事放在一个 task 里，回合 `asyncio.shield` 着等它。写入之后要报告的是三件：通知冷却、告诉照片模块回复已经记下、写运行记录。
  - 这样回合在写入期间被取消（比如用户关了页面），写进去的回合照样通知冷却、照样保留照片，不会丢。
  - 这个 task 在创建时复制回合的 context，所以 `_taking_turn` 不变。
  - 照片交给记录以后（`_Photo.handed_in`，原来叫 `recorded`），由这个 task 报告结局：写失败就 `dropped`，写成了就 `recorded`。回合的 `finally` 只在照片还没交出去时才 drop。
- `_cools` 改成 await `Affinities.cools`；`happenings.turn_recorded` 外面的 `to_thread` 删了。
- 回合开头的读任务里，好感度改走 `read_status(db, …)`，用的是同一条读连接（票 04 的交接）。开关作为参数 `affinity_on` 传进去。

**其他调用方**：

- `Admissions.refuse_while_cooling` 改成 async，`cooldown_until` 收的是 async 函数，`to_thread` 删了（票 05 的交接）。
- `Calls.ice_servers` 改成 async，`/api/ice-servers` 的 `run_in_threadpool` 删了。
- `KeepCall` 改成返回 Awaitable，`CallOutcome._settle` 直接 await，`to_thread` 和那句注释删了。
- `/api/characters/{id}/affinity` 和礼物的几个 handler 改成 async。
- `/api/stats` 先 await `tiers` 和 `tally`，再进线程算。线程里只剩对所有记录的 CPU 汇总，不再碰库。
- `ReachOuts._tier`：先在循环那边看 `affinities.on`，再在 `ReachOuts` 自己的连接上调 `read_status(self._db, …)`，算过渡写法（见 07 的交接）。

**判断题**：

- 抽奖也合成了一个事务，虽然票里没要求。好处是全有或全无，口径和其他几处一致。代价是成就结算失败时抽奖也一起失败，原来是只记日志。
- 通话结束合成一个事务以后，成就结算失败时，这通电话也不会记进统计（只记日志）。`except` 的注释改了。
- 冷却挂断没有另写新测试。现有的 `test_hang_up_from_elsewhere.py::test_a_cooldown_begun_elsewhere_has_her_hang_up_an_idle_call` 是从别处挂断，`test_she_hangs_up.py::test_the_turn_that_cools_her_has_her_hang_up_once_she_has_said_it` 是这一回合的通话说完再挂，两条都走真实的层，结束原因的断言也和现在一样。
- 中途失败用 SQLite 触发器来造（`test_all_or_nothing.py`）：往 `daily_task_progress` 插入时失败，或改 `affinities` 时失败。这样不用 monkeypatch 代码。

**测试**：

- affected-tests 505 个通过。另外按接口路径找出的 45 个 HTTP 测试文件，加上新测试，共 413 个也通过了。
- 回合在写入期间被取消这条路径，经 HTTP 接缝很难造出来，没有写测试。

**审查后改的**：

- 开关不再在事务体里读：ADR-0028「事务体里不读事件循环那边的状态」。
  - 改成在循环上看 `Affinities.on`、`Happenings.open`，作为参数传进事务体。
  - `Gifts.reward` 不再看开关；`_notice` 和 `_noticed` 收开关作参数。
- 回合在写入期间被取消时，写入之后的事照样做完（见上「取消」）。
- `refuse_unless_open` 收掉四处重复的守卫；`refuse_unless_given` 改名 `refuse_unknown`。
- 改了几处过时的文档和注释：`gifts`、`happenings` 的模块文档，`CallOutcome._settle` 的 except 注释，`Calls.cooled` 的注释。

**审查后没改、只记下的判断题**：

- 事务体里还调 `self._clock()`。时钟不是循环那边的状态，所以留着。
- `gifts._due` 和 `_give` 开头的「今天给过没有」判断重复。
- `Gifts._state(…, giving)` 用返回 None 表示「该给次数了，要写」。
- `/api/stats` 的 handler 和 `summed_up` 各调一次 `in_idol_order`。`gifts_on` 改成看 `tiers is not None`，和好感度那一栏取自同一次判断。
- `Happenings` 负责组合送礼、抽奖、通话结束，用重置卡留在 `Gifts`，因为它不算进任务和成就。所以 `gifts_api` 一半调 `happenings`，一半调 `gifts`。
- 她结束对话（`on_ended`）的通知还在回合本身的 task 里、写入之后。回合在写入之后、读 `ended` 之前被取消时，通知会丢。这和改动之前一样，没有变差，这一张没动它。
