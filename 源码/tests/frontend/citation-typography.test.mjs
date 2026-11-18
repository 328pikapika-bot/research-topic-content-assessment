import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

// 11/16 测试反馈：页面与报告中的文献引用标记必须与正文字号一致，不得再回退到小字号上标。
const component = fs.readFileSync(new URL('../../frontend/web/src/components/InlineCitations.vue', import.meta.url), 'utf8')
const typography = fs.readFileSync(new URL('../../backend/app/services/report_typography.py', import.meta.url), 'utf8')

test('inline citation markers render at body font size on pages', () => {
  const rule = component.match(/\.inline-citations \.citation\{[^}]+\}/)?.[0] || ''
  assert.ok(rule, 'citation rule must exist')
  assert.match(rule, /font-size:1em/)
  assert.doesNotMatch(rule, /\.78em|vertical-align:super|line-height:0/)
})

test('shared report typography keeps citations at body font size', () => {
  const rule = typography.match(/\.citation \{[^}]+\}/)?.[0] || ''
  assert.ok(rule, 'shared citation rule must exist')
  assert.match(rule, /font-size:1em/)
  assert.doesNotMatch(rule, /\.75em|vertical-align:super|line-height:0/)
})
