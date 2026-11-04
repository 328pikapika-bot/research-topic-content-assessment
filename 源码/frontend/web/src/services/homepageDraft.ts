export const NINE_FIELDS = [
  { key: 'research_direction', label: '研究方向', array: false },
  { key: 'research_goal', label: '研究目标', array: false },
  { key: 'research_content', label: '研究内容', array: false },
  { key: 'technical_route', label: '技术路线', array: false },
  { key: 'key_technologies', label: '关键技术', array: true },
  { key: 'evaluation_indicators', label: '考核指标', array: true },
  { key: 'timeline', label: '时间节点', array: false },
  { key: 'application_scenarios', label: '应用场景', array: true },
  { key: 'expected_outcomes', label: '预期成果', array: true },
] as const
export type NineFieldKey = typeof NINE_FIELDS[number]['key']
export type NineFields = Record<NineFieldKey, string | string[]>
export type NineDraft = Record<NineFieldKey, string>
export type SelectedAssessmentType = 'paper' | 'project' | 'guideline'
export function requiredNineFieldKeys(type: SelectedAssessmentType | 'auto'): NineFieldKey[] {
  if (type === 'paper' || type === 'project') return ['research_direction', 'research_content', 'key_technologies']
  if (type === 'guideline') return ['research_goal', 'research_content', 'evaluation_indicators', 'application_scenarios']
  // Resolve the type first; it never implies that all legacy fields are required.
  return []
}
export type OutputPreference = 'balanced' | 'recommended' | 'exploratory'
export function emptyNineDraft(): NineDraft {
  return Object.fromEntries(NINE_FIELDS.map(field => [field.key, ''])) as NineDraft
}
export function serializeNineDraft(draft: NineDraft): NineFields {
  return Object.fromEntries(NINE_FIELDS.map(field => [field.key, field.array
    ? draft[field.key].split(/[\n；;]/).map(value => value.trim()).filter(Boolean)
    : draft[field.key].trim()])) as NineFields
}
export function mergeNineDraft(draft: NineDraft, incoming: Partial<NineFields>, touched: Set<NineFieldKey>) {
  for (const field of NINE_FIELDS) {
    if (touched.has(field.key)) continue
    const value = incoming[field.key]
    draft[field.key] = Array.isArray(value) ? value.join('\n') : value || ''
  }
}
function meaningful(value: string) { return !!value.trim() && !/^(待确认|待从|待依据|未提供|未说明|材料未说明|不详|未知|待补充|暂无|未提取|无$)/.test(value.trim()) }
export function missingNineFields(draft: NineDraft, type: SelectedAssessmentType | 'auto' = 'auto') {
  const values = serializeNineDraft(draft)
  const required = requiredNineFieldKeys(type)
  const missing = NINE_FIELDS.filter(field => required.includes(field.key) && !(Array.isArray(values[field.key]) ? (values[field.key] as string[]).some(meaningful) : meaningful(values[field.key] as string))
    && !(type !== 'auto' && field.key === 'key_technologies' && meaningful(values.technical_route as string)))
  return missing
}
export function topicYearRange(range: string, year = new Date().getFullYear()): number[] {
  const count = Number(range)
  return [3, 5, 10].includes(count) ? [year - count + 1, year] : []
}

