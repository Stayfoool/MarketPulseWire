## 文档职责

- `AGENTS.md` 是长期工程规范的唯一文档真源，只保存稳定、可执行的项目红线。
- `docs/architecture-flow.md` 只描述当前代码和生产结构，不制定原则。若文档与代码/CI 冲突，以代码和生产只读审计为事实，并在同一 PR 修正文档。
- `docs/monitoring-plan.md` 是本机 living plan，只保存当前目标、实施状态、下一步、风险和验证缺口；不保存长期原则或完整历史。该文件可能包含个人生产计划，因此不进入 Git。
- `docs/deployment.md` 保存部署和运维步骤；服务器 Web 面板和服务器私有 `.env` 是生产配置真源。
- Git 历史保存已完成阶段的详细过程，不在 living plan 重复堆积。

## 信息处理不变量

- 除明确例外外，信息源最终必须归一化为 `NormalizedMarketItem`，并进入 `process_market_item -> decision_engine -> market_interpreter -> review_store -> market_delivery -> view`。
- collector 只负责合规采集、技术去重、正文/附件富化、标准化、来源状态和健康记录。collector 不得自行执行单条 review 持久化、规则去重占位或市场信息投递。
- `DecisionResult.action` 是即时推送资格的唯一权威。缺少有效 `DecisionResult` 时必须关闭式失败，不得从 `push_now`、`should_push_now`、`should_push`、importance 或 LLM 输出恢复推送资格。
- 旧 push 字段和旧表只可作为兼容存储、历史展示或派生投影；它们不是正确性真源。`pushed_at`、delivery status 和 dedup state 只记录执行结果，不能创建推送资格。
- 后处理可以把 `push` 降级为 daily/archive/ignore，但不能把非 push 提升为 push。只有决策层可以生成新的 push action。
- 持仓/关联关键词、重点主题、硬变量、宏观政策、归因研究和去重规则应优先设计为跨来源内容规则。同一内容只改变来源元数据时，通用决策原则上保持一致。
- `source_category`、`publisher_role`、`content_type` 只用于采集、存储、展示和审计，不得仅凭分类、内容形态或来源名称判定重要性。
- LLM 可以做受限抽取、关系识别和薄解读，但必须保留并校验原文证据；LLM 不得直接决定 importance/action，解析失败也不得改变确定性规则结果。
- 来源特殊性应限制在 API/RSS/浏览器、登录/WAF、thread/media、附件/OCR、基线、频率和展示等边界。独立路径必须在架构文档中记录原因、最小边界、测试和复核条件。
- 不为单一来源预建插件框架、第二套 decision、第二套 review store、独立缓存表或常驻服务。优先复用现有 source profile、runtime、规则、存储 adapter 和 delivery。

## 测试与变更

- `scripts/test_architecture_invariants.py` 执行统一 collector、禁止调用、兼容模块、来源 profile 和显式例外检查；新增来源不得绕过该测试。
- `scripts/run_test_suite.py` 是 CI-safe 回归测试清单的唯一真源，GitHub CI 和 `Justfile` 只能调用该入口，不得各自维护测试列表。所有 `scripts/test_*.py` 必须且只能分类为 CI-safe 或带真实外部副作用的 operator smoke；未分类、重复或过期条目必须关闭式失败。operator smoke 不进入普通 CI，不得在无明确批准时发送消息、上传媒体或调用生产凭据。
- 新增或实质修改生产 collector/provider 的普通有界 HTTP request/response 时，必须复用 `http_utils` 的线程隔离 client、代理、超时和重试语义；既有未迁移路径作为显式技术债登记，不得继续扩散。流式限长下载、长连接、官方 SDK 和独立运维工具可保留专用传输，但必须在架构不变量中登记具体边界和原因；不得为了形式统一而把流式安全边界改成整包内存缓冲。
- 新增或调整通用规则时，至少提供两个不同来源元数据的同文回归样例，并验证 `DecisionResult.action` 一致。
- 新来源至少验证规范化、停用、空内容/解析失败、重复、来源健康、最终 action、存储和投递审计。
- 私有 `.env`、`LOCAL_COMMANDS.md`、`config/portfolio.json`、SQLite、browser profile、cookie/session 和生产 source override 不进入 Git，也不被部署覆盖。

## GitHub 工作流

- 普通代码、文档和配置修改从最新 `main` 新建 `codex/` 分支，通过 PR 和必需 CI 后 merge，再按部署文档上线并做生产只读验证。
- 不直接向 `main` 推送普通改动。只有用户明确要求紧急恢复生产时才可绕过 PR，并须先运行相关测试、事后记录原因。

## 本机与生产环境边界

- 本机 Mac 只用于代码和文档编辑、Git/PR 操作、只读审计，以及现有环境能够执行的静态检查和单元测试；不在本机运行生产常驻服务或把本机配置视为生产配置。
- 部署、systemd 服务、生产数据库、生产凭据和生产运行依赖以阿里云轻量应用服务器为运行环境；生产配置仍以服务器 Web 面板和服务器私有 `.env` 为真源。
- 不为部署、生产运行或可选便利命令在 Mac 临时安装新的系统工具或 Python 包。`just` 等命令包装器不是项目正确性依赖；本机缺少时直接运行其底层命令，无法执行的依赖型测试交由必需 CI 和阿里云生产环境验证，并明确记录验证缺口。
- 新的项目运行依赖必须写入仓库的依赖清单并通过正常 PR/CI/部署流程安装到目标环境，不得只在 Mac 手工安装后形成未记录的本地隐式依赖。
- 本机测试通过不能替代 GitHub 必需 CI 或部署后的服务器服务账号测试；服务器验证也不得反向修改或覆盖 Web 面板、私有 `.env`、SQLite、浏览器会话等生产真源。

## 下载策略

- 外部数据、模型、论文和工具优先使用作者、维护者或发布方官方来源。
- 官方源因网络或 CDN 问题反复失败时，可使用华为云、清华、阿里等广泛使用的镜像，并记录官方来源、镜像 URL、文件名、版本和校验信息。
- WAF、登录或浏览器挑战阻断官方下载时停止并请求用户协助，不使用异常自动化或不可信镜像绕过。
