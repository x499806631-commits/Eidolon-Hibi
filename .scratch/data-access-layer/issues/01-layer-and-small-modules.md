# 01: 数据访问层，先接上公告、赞赏、价格、备份、实时配置

**What to build:** 把 spec 里的数据访问层加进来，并接进后端的启动和关停：
- 一条写连接，放在专用线程上，写入按顺序排队；
- 4 条 `query_only` 读连接，从默认线程池里借用；
- 在事件循环上拿连接的检查：测试里抛异常，线上记 error 并照常执行；
- 写任务排队或执行、读池等待，超过 200 ms 记 warning。

先让最小的五个模块走通整条路径：`Announcements`、`Sponsor`、`Prices`、`Backups` 的运行记录、`LiveConfig`。管理者在后台发公告、上传收款码、改价格、点「立即备份」、保存或还原配置时，读写都经过这一层，事件循环上不碰库。行为和现在一样。

**Blocked by:** None (can start immediately)

**Status:** done

- [x] 层对外只有 async 的 `read`、`write`、`close`（`checkpoint` 留给 03）。`write` 在写线程上用一个事务跑事务体：正常返回就提交，抛异常就回滚，异常交回给 `await` 的一方。
- [x] `read` 在一条读连接上开一个读事务跑事务体，同一次 `read` 读到的是同一份快照；长写入进行中照样能读。
- [x] 任务一旦交给写线程就一定执行完：`await` 的一方被取消，写入照样提交。
- [x] `close` 先跑完已交的写任务，之后再交的被拒；`Backend.aclose` 经由它关掉层的连接。
- [x] 在事件循环所在的线程上拿连接：测试模式抛异常，线上模式记 error、照常执行。`conftest` 默认是测试模式。
- [x] 写任务排队等待或执行超过 200 ms、读池等待超过 200 ms，各记一条 warning，带任务名（事务体的限定名）和当时队列的长度。
- [x] 各模块要用的自定义 SQL 函数可以由层注册到每一条连接上（这张票先提供这个能力，`spread_bucket` 由 05 用上）。
- [x] 建表和迁移作为写任务，按现在的构造顺序执行。
- [x] 五个模块不再自己开连接、不再持有 `threading.Lock`。碰库的公开方法改成 async；调用它们的 handler、监听器（`LiveConfig` 改了配置后的 `gifts.notice` 等）改成 await，碰库的同步 handler 改成 `async def`。
- [x] 层自己的规则（顺序、回滚、取消、关停、检查、日志）有层自己的测试；五个模块现有的测试改成 async 后照样通过。
- [x] 受影响的测试通过，CI 绿。

## Comments

### 实现说明（票 01）

**层**：`backend/src/eidolon/data_access.py` 的 `Database(path, readers=4)`。

- 对外的 async 方法：`read`、`write`、`close`。
- 写：一条连接，放在专用线程 `eidolon-writer` 上，用 `SimpleQueue` 先来先做。每个任务先 `BEGIN IMMEDIATE`，再 `with conn:` 跑事务体。审查时补上了 `BEGIN IMMEDIATE`：Python 默认要到第一条写语句才开事务，事务体里先读后写的判断就不原子了，后面「判断一律在写任务里做」的票都靠这一点。
  - 结果经 `call_soon_threadsafe` 交回 `await` 的一方。等的一方被取消，任务照样跑完。
- 读：4 条 `query_only` 连接，在默认线程池里借。每次 `BEGIN` 开读事务，最后回滚。读也用 `shield`，取消等待不撤回读任务，这样 `close` 才能等它们。
- `close`：在锁里置关闭、放停止标记，之后再交的 `write`/`read`/`prepare` 抛 `Closed`。然后等写线程把队列跑完、等进行中的读结束，最后关所有连接。
  - 审查时修了一处：读和关停赛跑时，读可能一直等不到连接。窗口太窄，不加内部钩子造不出来，所以没有专门的测试。
- 事件循环检查：层交出连接前（写线程、读线程都在这一处）看当前线程有没有在跑事件循环。`ON_THE_LOOP_RAISES` 为真时抛 `OnTheEventLoop`，否则记 error、照常执行。`conftest` 导入时设成真。测试用一个「在调用方线程上立刻执行」的默认 executor 来触发它。
- 慢任务日志：`SLOW = 0.2` 秒。
  - 写任务排队等待超时记「write task X waited … ms in the queue, N waiting behind it」，执行超时记「write task X took … ms, N waiting behind it」。
  - 读等连接超时记「read X waited … ms for a connection, N waiting then」。
  - X 是事务体的 `__qualname__`。

