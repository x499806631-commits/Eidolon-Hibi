# 02: 剧情库、资料、价格目录迁到数据访问层

**What to build:** 剧情库（`StoryCorpus`）、角色资料和作品资料的两个 `EmbeddedTexts`、价格目录（`Catalog`）都迁到数据访问层。回合里的剧情检索、资料检索，后台的价格目录，命令行的剧情同步，行为都和现在一样，只是读写不再经过各自的连接和锁，事件循环上也不再碰库。

**Blocked by:** 01

**Status:** done

- [x] 四个模块不再自己开连接、不再持有 `threading.Lock`；碰库的公开方法改成 async，SQL 留在私有的同步事务体里。
- [x] 价格目录自己的刷新线程改成 asyncio task：取数照样走 HTTP，写库经过这一层；关停时等它结束。
- [x] 检索和资料里包读库的 `to_thread` 删掉；纯 CPU 的相似度计算照样放在线程里。
- [x] 命令行（剧情同步等）用 `asyncio.run` 走同一个层。
- [x] 直接构造这些模块的测试改成把层传进去、改成 async，照样通过。
- [x] 受影响的测试通过，CI 绿。

## Comments

### 票 01 交接

- `Pricing.find`、`Pricing.listed` 还直接调 `Catalog.state()`、`Catalog.find()`（同步、碰库）。所以看板（`dashboard.summed_up`）和后台价格（`admin_prices_api.described_by`）先 `await prices.pricing()`，再把整段同步部分放进 `run_in_threadpool`。`Catalog` 迁完以后，`Prices.pricing()` 要在一次读里连目录一起读好，交给 `Pricing`；这两处的 `run_in_threadpool` 等 05 迁完 `ActivityLog` 再一起收掉。
- 刷新目录的 handler 现在是 `await run_in_threadpool(prices.catalog.refresh)`。

### 实现说明（票 02）

**剧情库 `StoryCorpus(db, embedder)`**（`corpus.py`）：不再开连接、不再持锁。碰库的方法全部改成 async：`save`、`remove`、`get`、`imported_from`、`is_translated`、`episode_ids`、`list_episodes`、`catalog`、`search`、`search_by_vector`、`embed_missing`。SQL 搬进模块级的私有事务体，原样不改。

- 层的连接是各模块共用的，不能设 `row_factory`。要按列名读的事务体用 `_by_name(db)` 拿一个带 `sqlite3.Row` 的游标。
- `save`：先在读连接上读已有向量，再调向量化服务，最后用一个写任务写这一批剧情。
- `search_by_vector`：先读段落，相似度计算（`nearest`）放在 `to_thread` 里，再读剧情。两次读不是同一份快照：其间被删掉的剧情直接跳过。原来这里会撞 `assert`。
- 新加 `episodes(ids)`：一次读取出若干段剧情。回顾剧情、剧情同步取分卷名都用它。`retrieval._episodes` 删掉。
- `count_missing_vectors` 换成构造时用 `db.prepare` 数好的 `missing_vectors_at_open`。`open_reference_sources` 是在同步的 `assembly.build` 里调的，没法 await。
- `Episode.translated` 收拢了「是汉化版」（`imported_from == "csv"`）这条判断。

**资料 `EmbeddedTexts(db, embedder)`**（`embedded_texts.py`）：

- 建表和搬旧表放在 `prepare(_create)` 里。原来自己发的 `BEGIN IMMEDIATE` 去掉，由层来开。
- 读已存向量、删过时文本、按批保存，各走一次 `read` 或 `write`。相似度计算仍在 `to_thread` 里。
- `forget_other_models` 改成 async。`CharacterFacts`、`WorkFacts` 改成接收 `db`，`close` 去掉。

**价格目录 `Catalog(db, clock, transport: httpx.AsyncBaseTransport)`**（`price_catalog.py`）：

