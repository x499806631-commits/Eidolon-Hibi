# 03: Store（一）：账号、会话、删账号

**What to build:** `Store` 里账号这一块迁到数据访问层，包括用户、会话、登录方式、制作人档案、封禁。

- **登录**：OAuth 回调、每个请求的会话续期、后台用户页，都不再在事件循环上碰库。平时的请求只读不写，不进写队列。
- **删账号**：删除是一个事务，提交后就回复用户。之后的 checkpoint 只等约 100 ms；有读者没做完，就隔几秒重试。这样删账号不会再把所有写入挡几十秒，旧页也不会悄悄留在库文件里（ADR-0006 的 2026-10-21 修订）。

**Blocked by:** 01

**Status:** done

- [x] 这一块的方法改成 async，SQL 留在私有的同步事务体里。`Store` 其余的方法暂时照旧用自己的连接（04 再迁）。
- [x] `signed_in_user`、`admin_user`、后台用户页的 handler 改成 async。登录回调里建用户、加登录方式、建会话都经过这一层。
- [x] `use_session` 先在读连接上读，到了续期间隔或已过期时再交写任务，并在写任务里重新确认。两个并发请求只续一次，有测试。
- [x] 层提供 `checkpoint()`：在写线程上把这一次的忙等调到约 100 ms，再做 `wal_checkpoint(TRUNCATE)`，并检查返回值。没做完就隔几秒作为新的写任务再试；超过约 10 分钟仍未做完，记一条 error，然后继续重试。层启动时先做一次。
- [x] 删账号：封禁记录和删除各是一个写任务，提交后就回复，然后调 `checkpoint()`。原来那种整个过程一直拿着 `Store` 的锁的做法去掉。
- [x] 新测试：另一条连接开着读事务时删账号，另一位用户的回合照常记下、不用等这个读者；读者结束后 WAL 被清空。
- [x] 层的测试：有读者时 `checkpoint` 不挡后面的写入，读者结束后重试成功；一直做不完时记 error。
- [x] 命令行里用到这一块的地方改成用 `asyncio.run`。
- [x] 受影响的测试通过，CI 绿。

## Comments

### 票 01 交接

- `/api/account` 已经改成 `async def`：先 `await backend.sponsor.state()`，剩下读 `Store`、`ReachOuts` 的部分整段放在 `run_in_threadpool(lambda: {...})` 里。`Store` 这一块迁完后，把这里改成直接 await。
- 层的形状：`Database.write` 在写线程上先 `BEGIN IMMEDIATE`；`prepare` 给构造时建表、迁移用（同步等）；`ON_THE_LOOP_RAISES` 在 `conftest` 里设成真。`checkpoint()` 还没做。

### 实现说明（票 03）

**层的 checkpoint**（`data_access.py`）：

- `Database.checkpoint()` 把一次 checkpoint 作为写任务交出去就返回，不等它做完。
  - 在写线程上把忙等调到 `CHECKPOINT_BUSY`（0.1 s），做 `wal_checkpoint(TRUNCATE)`，看返回的 busy，再把忙等恢复原值。
  - 这个任务不开事务（`_Task.in_transaction`）：事务里做不了 checkpoint。
- 没做完就在写线程上记下 `CHECKPOINT_RETRY`（3 s）后再试。写线程等下一个任务时用 `get(timeout=…)` 到点醒来；到点了先试 checkpoint，再接任务。
- 从第一次没做完算起，超过 `CHECKPOINT_OVERDUE`（600 s）记一条 error，只记一次，照样重试。被别的连接的写锁挡住（`OperationalError`）记 info，其他 `sqlite3.Error` 记 exception，都按没做完处理。
- 层构造时 `_checkpoint_due` 设成当下，写线程第一件事就是补做一次。关闭时还没到期的重试直接丢下，下次启动补做。
- 本机实测：有读者时一次尝试约 0.23 s（Windows 上 SQLite 忙等的睡眠粒度），线上 Linux 应接近 100 ms。

**`Store` 的账号这一块**（`store.py`）：

- 构造改成 `Store(db: Database)`：
  - 建表和迁移挪进模块级的 `_open`，经 `db.prepare` 执行。
  - 层存在 `self._database`。还没迁的方法照旧用 `Store` 自己的连接 `self._db`（`connect(db.path)`）和锁。
- 改成 async、SQL 搬进模块级事务体的方法：`create_user`、`list_users`、`get_user`、`first_user_named`、`find_users`、`last_active`、`set_banned`、`active_users`、`consent`、`user_signing_in_with`、`create_user_signing_in_with`、`add_sign_in_method`、`update_sign_in_method`、`sign_in_methods`、`remove_sign_in_method`、`delete_user`、`is_banned_identity`、`create_session`、`use_session`、`end_session`、`end_sessions`、`get_profile`、`save_profile`。
- 新加 `sign_in_methods_of(user_ids)`：后台用户页和命令行 `user list` 一次读出一批用户的登录方式。
- **先读后写的方法**（spec 要求列出）：这一块只有 `use_session`。
  - 先在读连接上读会话和用户。没到续期间隔、也没过期，就直接返回，不进写队列。
  - 否则交 `_use_session` 写任务，在写任务里重新读一遍，再判断过期和续期。
  - 其余方法要么只读，要么本来就是写，判断都在写任务里，比如 `set_banned` 的 `NotFound`、`remove_sign_in_method` 的「唯一一种登录方式」。
