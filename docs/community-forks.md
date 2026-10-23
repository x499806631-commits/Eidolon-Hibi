# 社区玩法仓库

主仓库不接 PR，玩法上主要做维护者自己想做的。其他人想要的玩法，在各自的 fork 上做，做好了登记在这里，方便大家找到。

这里收录的仓库由各自的作者维护。收录只是方便大家找到，不代表维护者审过或担保其中的代码；部署前请自己看过代码和配置。

## 全年龄

- **[Eidolon Hibi](https://github.com/x499806631-commits/Eidolon-Hibi)** · 山茶日记：让偶像像在过着自己日子的人：28 位偶像各有一份好感档案，关系走到关键处会停下来和你好好谈一次（重要的一幕）；她有每天的行程、会睡觉、会洗澡，会给你写信、写只给亲近的人看的日记，也记得你答应过她的约定。另有 ASMR 通话、环境音、天气、纪念日等，每项玩法都能在设置里自己关掉。 <!-- #172 -->
- **[eidolon-sc-new](https://github.com/r1ncu/eidolon-sc-new)** · Sallyn0225/R1ncu：**内核重构版** 把原来一体的后端拆成最小内核+功能插件的形式，和咲凌讨论后觉得拆的太碎了，确实，但还是放上来了 1. 纯Vibe coding产物（已在本地跑了两轮交叉验证），故如有各种代码编程上的问题，我无法解决。 2. 在原仓库功能的基础上新增了一个识图功能，需要自行添加API。 偷偷提一嘴，因为角色和角色数据也进行了插件化，你甚至可以制作本家、柏油、灰灰、sidem、初星中专等等的版本，甚至是大杂烩…… 方便各路能人异士使用，添加自己想要的功能的时候即可以插件的形式写成.py文件放入/plugins ** &gt; 感谢周晓玲开源、感谢deepseek v4.1flash、感谢glm、感谢claude但不感谢A÷，感谢小高但不感谢万代 ：D ** <!-- #174 -->

## 含成人内容（NSFW）

还没有收录的仓库。

## 怎么登记

1. fork 本仓库，在 fork 的 **Settings → General** 里把仓库名改掉，不再叫 `eidolon-sc`。改名后旧地址会自动跳转，已有的 clone 照常能推拉。
2. 在自己的仓库里做你的玩法。
3. 用 [玩法仓库登记](https://github.com/Sallyn0225/eidolon-sc/issues/new?template=fork_showcase.yml) 模板开一个 issue，填上仓库地址、项目名、署名、玩法简介和内容分级。
4. 开好后会自动检查一遍：仓库能不能打开、是不是公开、改没改名，结果写在 issue 的评论里。没通过的话改好后直接编辑 issue，会再查一遍。
5. 维护者看过后收录，issue 会自动关闭。之后地址变了、想更新简介或不想再被列出，在原 issue 下留言。

## fork 前要知道的

- **许可证**：本项目是 AGPL-3.0-or-later，修改后通过网络提供给别人用，就要向这些用户公开修改后的源码。通话立绘用的 Spine Runtimes 适用它自己的许可证（[ADR-0009](adr/0009-spine-runtime-without-editor-license.md)）。
- **素材**：剧情数据、角色音色和立绘素材不随仓库分发，也不要提交进你的公开仓库（[ADR-0003](adr/0003-corpus-and-voices-stay-private.md)）。
- **公开**：公开仓库的 fork 只能是公开的，不能单独改成私有。
- **Issues**：fork 默认关着 Issues。想让别人在你的仓库里反馈问题，到 **Settings → General → Features** 里勾上 Issues。
- **搜索**：GitHub 的仓库搜索默认不显示 fork，所以别人很难自己搜到你的仓库，这也是这份列表的用处。
- **跟进上游**：fork 页面上的 **Sync fork** 可以把主仓库的更新合进来。
- **脱离 fork**：在 **Settings → General → Danger Zone → Leave fork network** 里可以把 fork 变成独立仓库（要求公开、小于 1 GB、没有下级 fork）。这一步撤不回，之后不能再用 Sync fork，合并上游更新要自己用 git 拉。
