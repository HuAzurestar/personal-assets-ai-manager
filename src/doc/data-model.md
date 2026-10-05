# PAAM 当前数据模型：PIRC-35

同一进程、同一 SQLite，20表。精确列/类型/默认/索引以 [SQL资产](../asset/sql) 为物理字典，清单以 `backend/core/target_database.py::TARGET_TABLE_NAMES` 为准。业务约束由Service/Mapper同锁验证，不引入外键或新的业务CHECK。PIRC-9字典保存为 [历史](data-model-pirc9.md)，不指导本期实现。

## 两条计量链

Fact = `transaction_fact`。第一段 `review_transaction_ledger_allocation` 保存 `review_id / transaction_id / ledger_id / cash_amount / cash_currency_code`，连接Review、原Fact和现金Ledger；每Ledger恰一条关系，Fact可分成多条。

第二段 `review_ledger_position_leg_allocation` 保存 `review_id / ledger_id / position_leg_id / cash_amount / cash_currency_code`，说明现金与数量腿归因，不再次计现金。无现金的有据腿也能发布，不伪造Ledger。链接两端须属于同Review，币种/正量/覆盖同事务核验。

当前现金只认Allocation→Review.status=0，每Ledger计一次并排除DUPLICATE。当前数量只认Leg→Review.status=0，按IN−OUT；无证据UNKNOWN/null，来源失效NEEDS_REVIEW，不以零代未知。数量不是净资产/估值/账户余额；旧AL无明确腿时标对象身份待补，不猜债权。

## 不可变发布

Fact来源、已发布Review标题/分项、Ledger、两段关系和腿不原位改写。解释改变则新建Review，整组关闭直接冲突的旧Review；旧ID/内容仍可查。启停只改Review.status/updated_time，不复活此前被替代的其它人工解释。

首次新Fact同事务生成唯一系统NORMAL_TRANSACTION、等额TRANSACTION Ledger和第一段关系。补来源不重建默认/恢复解释；未被新解释承接的Fact恢复其既有原默认。身份须明确唯一，不取最早/最新或造残额默认。迁移保留旧分占/缺口并报告；超覆盖、缺失/歧义默认证据拒绝，不自动修财务。

Review物理类型0系统NORMAL_TRANSACTION、1 BORROW_AND_REPAY、2 CREDIT_CARD、3 SHARED_SETTLEMENT、4 OTHER_MANUAL；0不可人工创建。status=0 CONFIRMED/1 REVOKED。Ledger物理类型0 TRANSACTION、1 ACCOUNT_TRANSFER、2 ASSET_LIABILITY、3 DUPLICATE；direction=1 IN/2 OUT，公开PO用字符串。重复证据占解释覆盖但不计金融金额，真实转账两端保留。

## 20表和业务列

每表另有 `id / created_time / updated_time`，时间为严格UTC六位微秒 `YYYY-MM-DDTHH:mm:ss.ffffffZ`。精确DDL见同名SQL。

| 表 | 业务列 / 用途 |
| --- | --- |
| transaction_import_file | batch_code, source_type, filename, file_format, sha256, period_start/end, total_count, success_count, skip_count, issue_count, status；sha256唯一 |
| transaction_import_row | transaction_fact_id, transaction_import_file_id, source_row_number, source_reference, raw_payload, raw_hash, row_status, issue_code/message；文件+行号唯一，原文不变 |
| transaction_fact | fact_key, occurred_time, cash_direction, amount, currency_code, account_code, counterparty_name, counterparty_account_ref, summary；fact_key唯一 |
| review_case | behavior_type, status, title；发布后只启停 |
| review_transaction_ledger_allocation | review_id, transaction_id, ledger_id, cash_amount, cash_currency_code；ledger_id唯一 |
| review_revision | review_case_id, operation, request_json, before_json, after_json, actor, reason, idempotency_key；保留历史/系统默认证据，不是新命令回执或当前水位 |
| ledger_entry | entry_type, entry_direction, cash_amount, cash_currency_code, account_ref_id, account_code, counterparty_account_ref, occurred_time；旧来源列保留，当前归属用ref |
| tag_view | name, system_name, status；system_name唯一 |
| tag | view_id, name, system_name, status；view+system_name唯一 |
| ledger_entry_tag | ledger_id, tag_id；二者唯一，不存来源位/版本 |
| setting | value_json；id=1设置，密钥不存JSON |
| auto_tag_rule | name, view_id, method, method_config_json, enabled, cron, amount_mode, rule_revision, scan_after_ledger_id, scan_epoch, analyzed/failed/suggested/accepted/rejected_count |
| tag_assignment_request | rule_id, rule_revision, ledger_id, view_id, proposed_tag_id, status, reason_summary；建议先审批，不写财务 |
| llm_prompt_audit | run_id, rule_id, rule_revision, ledger_id, model_id, attempt, model_name, request_json, response_text, response_truncated, status, error_code；受保护模型尝试审计，不是普通运维日志 |
| ledger_account_party | name, status；被管理个人 |
| ledger_account | party_id, name, status, statement_interval_months, snapshot_interval_months；账户集合，频率仅元数据 |
| ledger_account_ref | account_id, name, institution, reference, source_namespace, source_identity, identity_strength, status；具体本方来源，可未分组 |
| position | title, description, type, usage_scenario, party_id, counterparty, unit_code, status；独立资产/负债身份 |
| position_leg | position_id, review_id, type, leg_amount, leg_direction, occurred_time, source_position_leg_id, basis；有据数量变化 |
| review_ledger_position_leg_allocation | review_id, ledger_id, position_leg_id, cash_amount, cash_currency_code；ledger+leg唯一 |