**判断题（超出票面「只有 async 三个方法」的两个同步方法）**：

- `prepare(body)`：交一个写任务并阻塞等它做完。`assembly.build` 是同步的，`LiveConfig` 还要在构造时拿到读出的覆盖项，所以建表和迁移用它，按原来的构造顺序排进写队列。测试里 `build` 在事件循环线程上调，会短暂挡住循环；线上 `build` 在 uvicorn 起循环之前调。它本身没有检查，只在构造时用。
- `function(name, arity, implementation)`：在写连接上（经 `prepare`）和每条读连接上注册 SQL 函数，供 05 的 `spread_bucket` 用。注册时把读连接全部借出来，所以也只在构造时调。

**五个模块**：都不再自己开连接、不再持锁，SQL 搬进模块级的私有同步事务体，原样不改。

- `Announcements(db, clock)`：所有方法改成 async。写完再读的那次 `get` 放进同一个写事务。
- `Sponsor(db, clock)`：方法改成 async。`upload` 里 Pillow 转码放 `asyncio.to_thread`，handler 不再自己包 `run_in_threadpool`。
- `Prices(db, clock, catalog)`：`overrides`、`pricing`、`save`、`restore` 改成 async。`describe` 挪到 `Pricing.describe(used)`，因为它只要覆盖价和目录，不用再碰 `Prices` 的库。
- `Backups(db, clock, transport=)`：`run`、`begin`、`status`、`start`、`records` 改成 async；`close` 去掉；`aclose` 只取消后台的备份。`VACUUM INTO` 照旧另开连接，从 `db.path` 拿路径。
  - `abandon_unfinished` 改成模块函数 `abandon_unfinished(path, clock)`，用自己的连接，给 `restore` 换库文件时用（spec：只有 restore 用它自己的连接）。
  - 命令行 `eidolon backup` 用 `asyncio.run` 里的 `Database`。
- `LiveConfig(..., db=, ...)`：构造时用 `prepare(_open)` 建表、删旧项、读覆盖项。`_write`、`_delete`、`_changed`、`_judge_changed` 改成 async。`on_change` 的监听器改成 `Callable[[], Awaitable[None]]`，逐个 await。`ServiceStatus._changed` 改成 async。
- `Backend` 多了 `database` 字段。`Database.close` 是第一个加进 `_closers` 的，所以最后关。

**还碰着没迁模块的 handler（过渡写法，后面的票收掉）**：

- 看板 `/api/admin/dashboard`、后台价格 `/api/admin/prices*`、`/api/account` 改成 `async def`：先 `await` 已迁的模块（`prices.pricing()`、`sponsor.state()`），剩下碰 `ActivityLog`、`Catalog`、`Store`、`ReachOuts` 的同步部分整段放 `run_in_threadpool`。这几段原来就在线程池里跑，事件循环上没有多出碰库的地方。
- `assembly` 里 `config.on_change(lambda: asyncio.to_thread(gifts.notice))`：`Gifts` 还在用自己的连接。

**测试**：

- `tests/test_data_access.py` 测层自己的规则：顺序、长写入中照样能读、`BEGIN IMMEDIATE`、回滚、同一快照、取消、关停、循环检查两种模式、两种慢日志、SQL 函数。
- `test_backup.py`、`test_admin_backups.py`、`test_restore.py` 改成经 `backups_in(path)` 把层传进去，改成 async。命令行那几个同步测试里用 `asyncio.run`。
- 本机跑了受影响的测试（307 个），另外跑了走这几个接口的 HTTP 测试（285 个）。其中 `test_proxy.py::test_tunnels_whose_tls_timed_out_leave_the_pool_room` 在整批里超时失败过一次，单独重跑三次都过，和这次改动无关。

**审查后没改、只记下的判断题**：

- `prepare` 阻塞调用方、没有检查，只靠约定在构造时用。
- 看板和后台价格两处 handler 是同一个过渡形状（先 await，再整段进线程池）。
- `Backups._begin`、`_finish` 只是补上时钟再转给 `write` 的薄包装。
