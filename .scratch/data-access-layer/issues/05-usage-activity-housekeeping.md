# 05: 限额、运行记录、清理迁到数据访问层

**What to build:** `UsageLimiter` 和 `ActivityLog` 迁到数据访问层。放行时的扣用量、回合和通话结束时的结算（退还用量、写运行记录），以及回合之外每一次计费服务调用的记录，都不再在事件循环上写库，也不再在循环上等线程拿着的锁。后台每 10 分钟的清理改成分批：停机后积压的那一次，也不会长时间占住写线程。限额照样准确；看板、统计、服务状态的数字不变。

**Blocked by:** 01

**Status:** done

- [x] 两个模块不再自己开连接、不再持有锁。`ActivityLog` 原来那条单独的快照读连接，由层的 `read`（一个读事务）代替。`spread_bucket` 由层注册到每一条连接上。
- [x] 扣用量和放行的「检查加扣减」都在一个写任务里判断。新测试：两个同时到达的回合不会都占到最后一个名额。
- [x] 回合、通话、搭话的结算改成 await。`TurnOutcome.over` 先在循环上给 `recording.calls` 拍快照，再 await 写入，不会丢掉回合结束后才记下的服务调用。c 步给「放行被取消」补的那次结算保留。
- [x] 运行记录仍然单独写，失败只记日志，不影响回合和通话。
- [x] housekeeping 分批删除过期用量、运行记录、服务调用记录和汇总，每批一个写任务。每批的行数实测确定，目标约 50 ms 以内，测量记在实现说明里。
- [x] 看板、统计、服务状态的读取走读连接；后台用户页里读用量和运行记录的地方也一样。
- [x] 受影响的测试通过，CI 绿。

## Comments

### 票 01 交接

- `spread_bucket` 用 `database.function("spread_bucket", 1, _bucket, deterministic=True)` 注册，在 `ActivityLog` 构造时调。它会把读连接全部借出来，所以只在构造时调。
- 看板（`dashboard.summed_up`）和后台价格（`admin_prices_api.described_by`）现在把读 `ActivityLog` 的同步部分整段放在 `run_in_threadpool` 里。迁完后改成直接 await，CPU 的汇总看情况留在线程里。

### 票 02 交接

- 价格目录的查找（`Pricing.find`、`listed`、`catalog_as_of`）都只读内存，不碰库。看板 `summed_up` 和后台价格 `described_by` 留在 `run_in_threadpool` 里的，只剩读 `ActivityLog`，加上纯 CPU 的汇总和定价。
- `refresh_soon` 必须在事件循环上调，现在在两个 handler 的 await 之后调。收掉 `run_in_threadpool` 时别把它挪回线程里。

### 票 03 交接

- 后台用户页（`admin_users_api`）里还放在 `run_in_threadpool` 里的有：`described` 和 `details` 读 `activity.taken_since`、`limiter.user_limits`、`activity.of_user` 的部分，以及 `set_limits`、`take_off_limits` 里的 `limiter.set_user_limits`。迁完改成 await。
- 命令行 `user usage` 还在 `asyncio.run` 里直接用同步的 `UsageLimiter`。

### 票 04 交接

- `/api/conversations/{id}/turns`（`app.text_turn`）改成了 `async def`，`admissions.turn` 暂时放在 `run_in_threadpool` 里。`admissions.turn` 改成 async 后直接 await。
  - 注意取消：handler 在等放行时被取消，放行拿到的用量和运行记录就没人结算了。通话那边（`Calls._admit_and_start`）用 `shield` 加 `never_set_up` 处理了这种情况，文字回合也要照样处理。
- `/api/ice-servers` 也改成了 `async def`，`calls.ice_servers` 整个放在 `run_in_threadpool` 里，因为它读冷却（`Affinities`，06）。
- `Photos.eligibility` 里的 `usage.check` 现在在回合开头那个读任务的事务体里调（`ConversationEngine._read_before_asking`），用的是 `UsageLimiter` 自己的连接。迁完后改成接收同一条读连接的事务体。

