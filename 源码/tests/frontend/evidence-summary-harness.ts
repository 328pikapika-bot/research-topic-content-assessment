import { createApp, h } from 'vue'
import EvidenceSummary from '../../frontend/web/src/components/EvidenceSummary.vue'
import { evidenceSummaries } from '../../frontend/web/src/services/evidenceSummary'

const records = Array.from({ length: 5 }, (_, i) => ({ evidence_id: `E${i}`, title: ['固态电池界面压力与循环稳定性的实验研究', '不同约束条件下的界面失效与适用限制', '固态电解质界面方法综述', '压力测量与控制方法', '电池循环验证条件对比'][i], year: 2025, authors: ['研究作者'], source_kind: i < 2 ? 'fulltext_library' : 'nstl', fulltext_available: true, anchor_verified: true }))
const links = records.map((r, i) => ({ evidence_id: r.evidence_id, relation: i === 1 ? 'contradicts' : i === 0 ? 'supports' : 'context', uncertainty: '仅适用于文献报告的材料、压力与循环条件，项目实际表现仍需实验验证。' }))
const chunks = records.map(r => ({ evidence_id: r.evidence_id, chunk_id: `${r.evidence_id}-1`, text: '原文展示验收内容：压力、温度和样本条件会影响循环稳定性。The effects depend on the electrolyte, stack pressure and cycling protocol. '.repeat(5), section_path: 'Results / Cycling experiments', locator: '结果章节 · 第 4 段', theme_ids: ['T1'] }))
createApp({ render: () => h(EvidenceSummary, { items: evidenceSummaries(links, records, chunks, 'C1') }) }).mount('#app')
