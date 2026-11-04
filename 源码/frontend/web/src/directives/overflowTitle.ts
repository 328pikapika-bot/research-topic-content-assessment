import type { Directive } from 'vue'

type OverflowTitleElement = HTMLElement & {
  __overflowTitleObserver?: ResizeObserver
}

function updateTitle(el: OverflowTitleElement) {
  const isOverflowing = el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1
  if (isOverflowing) {
    el.title = el.textContent?.trim() || ''
  } else {
    el.removeAttribute('title')
  }
}

export const overflowTitle: Directive<OverflowTitleElement> = {
  mounted(el) {
    updateTitle(el)
    el.__overflowTitleObserver = new ResizeObserver(() => updateTitle(el))
    el.__overflowTitleObserver.observe(el)
  },
  updated(el) {
    updateTitle(el)
  },
  unmounted(el) {
    el.__overflowTitleObserver?.disconnect()
    delete el.__overflowTitleObserver
  },
}
