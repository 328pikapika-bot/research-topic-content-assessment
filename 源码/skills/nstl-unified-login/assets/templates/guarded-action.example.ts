/**
 * 带鉴权守卫的业务操作示例
 * 展示如何在用户相关操作前强制检查登录状态
 */
import { requireUserScope } from './nstlAuth'

/**
 * 示例：收藏文献 —— 必须登录后才能操作
 */
export async function collectPaper(paperId: string) {
  // 强制鉴权：未登录会自动弹出登录框并抛出异常
  const userId = await requireUserScope()

  // 鉴权通过后执行业务逻辑
  const response = await fetch('/api/collect', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Scope': userId,
    },
    body: JSON.stringify({ paperId }),
  })

  if (!response.ok) {
    throw new Error('收藏失败')
  }

  return response.json()
}

/**
 * 示例：提交评价 —— 必须登录后才能操作
 */
export async function submitReview(taskId: string, content: string) {
  const userId = await requireUserScope()

  const response = await fetch('/api/review', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Scope': userId,
    },
    body: JSON.stringify({ taskId, content }),
  })

  if (!response.ok) {
    throw new Error('提交评价失败')
  }

  return response.json()
}