- `read_profile(conn, user_id)` 公开成模块函数，和原有的 `write_profile` 配对。`ReachOuts` 在它自己的连接上读时区、数据包导入在导入事务里读档案，都用它。
- `database.by_name` 从 `corpus._by_name` 挪出来共用。

**删账号**：

- `Accounts.delete`：挂断 → 忘掉记忆 → `Store.delete_user(user_id, 身份哈希)` → 再挂断一次。原来在 `to_thread` 里一直拿着 `Store` 锁的做法去掉了。
- `Store.delete_user` 依次做三件事：
  1. 封禁名单：一个写任务 `_ban_identities_if_banned`，在写任务里看这位用户有没有被封禁。审查后才从读连接挪进写任务。
  2. 删除：一个写任务 `database.delete_user_data`。它不再自己开 `with db:`、做 checkpoint，改成在调用方的事务里执行。
  3. `checkpoint()`，不等它做完。
- 所以回复时 WAL 可能还没清空。`test_delete_account` 里三个检查库文件的测试，改成先 `emptied` 再查。

**handler 和依赖**：

- 改成 `async def` 的：
  - `session_user`、`signed_in_user`、`admin_user`；
  - 登录回调、开始登录、登出、同意条款、开发登录、解绑、全部登出；
  - `/api/profile` 的两个接口、后台用户页全部接口、`/api/admin/gifts`、`/api/stats`。
- `/api/account`（票 01 交接）：`Store` 的部分直接 await；`reach_outs.switch_of` 还用它自己的连接，单独放在 `run_in_threadpool` 里。
- `is_admin` 改成 async，另加纯函数 `signs_in_as_admin(methods, settings)`。
- 过渡写法，后面的票收掉（见各票的交接）：
  - 后台用户页读 `ActivityLog`、`UsageLimiter` 的部分；
  - `/api/stats` 读 `Store` 其余部分、`Affinities`、`Happenings` 的部分（`summed_up`）；
  - `/api/admin/gifts` 的 `gifts.last_grant`。
  
  这几处都放在 `run_in_threadpool` 里。
- 回合和搭话里的档案读取，从那一包 `to_thread` 里拿出来，单独 `await get_profile`。
- 数据包导出改成 `async export`：先 await 档案，再在线程里读其余数据并打包。handler 不再用 `run_in_threadpool` 包着。
- `call._hang_up_users_gone` 里的 `active_users`、`get_user` 改成 await，走读连接。

**命令行**：

- `user list/ban/unban/usage` 在 `asyncio.run` 里开 `Database`。
- `memory_eval._Scratch` 自己开一个层，评测用户在 `start_with` 里建。

**测试**：

- 层：
  - 有读者时 checkpoint 不挡后面的写入，读者走后 WAL 被清空；
  - 一直做不完时记 error，并且照样重试；
  - 启动时补做一次。
- HTTP 接口：
  - 两个并发请求只续一次。先交一个写任务把写线程占住，让两个请求都读到旧会话。去掉写任务里的重新确认，这个测试就失败。
  - 读者开着时删账号，另一位用户的回合照常记下；读者结束后 WAL 被清空。把 `CHECKPOINT_BUSY` 调成 30 s（相当于旧行为）时，这个测试会超时。
- `conftest`：
  - `sign_in` 还是同步的，因为客户端工厂是同步的。它在另一个线程的事件循环上跑（`aside`）。
  - 新加 `store_in`、`users_in`，直接构造 `Store` 的测试都改用它们。
  - `Admin` 在另一个线程上跑 `main`，因为命令行现在会起事件循环。
- `test_off_the_event_loop`：删账号那个用例去掉了，回合用例里对 `get_profile` 的观察也去掉了，这两样现在由层的检查管住。
- 本机跑了受影响的测试（840 个），外加账号相关的 HTTP 测试。`test_proxy.py::test_tunnels_whose_tls_timed_out_leave_the_pool_room` 在整批里又超时失败了一次，单独重跑 3 次都通过（票 01、02 也记过），和这次改动无关。

**审查后没改、只记下的判断题**：

- `Store` 里 `self._database` 指层，`self._db` 指自己的连接；已迁的模块里 `_db` 指的却是层，正好相反。04 去掉自己的连接时一起改名。
- 「开 `Database`、建 `Store`、先 `store.close()` 再 `await db.close()`」出现了三处：命令行、`conftest.store_in`、`memory_eval`。04 去掉 `Store.close` 后会简化一些，剩下的和票 02 记下的 `async with Database` 一起留给 09。
- 「读者挡住 checkpoint 时不挡写入、读者走后清空」层测试和 HTTP 测试都测了：spec 的两个接缝各点名要了一个。
- `test_two_requests_at_once_renew_a_session_once` 靠睡 0.3 s 让两个请求都读到旧会话。机器太慢时它只会假通过，不会误报失败。
- 封禁名单和删除是两个写任务（照票的写法），两步之间刚好被封禁的话，身份进不了封禁名单。这个窗口极小。
