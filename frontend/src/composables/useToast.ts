import { reactive } from 'vue'

interface ToastItem {
  id: number
  text: string
  kind: 'success' | 'error'
}

const state = reactive<{ items: ToastItem[] }>({ items: [] })
let seq = 0

export function useToast() {
  function push(text: string, kind: 'success' | 'error' = 'success') {
    const id = ++seq
    state.items.push({ id, text, kind })
    setTimeout(() => {
      const idx = state.items.findIndex((i) => i.id === id)
      if (idx >= 0) state.items.splice(idx, 1)
    }, 3600)
  }
  return { toasts: state.items, toast: push }
}

