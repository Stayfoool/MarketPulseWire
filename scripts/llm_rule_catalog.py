"""Versioned human-reviewed materiality rules for the report-only LLM candidate.

The catalog contains product semantics only. It does not match article text,
call a model, read private configuration, or decide source admission.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from market_item import RuleFamily


LLM_DECISION_RULE_VERSION = "llm-decision-rules-v13-20260724"
# Historical comparison writers/readers still import these exact version names.
# New production decisions record only LLM_DECISION_RULE_VERSION.
RULE_MATRIX_VERSION = "llm-reviewed-rule-matrix-v12-20260724"
CATALOG_VERSION = "llm-rule-catalog-v13"
MODEL_ACTIONS = ("push", "daily", "archive")


@dataclass(frozen=True)
class LLMRuleDefinition:
    rule_id: str
    family: RuleFamily
    title: str
    action_conditions: Mapping[str, str]
    required_facts: tuple[str, ...]
    exclusions: tuple[str, ...]
    version: str = LLM_DECISION_RULE_VERSION

    def __post_init__(self) -> None:
        if not self.rule_id or not self.title:
            raise ValueError("rule_id and title are required")
        conditions = dict(self.action_conditions)
        if not conditions or set(conditions) - set(MODEL_ACTIONS):
            raise ValueError(f"invalid action conditions for {self.rule_id}")
        if any(not str(value).strip() for value in conditions.values()):
            raise ValueError(f"empty action condition for {self.rule_id}")
        if not self.required_facts:
            raise ValueError(f"required facts missing for {self.rule_id}")
        object.__setattr__(self, "action_conditions", MappingProxyType(conditions))

    @property
    def allowed_actions(self) -> tuple[str, ...]:
        return tuple(action for action in MODEL_ACTIONS if action in self.action_conditions)

    def to_prompt_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "action_conditions": dict(self.action_conditions),
            "required_facts": list(self.required_facts),
            "exclusions": list(self.exclusions),
        }


def _rule(
    rule_id: str,
    family: RuleFamily,
    title: str,
    *,
    push: str | None = None,
    daily: str | None = None,
    archive: str | None = None,
    required: tuple[str, ...],
    exclusions: tuple[str, ...] = (),
) -> LLMRuleDefinition:
    conditions = {
        action: condition
        for action, condition in (("push", push), ("daily", daily), ("archive", archive))
        if condition is not None
    }
    return LLMRuleDefinition(
        rule_id=rule_id,
        family=family,
        title=title,
        action_conditions=conditions,
        required_facts=required,
        exclusions=exclusions,
    )


RULES: tuple[LLMRuleDefinition, ...] = (
    _rule(
        "holding_immediate_alert",
        "holding",
        "持仓即时提醒关键词",
        push="命中显式配置且当前有效的即时提醒关键词，并有局部主体证据。",
        required=("已配置即时提醒关键词", "持仓主体", "局部原文证据"),
        exclusions=("不能从普通关联关键词自动继承", "默认空列表不构成命中"),
    ),
    _rule(
        "holding_rating_revision",
        "holding",
        "持仓评级或目标价变化",
        push=(
            "受信投行对持仓作出新覆盖、评级上调或下调、目标价上调或下调，或改变投资建议；"
            "或同一当前研报明确给出目标价和带日期的历史收盘价，按“目标价/历史收盘价-1”"
            "写出未四舍五入的计算结果并据此选择 action，结果大于等于30.0%或小于等于-30.0%。"
        ),
        daily=(
            "受信投行维持或重申既有评级、目标价，或只给当前观点而没有修订；"
            "或同一研报的未四舍五入目标价隐含涨跌幅绝对值低于30.0%且无其他修订；"
            "17.6%和29.9%均低于30.0%，不得选择 push。"
        ),
        archive="只转述历史评级，或者无法验证机构、对象和动作。",
        required=(
            "受信投行",
            "持仓对象",
            "评级、目标价或投资建议动作，或当前目标价及同一研报明确标注的带日期历史收盘价",
            "当前或修订时间",
        ),
        exclusions=(
            "历史转述、未归因观点或只描述市场已有看法",
            "研报为当前或新发布、仅给当前评级或目标价，都不算新覆盖或修订；必须有原文明确的新覆盖、上调/下调或改变建议动作",
            "价格标签或收盘价日期不清、前次/共识目标价、52周区间或外部实时价替代历史收盘价时须返回 uncertain",
            "币种或股类不同、拆并股等公司行动口径不清、评级/建议与计算方向明显冲突时须返回 uncertain",
        ),
    ),
    _rule(
        "holding_material_event",
        "holding",
        "持仓企业实质变化",
        push=(
            "正式业绩重大变化；实质增减资、并购处置或控制权变化；有投资、产能、采购或订单支撑的项目执行变化；"
            "正式订单、采购、约束性供货或首次确认向全球大厂供货。以下股份事项均为push，不设金额或比例阈值，"
            "也不要求已经实施：持仓公司正式公告拟、计划、审议通过、实施、完成、调整、终止或取消回购本公司股份"
            "（包括A股股份）；控股股东、实际控制人、董事、监事或高级管理人员等正式提议由持仓公司回购本公司股份；"
            "控股股东、实际控制人、一致行动人、董事、监事、高级管理人员或其他重要股东正式公告计划、实施、完成、"
            "调整、终止或取消出售或减持该持仓公司股份。回购股份用于维护公司价值或股东权益不构成程序性排除。"
        ),
        daily="普通经营更新；初步讨论或非约束性意向；正式动工但没有投资额、产能、订单或明确采购；其他客户首次确认供货或尚未执行的新进展。",
        archive="财报日期提醒、历史回顾、行情模板、程序性公告、能力宣传、供应名单传闻或普通框架合作。",
        required=(
            "持仓主体",
            "企业变化对象；股份交易还须局部绑定行为主体和该持仓公司自身股份，回购行为主体可以是持仓公司本身",
            "发生状态；股份回购提议或出售、减持计划本身构成当前状态",
            "当前事实和时间",
        ),
        exclusions=(
            "程序性或例行信息，但不得仅因股份回购、出售或减持仍处于正式提议或计划阶段而排除",
            "无约束意向",
            "历史事实",
            "未经确认的供应关系",
            "交易其他发行人的股份",
            "仅因员工离职或考核未达标而例行回购注销限制性股票",
        ),
    ),
    _rule(
        "semiconductor_price_supply_change",
        "semiconductor_ai",
        "半导体或 AI 产业价格和供需变化",
        push="半导体或 AI 产业产品、系统或服务的价格或供需已经发生，或者预计、计划、正在考虑发生明确重大变化；对象、方向以及量级或可比基准具体，足以显著改变收入、成本、需求或竞争预期。价格变化与供需变化可独立成立，不要求已经执行。",
        daily="只有模糊方向，缺少具体对象、重大量级或可比基准，或者变化程度一般；普通价格或供需展望尚未形成重大预期。",
        archive="股票、ETF 或板块行情，历史价格回顾，或者没有产业价格对象的泛涨价表述。",
        required=("产业对象", "价格或供需变化", "方向", "当前事实、具体预期或计划", "重大量级或可比基准"),
        exclusions=("证券行情", "历史回顾", "没有产业对象", "没有具体依据的模糊可能性"),
    ),
    _rule(
        "semiconductor_material_change",
        "semiconductor_ai",
        "半导体或 AI 产业实质变化",
        push="已发生、已确认或进入执行的产能、投资、订单、采购、部署、交付、平台产品或技术路线重大变化，并明确影响关注产业；具名超大规模云厂商或半导体、AI 基础设施核心厂商正式披露、上调、下调、提前、延后或取消重大资本开支，且有明确金额、幅度、前值比较、实际支出或关键产能用途，并绑定 AI 基础设施或半导体关键供需；具名重量级客户正在测试、验证、导入评估具体产品或平台，或明确考虑采用；具名核心厂商或其最高管理层对标志性产品从小规模生产扩大到稳定规模生产，披露明确重大进展或风险信号，包括关键量产节点顺利、按计划、提前、超预期，或关键瓶颈、同公司产品中最困难、受阻、延期、下调目标。上述预期变化不要求已经形成批量订单、收入或交付。",
        daily="缺少重大量级的一般计划或预期；一般资本开支或继续投资表态，缺少明确产业用途和重大金额、幅度、前值比较、实际支出或关键产能依据；非重量级或匿名客户的早期接触；尚未进入具体测试、验证或采用评估的无约束合作；只有量产计划、原型或试产展示、一般工程困难，或缺少当前量产阶段和可靠归因。",
        archive="历史能力或量产表态、城市招商、供应名单传闻、一般生态合作、教程、工具更新或普通产品宣传。",
        required=("产业主体和对象", "实质变化、重量级客户评估动作或标志性产品量产信号", "当前阶段", "当前时间和归因证据"),
        exclusions=("没有具体动作的泛计划", "没有新状态的量产计划", "一般工程困难", "无约束合作", "历史宣传或表态", "证券行情"),
    ),
    _rule(
        "semiconductor_performance_change",
        "semiconductor_ai",
        "半导体或 AI 相关企业业绩变化",
        push="正式业绩预告、指引或实际业绩显示重大经营变化，并能绑定半导体或 AI 业务事实；具名超大规模云厂商正式披露自由现金流大幅变化、由正转负或由负转正，并局部绑定 AI 基础设施资本开支、需求兑现或持续投资能力。",
        daily="相关公司一般经营更新，或者业绩说明未形成重大变化；自由现金流普通波动、仅有时点性原因或未局部绑定 AI 基础设施资本开支、需求兑现和持续投资能力。",
        archive="只谈股价、估值或历史业绩。",
        required=("公司主体", "正式业绩事实", "经营变化", "半导体或 AI 业务关系"),
        exclusions=("股价或估值变化", "历史业绩", "未绑定产业业务"),
    ),
    _rule(
        "industry_forecast_revision",
        "semiconductor_ai",
        "行业预测或预测修订",
        push="受信研究机构或可验证主体修订需求、出货、价格、产能、市场规模或增长路径，或者新预测本身显示周期反转、结构性拐点、明显加速减速、短缺持续期或资本开支预期等重大变化。",
        daily="其他新的行业市场规模或增长路径预测，尚未形成重大变化。",
        archive="纯历史回顾、没有新预测或者无法验证归因。",
        required=("可验证预测主体", "预测对象", "新预测或相对上次修订", "预测期"),
        exclusions=("历史回顾", "没有新预测", "无法验证归因"),
    ),
    _rule(
        "ai_compute_constraint",
        "semiconductor_ai",
        "AI 算力或数据中心实际约束",
        push="算力或数据中心短缺产生已签满、排队、延期、限流、订单、采购、供电或场地受阻等实际约束后果。",
        daily="区域算力紧张但尚无明确约束后果，或者只是一般需求展望。",
        archive="泛云服务宣传，或者没有 AI、数据中心上下文的电力事件。",
        required=("AI 或数据中心对象", "供需或资源约束", "实际后果或其缺失"),
        exclusions=("泛电力事件", "普通云服务宣传", "没有约束后果"),
    ),
    _rule(
        "ai_credit_constraint",
        "semiconductor_ai",
        "AI 基础设施信用和融资约束",
        push="AI 基础设施相关主体出现评级下调、信用显著恶化、保证金担保信用证要求、融资成本或融资限制，并与采购、订单、资本开支或项目执行压力局部绑定。",
        daily="一般融资担忧、估值压力或信用评论，尚无采购、资本开支或项目执行后果。",
        archive="普通公司债务、股价下跌，或者与 AI 基础设施没有局部关系。",
        required=("AI 基础设施主体", "信用或融资变化", "采购、订单、资本开支或项目后果"),
        exclusions=("普通债务", "股价或估值压力", "没有 AI 基础设施关系"),
    ),
    _rule(
        "investment_bank_allocation_change",
        "semiconductor_ai",
        "投行配置建议或动作变化",
        push=(
            "受信投行对关注个股或半导体、AI 主题作出新的明确做多做空、显著增配减配建议或动作，"
            "或提出完整双向轮动并明确从什么转向什么；或同一当前研报明确给出关注个股的目标价和"
            "带日期的历史收盘价，按“目标价/历史收盘价-1”写出未四舍五入的计算结果并据此"
            "选择 action，结果大于等于30.0%或小于等于-30.0%。"
        ),
        daily=(
            "存在有方向的新观点，但配置建议或动作不够明确；或维持既有建议；"
            "或同一研报的个股未四舍五入目标价隐含涨跌幅绝对值低于30.0%且无其他配置动作；"
            "17.6%和29.9%均低于30.0%，不得选择 push。"
        ),
        archive="只描述市场当前仓位、客户或基金已经发生的资金流，没有该机构新的配置建议或动作，或者只是泛市场评论。",
        required=(
            "受信投行",
            "关注个股或产业主题",
            "新的配置建议或动作，或个股当前目标价及同一研报明确标注的带日期历史收盘价",
            "方向和时间",
        ),
        exclusions=(
            "描述已有仓位、客户或基金资金流、泛市场评论",
            "研报为当前或新发布、给出当前评级或目标价，都不算新建议或配置动作；必须有原文明确的买入/卖出、做多/做空、增配/减配或轮动动作",
            "价格标签或收盘价日期不清、前次/共识目标价、52周区间或外部实时价替代历史收盘价时须返回 uncertain",
            "币种或股类不同、拆并股等公司行动口径不清、评级/建议与计算方向明显冲突时须返回 uncertain",
        ),
    ),
    _rule(
        "macro_surprise",
        "macro_data",
        "美国核心宏观数据相对预期偏离",
        push="当前正式发布的核心美国指标相对预期明确超预期或低于预期，并局部绑定指标、实际值或方向和预期。",
        daily="核心指标符合预期、偏离无法确认，或者只是发布前预览。",
        archive="不属于美国数据，或者只是历史回顾。",
        required=("美国核心指标", "当前正式发布状态", "实际值或方向", "预期比较"),
        exclusions=("其他国家同名指标", "历史回顾", "没有预期比较"),
    ),
    _rule(
        "fed_path_change",
        "fed_policy",
        "美联储重大政策路径变化",
        push=(
            "美联储正式作出重大政策决定或改变利率、QE/QT、资产负债表或操作框架；"
            "或者二次报道把具名专业机构和具名专业人士局部绑定到当前判断，且明确修订利率路径，"
            "或对资产负债表给出重大方向并提供时间、量级或操作机制之一。"
            "机构自身可识别的原始报告可用发布主体替代个人姓名；资产负债表判断不要求证明此前预测修订。"
        ),
        daily="其他当前预测、方向性观点、普通政策评论或符合预期的决定。",
        archive="历史回顾、泛政策传导或没有具体政策事实的内容。",
        required=(
            "美联储政策对象",
            "正式主体，或具名机构与具名专业人士/可识别原始报告",
            "利率路径修订，或资产负债表重大方向",
            "时间、量级或操作机制",
        ),
        exclusions=(
            "不得把预测、可能或考虑改写为正式决定",
            "仅有具名主体但没有重大政策事实",
            "其他央行、历史回顾或没有政策路径证据",
        ),
    ),
    _rule(
        "fed_official_stance_change",
        "fed_policy",
        "Fed 官员政策立场变化",
        push="Fed 官员明确改变既有政策立场，足以改变政策路径判断，并能由正文证明前后变化。",
        daily="新的偏鹰或偏鸽讲话，但无法核验相对既有立场发生变化。",
        archive="礼节性会面、普通政治评论或者没有政策内容。",
        required=("可验证 Fed 官员", "当前政策立场", "相对既有立场变化或其缺失"),
        exclusions=("礼节性活动", "无政策内容", "无法验证说话人"),
    ),
    _rule(
        "fed_policy_material_exception",
        "fed_policy",
        "重要金融机构负责人重大跨资产判断",
        push="受信大型金融机构负责人对利率、长期美债、股市估值和跨资产风险作出重大、明确且可归因的判断。",
        daily="Fed 沟通机制、央行独立性等制度变化但没有利率路径修订，或者只是一般风险评论。",
        archive="单一资产观点，或者泛泛表达风险很大。",
        required=("受信大型金融机构负责人", "利率或长期美债", "股市估值或跨资产风险", "明确当前判断"),
        exclusions=("单一资产观点", "泛泛风险表述", "无法验证归因"),
    ),
    _rule(
        "trade_escalation",
        "trade_policy",
        "官方贸易政策升级",
        push="目标贸易范围内、与关注产业明确相关的调查、限制、关税、制裁或管制进入正式启动、决定、生效或显著升级阶段。",
        daily="提案、磋商、弱升级信号，或者执行细节尚未确定。",
        archive="与关注产业距离较远的正式调查，或者无法证明目标贸易范围。",
        required=("目标贸易参与方", "政策工具", "当前行动阶段", "关注产业或持仓关系"),
        exclusions=("距离关注产业较远", "只有国家名", "历史政策回顾"),
    ),
    _rule(
        "trade_deescalation",
        "trade_policy",
        "官方贸易政策缓和",
        daily="出现有效缓和、撤销、豁免、终止或者履行承诺。",
        archive="只有外交表态，没有实质政策变化。",
        required=("目标贸易参与方", "政策工具", "缓和或撤销动作", "当前状态"),
        exclusions=("纯外交表态", "历史回顾", "无法证明目标贸易范围"),
    ),
)


RULES_BY_ID: Mapping[str, LLMRuleDefinition] = MappingProxyType({rule.rule_id: rule for rule in RULES})

if len(RULES_BY_ID) != len(RULES):
    raise RuntimeError("LLM rule catalog contains duplicate rule IDs")


def rules_for_families(families: tuple[RuleFamily, ...]) -> tuple[LLMRuleDefinition, ...]:
    wanted = set(families)
    return tuple(rule for rule in RULES if rule.family in wanted)
