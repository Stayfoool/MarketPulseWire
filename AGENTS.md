## 项目计划

- 主 living plan：`docs/monitoring-plan.md`。
- 当前路线图、实施状态、待定事项、风险和验证缺口都维护在这个文件里。
- 不要把活跃 TODO 分散到多个计划文档；如果以后需要深度参考文档，从主计划里链接过去。
- 下载外部工具时遵循全局下载策略：优先官方源；官方 GitHub release 因网络失败时，可使用华为云、清华、阿里等广泛使用的第三方镜像，并记录官方来源和镜像来源。

## GitHub 工作流

- 今后默认不要直接向 `main` 推送普通代码、文档或配置修改。
- 新改动应从最新 `main` 新建 `codex/` 前缀分支，提交后推送分支并创建 Pull Request。
- 等 GitHub Actions / CI 必需检查变绿后，再把 PR merge 到 `main`。
- 紧急生产 hotfix 只有在用户明确要求快速直推、或线上服务需要立即恢复时才可绕过 PR；直推前必须先跑本地相关测试，并在事后说明绕过原因。

## 信息源演进与主干一致性

- 新增或调整信息源时，默认只扩展采集适配、正文获取、来源健康和必要富化；最终条目必须尽量归一化为 `NormalizedMarketItem`，进入现有 `market_flow -> decision_engine -> market_interpreter -> review_store -> delivery/view` 主干。
- `source_category`、`publisher_role`、`content_type` 用于采集、展示、存储适配和审计，不得仅凭这些字段或来源名称把内容判为重要或不重要。
- 持仓/关联关键词、重点主题、硬变量、宏观政策、去重和其他通用规则应优先设计为跨来源规则。同一内容只改变来源元数据时，通用决策结果原则上应保持一致。
- `DecisionResult.action` 是唯一推送权威。不得为新来源增加第二个最终 action、独立 push 布尔值或由 LLM 自行覆盖确定性规则的入口。
- LLM 可以做受限抽取、跨句关系识别和薄解读，但输出必须回验原文证据；LLM 不得直接决定 importance/action，解析失败也不得改变已经成立的确定性规则结果。
- 信息源特殊性应尽量限制在采集边界，例如 API/RSS、登录/WAF、浏览器 profile、thread/reply、媒体附件、第一页 OCR、基线和轮询频率。特殊采集完成后应重新回到公共数据契约和决策层。
- 只有来源确实存在无法由公共主干表达的语义或投递要求时，才允许保留独立路径。例外必须在 `docs/architecture-flow.md` 和 `docs/monitoring-plan.md` 记录原因、准确边界、仍复用的公共层、测试要求和未来复核条件；不得因为旧脚本或旧表已经存在就继续扩展平行架构。
- 不为单一新来源预建通用框架、独立缓存表、第二套 review store 或新的常驻服务。优先复用现有 source profile、collector runtime、规则中心、存储适配和 delivery；只有真实需求证明现有结构不足时才增加抽象。
- 每次新增来源或调整通用规则，至少验证：规范化样例、同文跨来源一致性、来源特有失败状态、去重、`DecisionResult.action`、存储/投递审计，以及生产私有配置未被 Git 或部署覆盖。
