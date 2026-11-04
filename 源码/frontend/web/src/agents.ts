export type AgentId = 'assessment' | 'topic' | null

export type AgentStatus = 'available' | 'developing' | 'preview'

export interface AgentDefinition {
  id: Exclude<AgentId, null>
  name: string
  shortName: string
  icon: string
  status: AgentStatus
  statusLabel: string
  description: string
  placeholder: string
}

export const AGENTS: AgentDefinition[] = [
  {
    id: 'topic',
    name: '智能选题智能体',
    shortName: '智能选题',
    icon: 'lightbulb',
    status: 'available',
    statusLabel: '可用',
    description: '从文献趋势中发现值得研究的问题',
    placeholder: '输入研究方向或问题，例如：生成式人工智能如何重塑科研知识发现模式？',
  },
  {
    id: 'assessment',
    name: '创新性评估智能体',
    shortName: '创新性评估',
    icon: 'fact_check',
    status: 'available',
    statusLabel: '可用',
    description: '面向科研人员、评审专家与科技管理者，解决其在论文投稿、项目申报、成果鉴定等场景下，对内容创新性与合理性进行高效判断的需求，提供可验证的文献证据支撑，让评估更可靠、更科学、更高效。',
    placeholder: '输入评估对象、项目说明或核心主张，创新性评估智能体将为您形成可追溯的评估结论...',
  },
]

export function getAgent(id: AgentId | null | undefined): AgentDefinition | undefined {
  if (!id) return undefined
  return AGENTS.find((agent) => agent.id === id)
}

export function isAgentAvailable(id: AgentId): boolean {
  return id === 'assessment' || id === 'topic'
}