## 账户与数量身份

`party → account集合 → ref具体来源`。Ledger.account_ref_id=0为UNIDENTIFIED；正ref/account_id=0为UNASSIGNED；完整链为ASSIGNED。换组只改当前归属/筛选，不移动金融行。同名、尾号、订单号、对手账号不自动合并。仅解析器从账单本方表头明确证明的完整可靠身份允许精确namespace+identity复用/创建ref：当前银行为农行/建行/招行完整账号，钱包为支付宝完整本方手机号或邮箱、微信完整本方微信号；不是昵称、支付方式或资金卡尾号。来源字符串保持原样，不做别名、大小写或跨机构模糊归并。可靠ref随明确导入确认同事务创建/复用，可保持未分组，无需先创建个人/集合；预览和核验不自动建卡。弱/未知来源由用户按文件明确创建或绑定来源卡，该元数据操作不入账，也不将原Raw身份升级为可靠。identity_strength=1 RELIABLE有唯一索引，未知为0。账户状态ACTIVE/CLOSED。

来源卡标准list/search支持当前归属`party_id`正整数等于/不等于，与`account_id`及状态条件按标准逻辑组合；真实链为ref→account→party，未分组仍由`account_id=0`显式选择。COUNT与本页同快照，搜索游标绑定个人/集合范围；过滤不能隐藏破损引用。账户工作台以紧凑来源卡列表为主，个人/集合具名筛选并按需打开有界管理选择器，不默认加载三张目录表；低频维护继续使用原元数据/归属预览写口，三类身份和金融历史不变。

Position.type=ASSET/LIABILITY；usage_scenario为GENERAL、PERSONAL-LENDING、SHARED-SETTLEMENT、STORED-VALUE、DEPOSIT-PLEDGE、REIMBURSEMENT、CREDIT-CARD、FORMAL-LOAN、INVESTMENT之一。status=ACTIVE/ARCHIVED/SETTLED；归档不清量，SETTLED仅允许已知零且无失效来源。type/party/unit及原腿不改，回款/处置以新Review及明确来源腿关联。Leg.type=OPENING/MOVEMENT，direction=IN/OUT、正量，source=0为未指定来源。

Position标准list/search的只读条目扩展为PositionListItem：原PositionPO加party_name及与详情相同的quantity_state/quantity/cost_state/source_token；不改共享Review关系的PositionPO，不加财务状态表或估值。当前页先取≤100个对象，再一次读取本页数量证据流和本方名称，COUNT与摘要在同一读快照。无腿为UNKNOWN/null；真实历史腿全部停用且无有效失效依赖时当前有据零为KNOWN/0；有效OUT来源停用为NEEDS_REVIEW/null，与详情同口径、同token。数量流每请求（单详情或列表本页）≤50,000贡献腿/2秒，超限AGGREGATION_LIMIT拒绝整页并建议减小page_size，不返回部分量或把超限伪装UNKNOWN；普通搜索无累计候选上限。前端列表直接显示本方/对方、有据量及精确单位，元数据状态与数量状态分开。

上述共用数量读取若在Position元数据短写内发生503聚合预算失败，回滚完成且尚未进入commit时返回WRITE_BUSY（明确未提交）；413容量拒绝仍为AGGREGATION_LIMIT。commit边界之后发生任何异常优先RESULT_UNKNOWN，即使底层code来自已知读取分类也不能据此允许自动重发。

