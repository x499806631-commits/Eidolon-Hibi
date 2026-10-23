# 参与贡献

这是一个人维护的项目，维护者没有余力审阅外部代码，所以**仓库不接受 Pull Request**，这个功能对外是关闭的。玩法上，主仓库主要做维护者自己想做的；其他人想要的玩法，请在自己的 fork 上做。

下面这些方式欢迎参与：

- **报告 bug、对现有功能提建议**：用 [Issues](https://github.com/Sallyn0225/eidolon-sc/issues/new/choose) 里的模板。部署和配置遇到问题，先查 [docs/deploy.md](docs/deploy.md) 和 [docs/configuration.md](docs/configuration.md)。
- **安全问题**：不要开公开 issue，按 [SECURITY.md](SECURITY.md) 私下报告。
- **做新玩法**：fork 一份、改个名字，在自己的仓库里做，做好了用 [玩法仓库登记](https://github.com/Sallyn0225/eidolon-sc/issues/new?template=fork_showcase.yml) 模板登记，会收进 [社区玩法仓库](docs/community-forks.md)。
- **自己改**：欢迎 fork。动手前留意三件事：
  - 本项目是 AGPL-3.0-or-later：修改后通过网络提供给别人用，就要向这些用户公开你修改后的源码（[LICENSE](LICENSE)、[LICENSING.md](LICENSING.md)）。
  - 通话立绘用的 Spine Runtimes 适用它自己的许可证。按字面理解，修改或开发包含 runtime 的产物的人，都需要有效的 Spine Editor 许可证（[ADR-0009](docs/adr/0009-spine-runtime-without-editor-license.md)）。
  - 剧情数据、角色音色和立绘素材不随仓库分发，也不要提交进你的公开 fork（[ADR-0003](docs/adr/0003-corpus-and-voices-stay-private.md)）。

本地开发怎么跑，见 README 的「开发」一节。

---

**English:** This is a solo-maintained project and pull requests are not accepted. New gameplay in this repository follows the maintainer's own plans. Bug reports and suggestions for existing features are welcome via [Issues](https://github.com/Sallyn0225/eidolon-sc/issues/new/choose). If you want other gameplay, build it in your own renamed fork and register it with the [fork showcase template](https://github.com/Sallyn0225/eidolon-sc/issues/new?template=fork_showcase.yml) to be listed in [community forks](docs/community-forks.md) (Chinese); report security problems privately as described in [SECURITY.md](SECURITY.md). Forks are welcome under the AGPL; note the separate Spine Runtimes license ([ADR-0009](docs/adr/0009-spine-runtime-without-editor-license.md)) and keep game content out of public forks ([ADR-0003](docs/adr/0003-corpus-and-voices-stay-private.md)).
