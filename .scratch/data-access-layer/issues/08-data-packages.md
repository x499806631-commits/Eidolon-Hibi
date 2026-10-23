# 08: 数据包导入和导出经过数据访问层

**What to build:**
- **导入**：解压、解码照片、校验，都在写任务之外做完，最后用一个写任务、一个事务写进去。导入期间，别的用户的写入不用一直等着解压照片；导入要么完整写进去，要么什么都没写。
- **导出**：先用一个读任务读出这位用户的数据，再在线程里打包。
- 数据包的格式和用户看到的行为都不变。

**Blocked by:** 03, 04, 07

**Status:** done

- [x] 数据包不再另开连接，也不再绕开别的模块。导入时，对话、消息、事件、照片、通话、搭话、制作人档案、记忆和导入记录，在同一个事务里经由各模块的事务体写入；读 `get_profile` 也在这个事务里。
- [x] 每位用户同时只能导入一个，这个限制保持不变。
- [x] 导入返回后，在循环上启动补记忆向量的 task；补向量本身的读写经过这一层。
- [x] 导出的 handler 不再用 `run_in_threadpool` 包读库。
- [x] 受影响的测试通过，CI 绿。

## Comments

### 票 03 交接

- 导出已经改成 `async export`：先 await `get_profile`，其余数据在 `_export` 里（线程上）读出并打包。08 把这些读取也改成一个读任务。
- 导入在自己的连接、自己的事务里用 `store.read_profile(db, …)` 读档案。改成层的写任务后，照样在同一个事务里调它。

### 票 04 交接

- `DataPackages.export` 现在先 await `whole_conversations`。照片的图片在线程里一张一张读：`asyncio.run_coroutine_threadsafe(store.photo_image(…), loop)`。线程里直接读库的只剩 `memories.kept`。
  - 改成一个读任务时，可以用模块级事务体 `store._whole_conversations`、`_photo_image`。也可以考虑图片照旧一张一张读，免得一次全读进内存。
- `picture_of` 的 `.result()` 没设超时：事件循环在导出中途停掉时，打包线程会一直等。重做时改掉。

### 票 07 交接

- `Memories.kept` 已经是 async（读任务）。`export` 现在先 await 它，再把记忆作为参数交给 `_export` 在线程里打包；`_export` 不再收 `user_id`。改成一个读任务读出全部数据时，可以把 `Memories._kept` 也放进去，它是 `(db, user_id)` 形状的事务体。
- `embed_carried` → `embed_missing` 的读写已经走层（读任务 `_unembedded`、写任务 `_store_embedded`）。验收项「补向量本身的读写经过这一层」已经满足，只剩「导入返回后在循环上启动」，现在本来就是这样。
- `add_carried(db, user_id, carried)` 的形状没变，导入的写任务里照样传同一条连接调它。

### 实现说明（票 08）

- `DataPackages` 改为接收 `Database`；建表用 `prepare`，不再调 `connect`。`check_imports_left` 改成 async 读任务，导入的 handler 里的 `run_in_threadpool` 删了。`_working` 只在循环上用，`threading.Lock` 去掉。
- 导入：`_unpack` 在线程上解压、校验、解码检查图片，把要加的对话（`_Carried`）、档案、记忆和图片收进 `_Unpacked`，被跳过的对话也在这一步算好。然后交一个写任务 `_write_import`，一个事务写完：在事务里先读 `read_profile` 再 `write_profile`，再写 `add_carried_conversation`（消息、事件、照片、通话、搭话）、`add_carried`，最后写导入记录。`now` 由循环上取好后传进写任务。
- 导出：一个读任务 `_read_data` 读档案、对话、记忆。对话用 Store 的 `read_whole_conversations`（原来叫 `_whole_conversations`，`Store.whole_conversations` 已删），记忆用 `Memories.read_kept`（原来叫 `_kept`）。打包仍在线程里，图片照旧一张一张经 `Store.photo_image` 读；`.result()` 等最多 `PICTURE_WAIT`（60 秒），超时就撤掉读取、导出失败。
- 判断：
  - 图片解压后要一直留在内存里，等写任务把它们写进去。为此加了总量上限 `MAX_PICTURES_BYTES`（200 MB），超过按 `broken` 拒收。本部署导出的包里图片不压缩、包本身最大 200 MB，所以本部署的包不会碰到这个上限，它只防外来的包解压后变得很大。不导入照片时，图片校验完就丢掉。
  - 当日导入次数仍在读任务里查，写任务里不再确认。同一用户的导入有 `one_at_a_time` 排着队，不会有竞态。
- 新测试：`test_others_write_while_a_packages_pictures_are_unzipped`（解压照片期间，别的用户写入不被挡；旧代码在事务里解压，这条等满 5 秒超时）、`test_pictures_unzipping_too_large_together_are_refused_as_broken`。
- 审查时只报告、没改的几条：
  - 新测试 monkeypatch 了私有函数 `_unzipped`，用来在解压时停住，算擦边。
  - `PICTURE_WAIT` 超时没有测试。
  - 导出和导入两边按类别分支的写法原样保留。
  - 读事务体 `_read_data` 是方法（因为要用 `self._memories`），写事务体 `_write_import` 是模块级函数，两者写法不一致。
