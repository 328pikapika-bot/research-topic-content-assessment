<template>
  <div class="topic-agent">
    <section class="landing-header">
      <div class="platform-section-kicker"><span class="platform-kicker-line"></span>智能选题智能体<span class="platform-kicker-line"></span></div>
      <h2 class="main-title"><span>从文献趋势中发现值得研究的问题</span></h2>
      <p class="subtitle">输入研究方向，选择时间范围与输出偏好，生成可验证、可落地的研究选题。</p>
    </section>

    <section class="landing-card">
      <form class="landing-form" @submit.prevent="submit">
        <div class="form-group full">
          <textarea
            v-model="topic"
            class="landing-topic-textarea"
            rows="5"
            placeholder="输入研究方向或问题，例如：生成式人工智能如何重塑科研知识发现模式？"
            :class="{ 'has-error': error }"
            @input="error = ''"
          ></textarea>
          <span v-if="error" class="error-message">{{ error }}</span>
        </div>

        <div class="form-row">
          <div class="form-group no-direction">
            <label>时间范围</label>
            <select v-model="timeRange">
              <option value="近五年文献优先">近五年文献优先</option>
              <option value="近三年文献优先">近三年文献优先</option>
              <option value="近十年文献">近十年文献</option>
              <option value="不限时间范围">不限时间范围</option>
            </select>
          </div>
          <div class="form-group no-direction">
            <label>输出偏好</label>
            <select v-model="outputPref">
              <option value="5个推荐选题">5 个推荐选题</option>
              <option value="3个推荐选题+2个探索选题">3 个推荐选题 + 2 个探索选题</option>
              <option value="5个探索选题">5 个探索选题</option>
            </select>
          </div>
        </div>

        <div class="form-actions full">
          <button type="submit" class="submit-btn" :disabled="loading">
            <span v-if="!loading">开始智能选题</span>
            <span v-else>生成中...</span>
          </button>
        </div>
      </form>
    </section>

    <section v-if="result.length" class="landing-card topic-result">
      <div class="title">选题结果</div>
      <div class="topic-result__chips">
        <span class="chip">时间范围：{{ timeRange }}</span>
        <span class="chip">输出偏好：{{ outputPref }}</span>
      </div>
      <ol class="topic-result__list">
        <li v-for="(item, i) in result" :key="i" class="topic-result__item">
          <div class="topic-result__rank">{{ i + 1 }}</div>
          <div class="topic-result__body">
            <strong>{{ item.title }}</strong>
            <p>{{ item.reason }}</p>
            <span class="topic-result__tag" :class="item.tag === '探索' ? 'topic-result__tag--explore' : ''">{{ item.tag }}</span>
          </div>
        </li>
      </ol>
      <p class="topic-result__hint">以上为演示结果，仅用于展示选题智能体的交互流程。</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const topic = ref('')
const timeRange = ref('近五年文献优先')
const outputPref = ref('5个推荐选题')
const error = ref('')
const loading = ref(false)
const result = ref<Array<{ title: string; reason: string; tag: string }>>([])

const RECOMMENDED = [
  { title: '面向科研全流程的大模型辅助知识发现方法研究', reason: '结合时间窗口内高频主题，聚焦知识发现链路中可自动化的关键环节。', tag: '推荐' },
  { title: '基于文献计量与引文网络的学科前沿识别与演化分析', reason: '引文网络与计量指标支撑前沿识别，证据充分、方法成熟。', tag: '推荐' },
  { title: '多源异构科技文献的语义检索与证据溯源技术', reason: '多源文献融合与可溯源需求明确，具备明确的应用场景。', tag: '推荐' },
  { title: '科研选题质量评价指标体系与自动化评估方法', reason: '选题评价缺乏统一标准，属于可验证的方法论缺口。', tag: '推荐' },
  { title: '大模型驱动的研究空白发现与可验证假设生成', reason: '结合大模型能力定位证据空白，选题新颖且可落地。', tag: '推荐' },
]

const EXPLORATORY = [
  { title: '生成式人工智能对科研知识生产范式的重构机制', reason: '跨学科视角切入，探索 AI 对科研流程的深层影响。', tag: '探索' },
  { title: '跨学科知识融合视角下的科学创新机会发现', reason: '关注学科交叉地带，挖掘潜在创新增长点。', tag: '探索' },
  { title: '科研诚信与 AI 辅助写作的边界与治理研究', reason: '新兴治理议题，文献基础薄弱但现实需求迫切。', tag: '探索' },
  { title: '面向开放科学的可复现研究基础设施设计', reason: '开放科学趋势下的基础设施缺口，具备探索价值。', tag: '探索' },
  { title: '大模型幻觉对科研结论可靠性的影响与缓解', reason: '针对模型风险的新兴方向，值得前瞻布局。', tag: '探索' },
]

function buildResult() {
  if (outputPref.value === '3个推荐选题+2个探索选题') {
    return [...RECOMMENDED.slice(0, 3), ...EXPLORATORY.slice(0, 2)]
  }
  if (outputPref.value === '5个探索选题') {
    return [...EXPLORATORY]
  }
  return [...RECOMMENDED]
}

function submit() {
  if (!topic.value.trim()) {
    error.value = '请输入研究方向或问题'
    return
  }
  error.value = ''
  loading.value = true
  result.value = []
  window.setTimeout(() => {
    result.value = buildResult()
    loading.value = false
  }, 800)
}
</script>