现金/数量为整数+单位，单值≤9,000,000,000,000；不浮点/跨币净额/隐式换算。CNY=0.01、CNY_4=0.0001、JPY/KRW=1、KG_3=0.001kg、PCS=1件，白名单见 `backend/core/unit.py`。

前端单位元数据由只读 `GET /paam/ledger/v1/unit` 取得，成功 body 为 `{items}`，每项含 `code/label/dimension/quantum/precision/is_default`。这是完整的有限代码字典，不是数据库 PO 列表；不接受分页、Query、Filter 或 Sorter，也不提供写口。最多128项、32KiB，超限明确 `UNIT_DICTIONARY_LIMIT`，不截断。当前70个现金单位和2个数量单位沿用既有注册规则，不增加单位表。前端金额/数量格式化、币种筛选、披露配置和对象单位选择共用此字典；首次读取失败可由用户重新加载，只读重试不重发金融写入。现金选项排除数量单位，配置容量仍为最多64个金额区间单位，不等于支持单位数。

## 导入、读取、标签

先落PENDING、事务外限时解析，再明确选择≤1000行同事务确认；Raw不改，只改状态/Fact关联。上限20MiB/20,000行/100文件。ACCEPTED=1、SKIPPED=2、INVALID=3、UNKNOWN=0，未处理=total−前三者之和；完成不等于全有效。相同来源只补证据，不按商户/金额猜相同Fact，不恢复Review。

导入整次选择仍≤20,000行；`operation-preview`完整披露不可拆关联组、逐批效果/预算/撤销范围及阻断项。`operation-approve`只在现有有界预览缓存批准完全相同的计划，不写财务；`operation-confirm`每次仍≤1000行，携原操作摘要、准确批下标及本批摘要，在原短写锁内重验后只提交这一批。应用进程不派发后台批次、不新增表/持久队列/回执；缓存淘汰或重启后必须重新定位和核对，不能恢复自动执行。`operation-stop`撤销后续批准，正在执行的一批仍须等真实提交结果，不能把停止当撤销已提交金融。

后续批只将已成功前批在同一写事务内产生的精确可靠来源ref、指定Fact的新原证据证明和规则epoch/游标重置归一到原批准前提；证书只含实际ID/完整字段或原文hash，不含重放结果。来源换组/改名/状态/微秒token、额外非本次证据、规则/标签或当前解释变化仍使旧摘要失效。任何子批拒绝/失败/提交结果未知撤销后续批准，已经提交的批保留；继续须明确刷新剩余选择并批准新完整计划。缓存内容仍受单24MiB/总64MiB，容量不因批准扩大。默认输出批量取数据库真实ID，以精确Ledger字段归组建立Fact关联，不依赖RETURNING顺序或预猜ID；唯一原默认、现金覆盖和同事务回滚口径不变。

v1共用规范PO。标准列表 `{items,total,page_index,page_size}`、页≤100、同快照COUNT/Page并先验可信关系。金额排序先按单位分组，账户只Filter。文本/search串行literal NFC/casefold、total未知、空命中可继续、游标绑定条件，无累计50,000条截断。详情超过合计4000关系/2MiB明确拒绝，改用scoped关系页。Raw仅显式读单SourceRow，不随列表/汇总加载。

CandidatePO增加只读`current_reviews`：当前有效ReviewPO及`member_count`、`allocated_cash_amount`（本Fact在该Review内的完整现金分配量，单位沿候选cash_currency_code）。原`default_review`仍定位历史原系统默认，不冒充当前人工解释。先取本页≤100 Fact，再一次联读当前摘要；成员数按该Review全部不同Fact计算，不按当前页/筛选计数，不逐Fact SQL。同Review多个拆分合并为一项，遗留多Review分配逐项披露，新写仍要求每Review完整覆盖所选Fact。摘要标题脱敏；本页摘要组超过4000或响应超过2MiB明确DETAIL_LIMIT，建议减小页量，不返回截断当前归属。

只读`GET /paam/ledger/v1/review/{review_id}/fact/list`返回该不可变Review的全部不同原Fact成员，条目复用CandidatePO并附当前归属，不加载原payload/Review历史；停用后原成员仍可分页查看。标准四键、默认Fact ID升序、页≤100，Filter仅id等于/不等于、Sorter仅id、不支持非空Query。原成员与当前解释须分开理解，不能因新归属变化删除旧成员。COUNT/PAGE/摘要同快照、可信引用及30秒SQL/Python预算共用；接口不建立新财务表/状态/版本或写口。

