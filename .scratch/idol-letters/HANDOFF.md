# 交接：偶像来信（给接手的 AI 助手）

你好！你要接手的是制作人小姐本地自用的 eidolon-sc（偶像大师 闪耀色彩 AI 偶像聊天/通话，
FastAPI + Pipecat 后端，Next.js 16 前端，Docker Compose）。前面的功能（日历和节日、搭话、ASMR 模式、
贴耳音效、环境音和「自动」）都是 Claude 做的，已经全部通过测试。这份文档说明：环境怎么用、项目规矩、
偶像来信要做成什么样、分几步做、每步怎么验收。

**先读这几份再动手**（都在仓库里）：`AGENTS.md`、`CODING_STANDARDS.md`、`GLOSSARY.md`（术语）、
`frontend/AGENTS.md`（shadcn 纪律）、`DESIGN.md`（视觉规范，前端改动以它为准）、
`docs/agents/implementing.md`。参考实现：`backend/src/eidolon/reach_outs.py`（搭话：后台定时、
时区、夜里不打扰、特别的日子）和 `special_days.py`（日历）。

---

## 0. 工作环境（很重要）

- 制作人小姐用 **Windows 11 + Git CMD + Docker Desktop**。仓库在 `C:\Users\Aa499\eidolon-sc`。
- 如果你不能直接在她电脑上跑命令：每次给出**完整的新文件**或**清楚的补丁**（写明文件路径、改哪几行），
  然后请她跑测试，把输出截图给你。一次只做一小步，测试通过再进下一步。
- 她会把命令**整行**粘贴到 Git CMD；不要给需要 Linux shell 的命令（`&&` 可以，`rm`/`grep` 不行，
  Windows 用 `findstr`）。
- **测试命令**：
  - 后端（在 `backend` 文件夹）：
    `uv run pytest -q`（全部；也可以只跑相关文件 `uv run pytest tests/test_letters.py -q`）、
    `uv run pyright src tests`、`uv run ruff check`
  - 前端（在 `frontend` 文件夹）：`npm run typecheck`、`npm test`
  - 跑起来看效果（在仓库根目录）：`docker compose up --build`，浏览器开 http://localhost:3000
  - 看后端日志：`docker compose logs backend --since 10m | findstr /i letter`
- **安全**：绝对不要让她把 API key 或 `.env` 的内容发给你。要改 `.env` 时，只告诉她加哪一行
  （例如 `EIDOLON_LETTERS=1`），让她自己用记事本加。项目只在本机用，`docker-compose.yml` 的端口
  已绑定 `127.0.0.1`，不要改成对外开放。
- **动手前先存档**（请她跑一次，之后每完成一步再提交一次，出问题可以退回）：
  ```
  git checkout -B my-features
  git add -A
  git commit -m "存档：环境音自动挑选之前的全部改动"
  ```
  不要 `git push`，不要提交 `.env`（它在 `.gitignore` 里，`git add -A` 不会加进去，但还是确认一下）。

## 1. 项目规矩（违反了测试会挂）

**后端**
- 测试用仓库里现成的假件：`tests/fakes.py` 的 `FakeLLM`（按顺序吐出预设回复）、`build_faked`、
  conftest 的 `make_client`；时间用 `clock=lambda: NOW` 这类可替换时钟（`eidolon/clock.py`），
  随机数用 `eidolon/randomness.py` 的 `Random`，测试里用 `FakeRandom`，**抽了几次随机数要和测试写的一致**。
  先读 `tests/test_reach_outs.py` 学它的写法。
- 新表加在 `store.py` 的 schema 里，旧数据库靠 `Store._migrate` 补上（照着里面已有的写法加，
  `CREATE TABLE IF NOT EXISTS` 即可）。
- 模块、类、函数都写 docstring，风格照现有代码（英文 docstring，用户可见的文字是中文）。
- pyright 是严格的：`Literal` 类型的字典要显式标注（例如 `read: dict[Kind, float] = {...}`），
  否则会被推成 `dict[str, float]` 报错。

**前端**
- 只用 shadcn 组件（`components/ui/`），**不许写任意颜色**（`text-[#...]`、`bg-red-500` 都不行），
  颜色用 `globals.css` 里注册的主题色；新颜色要注册到 `@theme`，并在 `themeContrast.test.ts` 里加对比度测试。
  调用组件时 `className` 只放布局（间距、网格、宽度）。
- 图标只从 `src/theme/icons.ts` 导出 Lucide 别名（例如加 `Mail as LettersIcon`），不要在组件里直接 import lucide。
- **所有界面文字**写在 `src/i18n/zh.ts`；文字是函数的话，测试会用 `("甲","乙","丙")` 调用它，
  **返回值必须包含「甲」**（也就是第一个参数一定要用上）。
- 改完前端在仓库根目录跑 `sh scripts/check-discipline.sh`（Git CMD 里可以用 `bash scripts/check-discipline.sh`），
  要看到「shadcn 纪律检查通过」。
- `DESIGN.md` 是规范：新页面、新组件要在里面补一段描述。「她的颜色只出现在…」那条规则
  （The Her-Colour-Only-Here Rule）如果信纸要用她的印象色，**先改这条规则**再写代码。
- 导航项在 `src/lib/navShell.ts`（有测试 `navShell.test.ts`）和 `components/app/AppNav.tsx`，
  日历页（`app/(app)/(nav)/calendar/page.tsx` + `components/calendar/CalendarPage.tsx`）是最近加的，
  照它的样子加「信箱」页最省事。

## 2. 功能说明：偶像来信

偶像们偶尔会给制作人**写一封信**，放进应用里的「信箱」。和「搭话」（聊天框里短短一句）不同，
信是**长一点、更用心、可以反复看的东西**：有称呼、正文、落款，像手写信。

