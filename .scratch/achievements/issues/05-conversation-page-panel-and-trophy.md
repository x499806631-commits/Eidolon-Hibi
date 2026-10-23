# 05: 她的对话页：更多菜单「成就」、面板、奖杯、点

**What to build:** 用户在她的对话页更多菜单最后一项点「成就」，打开一个用她颜色的面板（宽屏对话框、手机从底下拉出来），看到她的四个成就和「集齐」一行。在她的页上解锁她的阶段时，页首好感度标识左边跳出奖杯和「+n」，约 3 秒收起；更多菜单键和菜单项「成就」上留点，面板里新解锁的那一行带点，打开面板点就消失。在别处解锁的（通话挂断后结算的时长、别的标签页），外壳轮询读到 `rewarded_at` 变新时重读她的成就、出点。不弹提示条。见规格「『看过』和点」「她的对话页」。

**Blocked by:** 01

**Status:** done

- [x] 更多菜单最后一项「成就」，新图标放进 `theme/icons`；开关关着时没有这一项
- [x] 面板照 `PastConversations`：宽屏 `Dialog`、窄屏 `Drawer`，重新注入她的颜色；四行加「集齐」一行，每行用票 01 的行写法函数；集齐了是勾和「集齐了」
- [x] 看过记在这台设备上，和每日任务的导航点同一套做法（按用户、按范围存看过的最近解锁时刻；存储不可用退回内存；没存过时都算没看过）；哪一行带点、菜单键亮不亮收进 `lib/` 纯函数
- [x] 更多菜单键右上角的点、菜单项后面的点：她的最近解锁时刻晚于看过的就亮；对话页打开时读她的成就；回合返回带着她的阶段时当场亮；外壳轮询读到 `rewarded_at` 变新时正开着的对话页重读
- [x] 面板里的点：打开时解锁时刻晚于打开前看过的那几行带点；打开就记成看过，点保留到面板关上
- [x] 奖杯：回合返回带着这位角色的阶段、通话层没盖着页首时，好感度标识左边跳出奖杯和「+n」（这次她的阶段到账的抽奖次数），约 3 秒收起；照 `AffinityBadge` 变化提示的做法（`LiveConversation` 快照给出带 key 的一次性提示，过一段时间清掉）；减少动态时只淡入淡出；通话层盖着时不跳只留点；别的角色的、合起来的成就不跳
- [x] 读屏：奖杯用礼貌的 live region 读「成就，抽奖 +n」；带点的更多菜单键读「更多，有新解锁的成就」
- [x] 隐藏的角色照样在她的对话页里看得到
- [x] 照 `frontend/AGENTS.md` 的「shadcn 纪律」；`DESIGN.md` 补上成就面板、奖杯和更多菜单上的点
- [x] 前端测试：点的亮灭（看过以后不亮、新解锁又亮、存储不可用退回内存、没存过时都亮）、面板里哪几行带点；奖杯给一次带 key 的提示后清掉、别的角色的和合起来的不给、通话层开着不给（照 `liveConversation.test.ts` 的 `FakeCall` 和假 ports）
- [x] 本地构建版上用 Playwright 验（`docs/agents/local-acceptance.md`）：面板宽屏和手机的样子、回合里解锁时奖杯跳一下、菜单和面板上的点、打开后消失；状态能用接口或改库造就自己造

## Comments

### 票 01 交接

- `lib/achievements.ts` 已有 `achievementRow`、`completeRow` 和类型 `HerAchievements`（接口 `GET /api/characters/{id}/achievements` 的形状，开关关着为 null）；`Rewarded.achievements` 的一项是 `lib/rewards` 的 `UnlockedStage`（合起来的 `character_id` 为 null）。读接口的函数还没写。
- `completeRow` 给 `isComplete`、`name`（「集齐」/「集齐了」）、`reward`（「重置卡 ×1」；集齐了、或这位的重置卡已经给过时为 null）；行的 `stages` 是阶段点（解锁的为 true）。

### 实现说明（票 05）

- 新模块 `lib/seenOnDevice`：按键存「看过的最近时刻」（`seenOnDevice`、`keepSeenOnDevice`），连同 `isUnseen`、`later`；`lib/rewards` 的礼物点和成就的点都用它，`later` 从 `lib/rewards` 搬过来了。
- `lib/achievements` 加 `getHerAchievements`、`achievementsSeenUpTo`/`keepAchievementsSeenUpTo`（键 `eidolon.achievementsSeen.<用户 id>.<范围>`）、`latestUnlock`、`unseenRows`（集齐那一行的 id 是 `COMPLETE`）。
- `LiveConversation` 快照加 `trophy`（`{ draws, key }`，`TROPHY_MS` 3 秒后清掉；通话层展开时不给）和 `herUnlockedAt`（这一页的回合解锁她的阶段时那次到账的时刻，让点当场亮）。
- 对话页：`useHerAchievements`（读她的成就，外壳 `rewardedAt` 或 `herUnlockedAt` 变了就重读；管看过和面板里的点），`AchievementsPanel`（宽屏 Dialog、窄屏 Drawer），页首奖杯 `TrophyCue`（`animate-trophy`，减少动态时 `animate-trophy-fade`），读屏走一个 sr-only 的 `role="status"`。图标 `AchievementsIcon`（`Trophy`）。
- 判断题：更多菜单键带点时读屏读「更多操作，有新解锁的成就」，跟着原来的读屏名字「更多操作」，没照规格原文写「更多，…」。阶段点和面板里的点另加了读屏文字（「解锁了 n/m 个阶段」「新解锁」）。面板开着时又解锁的，当场记成看过、那一行照样带点。
- 已用 Playwright 在本地构建版上验过：宽屏对话框、手机底部面板、真回合解锁时奖杯跳出、读屏文字、菜单键和菜单项的点、打开面板后消失、库里加阶段后外壳轮询让点重新亮。
