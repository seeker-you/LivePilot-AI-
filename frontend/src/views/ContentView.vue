<script setup lang="ts">
import { ref } from 'vue'
import {
  Check,
  Copy,
  Hash,
  Megaphone,
  MessageSquare,
  Mic,
  Save,
  ShoppingBag,
  Sparkles,
} from '@lucide/vue'
import { api, streamContent } from '../api'
import { useToast } from '../composables/useToast'

const { toast } = useToast()

const kinds = [
  { kind: 'title', label: '直播标题', icon: Hash, placeholder: '例如：熬夜修护面霜专场' },
  { kind: 'opening', label: '开场话术', icon: Mic, placeholder: '例如：今晚美妆专场' },
  { kind: 'closing', label: '逼单话术', icon: Megaphone, placeholder: '例如：熬夜修护面霜' },
  { kind: 'qa', label: '答疑话术', icon: MessageSquare, placeholder: '例如：熬夜修护面霜' },
  { kind: 'product_script', label: '商品口播', icon: ShoppingBag, placeholder: '例如：熬夜修护面霜' },
]

const styles = ['自然亲和', '专业严谨', '幽默活泼', '高转化逼单']

const kind = ref('opening')
const topic = ref('')
const style = ref('自然亲和')
const output = ref('')
const sources = ref<string[]>([])
const generating = ref(false)
const copied = ref(false)
const saving = ref(false)

const activeKind = () => kinds.find((k) => k.kind === kind.value) ?? kinds[0]

async function generate() {
  if (generating.value) return
  generating.value = true
  output.value = ''
  sources.value = []
  try {
    await streamContent(
      { kind: kind.value, topic: topic.value.trim(), style: style.value, context: [] },
      (delta) => (output.value += delta),
      (s) => (sources.value = s),
    )
  } catch (e: any) {
    toast(e.message, 'error')
  } finally {
    generating.value = false
  }
}

async function copy() {
  if (!output.value) return
  try {
    await navigator.clipboard.writeText(output.value)
    copied.value = true
    toast('已复制到剪贴板')
    setTimeout(() => (copied.value = false), 1600)
  } catch {
    toast('复制失败', 'error')
  }
}

async function saveToLibrary() {
  if (!output.value || saving.value) return
  saving.value = true
  try {
    const label = activeKind().label
    const subject = (topic.value || '通用').trim().slice(0, 40)
    await api.createScript({
      title: `${label}｜${subject}`.slice(0, 120),
      content: output.value,
      type: 'text',
      tags: [label, ...(subject !== '通用' ? [subject] : [])],
    })
    toast('已存入话术库，直播中会自动轮播')
  } catch (e: any) {
    toast(e.message || '存入失败', 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="content">
    <div class="page-title">
      <div>
        <h1>内容生成</h1>
        <p>一键生成直播标题与口播脚本，结合知识库与文案风格</p>
      </div>
    </div>

    <section class="card form-card">
      <div class="kind-row">
        <button
          v-for="item in kinds"
          :key="item.kind"
          class="kind-btn"
          :class="{ active: kind === item.kind }"
          @click="kind = item.kind"
        >
          <component :is="item.icon" :size="16" />
          <span>{{ item.label }}</span>
        </button>
      </div>

      <div class="form-grid">
        <div class="field">
          <label>主题 / 商品</label>
          <input
            v-model="topic"
            class="input"
            :placeholder="activeKind().placeholder"
          />
        </div>
        <div class="field">
          <label>文案风格</label>
          <select v-model="style" class="select">
            <option v-for="s in styles" :key="s" :value="s">{{ s }}</option>
          </select>
        </div>
      </div>

      <div class="form-foot">
        <button class="btn btn-primary" :disabled="generating" @click="generate">
          <Sparkles :size="15" />
          {{ generating ? '生成中…' : '生成内容' }}
        </button>
      </div>
    </section>

    <section class="card result-card">
      <header class="result-head">
        <span class="result-title">{{ activeKind().label }}结果</span>
        <div class="result-actions">
          <button class="btn btn-primary" :disabled="!output || saving" @click="saveToLibrary">
            <Save :size="15" />
            {{ saving ? '存入中…' : '存入话术库' }}
          </button>
          <button class="btn" :disabled="!output" @click="copy">
            <Check v-if="copied" :size="15" />
            <Copy v-else :size="15" />
            {{ copied ? '已复制' : '复制' }}
          </button>
        </div>
      </header>
      <div class="result-body">
        <div v-if="output" class="result-text">
          {{ output }}
          <span v-if="generating" class="cursor">▍</span>
        </div>
        <div v-else class="empty">选择类型并填写主题，点击「生成内容」</div>
      </div>
      <div v-if="sources.length" class="result-sources">
        参考知识：<span v-for="s in sources" :key="s" class="source-chip">{{ s }}</span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.form-card {
  padding: 16px;
  margin-bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.kind-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.kind-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 36px;
  padding: 0 14px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-muted);
  font-weight: 550;
  transition:
    background 0.15s,
    color 0.15s,
    border-color 0.15s;
}

.kind-btn:hover {
  color: var(--text);
  border-color: #35435a;
}

.kind-btn.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}

.form-grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 14px;
}

@media (max-width: 700px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}

.form-foot {
  display: flex;
  justify-content: flex-end;
}

.result-card {
  padding: 16px;
}

.result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-soft);
  margin-bottom: 14px;
}

.result-actions {
  display: flex;
  gap: 8px;
}

.result-title {
  font-weight: 650;
}

.result-body {
  min-height: 160px;
}

.result-text {
  white-space: pre-wrap;
  line-height: 1.8;
  color: var(--text);
  word-break: break-word;
}

.cursor {
  color: var(--accent);
  animation: pulse 1s infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

.result-sources {
  margin-top: 14px;
  font-size: 12.5px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.source-chip {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 1px 9px;
  color: var(--accent);
}
</style>

