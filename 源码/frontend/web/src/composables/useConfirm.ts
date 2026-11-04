import { reactive, onMounted } from 'vue'

interface ConfirmState {
  visible: boolean
  message: string
  okText: string
  cancelText: string
}

const state = reactive<ConfirmState>({
  visible: false,
  message: '',
  okText: '确认',
  cancelText: '取消',
})

let pending: ((value: boolean) => void) | null = null

function showConfirm(message: string, okText = '确认', cancelText = '取消'): Promise<boolean> {
  state.message = message
  state.okText = okText
  state.cancelText = cancelText
  state.visible = true
  return new Promise((resolve) => { pending = resolve })
}

function ok() { state.visible = false; pending?.(true) }
function cancel() { state.visible = false; pending?.(false) }

export function useConfirm() {
  onMounted(() => {
    _showConfirm = showConfirm
  })
  return { state, ok, cancel }
}

let _showConfirm: typeof showConfirm | null = null

export async function confirm(message: string, okText?: string, cancelText?: string): Promise<boolean> {
  if (_showConfirm) return _showConfirm(message, okText, cancelText)
  return window.confirm(message)
}
