<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Plus, Save, ShieldCheck, X } from '@lucide/vue'
import { api, getUser } from '../api'
import type { ModerationRules } from '../types'
import { useToast } from '../composables/useToast'

const { toast } = useToast()
const canWrite = () => {
  const role = getUser()?.role
  return role === 'admin' || role === 'host'
}

const rules = ref<ModerationRules>({
  gift_alert_threshold: 500,
  gift_thank_threshold: 50,
  vip_welcome_level: 40,
  enabled_actions: [],
  sensitive_words: [],
})
const newWord = ref('')
const saving = ref(false)

const actionOptions = [
  { key: 'auto_reply', label: '自动回复', desc: '对提问、价格、下单弹幕回复知识库内容' },
  { key: 'welcome', label: '进场欢迎', desc: 'VIP 或高等级观众进场时自动欢迎' },
  { key: 'alert', label: '告警提醒', desc: '敏感、负面弹幕与大额礼物触发告警' },
  { key: 'escalate', label: '升级处理', desc: '疑似引流、垃圾弹幕升级给场控处理' },
]

onMounted(load)

async function load() {
  try {
    rules.value = await api.getRules()
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

function toggleAction(key: string) {
  const actions = rules.value.enabled_actions
  if (actions.includes(key)) {
    rules.value.enabled_actions = actions.filter((a) => a !== key)
  } else {
    rules.value.enabled_actions = [...actions, key]
  }
}

function addWord() {
  const word = newWord.value.trim()
  if (!word) return
  if (rules.value.sensitive_words.includes(word)) {
    toast('该敏感词已存在', 'error')
    return
  }
  rules.value.sensitive_words = [...rules.value.sensitive_words, word]
  newWord.value = ''
}

function removeWord(word: string) {
  rules.value.sensitive_words = rules.value.sensitive_words.filter((w) => w !== word)
}

async function save() {
  if (!canWrite()) {
    toast('当前角色无权限修改规则', 'error')
    return
  }
  saving.value = true
  try {
    rules.value = await api.updateRules(rules.value)
    toast('规则已保存，实时生效')
  } catch (e: any) {
    toast(e.message, 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="rules">
    <div class="page-title">
      <div>
        <h1>场控规则</h1>
        <p>调整 AI 自动动作、礼物阈值与敏感词，保存后实时生效</p>
      </div>
      <button class="btn btn-primary" :disabled="saving || !canWrite()" @click="save">
        <Save :size="15" />
        {{ saving ? '保存中…' : '保存规则' }}
      </button>
    </div>

    <div class="layout">
      <section class="card panel">
        <header class="panel-head">
          <ShieldCheck :size="16" />
          <span>自动动作</span>
        </header>
        <div class="action-list">
          <label v-for="item in actionOptions" :key="item.key" class="action-item">
            <div class="action-text">
              <span class="action-label">{{ item.label }}</span>
              <span class="action-desc">{{ item.desc }}</span>
            </div>
            <span class="switch" :class="{ on: rules.enabled_actions.includes(item.key) }">
              <input
                type="checkbox"
                :checked="rules.enabled_actions.includes(item.key)"
                @change="toggleAction(item.key)"
              />
              <span class="track"><span class="knob" /></span>
            </span>
          </label>
        </div>
      </section>

      <section class="card panel">
        <header class="panel-head">
          <span>阈值设置</span>
        </header>
        <div class="threshold-list">
          <div class="field">
            <label>礼物感谢阈值（价值 ≥ 此值自动感谢）</label>
            <input v-model.number="rules.gift_thank_threshold" type="number" min="0" class="input" />
          </div>
          <div class="field">
            <label>大额礼物告警阈值（价值 ≥ 此值置顶告警）</label>
            <input v-model.number="rules.gift_alert_threshold" type="number" min="0" class="input" />
          </div>
          <div class="field">
            <label>VIP 欢迎等级（等级 ≥ 此值自动欢迎）</label>
            <input v-model.number="rules.vip_welcome_level" type="number" min="1" max="200" class="input" />
          </div>
        </div>
      </section>

      <section class="card panel wide">
        <header class="panel-head">
          <span>敏感词管理</span>
        </header>
        <div class="word-input-row">
          <input
            v-model="newWord"
            class="input"
            placeholder="输入敏感词，回车或点击添加"
            @keydown.enter.prevent="addWord"
          />
          <button class="btn" @click="addWord">
            <Plus :size="15" /> 添加
          </button>
        </div>
        <div class="words">
          <span v-for="word in rules.sensitive_words" :key="word" class="word-chip">
            {{ word }}
            <button class="word-x" title="移除" @click="removeWord(word)">
              <X :size="13" />
            </button>
          </span>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  align-items: start;
}

@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
}

.panel {
  padding: 16px;
}

.panel.wide {
  grid-column: 1 / -1;
}

.panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 650;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-soft);
  margin-bottom: 14px;
}

.panel-head svg {
  color: var(--accent);
}

.action-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.action-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 12px;
  border: 1px solid var(--border-soft);
  border-radius: var(--radius);
  cursor: pointer;
}

.action-item:hover {
  border-color: var(--border);
}

.action-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.action-label {
  font-weight: 600;
}

.action-desc {
  font-size: 12.5px;
  color: var(--text-muted);
}

.switch input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.track {
  position: relative;
  display: inline-block;
  width: 40px;
  height: 22px;
  border-radius: 999px;
  background: var(--surface-3);
  border: 1px solid var(--border);
  transition: background 0.18s;
}

.knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--text-muted);
  transition:
    transform 0.18s,
    background 0.18s;
}

.switch.on .track {
  background: var(--accent-soft);
  border-color: var(--accent);
}

.switch.on .knob {
  transform: translateX(18px);
  background: var(--accent);
}

.threshold-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.word-input-row {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}

.words {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.word-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 4px 11px;
  border-radius: 999px;
  background: rgba(248, 113, 113, 0.12);
  border: 1px solid rgba(248, 113, 113, 0.3);
  color: #fecaca;
  font-size: 13px;
}

.word-x {
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: inherit;
}

.word-x:hover {
  background: rgba(248, 113, 113, 0.2);
}
</style>

