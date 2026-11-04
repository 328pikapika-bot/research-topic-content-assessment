import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'

const linkSource = fs.readFileSync(new URL('../../frontend/web/src/services/evidenceLinks.ts', import.meta.url), 'utf8')
  // Keep this contract test runnable on the repository's Node 18 runtime without
  // requiring a TypeScript loader: strip only the type syntax from this tiny helper.
  .replace(/export function resolveEvidenceUrl\(evidence: \{[^}]+\}\): string/, 'function resolveEvidenceUrl(evidence)')
  .replace(/\?:\s*string/g, '')
const context = { URL }
vm.runInNewContext(`${linkSource}; globalThis.resolveEvidenceUrl = resolveEvidenceUrl`, context)
assert.equal(context.resolveEvidenceUrl({ nstl_url: 'https://nstl.test/1', source_url: 'https://source.test/1', doi: '10.1/x' }), 'https://nstl.test/1')
assert.equal(context.resolveEvidenceUrl({ source_url: 'https://source.test/1', doi: '10.1234/x' }), 'https://source.test/1')
assert.equal(context.resolveEvidenceUrl({ doi: '10.1234/x' }), 'https://doi.org/10.1234/x')
assert.equal(context.resolveEvidenceUrl({ paper_id: 'NSTL-002' }), 'https://www.nstl.gov.cn/paper_detail.html?id=NSTL-002')
assert.equal(context.resolveEvidenceUrl({ url: '/', doi: '' }), '')

const page = fs.readFileSync(new URL('../../frontend/web/src/pages/TaskDetailPage.vue', import.meta.url), 'utf8')
const resultStyles = fs.readFileSync(new URL('../../frontend/web/src/styles/assessment-results.css', import.meta.url), 'utf8')
assert.match(page, /const claimPageSize = 8/)
assert.doesNotMatch(page, /reportClaims\.value\.slice\(0,\s*8\)/)
const stageSource = fs.readFileSync(new URL('../../frontend/web/src/services/userSteps.ts', import.meta.url), 'utf8').split('export const ASSESSMENT_STEPS:')[1]
const stageKeys = new Set([...stageSource.matchAll(/'(document_intake|claim_extraction|retrieval|comparison|assessment|landscape|quality_gate|report)'/g)].map((match) => match[1]))
assert.deepEqual([...stageKeys].sort(), ['assessment', 'claim_extraction', 'comparison', 'document_intake', 'landscape', 'quality_gate', 'report', 'retrieval'])
assert.match(page, /optgroup[^>]+label="上传材料"/)
assert.match(page, /optgroup[^>]+label="检索文献"/)
assert.doesNotMatch(page, /<label>评估范围<select/)
assert.doesNotMatch(page, /重新检查上传材料/)
assert.doesNotMatch(page, /按当前自动判断继续/)
assert.match(page, /pendingClarificationOption\.value = isClaimExtractionConfirmation\.value \? '' : 'continue_with_assumptions'/)
assert.match(page, /isReportRecovery/)
assert.match(page, /isClaimExtractionConfirmation && !pendingClarificationOption/)
assert.doesNotMatch(page, /基于原材料生成新版/)
assert.doesNotMatch(page, /待实验验证/)
assert.match(page, /userSteps.length.*个评估阶段/)
assert.match(page, /LiteratureTrendChart[^>]*:series="frozenTrendSeries"/)
const trend = fs.readFileSync(new URL('../../frontend/web/src/components/LiteratureTrendChart.vue', import.meta.url), 'utf8')
assert.match(trend, /overflow-x:\s*auto/)
assert.match(page, /按报告引用文献统计年份分布，反映本次检索样本/)
assert.match(page, /stage\.started_at \? Date\.parse\(stage\.started_at\)/)
assert.match(page, /Math\.round\(elapsed \* 1000\).*毫秒/)
assert.match(page, /用时未记录/)
assert.match(page, /用户输入材料/)
console.log('content assessment priority UI contracts passed')

assert.doesNotMatch(page, /<summary>追溯详情<\/summary>/)
assert.match(page, /comparisonGroups/)
assert.match(page, /researchPointName\(claim\)/)