### 实现说明（票 05）

**`UsageLimiter(db, limits, clock)`**（`usage.py`）：

- 不再有自己的连接和锁，`close` 删了。建表和迁移经 `prepare(_open)`。
- `take` 是一个写任务 `_take`：检查和扣减在同一个事务里（`BEGIN IMMEDIATE`），所以两个同时到达的回合不会都占到最后一个名额。限额（实时配置）在循环上取好，作为参数传进去，事务体不读循环那边的状态。
- `Use.give_back`、`user_limits`、`set_user_limits`、`last_day`、`forget_old` 都改成 async。新加 `limits_of(user_ids)`：后台用户列表一次读出整页用户的限额，不再一个一个读。
- `check(db, user_id, kind)` 改成接收调用方读任务的连接。`Photos.eligibility` 多收一个 `db`，回合开头的读任务 `_read_before_asking` 把同一条读连接传进去（票 04 的交接）。
- 模块函数 `read_user_limits(db, user_id)` 给还没迁的 `ReachOuts` 在它自己的连接上用（见 07 的交接）。

**`ActivityLog(db, clock)`**（`activity.py`）：

- 两条连接、两把锁都去掉了，`close` 删了。原来单独的快照读连接由层的 `read` 代替：`tally` 在一个读任务里读完、汇总完，汇总的 CPU 活也在读线程上。`spread_bucket` 由 `db.function` 注册到每一条连接上。
- 读的方法改成 async：`since`、`tally`、`billed_models`、`deployer_calls_since`、`of_user`、`taken_since`。`reached_out_since` 改成模块函数 `read_reached_out_since(db, …)`，只有搭话用它。
- **结算改成 await**：`Recording.finish`、`Admission.settle`、`TurnOutcome.over`（连同 `running()`，改成 async 的上下文管理器）、`CallOutcome.settle`、`set_up_failed`，以及 `Admissions.turn`、`call`。写运行记录失败只记日志，不往外抛。
- **快照和迟到的服务调用**：记录交给写线程之前，在循环上同步地给它（通话连同它已经结束的回合）设 `handed_in`，并把 `calls` 拍成元组，作为参数交进写任务。之后再记下的服务调用（比如回合结束后才出来的照片）单独作为一个写任务，在写线程上读 `into.row_id`。写线程先来先做，所以记录那一条一定先写完。记录写失败、回滚时，`row_id` 置回 None，迟到的那些就单独记（`record_id` 为 NULL）。
  - 还没结束的回合不跟着通话一起交出去，等它自己结束时再写，挂在通话的记录下。
- **服务调用**：`ActivityLog.call`、`record` 改成 async，`timed` 和 `_MeteredLLM` 都 await 写入。重试失败的那几次先用 `ended_call` 拍下来，到 finally 里一起记。`Recording.add` 在通话管线里同步调用：记录还没交出去就追加；交出去以后用 `record_soon` 起一个 task 去写，task 由 `ActivityLog.aclose` 在关停时等完。
- **过渡写法**：`daily_task_done`、`achievement_unlocked`、`character_complete` 还是同步方法，经 `prepare` 阻塞地写。原因是 `Happenings` 还在线程里调它们（见 06 的交接）。

**放行被取消**：`Admissions.admitted(admitting, never_began)` 把 c 步的那次结算收成一处，文字回合（`app.text_turn`）和通话（`Calls._admit_and_start`）共用。等放行时被取消，放行照样跑完；放进来的回合或通话交给 `never_began` 结算。这些 task 由 `Admissions.aclose` 在关停时等完，它排在 `calls.aclose` 之后、`database.close` 之前。

**housekeeping**（`housekeeping.py`）：