- 内存里的目录收成一个不可变的 `_Listing`。构造时经 `prepare(_open)` 建表并读出来；刷新后经一次 `read` 整个换掉。`find`、`thinking`、`known_provider`、`providers`、`offers`、`state` 都只读内存，不碰库。
- 刷新线程换成 asyncio task（`_fetch_in_background`）。HTTP 用 `httpx.AsyncClient`，JSON 解析和 `parse_*` 放在 `to_thread` 里，写库（`_save`、`_save_failure`）走写任务。
- `refresh()` 等进行中的那次取数，没有就起一次，用 `shield` 包住：等的一方被取消，取数照样做完。关停之后不再起新的取数。
- `refresh_soon` 必须在事件循环上调。所以看板和后台价格把它从 `run_in_threadpool` 里挪到 await 之后；`/api/account/keys/llm/efforts`、`/providers` 两个 handler 改成 `async def`。后台价格的 `described_by` 现在只在线程里算价格表和未定价列表，`catalog` 状态在循环上填。
- `close` 换成 `aclose`：等进行中的取数结束，再关 HTTP 客户端。`Backend` 里它排在层的关闭之前。

**调用方**：

- 检索：`StoryReferences.search` 和回顾剧情里包读库的 `to_thread` 删掉。参考来源、`ReferenceSources`、`Retrieval` 的 `close` 去掉，`ReferenceSource` 协议也不再要求 `close`。
- `Reembeddable.forget_other_models` 改成 async。`Memories.forget_other_models`（07 的模块）跟着改成 async，原来的同步体放进 `to_thread`，07 迁移时收掉。
- 剧情导入、剧情同步、脚本缓存、角色表草稿都改成 await 剧情库。
- 命令行 `import-story`、`sync-story`、`draft-sheet`、`eval rerank` 在 `asyncio.run` 里开 `Database`，用完 `await db.close()`。`reembed(path, …)` 和记忆评测自己开层。

**测试**：

- `test_embedded_texts` 用 `db` fixture 把层传进去，在同一个层上重新构造就当作重启。
- 剧情导入、同步的 fixture 改成 async，自己开层、自己关。
- `tests/stories.corpus_in(path, embedder)` 仿照 `backups_in`，给「另一个进程导入剧情」的测试用。
- `test_proxy` 的 `catalog` fixture 改成 async，`await catalog.refresh()`。
- 本机跑了受影响的测试（590 个），加上走看板、后台价格、模型档位、服务商列表接口的 HTTP 测试（119 个）。`test_proxy.py::test_tunnels_whose_tls_timed_out_leave_the_pool_room` 在整批里又超时失败了一次，单独重跑 3 次都过（票 01 也记过），和这次改动无关。

**审查后没改、只记下的判断题**：

- 票 01 交接说「`Prices.pricing()` 要在一次读里连目录一起读好」，没有做。目录查找本来就只读内存，不碰库。`Pricing` 拿的是 `Catalog` 本身，看板算到一半时如果刚好刷新完，同一份响应里前后几行可能按两版目录定价。迁移前也是这样（原来刷新在线程里换属性）。要修，可以把 `_Listing` 交给 `Pricing`。
- 「开 `Database`、`try`、`finally: await db.close()`」在命令行出现 4 处，在测试 fixture 里有 6 处左右。给 `Database` 加 `async with` 可以全部收掉，但这是层的接口，留给 09 收口时决定。`test_rerank_eval` 的 `world` fixture 被一个同步测试用着，所以是同步 fixture，关层用 `asyncio.run(db.close())`。
- 剧情同步每个剧本单独 `await corpus.is_translated`，每次都要切一趟线程。命令行用，不影响通话。
- `_from_models_dev` 返回的五元组原样传进 `_save`，这组参数是原有的，没有另外打包。
- 剧情库和价格目录建表时用 `executescript`，它会先提交层开的 `BEGIN IMMEDIATE`，和票 01 的几个模块一样。
