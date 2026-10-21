# 给 DeepSeek 的开发指南：eidolon-sc 新功能

你好！这个项目是 eidolon-sc（偶像大师 闪耀色彩 AI 偶像聊天/通话，FastAPI + Pipecat 后端，
Next.js 16 前端，Docker Compose），只在主人自己电脑上用。项目已经装了一个功能补丁
（日历和节日、ASMR 通话、环境音和「🎲 自动」），在它的基础上，下面有三个适合你做的新功能。
**一次只做一个功能，每个功能分小步做，每步测试通过再进下一步。**

## 0. 开工前

1. **先读这些文件**（都在仓库里）：`AGENTS.md`、`CODING_STANDARDS.md`、`GLOSSARY.md`、
   `frontend/AGENTS.md`（shadcn 纪律）、`DESIGN.md`（视觉规范）。
   参考已有实现：`backend/src/eidolon/special_days.py`（日历）、`backend/src/eidolon/ambience.py`
   （环境音自动挑选）、`backend/src/eidolon/reach_outs.py`（后台定时任务的写法）、
   `frontend/src/components/calendar/CalendarPage.tsx`（新页面的写法）。
2. **请主人先存档**（出问题可以退回）：
   ```
   git checkout -B my-features
   git add -A
   git commit -m "存档"
   ```
   不要 `git push`。
3. **安全**：绝对不要让主人把 API key 或 `.env` 的内容发给你。要加配置时只告诉他加哪一行
   （例如 `EIDOLON_WEATHER_LAT=31.23`），让他自己用记事本加。不要把 `docker-compose.yml` 的端口改成对外开放。
4. **你大概不能直接运行命令**：每次给出**完整的新文件**，或者写清楚「哪个文件、把哪几行换成什么」的补丁，
   然后请主人跑测试并把输出贴给你。他用 Windows 的命令行，命令要能整行粘贴（可以用 `&&` 连起来）。

## 1. 测试命令

- 后端（在 `backend` 文件夹）：
  `uv run pytest -q`（全部，几分钟）或 `uv run pytest tests/test_xxx.py -q`（只跑一个文件）；
  `uv run pyright src tests`（类型检查，必须 0 errors）；`uv run ruff check`
- 前端（在 `frontend` 文件夹）：`npm run typecheck`、`npm test`
- 前端规范检查（仓库根目录）：`bash scripts/check-discipline.sh`，要看到「shadcn 纪律检查通过」
- 跑起来看（仓库根目录）：`docker compose up --build`，打开 http://localhost:3000

## 2. 项目规矩（违反了测试会挂）

**后端**
- 测试用 `tests/fakes.py` 里的假件：`FakeLLM`（按顺序吐出预设回复，回复用完会报错）、`FakeClock`、
  `FakeRandom`（**抽几次随机数必须和测试给的数字个数一致**），以及 conftest 的 `make_client`。
  先读一个现成的测试文件（如 `tests/test_letters.py` 如果有，或 `tests/test_reach_outs.py`）照着写。
- 时间一律用注入的 `clock`（`eidolon/clock.py`），不要直接 `datetime.now()`。
- 访问外网用 `httpx.AsyncClient`，要有超时；测试里用 `httpx.MockTransport` 假装外网，**测试不许真的联网**。
- pyright 严格：`Literal` 类型的字典要写类型标注（`x: dict[Kind, float] = {...}`）。
- 新的数据库表：在 `database.py` 的 `USER_DATA`（属于某个用户的）或 `NOT_USER_DATA` 里登记，否则 `test_database.py` 会挂。
- 写 docstring（英文），用户看得到的文字用中文。

**前端**
- 只用 `components/ui/` 里的 shadcn 组件；**不许写任意颜色**（`text-[#..]`、`bg-red-500` 都不行），
  用 `globals.css` 里注册的主题色。组件的 `className` 只放布局（间距、宽度、网格）。
- 图标只从 `src/theme/icons.ts` 导出 Lucide 别名再用。
- **所有界面文字写在 `src/i18n/zh.ts`**，不能是空字符串；文字是函数的话，测试会用 `("甲","乙","丙")` 调用，
  **返回值必须包含「甲」**（第一个参数一定要用上）。
- 新页面、新组件要在 `DESIGN.md` 里补一段描述。

## 3. 功能 A：真实天气（推荐先做）

她知道制作人那边现在的真实天气，聊天时自然提起；环境音「🎲 自动」也参考天气（下雨就更可能放雨声）。