- `keep_tidy`、`tidy` 改成 async。`in_batches(db, delete, *args)` 每批一个写任务，删不满 `BATCH` 就停。批与批之间，别的写任务可以插进来。
- 删除顺序：过期用量，服务调用，运行记录，再到各汇总表。汇总表是 WITHOUT ROWID 的，按主键用行值 `IN (SELECT … LIMIT ?)` 分批删，主键从 `PRAGMA table_info` 读。查询计划确认走的是主键查找。
- **测量**（本地 NVMe，`secure_delete` 开着）：
  - 数据：按一天积压造，每天 4000 条运行记录、每条 4 次服务调用、4000 条用量；另加 10 天较新的数据。到期的一天：4000 条记录、1.6 万次服务调用，汇总表 1.2–33 万行。
  - 不分批一次删完：231–256 ms，和 grilling 时测的约 220 ms 相当。
  - 每批 500 行：最慢一批 30–39 ms，合计约 450 ms。
  - 每批 1000 行：最慢一批 37–42 ms，合计 340–380 ms。
  - 每批 2000 行：最慢一批 54–74 ms。
  - 取 `BATCH = 1000`。测量脚本不进仓库。

**读取走读连接**：

- 看板（`dashboard.summed_up`）、后台价格（`described_by`）、服务状态（`ServiceStatus.now`，连同 `/api/service-status` 改成 async；它的锁去掉了，都在循环上）、后台用户页（`described`、`details`、`set_limits`、`take_off_limits`）都直接 await，不再用 `run_in_threadpool`。
- 价格目录的 `refresh_soon` 仍然在 handler 里、await 之后调。
- `/api/stats` 不读用量和运行记录，不受影响。

**其他**：

- `ReachOuts` 去掉了 `usage` 参数。
- 命令行的 `user` 子命令改成接收 `Database`，`user usage` 走层。
- `CallOutcome._settle` 里记下通话（`keep`：`keep_call` 和 `happenings.call_ended`）改到线程里跑。原来它在循环上阻塞地等写队列（`prepare`），`happenings` 也在循环上用自己的连接写。

**测试**：

- 新加：
  - `test_rate_limit`：两个同时到达的回合（同一个后端、两个对话）一个 200、一个 429。把检查挪到读连接、和扣减分开时，这个测试会失败。
  - `test_activity_records`：记录正在写入时记下的服务调用，照样挂在它下面。改回原来按 `row_id` 判断时，会丢掉一条。
  - `test_housekeeping`：分批时，别的写任务插在批与批之间。
- 改成 async 的：`test_usage`、`test_admission`、`test_turn_outcome`、`test_call_outcome`、`test_activity_rollups` 等。`conftest` 加了 `activity_in`。`test_live_config.recorded` 改成直接查库，保持同步。
- `test_call_records` 里「放行时被取消」那个测试，原来是拿着 `UsageLimiter._lock` 把放行卡住，现在改成把 `admissions.call` 换成一个等事件的版本。
- 本机跑了 affected-tests（538 个），另外按接口路径找出的 66 个 HTTP 测试文件（705 个）也都通过了。`test_proxy.py::test_tunnels_whose_tls_timed_out_leave_the_pool_room` 在整批里超时过一次，单独跑都过，票 01 也记过它。

**审查后改的**：

- 关停时等完 `record_soon` 和放行被取消后的结算（`ActivityLog.aclose`、`Admissions.aclose`）。
- `keep` 挪到线程里。
- 还没结束的回合不跟通话一起交出去。
- 改名：`service_call` → `ended_call`，`users_limits` → `limits_of`。
- 竞态测试改成同一个后端。

**审查后没改、只记下的判断题**：

- `timed` 的 finally 里逐条 await 写入。task 在这期间被再取消一次（只取消一次时 CancelledError 已经抛过了），剩下几条重试的记录会丢。
- 三个分批删除的事务体（用量、服务调用、运行记录）SQL 形状一样，没抽成一个。
- `record_soon` 和 `record` 开头的「还没交出去就追加」重复。
- `test_usage` 里分批删除的测试把 `BATCH` 设成 1，和 `test_housekeeping` 测的是同一个循环，但它顺带验了 `LIMIT` 那句 SQL。
- `ReachOuts` 现在拿自己的连接读 `usage` 表、`activity_records` 表。这是过渡，07 收掉。