R08前端候选及已选人工事项提供原成员入口，多个遗留有效组全部可达。查看不改草稿，原现金/数量复用只读Inspection及既有超大关系分页。显式整组选择串行读取≤100条原成员页，开始/结束重验Review状态和updated_time、逐成员当前归属；最多2000成员及累计2MiB/30秒，失败/取消/变化/超限不部分应用，合并所选仍限2000。成功保留已有现金草稿及稳定引用，仅新增缺少成员，旧预览失效；发布仍受独立扩展Review组预算，不因为可读大组而放宽。前端/接口定向证据不能替代固定最终SHA的22项回归和用户验收。

标签沿既有三表。停用Review保留旧关系，新解释仅唯一完整等义输出继承活动标签；拆分/合并/类型或数量语义变化默认待核对。DUPLICATE排除自动分析；归档不冒充活动值。仅唯一且当前一致的已批准请求可证明AUTO_RULE，否则UNKNOWN，不凭值伪称MANUAL。

流水条件的具名标签选择使用只读 `/paam/tag/v1/tag/list`、`/search`、`/{tag_id}`：平铺Tag及所属View名称/状态，不带全View标签或Ledger赋值。列表标准四键，页≤100、默认id升序；search用既有有界literal游标协议，Query仅name/view_name/display_label，Filter仅id/view_id/status/view_status的等于/不等于，Sorter为id/created_time/updated_time。归档项不隐式隐藏；名称读取不改变已选ID。读取在同快照先检查Tag→View引用与状态，破损不因用户筛选或空命中被隐藏；SQL及Python组装共用30秒预算，响应≤2MiB，超限明确拒绝。现有View管理写口不变，没有新表或新写语义。

Fact/Flow共用宽字面搜索、日期/方向主行和紧凑币种/排序，账户与标签用高级具名条件及可移除条件标签。来源ref=0明确“来源未识别”，集合=0明确“已识别未分组”，空值才是不限；名称读取失败保留所选范围。ID在金融读取前检查精确范围，日期继续按选定时区转UTC半开区间。显式相同条件查找或清空重新读取且重置扫描，不自动重发写入；未提交输入/打开高级区/正在读取名称时后台刷新不覆盖操作。

R19只扩展Flow列表/搜索的只读条目`FlowListItem`，原九字段`FlowPO`仍用于原现金详情、关系及Review输出，不修改其金额或历史ID。新条目增加`active`（原所属Review状态派生）、脱敏Fact `summary`、`transaction_id`、原所属`review:{id,title,status}`及当前元数据归属`account:{state,display_label}`。历史流水指向原Review，不替换为当前新解释；标签/数量及原文仍按需详情读取。状态、摘要和标题在同一快照取页，具名来源复用既有Account遮罩投影按本页ref批量加载，不逐Ledger SQL。响应≤2MiB、SQL/Python共用30秒；超限明确拒绝完整页，不静默截断。公开邮箱脱敏避免在超长连续局部字符上重复尝试同一段文本。

Flow标准API不带active Filter仍读全部存储输出，保持显式PO检查能力；UI缺省/空active为当前有效，URL `active=all`明确含历史，`active=false`仅历史，非法值在金融读取前拒绝。模式在紧凑次行始终可见，清空或移除历史条件回当前；分页/文本扫描沿同一模式，改变条件重启游标。行内标状态、脱敏摘要、具名来源与只读原事项入口，历史不计当前summary；不新增Ledger状态、业务表、写口或内容历史。本契约是R19授权的展示调整，不声称旧FlowPO违反当时确认的字段形状；已确认MPA方案保留历史原文。

PIRC-24共享调度，模型调用在事务外；完整扫描前缀短事务复核资格/epoch/配置后落库，不回放未知提交。人工设值/Review启停/字典变化同事务失效旧建议。没有Ledger status、ledger view status或标签历史镜像表。

## 安全迁移

空库创建20表。旧14表停写后用SQLite backup含已提交WAL生成新副本；定向RENAME第一段表/五列、Ledger金额两列，建六表/加ref。原ID/原文/状态/标签/逐币值守恒，只允许严格等价UTC补齐与声明的行为分类。完整schema profile/manifest/覆盖/完整性验证READY，切换前同快照只读复核。启动不迁移/补默认/猜债；中断/未知提交/提交后副本保留，不重用目标或删除新写。切库/合并/部署另授权，见 [操作说明](pirc35-delivery.md)。
