# 03: 礼物页那一节补全，抽奖台超过 7 次

**What to build:** 礼物页「每日任务」一节照原型补全：三项都做完时整节收成一行；没做完的任务旁边有一步到能做它的地方的按钮。抽奖台在次数超过 7 次时也说得通。见规格「礼物页那一节」「抽奖台超过 7 次」，原型 `prototype/daily-tasks` 的 `?dt=A`。

**Blocked by:** 01

**Status:** done

- [x] 三项都做完：整节收成一行，一个勾、「每日任务都做完了」、「0 点换新」
- [x] 没做完的回合任务旁边「去对话」（去对话列表）；送礼那项没做完、背包里有礼物时旁边「挑一件」（滚到「我的礼物」），背包空时不出现
- [x] 抽奖台次数超过 `max_draws` 时 7 个点全亮、后面写「+n」；下面那句说明改成「每天发的已攒满，抽掉之前不会再发」
- [x] 宽屏和手机同一个版式；照 `frontend/AGENTS.md` 的「shadcn 纪律」和 `DESIGN.md`
- [x] 前端测试：收成一行、「去对话」「挑一件」出现的条件（`lib/` 显示函数）；抽奖台的点数、「+n」和说明（照 `gifts.test.ts`）
- [x] 本地构建版上用 Playwright 看全做完的一行、两个按钮能去对的地方、超过 7 次的抽奖台

## Comments

### 实现说明（票 03）

- `dailyTasksView(daily, next, hasGifts)` 多给 `isAllDone`（三项都做完；一项都没有时不算），每行多给 `action`：`"converse"`（回合任务没做完，「去对话」去 `/conversations`）、`"pick"`（送礼没做完且背包里有礼物，「挑一件」滚到「我的礼物」），否则 null。哪项带哪个按钮是 `lib/dailyTasks.ts` 的 `ACTIONS`。
- `DailyTasksSection` 收成一行时，送礼刚做完最后一项弹的是这一行的勾。按钮 ghost `sm`，手机上 `max-md:h-11`（同 `AffinityBadge`）。「我的礼物」标题的 id 改成 `MyGifts` 导出的 `MY_GIFTS_ID`。
- 抽奖台：`lib/gifts` 的 `drawDots(draws, max)` 给亮几个、「+n」和读屏（超过 7 次时只读「抽奖还有 n 次」）。`drawNote` 在 7 次及以上都写「每天发的已攒满，抽掉之前不会再发」：规格写的是「在 `max_draws` 以上」「不再写『已攒满 7 次』」，正好 7 次也换。
- `DESIGN.md`「礼物页」的抽奖台、每日任务两条已补。
- 本地构建版上用 Playwright 看过宽屏和手机：做了 3 个回合时「挑一件」「去对话」各一个，点了分别到「我的礼物」和对话列表；背包空时没有「挑一件」；全做完收成一行；9 次时 7 点全亮加「+2」、新说明；正好 7 次时新说明、没有「+n」。

审查后的判断题（没改）：
- 任务 id 在 `zh.ts` 的 `names` 和 `ACTIONS` 两张表里各出现一次，加任务要改两处；没并成一张。
- 「挑一件」在页面里用 `document.getElementById(MY_GIFTS_ID)` 滚过去，没改成 ref 或让 `MyGifts` 提供滚动方法。
- `dailyTasksView` 第三个参数是布尔值，没改成选项对象。
- 手机 44px 用调用处的 `max-md:h-11`，没给 `ui/button` 加触控尺寸的 variant（仓库已有同样写法）。
