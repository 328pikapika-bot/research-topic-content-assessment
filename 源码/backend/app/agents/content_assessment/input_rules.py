"""Material classification shared by preview and task execution."""
import re

FIELD_LABELS = {"研究方向": "research_direction", "研究目标": "research_goal", "研究内容": "research_content",
                "关键技术或方法": "key_technologies", "关键技术或技术路线": "key_technologies",
                "技术路线": "technical_route", "关键技术": "key_technologies", "关键方法": "key_technologies",
                "考核指标": "evaluation_indicators", "时间节点": "timeline", "应用场景": "application_scenarios",
                "预期成果": "expected_outcomes"}

# Keep canonical labels separate: exports and task supplements use FIELD_LABELS
# to name fields, while input can use any of these equivalent headings.
FIELD_ALIASES = {**FIELD_LABELS,
    "研究领域": "research_direction", "所属领域": "research_direction", "研究主题": "research_direction",
    "研究目的": "research_goal", "总体目标": "research_goal", "建设目标": "research_goal",
    "研究任务": "research_content", "主要内容": "research_content", "主要研究内容": "research_content",
    "研究方法": "key_technologies", "技术方法": "key_technologies", "核心技术": "key_technologies",
    "关键技术与方法": "key_technologies", "关键技术/方法": "key_technologies",
    "关键技术及技术路线": "key_technologies", "关键技术/技术路线": "key_technologies",
    "实施路线": "technical_route", "研究方案": "technical_route",
    "评价指标": "evaluation_indicators", "评估指标": "evaluation_indicators",
    "验收指标": "evaluation_indicators", "验收要求": "evaluation_indicators",
    "考核要求": "evaluation_indicators", "评价标准": "evaluation_indicators",
    "验收标准": "evaluation_indicators", "验证要求": "evaluation_indicators",
    "适用场景": "application_scenarios", "适用范围": "application_scenarios",
    "应用领域": "application_scenarios", "使用场景": "application_scenarios",
    "实施周期": "timeline", "进度安排": "timeline", "成果形式": "expected_outcomes",
}
ARRAY_FIELDS = {"key_technologies", "evaluation_indicators", "application_scenarios", "expected_outcomes"}
REQUIRED_FIELDS = {
    "paper": ("research_direction", "research_content"),
    "project": ("research_direction", "research_content"),
    "guideline": ("research_goal", "research_content", "evaluation_indicators", "application_scenarios"),
}
INPUT_RULE_VERSION = "assessment-input-semantic-v2"


def meaningful(value: str) -> bool:
    value = value.strip().strip("|* _。；;，,：:").strip()
    if not value or re.fullmatch(r"[—–\-…./／\s]+", value):
        return False
    if re.match(r"^(?:待确认|待从|待依据|未提供|未说明|材料未说明|不详|待补充|未提取)", value):
        return False
    return not re.fullmatch(r"(?:无|暂无(?:信息|内容|数据)?|未知|待定|略|N/?A|达到(?:国际|国内)?领先(?:水平)?|(?:国际|国内)领先(?:水平)?|高水平|创新性强)", value, re.I)


def explicit_fields(text: str) -> dict:
    """Read equivalent headings without swallowing the next empty prompt row."""
    result = {}
    stops = ("材料名称", "项目名称", "论文题目", "指南名称", "材料说明", "要点原文", "补充说明", "参考文献")
    labels = "|".join(map(re.escape, sorted([*FIELD_ALIASES, *stops], key=len, reverse=True)))
    pattern = (rf"(?:^|(?<=[\n\r，,；;。\t |]))[ \t]*"
               rf"(?:(?:\#{{1,6}}|[-*+]|\d+[.、)）]|[（(][一二三四五六七八九十\d]+[）)])[ \t]*)?"
               rf"(?:\*\*|__|【|\[)?({labels})(?:\*\*|__|】|\])?[ \t]*[：:|][ \t]*")
    matches = list(re.finditer(pattern, text))
    for index, match in enumerate(matches):
        field = FIELD_ALIASES.get(match[1])
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end():end].strip().strip("|").strip()
        if field and meaningful(value):
            # Preserve complete indicator clauses: commas and enumeration marks
            # often connect a measurement to its conditions, rather than new items.
            result[field] = [v.strip() for v in re.split(r"[；;\n]", value) if meaningful(v)] if field in ARRAY_FIELDS else value
    return result


def explicit_artifact_type(text: str) -> str:
    """Recognize the user's evaluation request, rather than cited document names."""
    patterns = [r"我要评估的是[：:][ \t]*(论文|项目|指南)",
                r"(?:评估|评价|评审|分析|(?:我要|我想|我需要|我希望)研究)[ \t]*(?:对象|材料)?[ \t]*(?:的)?[ \t]*(?:是|为|[:：])?[ \t]*(?:这|该|一)?[ \t]*(?:个|篇|份|项)?[ \t]*(论文|项目|指南)",
                r"(?:材料类型|评估类型|评估对象)[ \t]*(?:是|为|[:：])[ \t]*(论文|项目|指南)"]
    matches = sorted((m.start(), m[1]) for pattern in patterns for m in re.finditer(pattern, text))
    return {"论文": "paper", "项目": "project", "指南": "guideline"}[matches[-1][1]] if matches else "unknown"


def infer_artifact_type(text: str, names: list[str], requested: str = "auto") -> str:
    if requested != "auto":
        return requested
    explicit = explicit_artifact_type(text)
    if explicit != "unknown":
        return explicit
    # The primary object's explicit heading takes precedence over words in
    # references and supplementary files (a proposal can cite papers/guidelines).
    headings = []
    for pattern, kind in ((r"(?:^|\n)\s*(?:项目名称|课题名称|项目申报书)\s*[:：]", "project"),
                          (r"(?:^|\n)\s*(?:论文题目|论文标题)\s*[:：]", "paper"),
                          (r"(?:^|\n)\s*(?:指南名称|指南标题)\s*[:：]", "guideline")):
        match = re.search(pattern, text)
        if match:
            headings.append((match.start(), kind))
    if headings:
        return min(headings)[1]
    corpus = f"{text} {' '.join(names)}".lower()
    if re.search(r"项目申报|申请书|project proposal", corpus):
        return "project"
    if re.search(r"指南|guideline|call for proposals", corpus):
        return "guideline"
    if re.search(r"论文|学位论文|摘要|引言|paper|thesis", corpus):
        return "paper"
    if re.search(r"项目|里程碑|验收|project", corpus):
        return "project"
    return "unknown"
