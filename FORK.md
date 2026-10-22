# 这个 fork 加了什么

这是 [Sallyn0225/eidolon-sc](https://github.com/Sallyn0225/eidolon-sc) 的个人 fork，基于原仓库的 `d1f6bfd4`，
改动都在 `my-features` 分支上。原作者计划把主仓库精简、功能拆成插件（[#168](https://github.com/Sallyn0225/eidolon-sc/issues/168)），
这里的功能都尽量写成了独立的模块，等插件仓库开放后可以改成插件提交。

每个功能都按原项目的规矩做：`GLOSSARY.md` 和 `DESIGN.md` 写了新概念和新界面，后端和前端都有测试，
shadcn 纪律检查通过，前后端全部测试通过。

## 功能一览

| 功能 | 怎么打开 | 主要的文件 |
|---|---|---|
| 日历和节日 | 有 `characters/calendar.toml` 就有 | `special_days.py`、`special_days_api.py`、`characters/calendar.toml`、`components/calendar/`、`lib/calendar.ts` |
| ASMR 模式通话 | 对话页「更多」菜单 | `engine.py`（`asmr_rules`）、`call.py`、`lib/characterVoice.ts`（贴耳音效） |
| 环境音和「🎲 自动」 | 通话底栏的环境音键 | `ambience.py`、`lib/ambience.ts`、`components/conversations/AmbienceKey.tsx`、`public/ambience/` |
| 偶像来信 | `.env` 里 `EIDOLON_LETTERS=1` | `letters.py`、`letters_api.py`、`components/letters/`、`lib/letters.ts` |
| 日语里不混中文 | 默认 | `reply_format.py` |
| 她的心（每位偶像自己的好感度档案） | 有 `characters/hearts/{id}.toml` 就有 | `hearts.py`、`characters/hearts/`、`affinity.py`、`affinity_judge.py` |
| 偶像日记 | `.env` 里 `EIDOLON_DIARY=1` | `diary.py`、`diary_api.py`、`components/diary/`、`lib/diary.ts` |
| 行程 | `.env` 里 `EIDOLON_SCHEDULES=1` | `schedules.py`、`schedule_writer.py`、`schedules_api.py` |
| 重要的一幕 | 默认（要好感度） | `affinity.py`（`scene_prompt`、`affinity_scenes`）、`characters/hearts/` 的 `[scenes]` |
| 「我不是这个意思」 | 好感度下降的胶囊下面 | `appeals.py`、`appeals_api.py`、`lib/appeals.ts` |
| 天气 | `.env` 里填坐标 | `weather.py` |
| 番茄钟「一起专心」 | 对话页「更多」菜单 | `components/conversations/FocusSetup.tsx`、`FocusTimerBar.tsx`、`lib/focusTimer.ts` |
| 纪念日 | 默认 | `anniversaries.py` |
| 约定 | 默认（要记忆） | `promises.py`、`promise_judge.py`、`promises_api.py` |
| 联网 | 对话页「更多」菜单，按角色开关 | `web_search.py`、`search_judge.py`、`lib/webToggle.ts` |
| 她们的经历 | 设置 →「她们的经历」 | `experiences.py`、`experiences_api.py`、`components/ExperiencesSettings.tsx` |

### 日历和节日

导航多了「日历」页：月历上标出 28 位偶像的生日（铺她的印象色）、制作人自己的生日和节日（小图标），
周末和今天高亮，下面「这个月」列出每件事，偶像生日那行可以直接「去找她」。生日和节日写在
`characters/calendar.toml`（农历节日按年份写日期），后端 `/api/calendar?year=` 读出来。

偶像在这些日子会知道今天是什么日子，聊到合适的时候自然提起；打开搭话（`EIDOLON_REACH_OUTS=1`）时，
制作人生日、她自己的生日和一天的节日，她会在当天早上先来说一句。

### ASMR 模式通话

对话页「更多」菜单里的「ASMR 模式」。她贴着耳边轻声慢慢地说，聊什么都可以：提示词里加了 ASMR 的说法，
用 Fish Audio 的 drama-3 时会带上 `<whisper>`、`<slow>` 这类配音标签。浏览器端把她的声音处理成贴耳的效果
（低频加暖、高频加空气感、轻压缩、左右缓慢飘动）。

### 环境音和「🎲 自动」

通话底栏多了一个环境音键：雨声、雷雨、暖炉、海浪、夏夜、书房、咖啡店，三档音量；她说话时自动压低，
换场景交叉淡入淡出，循环无缝。ASMR 通话接通时自动放上次选的场景。音频都是 Freesound 上的 CC0 素材，
来源在 `frontend/public/ambience/CREDITS.md`。

选「🎲 自动」时，接通后浏览器发 `ambience-auto` 客户端消息，后端用判断模型（一道 Choice 题）按制作人那边的
时间、季节和最近的聊天挑一类，之后每 4 个回合再看一次，明显更合适才换（`ambience` 服务器消息）。

### 偶像来信

导航多了「信箱」：偶像会给制作人写信，有称呼、正文和落款，日语原文配中文翻译，信纸是她的印象色。

- 平时偶尔来信：每天按好感度档位抽一次签，同一位 5 天内最多一封。
- 制作人生日：前一晚写好，零点送到。
- 她自己的生日、日历里一天的节日：晚上送到，讲这一天是怎么过的（节日的名字和说明取自 `calendar.toml`）。
- 信都是提前写好、到时间才出现；每位用户每天最多 3 封；有未读时导航上有小圆点。
- 之后两周里聊天时，她知道自己写过什么信、制作人看没看过。

写信用部署者的聊天模型，和搭话一样不计入用户的限额，每封有一条运行记录（`letter`）。
开发登录下可以用 `POST /api/letters/write-now` 立刻写一封来试。

### 她的心

每位偶像一份好感度档案，放在 `characters/hearts/{角色 id}.toml`：什么特别让她开心、什么会让她受伤
（判断模型评判好感度时参考，但不会因为说中一条就判成最高或最低），以及每一档好感度她是什么样子。
已经写了大崎甘奈、大崎甜花、铃木羽那、黛冬优子四位，资料参考萌娘百科（CC BY-NC-SA 4.0）。

成年偶像在「亲近」「亲密」两档可以有恋爱的心动，按制作人的性别分开写（女制作人是百合）；性方面的要求仍然拒绝。
**未满 18 岁的偶像永远不会是恋爱**：档案里写了恋爱也会被拒绝加载，亲近只是信赖和依赖。

### 行程

最近聊过的偶像，每天早上会有一份新写的「大概的安排」：三到六段，留着空白，每天抽一两个小变数（临时加练、下雨改到室内……），
所以不会被摸透。世界设定里那天有她的 live 就一定排进去。聊天时她知道「现在大概在干嘛」，被问到时自然地说；写日记时也知道那天原本的安排。
对话页她名字下面一行写着她现在大概在干嘛。行程里也有她几点起、几点睡（作息习惯写在 `characters/hearts/` 的 `sleep`）：
睡着时找她，她会迷迷糊糊地回；睡着时打电话，铃声响完她会先接起来开口。
界面上暂时看不到行程；开发登录下可以用 `POST /api/schedules/write-now` 让她立刻排一份，`GET /api/characters/{id}/schedule` 看。

### 重要的一幕

好感度第一次走到「亲近」「亲密」门口时，先停在门口，开始她的「重要的一幕」：她会（搭话里）约你好好聊聊，
接下来 6 个回合是这一幕——第一轮她把藏在心里的话说出口，最后一轮好好收尾，然后才升档，那天的日记一定会写它。
没有失败，离门口远了就先等着。成年偶像在「亲密」门口可以告白（女制作人是百合），未成年偶像是信赖的谈话；
每位偶像怎么开口写在 `characters/hearts/` 的 `[scenes]` 里。

### 「我不是这个意思」

判断模型也会看错。她最新的那条回复让好感度降了时，变化胶囊下面有一个「我不是这个意思」：
点了以后这一回合扣的分退回来，也不再算进她结束对话、挂电话的账里；你下一句话时，她会知道你说刚才是开玩笑或她误会了。
每条回复一次，每天 3 次（所有偶像一起算）；冷却中、对话结束了、电话里说的话都不能用。

### 偶像日记

导航多了「日记」：偶像只在对她来说重要的日子写日记——关系第一次变成「亲近」「亲密」、收到喜欢的礼物、约定做到或落空、
认识满一百天或一年、谁的生日，以及聊天里后台模型认为对她特别重要的一刻。那天过完，她在第二天凌晨写一篇，日语原文配中文翻译。

能看多少按你们现在的关系：冷淡或疏远时日记本合上，信任时只给看开头，亲近能看全文，亲密时连她「没打算给你看的一页」也能看到。
看不到的部分不会发到浏览器。开发登录下可以用 `POST /api/diary/write-now` 让她立刻写一篇来试。

### 天气、番茄钟、纪念日、约定、联网、她们的经历

这一批是朋友（用 DeepSeek）做的，克劳德审阅并修正过（约定不会被失败的回合白白用掉、提示词里不把制作人写成「他」、
网页内容标明不是指令）。

- **天气**：她知道制作人那边现在的天气（Open-Meteo，免费不要密钥），合适的时候提一句；自动环境音也会参考。
- **番茄钟「一起专心」**：自己定专心多久，休息固定 5 分钟，页首下方一条计时条，只在前端。
- **纪念日**：和每位偶像认识满 100 天、满整年的那天，她会自己想起来。
- **约定**：你答应她某一天做的事会被记下，那天她会问起；没做到，之后几天她会抱怨。送了约定里的礼物、
  她问起时你说已经做到了、或者在日历里点「已经做到了」都算兑现。
- **联网**：打开后，她遇到自己感兴趣的、不知道的事会上网查一下（DeepSeek 的搜索接口）；你让她去查她就查。
  通话里不联网。
- **她们的经历**：她在聊天里说过自己做过的事会按角色记下来，相关的话题会让她想起；30 天没被想起就忘掉，
  可以在设置里永久保留或删掉。

## 打开方式

`.env` 里按需加：

```
EIDOLON_REACH_OUTS=1      # 搭话（节日和生日早上的那句要用到）
EIDOLON_LETTERS=1         # 来信
EIDOLON_DIARY=1           # 偶像日记
EIDOLON_SCHEDULES=1       # 行程
FISH_AUDIO_MODEL=drama-3  # ASMR 推荐
# EIDOLON_WEATHER_LAT=    # 天气：你所在位置的纬度和经度，两个一起填（北纬、东经为正）
# EIDOLON_WEATHER_LON=
# EIDOLON_SEARCH_KEY=     # 联网：聊天模型不是 DeepSeek 时，填一把 DeepSeek 的钥匙
```

「🎲 自动」环境音、好感度（她的心、我不是这个意思）、约定和联网的判断都需要配置判断模型（`JUDGE_PROVIDER`）。

## 其他改动

- `docker-compose.yml` 的端口只绑在本机（`127.0.0.1:3000`），个人部署更安全。