export interface HomepageCase { id: string; title: string; category: string; icon: string; agent: 'topic' | 'assessment'; input: string; scenarioId?: string }
const topicCases: HomepageCase[] = [
  { id: 'topic-urban-heat-v2', title: '城市热环境与遥感观测', category: '地球科学', icon: 'satellite_alt', agent: 'topic', input: '研究对象为长三角三座高密度城市2015—2025年夏季热环境，重点关注老旧居住区、产业园区和滨水绿地周边的人体热暴露差异。研究问题包括：卫星地表温度能否在不同下垫面上稳定表征近地气温，绿地连通性和树冠结构对昼夜热暴露的影响是否存在阈值，以及高温预警模型能否迁移到未参与建模的城市。拟比较Landsat 8/9、Sentinel-3和地面自动站的空间分辨率、过境时间与校准误差，采用云量筛选、站点交叉验证、分层回归和时空留出验证；同步纳入不透水率、建筑高度、风速、湿度和老年人口比例。研究边界限定为无云夏季晴天和城市建成区，不把地表温度直接等同于人体体感温度，也不把单一城市的降温相关性外推为普遍因果结论。' },
  { id: 'topic-phage-v2', title: '耐药菌与噬菌体治疗研究', category: '微生物学', icon: 'biotech', agent: 'topic', input: '研究对象为碳青霉烯耐药肺炎克雷伯菌和铜绿假单胞菌，拟评价噬菌体单药、噬菌体鸡尾酒与抗生素联用对感染控制的作用。需要回答宿主范围和受体变异如何影响疗效、噬菌体抗性是否伴随毒力或耐药性代价，以及体外结果在动物模型中能否复现。样本应覆盖不同克隆型和临床来源，记录噬菌体效价、感染复数、一步生长曲线、时间杀菌曲线、耐药突变频率和生物膜状态；用未处理、抗生素单药、噬菌体单药和联用组进行平行对照，并以盲法菌落计数和预设终点分析。体外抑菌、急性感染动物存活率和临床治疗获益分别定义，不将少数菌株的清除结果外推至全部耐药菌，也不把噬菌体敏感性变化直接解释为临床安全性。' },
  { id: 'topic-perovskite-v2', title: '钙钛矿光伏组件稳定性', category: '能源材料', icon: 'solar_power', agent: 'topic', input: '研究对象为面积不小于10 cm²的钙钛矿光伏组件，关注水氧侵入、离子迁移、界面反应和热应力共同作用下的功率衰减。研究问题包括：封装阻隔层和界面钝化分别抑制哪一种衰减路径，实验室加速条件与户外运行之间能否建立可解释的寿命映射，以及从小面积电池放大到组件后失效位置是否发生改变。比较未封装、常规封装和多层阻隔封装，设置不同界面处理和串联电阻控制，按IEC 61215相关光照、湿热、热循环和户外暴露条件记录初始效率、稳态输出、短路电流、开路电压、填充因子、T80时间与失效模式。分析应区分器件效率、组件功率和有效面积，明确加速老化的温度、湿度、光谱和负载边界；短期T80结果只能作为寿命模型输入，不能直接等同于真实户外运行年限。' },
]
type AssessmentCaseType = 'paper' | 'project' | 'guideline'
type AssessmentCaseValues = {
  research_direction?: string
  research_content?: string
  key_method?: string
  key_technology_or_route?: string
  research_goal?: string
  evaluation_indicators?: string
  application_scenarios?: string
}

function assessmentCase(id: string, type: AssessmentCaseType, title: string, category: string, icon: string, values: AssessmentCaseValues): HomepageCase {
  const fields = type === 'paper'
    ? [['研究方向', values.research_direction], ['研究内容', values.research_content], ['关键方法', values.key_method]]
    : type === 'project'
      ? [['研究方向', values.research_direction], ['研究内容', values.research_content], ['关键技术或技术路线', values.key_technology_or_route]]
      : [['研究目标', values.research_goal], ['研究内容', values.research_content], ['考核指标', values.evaluation_indicators], ['应用场景', values.application_scenarios]]
  return {
    id,
    title,
    category: `${type === 'paper' ? '论文' : type === 'project' ? '项目' : '指南'} · ${category}`,
    icon,
    agent: 'assessment',
    scenarioId: id,
    input: [`我要评估的是：${type === 'paper' ? '论文' : type === 'project' ? '项目' : '指南'}`, `材料名称：${title}`, ...fields.map(([label, value]) => `${label}：${value}`)].join('\n'),
  }
}

