import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(process.cwd(), "..");
const out = path.join(root, "资料", "测试输入", "创新性评估", "创新性评估智能体测试资料", "五领域实测报告集_20260917");
const specs = JSON.parse(await fs.readFile(path.join(out, "domain_specs.json"), "utf8"));

const COLORS = { navy: "#1F4E78", blue: "#DCE6F1", pale: "#F3F6F8", gray: "#D9D9D9", red: "#FCE4D6", green: "#E2F0D9" };

function setHeader(sheet, address) {
  const range = sheet.getRange(address);
  range.format.fill = COLORS.navy;
  range.format.font = { bold: true, color: "#FFFFFF" };
  range.format.wrapText = true;
  range.format.verticalAlignment = "center";
  range.format.borders = { preset: "all", style: "thin", color: COLORS.gray };
}

function setGrid(sheet, address) {
  const range = sheet.getRange(address);
  range.format.wrapText = true;
  range.format.verticalAlignment = "center";
  range.format.borders = { preset: "all", style: "thin", color: COLORS.gray };
}

function titleBlock(sheet, title, subtitle, columns) {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${columns}1`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${columns}1`).format = { fill: COLORS.navy, font: { bold: true, color: "#FFFFFF", size: 14 }, verticalAlignment: "center" };
  sheet.getRange(`A2:${columns}2`).merge();
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${columns}2`).format = { fill: COLORS.blue, font: { color: "#1F1F1F", italic: true }, wrapText: true };
  sheet.getRange("A1:A2").format.rowHeight = 28;
}

function rawRows(domain) {
  const rows = [];
  for (let i = 1; i <= 24; i += 1) {
    const batch = `B${1 + ((i - 1) % 3)}`;
    const group = i % 2 ? "对照" : "方案";
    const qc = i % 11 === 0 ? "FAIL_REVIEW" : "PASS";
    const status = i % 13 === 0 ? "missing_sensor" : "usable";
    let condition = "标准条件";
    let a = 10 + i * 0.12;
    let b = 0.84 + (i % 5) * 0.012;
    let c = 0.07 + (i % 4) * 0.006;
    if (domain.key === "新能源") { condition = i % 3 === 0 ? "-20°C 快充" : "25°C 循环"; a = group === "方案" ? 42 + i * 0.11 : 58 + i * 0.18; b = group === "方案" ? 3.1 + (i % 4) * 0.2 : 4.6 + (i % 3) * 0.2; c = group === "方案" ? 12.3 + (i % 5) * 0.7 : 5.2 + (i % 3) * 0.4; }
    if (domain.key === "新材料") { condition = i % 2 ? "初始样" : "第5次重塑"; a = group === "方案" ? 121 + i * 0.4 : 134 + i * 0.2; b = group === "方案" ? 84 + (i % 4) * 0.6 : 100; c = group === "方案" ? 17.1 + (i % 4) * 0.3 : 27.6 + (i % 3) * 0.4; }
    if (domain.key === "生物医药") { condition = i % 3 === 0 ? "联合治疗" : "单独类器官"; a = group === "方案" ? 0.78 + (i % 5) * 0.01 : 0.63 + (i % 4) * 0.012; b = group === "方案" ? 73 + (i % 5) * 1.2 : 58 + (i % 4) * 1.5; c = group === "方案" ? 9.4 + (i % 3) * 0.4 : 13.7 + (i % 3) * 0.6; }
    if (domain.key === "人工智能") { condition = i % 2 ? "文本+图表" : "纯文本"; a = group === "方案" ? 0.84 + (i % 4) * 0.008 : 0.77 + (i % 4) * 0.006; b = group === "方案" ? 0.91 + (i % 3) * 0.01 : 0.72 + (i % 3) * 0.01; c = group === "方案" ? 18 + (i % 5) * 1.2 : 29 + (i % 4) * 1.5; }
    if (domain.key === "先进制造") { condition = i % 2 ? "开环打印" : "闭环调控"; a = group === "方案" ? 0.52 + (i % 4) * 0.025 : 1.13 + (i % 4) * 0.04; b = group === "方案" ? 0.91 + (i % 3) * 0.01 : 0.76 + (i % 3) * 0.02; c = group === "方案" ? 10.7 + (i % 4) * 0.5 : 13.2 + (i % 4) * 0.7; }
    rows.push([`${domain.code}-R${String(i).padStart(3, "0")}`, group, batch, condition, i, Number(a.toFixed(3)), Number(b.toFixed(3)), Number(c.toFixed(3)), qc, status, "构造测试数据", `原始记录行 ${i + 5}`]);
  }
  return rows;
}

function resultFor(domain, idx, target) {
  const numbers = (String(target).match(/-?\d+(?:\.\d+)?/) || [""])[0];
  const n = Number(numbers);
  if (!Number.isFinite(n)) return idx === 7 ? "待定义" : "未形成可核对结果";
  if (domain.key === "新能源") return [49.8, 4.7, 8.1, 86.4, 91.2, "待定义", 11.7, 118][idx];
  if (domain.key === "新材料") return [124.6, 82.9, 4, 19.2, 88.1, 39.7, 106.5, 3.8][idx];
  if (domain.key === "生物医药") return [13, 0.74, 0.81, 76.0, 79.0, 12.6, 68.0, 9][idx];
  if (domain.key === "人工智能") return [0.82, 0.86, 5.1, 21.0, 3.8, 3.2, 92.0, 24][idx];
  return [0.61, 87.0, 84.0, 230, 99.1, 13.7, "待定义", 78.0][idx];
}

function dataWorkbook(domain) {
  const wb = Workbook.create();
  const raw = wb.worksheets.add("实验原始数据");
  const summary = wb.worksheets.add("统计汇总");
  const dict = wb.worksheets.add("数据字典");
  const rawData = rawRows(domain);
  const headers = ["记录编号", "组别", "批次", "条件", "重复编号", "观测值 A", "观测值 B", "观测值 C", "质控标记", "可用状态", "数据性质", "来源定位"];
  titleBlock(raw, `${domain.title} 实验数据与统计表`, `${domain.code} | 该工作簿所有观测与汇总值均为构造测试数据，仅用于检测解析、目标结果区分、统计和回链。`, "L");
  raw.getRange("A4:L4").values = [headers];
  setHeader(raw, "A4:L4");
  raw.getRange(`A5:L${4 + rawData.length}`).values = rawData;
  setGrid(raw, `A5:L${4 + rawData.length}`);
  raw.getRange(`F5:H${4 + rawData.length}`).format.numberFormat = "0.000";
  raw.freezePanes.freezeRows(4);
  raw.getRange("A:A").format.columnWidth = 18; raw.getRange("B:E").format.columnWidth = 13; raw.getRange("F:H").format.columnWidth = 13;
  raw.getRange("I:L").format.columnWidth = 18; raw.getRange(`I5:I${4 + rawData.length}`).conditionalFormats.add("containsText", { text: "FAIL", format: { fill: COLORS.red, font: { color: "#9C0006", bold: true } } });

  titleBlock(summary, `${domain.title} 统计汇总`, `${domain.code} | 目标值与构造结果分列；基线、统计和验收条件的缺失是有意设置的测试差异。`, "K");
  summary.getRange("A4:K4").values = [["指标编号", "指标名称", "单位", "项目目标", "构造汇总值", "公开/对照基线", "样本与重复", "统计方法", "验收条件", "结果状态", "来源主张"]];
  setHeader(summary, "A4:K4");
  const statRows = domain.indicators.map((row, idx) => {
    const result = resultFor(domain, idx, row[3]);
    const baseline = idx === 5 || (domain.key === "人工智能" && idx === 2) ? "待补：缺少统一基线或对照协议" : "对照组/公开基线需按七字段核对";
    const n = idx === 2 ? "每条件 n=8-30，分批记录" : "每组 n=6-12，至少 3 批";
    const stats = idx === 2 ? "统计口径待固定" : "均值±SD；95% CI；异常值按质控规则处理";
    const acceptance = idx === 5 ? "先补定义、分母和预注册阈值" : `按目标 ${row[3]} ${row[2]} 验收，需同条件复核`;
    const status = typeof result === "number" ? (idx === 3 || idx === 6 ? "目标未达或待解释" : "构造结果") : "缺失/待补";
    return [row[0], row[1], row[2], row[3], result, baseline, n, stats, acceptance, status, `C${idx + 1}`];
  });
  summary.getRange(`A5:K${4 + statRows.length}`).values = statRows;
  setGrid(summary, `A5:K${4 + statRows.length}`);
  summary.getRange(`D5:E${4 + statRows.length}`).format.numberFormat = "0.00";
  summary.getRange("A:A").format.columnWidth = 11; summary.getRange("B:B").format.columnWidth = 22; summary.getRange("C:E").format.columnWidth = 13; summary.getRange("F:I").format.columnWidth = 24; summary.getRange("J:K").format.columnWidth = 16;
  summary.getRange(`J5:J${4 + statRows.length}`).conditionalFormats.add("containsText", { text: "未达", format: { fill: COLORS.red, font: { color: "#9C0006", bold: true } } });
  summary.freezePanes.freezeRows(4);
  summary.getRange(`A${7}:A${7}`).values = [["计算核对说明"]];
  summary.getRange(`B${7}:K${7}`).merge();
  summary.getRange("B7").values = [["汇总值由构造数据脚本按记录编号和组别聚合；本表不把项目目标值当成结果。原始数据含故意的质控异常与缺失状态，评估系统应保留并解释。"]];
  summary.getRange("A7:K7").format.fill = COLORS.blue; summary.getRange("A7:K7").format.wrapText = true;

  titleBlock(dict, `${domain.title} 数据字典与质控规则`, `${domain.code} | 解析锚点按工作表和行号记录，内部数据不具备真实项目证明效力。`, "F");
  const dictRows = [["字段", "类型", "单位", "含义", "质控规则", "示例定位"], ["记录编号", "文本", "-", "每条原始观测的稳定 ID", "不得重复", "实验原始数据!A5"], ["组别", "分类", "-", "方案或对照", "两组均需存在", "实验原始数据!B5"], ["批次", "分类", "-", "独立制备/实验批次", "至少三批", "实验原始数据!C5"], ["条件", "文本", "-", "温度、暴露、数据或打印条件", "比较时必须对齐", "实验原始数据!D5"], ["观测值 A-C", "数值", "按领域", "原始仪器或模型观测", "保留原值；异常不覆盖", "实验原始数据!F5:H5"], ["质控标记", "分类", "-", "PASS 或 FAIL_REVIEW", "FAIL_REVIEW 需复核", "实验原始数据!I5"], ["可用状态", "分类", "-", "usable 或 missing_sensor", "缺失不当作 0", "实验原始数据!J5"], ["数据性质", "标签", "-", "构造测试数据", "禁止外推真实项目", "实验原始数据!K5"], ["来源定位", "文本", "-", "工作表/行定位", "用于回链", "实验原始数据!L5"]];
  dict.getRange("A4:F13").values = dictRows;
  setHeader(dict, "A4:F4"); setGrid(dict, "A5:F13");
  dict.getRange("A:A").format.columnWidth = 16; dict.getRange("B:C").format.columnWidth = 13; dict.getRange("D:E").format.columnWidth = 32; dict.getRange("F:F").format.columnWidth = 23;
  dict.freezePanes.freezeRows(4);
  wb.recalculate();
  return wb;
}

function evidenceWorkbook(domain) {
  const wb = Workbook.create();
  const evidence = wb.worksheets.add("文献证据");
  const plans = wb.worksheets.add("检索计划");
  const compare = wb.worksheets.add("七字段比较");
  const rules = wb.worksheets.add("证据等级规则");
  titleBlock(evidence, `${domain.title} 文献证据与主张对照矩阵`, `${domain.code} | 公开来源与证据等级用于测试可追溯性和比较边界；不代表对构造项目的认可。`, "J");
  evidence.getRange("A4:J4").values = [["证据编号", "题名", "来源机构/期刊", "年份", "来源类型", "证据等级", "DOI/URL", "可定位信息", "可支撑范围", "主张关联"]];
  setHeader(evidence, "A4:J4");
  const sourceRows = domain.sources.map((row, idx) => [row[0], row[1], row[2], Number(row[3]), idx < 3 && row[5] === "A" ? "官方页面/全文" : "论文摘要/题录", row[5], row[6], row[7], row[5] === "A" ? "背景、标准范围或方法边界" : row[5] === "B" ? "方向性支持，不能证明项目指标" : "只能证明题录存在，禁止强结论", `C${(idx % 6) + 1}`]);
  evidence.getRange(`A5:J${4 + sourceRows.length}`).values = sourceRows;
  setGrid(evidence, `A5:J${4 + sourceRows.length}`);
  evidence.getRange("A:A").format.columnWidth = 13; evidence.getRange("B:B").format.columnWidth = 31; evidence.getRange("C:C").format.columnWidth = 24; evidence.getRange("D:F").format.columnWidth = 12; evidence.getRange("G:H").format.columnWidth = 35; evidence.getRange("I:J").format.columnWidth = 28;
  evidence.getRange(`F5:F${4 + sourceRows.length}`).conditionalFormats.add("containsText", { text: "C", format: { fill: COLORS.red, font: { color: "#9C0006", bold: true } } });
  evidence.freezePanes.freezeRows(4);

  titleBlock(plans, `${domain.title} 检索计划`, `${domain.code} | 每条主张至少保留系统先例、机制方法、指标基线、验证应用和标准监管五类检索意图。`, "I");
  plans.getRange("A4:I4").values = [["计划编号", "关联主张", "查询组", "检索意图", "建议检索词", "年份", "来源范围", "选择规则", "预期限制"]];
  setHeader(plans, "A4:I4");
  const roles = ["系统或技术路线先例", "关键机制与实现方法", "指标基线与验证方法", "应用验证与实验方案", "产业标准与监管边界"];
  const planRows = [];
  for (let i = 0; i < 6; i += 1) {
    for (let j = 0; j < roles.length; j += 1) {
      const cid = `C${i + 1}`;
      planRows.push([`${domain.code}-Q${String(planRows.length + 1).padStart(2, "0")}`, cid, roles[j], `检查 ${cid} 在 ${roles[j]} 上的可比先例和证据边界`, `${domain.direction} ${roles[j]} ${cid}`, "2020-2026", "官方页面、开放论文、公开数据集", "优先同对象同方法全文；否则降级", "跨对象、摘要或题录不可支撑强创新"]) ;
    }
  }
  plans.getRange(`A5:I${4 + planRows.length}`).values = planRows;
  setGrid(plans, `A5:I${4 + planRows.length}`);
  plans.getRange("A:A").format.columnWidth = 16; plans.getRange("B:C").format.columnWidth = 18; plans.getRange("D:E").format.columnWidth = 32; plans.getRange("F:G").format.columnWidth = 18; plans.getRange("H:I").format.columnWidth = 28;
  plans.freezePanes.freezeRows(4);

  titleBlock(compare, `${domain.title} 七字段主张对照矩阵`, `${domain.code} | 对象、方法、数据、基线、指标、时间、验证条件必须逐项核对；差异不应被关键词重叠掩盖。`, "K");
  compare.getRange("A4:K4").values = [["主张", "证据", "对象", "方法", "数据", "基线", "指标", "时间", "验证条件", "比较结论", "补证任务"]];
  setHeader(compare, "A4:K4");
  const compareRows = domain.sources.map((row, idx) => {
    const cid = `C${(idx % 6) + 1}`;
    const weak = row[5] !== "A" || idx % 3 === 1;
    return [cid, row[0], weak ? "对象/样本不同" : "同领域对象，规格仍需核验", weak ? "方法或参数未完全一致" : "路线相近，参数可部分对齐", weak ? "样本量或数据集不可比" : "数据口径可部分对应", idx % 2 ? "无同口径基线" : "有对照但统计口径不同", weak ? "指标定义或单位不同" : "指标可比性待全文核对", weak ? "时间窗不同/未报告" : "时间窗相近但仍需核验", weak ? "设备、环境或后验验证条件不同" : "验证条件部分对应，仍需核验", row[5] === "A" ? "方向性支持" : "不可比或证据不足", "补充全文、原始数据、基线和独立验证条件"];
  });
  compare.getRange(`A5:K${4 + compareRows.length}`).values = compareRows;
  setGrid(compare, `A5:K${4 + compareRows.length}`);
  compare.getRange("A:B").format.columnWidth = 13; compare.getRange("C:I").format.columnWidth = 22; compare.getRange("J:K").format.columnWidth = 28; compare.freezePanes.freezeRows(4);

  titleBlock(rules, `${domain.title} 证据等级规则`, `${domain.code} | 等级由证据可重新打开程度和定位信息决定，不由题名相似度决定。`, "D");
  rules.getRange("A4:D4").values = [["等级", "最低要求", "可支撑结论", "禁止用途"]]; setHeader(rules, "A4:D4");
  rules.getRange("A5:D7").values = [["A", "全文或官方页面可重新打开，作者/机构、日期和章节/页码可定位", "同口径比较、受限的直接支持", "把背景页写成项目通过认证"], ["B", "摘要、主题页或部分公开内容，定位不完整", "方向性支持、方法背景和检索线索", "强创新、国际领先或直接证明指标"], ["C", "只有题录、关键词或未核验链接", "存在检索线索，提示补证", "任何强创新、领先、临床或监管结论"]]; setGrid(rules, "A5:D7");
  rules.getRange("A:A").format.columnWidth = 10; rules.getRange("B:B").format.columnWidth = 42; rules.getRange("C:D").format.columnWidth = 34; rules.freezePanes.freezeRows(4);
  wb.recalculate();
  return wb;
}

async function saveWorkbook(wb, filePath, previewName) {
  const workbook = wb;
  const check = await workbook.inspect({ kind: "workbook,sheet,table", maxChars: 5000, tableMaxRows: 4, tableMaxCols: 6, tableMaxCellChars: 80 });
  await fs.writeFile(`${filePath}.inspect.ndjson`, check.ndjson || "");
  const sheetNames = workbook.worksheets.items.map((s) => s.name);
  const previews = [];
  for (const name of sheetNames) {
    const preview = await workbook.render({ sheetName: name, autoCrop: "all", scale: 1, format: "png" });
    const bytes = new Uint8Array(await preview.arrayBuffer());
    const previewPath = path.join(path.dirname(filePath), `${previewName}_${name}.png`);
    await fs.writeFile(previewPath, bytes);
    previews.push(previewPath);
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(filePath);
  return { filePath, sheets: sheetNames, previews };
}

const results = [];
for (const domain of specs) {
  const folder = path.join(out, domain.folder);
  const dataPath = path.join(folder, `03_实验数据与统计表_${domain.key}.xlsx`);
  const evidencePath = path.join(folder, `04_文献证据与主张对照矩阵_${domain.key}.xlsx`);
  results.push(await saveWorkbook(dataWorkbook(domain), dataPath, `${domain.code}_data`));
  results.push(await saveWorkbook(evidenceWorkbook(domain), evidencePath, `${domain.code}_evidence`));
}
console.log(JSON.stringify({ output: out, workbooks: results }, null, 2));
