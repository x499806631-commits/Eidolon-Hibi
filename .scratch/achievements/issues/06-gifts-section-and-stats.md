# 06: 礼物页「成就」一小节和它的点、统计页「成就」一节

**What to build:** 用户在礼物页每日任务下面、重置卡上面看到「成就」一小节：往来过的角色、到过亲密的角色、来过的天数、抽过的奖四行，新解锁的行名字旁有点，离开礼物页算看过；在礼物页送礼、抽奖时解锁的当场更新。统计页「好感度」一节旁边多一节「成就」，两个数字：解锁的阶段、集齐的角色，不随时间跨度变。见规格「礼物页那一小节」「统计页」「『看过』和点」。

**Blocked by:** 01

**Status:** done

- [x] 礼物页顺序：抽奖台、每日任务、成就、重置卡、我的礼物；四行用票 01 的行写法函数（「5 位 / 10 位」「12 天 / 30 天」「37 次 / 50 次」）；开关关着时没有这一小节
- [x] 那一小节的点：解锁时刻晚于看过的那几行带点；离开礼物页时记成看过；和票 05 的看过用同一个 `lib/` 模块（范围是「合起来」）；哪几行带点收进 `lib/` 纯函数
- [x] 送礼、抽奖返回里带着解锁时当场更新，再照现在的做法重读 `GET /api/gifts`
- [x] `GET /api/stats` 加 `achievements`：解锁的阶段一共几个、集齐的角色几位；照成就自己记下的算；不随时间跨度变；开关关着时为 null
- [x] 统计页在「好感度」旁边加「成就」一节，两个 `Figure`，不写说明；开关关着时不出现；分享图不加
- [x] 用现有的 shadcn 部件和礼物页已有的分节样式；`DESIGN.md` 礼物页、统计页补上
- [x] 后端测试：统计页两个数、开关关着为 null、删了历史对话不变少（照 `test_stats.py`、`test_delete_past_conversations.py`）
- [x] 前端测试：礼物页那一小节哪几行带点；统计页「成就」一节什么时候出现（`stats.test.ts`）
- [x] 本地构建版上用 Playwright 验：礼物页那一小节宽屏和手机、带点和离开后消失、统计页那一节

## Comments

### 票 01 交接

- `GiftState.achievements`（`lib/gifts`）是合起来的四个，顺序 `characters`、`intimate_characters`、`days`、`draws`，开关关着为 null；每一行用 `lib/achievements` 的 `achievementRow`，文案已在 `copy.achievements`（「5 位 / 10 位」「12 天 / 30 天」「37 次 / 50 次」）。

### 票 05 交接

- 看过用 `lib/achievements` 的 `achievementsSeenUpTo(userId, 范围)`/`keepAchievementsSeenUpTo`；票 05 没定合起来的范围名，这里自己定一个（例如 `"all"`，角色 id 里没有这个）。
- 哪几行带点：`unseenRows({ achievements }, seenAt)`（不传 `complete`），最近解锁：`latestUnlock({ achievements })`；比较时刻用 `lib/seenOnDevice` 的 `isUnseen`、`later`。
- 面板里的点 `UnseenDot`（6px，读屏「新解锁」）在 `components/conversations/AchievementsPanel.tsx` 里导出，那一小节可以拿去用。

### 审查（票 06）后留下的判断题

- 「集齐的角色」按现在的门槛判断（`achievements.tally` 用 `_complete`，和面板的「集齐了」一致）：以后给按角色的成就加阶段，已经集齐、拿过重置卡的角色会从这个数里掉出去。要「只增不减」就改数给过集齐重置卡的那一行（`achievement_progress` 里的 `complete`）。没改。
- 礼物页那一小节的「离开礼物页记成看过」只在页面卸下（应用里换页）时记；直接关标签页、刷新不记，下次进来同样的行还带点（只会多出点，不会漏）。要补可以加 `pagehide`。没改。
- 礼物页的看过记法（进页读、离开记最近解锁）和 `useHerAchievements`（打开面板就记）形状相近，时机不同，没有抽成共用 hook。
