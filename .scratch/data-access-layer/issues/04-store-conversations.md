# 04: Store（二）：对话、消息、照片、通话记录

**What to build:** `Store` 余下的部分迁到数据访问层：对话列表、历史对话、删对话、对话标题、照片保存、通话的查询和记下、搭话的存取、回合开头的读取。回合、通话、搭话这些路径上，读库不再靠调用点零散地包 `to_thread`；照片保存（约 150 KB）也不再在事件循环上写。

**记录回合、送礼的写入不在这张票里**：`add_turn`、`add_gift`、`keep_call` 留给 06，这里只保证它们照常能被调用。

**Blocked by:** 01, 03（同一个文件，为了避免冲突，不是逻辑上的前置）

**Status:** done

- [x] 这一部分的方法改成 async，SQL 留在私有的同步事务体里。
- [x] 回合开头读的那一组（是否已结束、对话记录、上次联系、好感度、能不能发照片、制作人档案）收成一个读方法，回合和搭话共用。搭话里打包成元组的 lambda 删掉。
- [x] 回合、建立通话、对话列表、标题这些地方包读库的 `to_thread` 删掉。
- [x] 删对话仍是一个事务（连同把记忆的来源断开、删掉搭话日程），经过这一层。
- [x] 照片保存是一个写任务；Pillow 转码照样在线程里做。
- [x] 通话期间每 5 秒查一次「人还在不在」改成走读连接。
- [x] 命令行里用到这一部分的地方改成用 `asyncio.run`。
- [x] 受影响的测试通过，CI 绿。

## Comments

### 票 03 交接

- `Store(db)` 已经接收层，账号这一块走层，存在 `self._database`。其余方法还用自己的连接 `self._db` 和 `self._lock`。迁完去掉它们，再把 `self._database` 改名 `_db`。
- `call._hang_up_users_gone` 里的 `active_users`、`get_user` 已经 await，走读连接。「通话期间每 5 秒查一次改成走读连接」这一项可以直接勾掉。
- 回合开头、搭话的档案读取，现在是单独 `await self._store.get_profile`（`_BeforeAsking` 已经去掉 `profile` 字段）。收成一个读方法时，把它并进去。
- `/api/stats` 里的 `contacts`、`kept_calls`、`first_records` 在 `run_in_threadpool` 的 `summed_up` 里，迁完改成 await。
- 测试辅助：`tests.conftest.store_in`、`users_in`；`CallUnderTest.of` 和 `test_turns.turns_in` 经 `store_in` 读对话。

### 实现说明（票 04）

**`Store`**（`store.py`）：

- `Store` 不再有自己的连接和锁。`self._database` 改名 `self._db`，`Store.close` 删掉，`assembly` 里也不再关它。
- 改成 async、SQL 搬进模块级事务体的方法：
  - 读：`get_conversation`、`list_conversations`、`whole_conversations`、`contacts`、`kept_calls`、`first_records`、`latest_conversations`、`photo`、`photo_image`、`ended`、`typed_turns`、`list_messages`、`record`、`current_conversations`；
  - 写：`create_conversation`、`name_conversation`、`delete_conversation`、`finish_photo`、`hang_up`、`add_reach_out`、`reach_out_recorded_as`；
  - 先读后写：`read_by_user`。
- 删对话（`_delete_conversation`）是一个写任务，「是不是当前对话」在写任务里判断。断开记忆来源、删搭话日程照旧由 `database.delete_conversation_data` 在同一个事务里做。
- `Store.last_contact` 删了，只有回合开头用到它。事务体公开成模块函数 `read_ended`、`read_record`、`read_last_contact`，和原有的 `read_profile` 配对，给回合开头那个读任务用。
- **留给 06 的写入**：`add_turn`、`add_gift`、`keep_call` 还是同步方法，SQL 已经搬进 `_add_turn`、`_add_gift`、`_keep_call`，经 `Database.prepare` 阻塞地交给写线程。
  - 原因：`Affinities.change`、`receive_gift` 拿着锁同步回调它们，通话结算也是同步的。
  - 现在调用方等的是写队列，不再是 SQLite 的忙等。
  - `give_up_photos` 只在启动时调，同样用 `prepare`。
- **先读后写的方法**：这一部分只有 `read_by_user`。打开对话就会调它，平时只读；读到的搭话里有没看过的，才交写任务标成已看。原来只要对话里有搭话，每次打开都写一次。
- `taking_turn` 只在事件循环上计数，去掉了锁。`is_taking_turn` 只查一下有没有这个键，`ReachOuts._due` 在线程里也会调它，不用加锁。
- `add_reach_out(conversation_id, said, contact, in_call: bool)`：调用方在循环上查好 `in_call`；`add_reach_out` 交写任务之前，在循环上看有没有文字回合正在进行。这也把 07 的那一项做掉了。
  - 判断题：原来这两项是在 `Store` 的锁里、和写入一起判断的。现在从循环上查完到写任务提交，中间有几毫秒的窗口。这期间开始的文字回合，可能读不到这条搭话。spec 本来就接受「她在不在通话里」这样查，文字回合是同一类情况，所以没有另外加东西把这个窗口堵上。