export const ASSESSMENT_CASES = [
  assessmentCase('assessment-platform', 'paper', '科研文献证据链验证方法', '科学信息学', 'menu_book', {
    research_direction: '科学信息学与文献知识发现，侧重综述主张的可追溯引用及人工复核效率；本例为待评审论文设计，不将拟开展实验表述为已获得结果',
    research_content: '以生物医学和材料学文献综述为对象，研究检索增强方法如何把“研究对象—干预或方法—比较条件—结果”拆成可复核主张，并从题录、摘要和可读全文中定位支持句。实验比较仅用题名检索、关键词检索、向量检索和混合检索时，综述人员找到有效依据的时间、引用遗漏率和误配率；另分析表格、补充材料和术语同义变化对证据链完整性的影响。数据集按主题、年份和文献类型分层，保留训练、调参和独立测试的时间边界。',
    key_method: '采用PICO/PECO要素拆分主张，使用BM25与向量召回融合，再以跨编码器重排；对每条结论返回原文句、段落位置、文献版本和证据类型。以两名领域研究者的双人标注作为参照，报告主张级精确率、召回率、定位偏差、引用覆盖率和人工复核时间，并用未见主题进行外部验证。题录只能支撑书目信息，摘要只支撑摘要中明确表述，全文定位结果仍需人工抽查。',
  }),
  assessmentCase('assessment-drug', 'paper', '面向药物发现的可解释模型评估', '计算药物学', 'medication', {
    research_direction: '人工智能辅助药物发现，侧重分子活性预测的解释质量、外部泛化及不确定性校准；本例提交方法与实验设计供评审，未提供的数值结果不作为实测结论',
    research_content: '围绕激酶抑制剂和药物相似性筛选，评估分子性质预测模型在活性预测、候选排序和不确定性识别中的可靠性。研究同时考察训练集与后续发表化合物之间的时间漂移、scaffold变化和实验室测定条件差异，回答模型解释是否能够帮助研究者排除结构相似但活性不稳定的候选。材料包括公开活性数据库、按年份冻结的外部测试集和一组独立实验记录，明确IC50、Ki等指标不能混合比较。',
    key_method: '比较分子图神经网络、随机森林和基于指纹的相似性基线，采用随机切分、时间切分和scaffold切分三种验证。以AUROC、AUPRC、前1%富集倍数、回归误差、置信度校准误差和不确定性覆盖率评价模型；用注意力或子结构归因生成解释，并由化学信息学专家盲评解释是否对应已知药效团。外部实验仅用于验证排序和方向，不把模型预测值写成实测活性，明确适用化学空间和实验条件。',
  }),
  assessmentCase('assessment-battery-v2', 'project', '固态电池电极界面稳定化研究', '能源材料', 'battery_charging_full', {
    research_direction: '全固态锂电池电极与电解质界面，重点考察高镍正极与硫化物电解质的化学稳定性和循环接触损失；本例为拟立项研究，评价技术路线及验证设计',
    research_content: '面向硫化物固态电解质与高镍正极界面，解决充放电过程中空间电荷层、化学副反应和颗粒接触损失导致的阻抗增长。项目比较氧化物薄层、磷酸盐涂层和柔性聚合物复合层三种界面方案，并设置未处理电极、不同涂层厚度和不同压实密度对照。研究内容包括界面化学组成、微裂纹演化、初始库仑效率、倍率性能、循环衰减及拆解后的失效位置，明确材料配方、载量、压力、温度和截止电压范围。',
    key_technology_or_route: '采用同批次正极、固态电解质和集流体制备扣式与小型软包电池；先用XPS、ToF-SIMS、截面SEM和纳米压痕建立界面基线，再以电化学阻抗谱分离界面阻抗与体相阻抗。按照统一载量和压力开展恒流循环、倍率和高温存储，对每组设置至少三只平行电池并预先定义剔除规则。以阻抗增长率、容量保持率、首效、临界电流密度和循环后界面完整性作为评价指标，最后在更高载量软包中复验；项目目标和阶段性预测不得代替实测结果。',
  }),
  assessmentCase('assessment-chip-v2', 'project', '多器官芯片耦合与屏障稳定性研究', '生物工程', 'view_stream', {
    research_direction: '多器官芯片模块耦合和微血管屏障，重点考察肝代谢与血脑屏障之间的暴露传递和屏障稳定性；本例为研究项目方案，拟验证指标不作为已完成结果',
    research_content: '以肝脏—血脑屏障双器官芯片为对象，研究模块接口、连续灌流和跨器官代谢对屏障通透性及药物暴露的影响。项目需要区分单器官模型的屏障基线、连接后流体剪切变化和肝代谢产物造成的二次影响；比较直接串联、储液池隔离和不同流量配比对细胞状态的作用。研究包含内皮细胞来源、肝细胞成熟度、通道尺寸、灌流时间、示踪分子分子量及阳性/阴性药物对照，并记录气泡、泄漏和批次差异。',
    key_technology_or_route: '先分别建立肝模块和血脑屏障模块的单器官基线，确认白蛋白分泌、紧密连接蛋白、TEER和荧光示踪通量达到预设范围，再通过标准化接口连接双器官回路。设置静态培养、单器官灌流、双器官灌流和无细胞通道对照，连续记录流量、压力、TEER、细胞活率及药物浓度，使用质量守恒模型核对跨模块传输。以通透性系数、屏障保持时间、代谢清除率和批内/批间变异评价可重复性，异常结果需区分设备故障、细胞状态和真实生物学差异。',
  }),
  assessmentCase('assessment-standard', 'guideline', '器官芯片研究标准化指南', '生物医学工程', 'rule', {
    research_goal: '建立适用于屏障类器官芯片研究的统一制备、培养、检测、质量控制和结果报告规范，使不同实验室能够依据相同的对象定义、操作条件和判定口径复现实验并比较结果。指南同时明确哪些指标用于放行、哪些结果仅作探索性观察，避免把芯片模型结果直接等同于临床疗效。',
    research_content: '规定细胞来源与鉴定、芯片材料和通道尺寸、表面处理、接种密度、灌流剪切力、培养周期、阳性/阴性对照及污染排查要求；统一TEER、荧光示踪通量、紧密连接蛋白、细胞活率和屏障破坏后的恢复测试流程。每个实验需记录批次、设备、温度、流量、示踪分子分子量、采样时间点和原始图像，报告均值、离散程度、异常值处理和生物学重复数，并给出从原始信号到通透性系数的计算公式。',
    evaluation_indicators: '形成可执行的分层标准操作流程和记录模板；明确TEER、通透性系数、剪切力、细胞活率及阳性对照响应的测量口径和建议范围；完成至少三家实验室、两批芯片和两名操作人员的复现性验证，报告批内与批间变异、失效比例和偏差来源。所有指标须注明检测方法、单位、基线、适用模型和不适用情形。',
    application_scenarios: '血脑屏障药物通透性、肠道吸收和炎症屏障研究，吸入制剂及纳米材料的体外安全性评价，药物筛选前的机制验证，以及器官芯片公共实验平台的培训、放行和质量审计。指南成果应包含按模型分类的适用条件、操作记录模板及跨实验室验证数据包，由平台人员和独立操作人员按同一测量口径复核；本例是指南草案，复现性验证为拟开展任务。',
  }),
  assessmentCase('assessment-cost', 'guideline', '微流控器件制造与验证指南', '微纳制造', 'precision_manufacturing', {
    research_goal: '形成适用于科研级微流控器件从设计、打样、制造到入库和使用前验证的统一要求，帮助实验人员判断器件是否满足尺寸、密封、流量控制和生物相容性条件，并为跨批次实验提供可追溯的质量记录。指南不替代特定诊断产品的法规检验，也不把单个器件的合格判定外推为长期临床性能。',
    research_content: '覆盖软光刻、激光加工、三维打印和热压成型等工艺的材料选择、模具精度、通道尺寸、圆角和表面处理；规定等离子键合、热键合和胶黏剂封装的工艺窗口及外观检查。验证部分包括静态泄漏、耐压、流量—压力曲线、混合效率、气泡耐受、连续运行、灭菌兼容性和细胞/试剂接触后的材料析出检查。每批器件需关联设计版本、材料批次、工艺参数、检测设备校准状态、原始曲线和不合格处置记录。',
    evaluation_indicators: '规定关键通道宽度和高度偏差、表面粗糙度、键合强度、泄漏率、批内一致性、流阻偏差、连续运行时间和气泡引入后的恢复时间的验收方法；建议以设计值、测量不确定度和适用流量范围同时报告，不用单一阈值替代完整判定。建立缺陷分级、复检、返工、报废和追溯表，至少用三批器件验证测量重复性和操作者间一致性。',
    application_scenarios: '器官芯片和细胞培养芯片的批次放行，化学分析与液滴生成芯片的流量和混合性能验证，环境样品前处理，以及高校公共实验平台、企业中试线和共享微纳制造中心的器件验收与质量培训。实施时按材料、制造工艺及设计流量分组验收，提供设计版本、逐批测量记录、校准凭证和不合格处置清单；本例是指南草案，三批器件验证为拟开展任务。',
  }),
]
export const TOPIC_CASES = topicCases
export const PUBLIC_CASES: HomepageCase[] = [...topicCases, ...ASSESSMENT_CASES]
