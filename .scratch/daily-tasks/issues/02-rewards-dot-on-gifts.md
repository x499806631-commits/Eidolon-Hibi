# 02: 做完时当场知道：导航「礼物」上的点

**What to build:** 用户在对话页或通话里做完一项任务时，导航栏和 dock 的「礼物」上当场出现一个小点，意思是「有新到的抽奖次数」；在别的设备、别的标签页做完的，过一会儿这边也出点；进了礼物页点就消失。在礼物页上送礼做完的不出点，那一行当场打勾、弹一下。这个点之后由成就共用。见规格「到账了什么：随回合、送礼一起返回」「导航『礼物』上的点」。

**Blocked by:** 01

**Status:** done

- [x] 往来记账返回「这次到账」（抽奖次数、重置卡张数、做完了哪几项任务）；打字回合的 `done`、通话里回合的 `done` 和送礼的返回都带上这个字段，没到账为 null
- [x] `GET /api/gifts` 加 `rewarded_at`：最近一次到账的时刻，从来没有为 null
- [x] 新的 `lib/` 模块管点和看过：localStorage 按用户存看过的最近一次到账时刻，存储不可用时退回内存；没存过时任何到账都算没看过；开关关着不亮
- [x] 外壳现有的 60 秒轮询（切回前台立即拉）一并读 `rewarded_at`；回合、送礼的返回带着到账时当场亮，不等轮询
- [x] 点照「对话」上的点：6px、`bg-foreground`，导航栏和 dock 都有；带点时读屏名字「礼物，有新到的抽奖次数」；进礼物页记成看过；在礼物页上发生的到账当场记成看过
- [x] 礼物页送礼成功后用「这次到账」当场给那一行打勾，刚做完的弹一下（照顾减少动态），然后照现在的做法重读
- [x] 任务没做完时导航上什么都不标；不弹提示条
- [x] `DESIGN.md` 导航一节补上「礼物」也有这个点
- [x] 后端测试：回合 `done`、通话里回合的 `done`、送礼返回里的「这次到账」；`rewarded_at` 随到账更新
- [x] 前端测试：点的亮灭（照 `announcements.test.ts`）；导航相关的照 `navShell.test.ts`、`conversationList.test.ts` 补
- [x] 本地构建版上用 Playwright 验：对话页做完一项出点、通话里做完出点（能造就造）、进礼物页消失、礼物页送礼当场打勾不出点

## Comments

### 票 02 交接

- 「这次到账」已经有了：`Happenings.turn_recorded` / `gift_given` 返回 `Rewarded | None`（`draws`、`reset_cards`、`daily_tasks`），只是现在没人用。回合在 `engine.py` 里 `_record` 之后调用（把返回值带进 `TurnDone`），送礼在 `gifts_api.py` 的 `give` 路由里调用（把它并进 `given.shown()`）。
- `rewarded_at` 取 `gift_rewards` 里这位用户最新的 `rewarded_at`。

### 实现说明（票 02）

- 后端：`Rewarded` 多了 `rewarded_at`（这次到账的服务器时刻，`Gifts.reward` 现在返回 `(给了哪些, 时刻)`），`Rewarded.shown()` 是浏览器看到的形状。`TurnDone.rewarded` 由 `engine.shown` 下发，打字和通话同一处；送礼返回并上 `rewarded`。`GiftState.rewarded_at` 取 `gift_rewards` 里这位用户的 `MAX(rewarded_at)`。
- 前端新模块 `lib/rewards.ts`：`Rewarded` 类型、看过（`eidolon.rewardsSeen.<用户 id>`，写入时取较晚的那个，存不进去退回内存）、`hasNewRewards`、`later`。`lib/navShell.ts` 加 `isGiftsInView`（在礼物页上、通话层没展开）。`lib/dailyTasks.ts` 加 `withDone`。
- 外壳（`ConversationDirectory`）持有开关、最近一次到账和看过：载入时和 60 秒轮询各拉一次 `GET /api/gifts`；`LiveConversation` 新 port `onRewarded` 在回合 `done` 带着到账时当场更新；礼物页在眼前时到账随即记成看过；监听 `storage`，别的标签页记下看过时这边的点跟着灭。对外给 `newRewards`、`rewardedAt`、`giftsRead`。
- 礼物页把读到的状态都交给外壳（`giftsRead`）；送礼成功用「这次到账」当场打勾、加次数，刚做完的勾 `motion-safe:animate-jump`，然后重读。外壳知道的最近一次到账比页面读到的新（通话里、别的设备）时，重读一次。
- 判断题：
  - 比较用服务器时刻，不用「现在」，避开浏览器时钟偏差。
  - 通话层展开盖住礼物页时不算「在礼物页上」，照常出点。
  - `conversationList.test.ts` 没补：「礼物」上的点和对话列表无关。
  - 审查提的数据泥团（外壳里开关和最近一次到账是一个 state，看过另一个 state）没改。