**回合开头的读取**（`engine.py`）：

- `ConversationEngine` 构造时多收一个 `Database`。回合用 `_before_turn`，搭话用 `_before_reaching_out`，两个读事务体都调共用的 `_read_before_asking`，各自一次 `self._db.read` 读完。读的是：是否已结束、对话记录、上次联系、好感度、能不能发照片、制作人档案。搭话里打包成元组的 lambda 删了，单独的 `get_profile` 也并进来了。
- `_BeforeAsking` 多了 `ended`、`profile` 两个字段，不再用 None 表示「已结束」：搭话照旧不看「已结束」，交给 `add_reach_out` 拒绝，行为和原来一样。搭话传 `sends_photo=False`，不读照片限额。
- 好感度（`Affinities.status`）和照片限额（`UsageLimiter.check`）还没迁，在这个事务体里照旧用它们自己的连接，跑在读线程上。05、06 迁完后换成接收同一条读连接的事务体（已写进交接）。
- `typed_turns`、回合记下后的 `ended` 改成 await。`_cools`、`happenings.turn_recorded` 的 `to_thread` 留给 06。

**调用方**：

- `app.py`：
  - `conversation_or_404`、`refuse_if_ended`、`conversation_to_call` 改成 async；`/api/offer` 里包它们的 `to_thread` 删掉了。
  - 新建对话、对话列表、最近对话、删对话、读对话记录、文字回合、`/api/ice-servers` 这些 handler 改成 `async def`。
  - 过渡写法：`text_turn` 里的 `admissions.turn`（05）、`/api/ice-servers` 里的 `calls.ice_servers`（读冷却，06）放在 `run_in_threadpool` 里，免得落到循环上。
  - `text_turn` 改成 async 以后，在等放行时可能被取消。照通话那边的做法用 `shield`：被取消时，已经放行的回合在 `never_ran` 里按「离开」结算。
- `photos_api` 的两个 handler 改成 async。
- `Photos.recorded`、`_keep` 改成 async，照片保存是一个写任务。`_keep` 用 `try/finally` 调 `_forget`：等写入时被取消，也不会让 `came_out` 一直等下去。
- `call._end` await `hang_up`。
- `titles`、`reach_outs` 里包读库的 `to_thread` 删掉了。
- `/api/stats` 的 `contacts`、`first_records`、`kept_calls` 在 handler 里 await，作为参数传进 `summed_up`。
- `DataPackages.export` 先 await `whole_conversations`。照片的图片在打包线程里一张一张读，用 `run_coroutine_threadsafe` 交回循环 await `photo_image`，这样不会一次把所有图片读进内存。
- 命令行 `_user_command` 和 `memory_eval._Scratch` 不再关 `Store`；`_Scratch.conversation` 改成 async。

**测试**：

- 直接用 `Store` 的测试都改成 await：`test_memories` 的 `World.conversation`、`test_turns` 的 `Turns.record`，以及 `test_call`、`test_call_photos`、`test_call_turns`、`test_assembly`、`test_delivery_cues`、`test_store_at_scale`、`test_reembed`、`test_photos`。
- `test_off_the_event_loop` 里「文字回合在线程里读库」的用例删了：它盯的 `Store` 方法现在都是层的读任务，由层的检查管住。
- 没有新加测试：spec 的测试决定没给这张票点名新的行为，行为不变的部分由现有用例和 conftest 里打开的层检查保证。
- 本机跑了 affected-tests（740 个），外加按接口路径 grep 出来的 64 个 HTTP 测试文件（645 个），都通过了。

**审查后改的**：

- `_read_before_asking` 的调用点上原来是一串位置布尔参数，拆成了 `_before_turn`、`_before_reaching_out`，内部用关键字参数。
- 游标变量 `rows = by_name(db)` 改名 `named`。
- `text_turn` 等放行时被取消的结算（见上）。
- `is_taking_turn` 的说明改正：线程里也会调。

**审查后没改、只记下的判断题**：

- 搭话和文字回合之间的窗口（见上）：搭话交写任务之前，在循环上查了有没有正在进行的回合；写任务提交之前开始的回合，读不到这条搭话，而它记下时会把这条搭话标成已回应。窗口只有写队列的等待时间。原来靠 `Store` 的锁完全排开。
- `add_turn`、`keep_call` 在事件循环上经 `prepare` 等写队列，层的检查查不到这种等待。06 收掉（已写进交接）。
- `read_last_contact` 和 `_last_contact_in` 形状相似（一个按用户和角色，一个按对话）。是原样搬过来的，没合并。
- `/api/stats` 读 `contacts`、`first_records`、`kept_calls` 是三次读、三份快照，`summed_up` 的参数也多了三个。和原来一样是分开读的，没有合成一个读方法。
- 导出时 `picture_of` 的 `run_coroutine_threadsafe(...).result()` 没设超时。事件循环在导出中途停掉时，打包线程会一直等。08 重做导出时一起处理（已写进交接）。