### 2.1 什么时候来信
- **特别的日子**（用 `special_days.py`，已有 `greets`、`birthdays_on`、`holidays_on`）：
  - 制作人生日（档案里填的）：和她关系够近的偶像各写一封生日信（当天早上送到）。
  - 偶像自己的生日：她写一封「谢谢你陪我过生日」的信（如果当天有聊过天，信里可以提到）。
  - 一天的节日（圣诞、新年、情人节等，看 `characters/calendar.toml`）：关系最近的 1～2 位写贺卡式的短信。
- **平时**：每位偶像大约每 7～14 天最多一封，随机抽时间（用 `Random`），只给最近 30 天内聊过天的偶像，
  好感度越高越容易写（好感度关了就用信任度，照 `reach_outs.py` 的 `_tier`）。
  冷却中、冷淡、对话被她结束的不写。
- **不打扰**：信只在制作人时区的白天送达（夜里到期的推到第二天早上，照 `reach_outs.py` 的 `NIGHT`/`MORNING`）。
  每个用户每天最多收 3 封。后端没开着时错过的就算了，不补。
- 有总开关 `EIDOLON_LETTERS=1`（照 `EIDOLON_REACH_OUTS` 的做法放进 config/live_config）。

### 2.2 信的内容
- 用部署者的聊天模型（`.env` 里是 DeepSeek），和搭话一样，不算用户的额度。
- 提示词里给：她的角色设定（character sheet）、对制作人的记忆（`Memories.for_reaching_out`，见
  `engine.reach_out`）、最近几次对话的大意、今天是什么日子（如果有）、现在的季节和时间、好感度阶段。
- 格式：**日语原文 + 中文翻译**，和聊天消息一致（仓库的 `<ja>…</ja><zh>…</zh>` 分段格式，
  看 `reply_format.py`）。信不需要语气标签（`[whispering]`、`<whisper>` 这些是给配音用的，信里去掉）。
  - 长度：平时 200～400 个日文字；节日贺卡 100～200 字；生日信可以长一点。
  - 结构：开头称呼（按档案里的称呼）、正文、结尾落款（她的名字，可以加日期）。
  - 要像她本人写的：口癖、对制作人的称呼、她会关心的事。不要每封都一个模板。
- 写好后在**对话里留一条记录**（照 `conversation_events` 或搭话的做法），让她之后聊天时知道
  「我前几天给制作人写过信」，制作人提起时能接上话。

### 2.3 信箱页面
- 导航加一项「信箱」（图标 Lucide `Mail`），有未读时右上角一个小圆点（照对话列表「没看过的搭话」圆点）。
- 列表：每封一行——她的头像、她的名字、信的第一句（单行截断）、日期；未读的名字加粗、带圆点。
  按时间倒序。没有信时一个 `Empty`：「还没有收到信」。
- 打开一封：信纸样式的卡片（可以用她的印象色做淡淡的信纸底色和边框，注意先改 DESIGN.md 的规则），
  日语原文，下面或切换显示中文翻译（照聊天里字幕的显示方式），右下角落款。打开即标为已读。
  下面一个按钮「去找她」去她的对话页（和日历页的「去找她」一样）。
- 手机和电脑都要能看（日历页的布局可以照抄）。

### 2.4 回信（第二步再做，先不做）
- 制作人可以在信下面写回信；她下一封信会回应回信的内容，或者在聊天里提到「收到你的回信了」。

## 3. 分步实施（每步测试通过、她确认后再下一步，每步结束请她 `git commit`）

1. **数据和接口**：`letters` 表（id、user_id、character_id、occasion〔birthday/her_birthday/holiday/
   ordinary〕、ja、zh、segments、created_at、read_at）；`Store` 的增、列、读、标已读、未读数；
   `letters_api.py`：`GET /api/letters`、`GET /api/letters/{id}`（顺便标已读）、`GET /api/letters/unread`；
   在 `app.py` 注册路由（照 `special_days_api.py` / `add_calendar_routes`）。测试：`tests/test_letters_api.py`。
2. **写信**：`engine` 里加 `write_letter(conversation, llm, occasion)`（照 `reach_out` 写提示词），
   测试用 `FakeLLM` 断言提示词里有记忆、日子、称呼，输出存成信。
3. **什么时候写**：`letters.py` 里的定时器（照 `ReachOuts` 的 tick，但简单些：每天早上为每位用户算一次
   今天谁写、几点送），在 `assembly.py` 里接上，受 `EIDOLON_LETTERS` 开关控制。测试用假时钟/假随机数
   覆盖：生日、节日、平时间隔、夜里推迟、每天上限、冷却中不写。
4. **前端信箱页**：`lib/letters.ts`（+ 测试）、`app/(app)/(nav)/letters/page.tsx`、
   `components/letters/LettersPage.tsx`、导航项和未读圆点、`zh.ts` 文案、`icons.ts` 图标、`DESIGN.md` 描述。
5. **验收**：`docker compose up --build`，为了马上看到效果，可以临时加一个只在开发登录下可用的
   「现在就写一封」后端接口，或者在测试里验证；看完记得删掉或保持只在 `EIDOLON_DEV_LOGIN=1` 时可用。
6. （以后）回信。

## 4. 做完后
- 请她把 `.scratch/idol-letters/` 里的这份文档留着，在下面「进度」里记下做到哪一步，方便之后 Claude 接着做。

## 进度
- [x] 1 数据和接口
- [x] 2 写信
- [x] 3 定时
- [x] 4 信箱页
- [ ] 5 验收

（2026-10-21 Claude 已做完 1～4：特别日子的信提前写好定时送，生日信零点送到，节日信晚上送到、讲当天怎么过的；开发登录下有 `POST /api/letters/write-now` 立刻写一封。剩下：回信。）