- **配置**：`.env` 加 `EIDOLON_WEATHER_LAT`、`EIDOLON_WEATHER_LON`（纬度、经度，主人自己查好填），
  不填就没有天气，其它一切照旧。在 `config.py` 的 `Settings` 里加对应字段（照 `EIDOLON_REACH_OUTS` 的读法），
  `.env.example` 里加说明。
- **数据来源**：Open-Meteo（免费、不要密钥）：
  `https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&current=temperature_2m,weather_code&timezone=auto`
  把 `weather_code`（WMO 天气代码）翻成中文：晴、多云、阴、雾、毛毛雨、雨、雪、雷雨等。
- **新模块** `backend/src/eidolon/weather.py`：类 `Weather`，`async def now() -> str | None`，
  返回一句「制作人那边现在是小雨，12°C。」；**缓存 30 分钟**（用注入的 clock 判断），
  请求失败或超时（5 秒）就返回上次的结果或 None，**绝不能让聊天因为天气失败**。
- **接到提示词**：`engine.py` 里每个回合拼「# 此刻」那段时（找 `now_prompt(` 的调用，传入的
  `today_lines`），在末尾加上天气那句。turn 的路径是 async，可以 `await weather.now()`。
- **接到环境音**：`ambience.py` 的 `moment_state` 返回的字典里加一项 `"天气"`（有才加）；
  `_QUESTION` 的说明里加一句「真实天气也可以参考，比如下雨时雨声更合适」。
- **组装**：在 `assembly.py` 里建一个 `Weather`（有配置时），传给 engine 和通话的环境音挑选；
  `Backend.aclose` 时关掉它的 httpx client。
- **测试**：`tests/test_weather.py`——天气代码翻译、缓存 30 分钟内不重复请求、失败时回退、
  提示词里出现天气句、没配置时提示词里没有。

## 4. 功能 B：番茄钟陪伴

主人学习或工作时开着通话或对话页，她每 25 分钟提醒休息 5 分钟。

- 纯前端：对话页「更多」菜单（`ConversationView.tsx` 里已有「ASMR 模式」那一项，照着加）
  加一项「一起专心」，打开后页首下方出现一条小计时条（剩余时间、暂停、结束），用 shadcn 的 `Button`、`Progress`（如果没有就用文字）。
- 到点时在对话里发一句**用户看不到的提示**比较复杂，**第一版只做前端提示**：到点时播放一声提示音
  （可以复用 `public/ambience/pages.mp3`），计时条上显示「她说：休息一下吧～」这类文案（写在 zh.ts，按角色名插值）。
- 时间计算写成纯函数放在 `src/lib/focusTimer.ts`，配 `focusTimer.test.ts`（用 vitest 的假计时器）。
- 记住上次的时长设置（localStorage，读写都包 try/catch）。

## 5. 功能 C：纪念日

制作人和每位偶像第一次聊天的日子，每满 100 天、每满一年，日历上标出来，她当天也会知道。

- **后端**：`store.py` 加一个方法，查每位偶像和用户**最早的一条消息**的日期（`messages` 表按
  `conversations.user_id`、`character_id` 关联，取 `MIN(created_at)`）。
  `special_days.py` 或新模块 `anniversaries.py` 算出某天是不是「认识第 N 天（N 是 100 的倍数）」或「认识 N 周年」。
- **她知道**：`engine.py` 里拼「此刻」那段时（`_special_lines` 附近），当天是纪念日就加一句
  「今天是你和制作人认识的第 100 天。」，并沿用日历那句「聊到合适的时候自然地提起」。
- **日历页**：`/api/calendar` 返回里加 `anniversaries: [{character_id, month, day, label}]`（只算这一年的），
  `lib/calendar.ts` 的 `monthEvents` 加一种事件，`CalendarPage.tsx` 里显示（图标可用 Lucide `Heart`，在 icons.ts 导出）。
- **测试**：纪念日的计算（100 天、一周年、闰年 2 月 29 日）、提示词、API。

## 6. 完成标准

- 每个功能：后端全部测试、pyright、ruff、前端 typecheck 和 test、规范检查**全部通过**，
  `docker compose up --build` 后主人亲眼看过效果。
- 每完成一个功能，请主人 `git add -A && git commit -m "功能名"`。
- 拿不准的设计（文案、时长、要不要某个选项）先问主人，不要自己猜着做大改动。
