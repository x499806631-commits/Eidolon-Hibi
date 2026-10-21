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

## 打开方式

`.env` 里按需加：

```
EIDOLON_REACH_OUTS=1      # 搭话（节日和生日早上的那句要用到）
EIDOLON_LETTERS=1         # 来信
FISH_AUDIO_MODEL=drama-3  # ASMR 推荐
```

「🎲 自动」环境音需要配置判断模型（`JUDGE_PROVIDER`）。

## 其他改动

- `docker-compose.yml` 的端口只绑在本机（`127.0.0.1:3000`），个人部署更安全。
