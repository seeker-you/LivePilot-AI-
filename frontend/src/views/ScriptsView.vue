<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Library, Pencil, Play, Plus, Trash2, X } from '@lucide/vue'
import { api, getUser } from '../api'
import type { Script } from '../types'
import { useToast } from '../composables/useToast'

const { toast } = useToast()
const scripts = ref<Script[]>([])
const showingForm = ref(false)
const editing = ref<Script | null>(null)
const role = getUser()?.role
const canWrite = computed(() => role === 'admin' || role === 'host')

const form = ref<{
  title: string
  content: string
  type: 'text' | 'audio'
  tags: string
}>({ title: '', content: '', type: 'text', tags: '' })

onMounted(load)

async function load() {
  try {
    scripts.value = await api.listScripts()
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

function openCreate() {
  editing.value = null
  form.value = { title: '', content: '', type: 'text', tags: '' }
  showingForm.value = true
}

function openEdit(item: Script) {
  editing.value = item
  form.value = {
    title: item.title,
    content: item.content,
    type: item.type,
    tags: item.tags.join(', '),
  }
  showingForm.value = true
}

async function submit() {
  if (!form.value.title.trim() || !form.value.content.trim()) {
    toast('请填写标题和内容', 'error')
    return
  }
  const body = {
    title: form.value.title,
    content: form.value.content,
    type: form.value.type,
    tags: form.value.tags
      .split(/[,，]/)
      .map((t) => t.trim())
      .filter(Boolean),
  }
  try {
    if (editing.value) {
      await api.updateScript(editing.value.id, body)
      toast('话术已更新')
    } else {
      await api.createScript(body)
      toast('话术已添加')
    }
    showingForm.value = false
    await load()
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

async function toggle(item: Script) {
  try {
    await api.updateScript(item.id, { enabled: !item.enabled })
    item.enabled = !item.enabled
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

async function remove(item: Script) {
  try {
    await api.deleteScript(item.id)
    await load()
    toast('已删除')
  } catch (e: any) {
    toast(e.message, 'error')
  }
}
</script>

<template>
  <div class="scripts">
    <div class="page-title">
      <div>
        <h1>话术库</h1>
        <p>沉淀可循环口播的话术，AI 场控在无人互动时自动补位播放</p>
      </div>
      <button v-if="canWrite" class="btn btn-primary" @click="openCreate">
        <Plus :size="15" /> 新增话术
      </button>
    </div>

    <form v-if="showingForm" class="card form-card" @submit.prevent="submit">
      <div class="form-grid">
        <div class="field">
          <label>标题</label>
          <input v-model="form.title" class="input" placeholder="例如：欢迎新朋友" />
        </div>
        <div class="field">
          <label>类型</label>
          <select v-model="form.type" class="select">
            <option value="text">文字</option>
            <option value="audio">音频</option>
          </select>
        </div>
        <div class="field span-2">
          <label>标签（逗号分隔）</label>
          <input v-model="form.tags" class="input" placeholder="通用, 开场, 逼单" />
        </div>
      </div>
      <div class="field">
        <label>话术内容</label>
        <textarea v-model="form.content" class="textarea" placeholder="主播可循环口播的一句话" />
      </div>
      <div class="form-foot">
        <button type="button" class="btn" @click="showingForm = false">取消</button>
        <button type="submit" class="btn btn-primary">{{ editing ? '保存修改' : '添加话术' }}</button>
      </div>
    </form>

    <div v-if="!scripts.length" class="card empty-state">
      <Library :size="30" />
      <div>暂无话术，添加后即可用于自动补位播放</div>
    </div>

    <div v-else class="list">
      <article
        v-for="item in scripts"
        :key="item.id"
        class="card script-item"
        :class="{ disabled: !item.enabled }"
      >
        <div class="script-head">
          <div class="script-title">{{ item.title }}</div>
          <div class="script-actions">
            <span class="badge play-badge">
              <Play :size="12" /> {{ item.play_count }}
            </span>
            <button
              v-if="canWrite"
              class="icon-btn"
              title="编辑"
              @click="openEdit(item)"
            >
              <Pencil :size="14" />
            </button>
            <button
              v-if="canWrite"
              class="icon-btn danger"
              title="删除"
              @click="remove(item)"
            >
              <Trash2 :size="14" />
            </button>
          </div>
        </div>
        <p class="script-content">{{ item.content }}</p>
        <div class="script-foot">
          <div class="tags">
            <span v-for="t in item.tags" :key="t" class="tag"># {{ t }}</span>
          </div>
          <button class="toggle" :class="{ on: item.enabled }" @click="toggle(item)">
            {{ item.enabled ? '已启用' : '已停用' }}
          </button>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.form-card {
  padding: 16px;
  margin-bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-grid {
  display: grid;
  grid-template-columns: 2fr 1fr 2fr;
  gap: 12px;
}

.span-2 {
  grid-column: span 2;
}

@media (max-width: 800px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
  .span-2 {
    grid-column: span 1;
  }
}

.form-foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 48px;
  color: var(--text-faint);
}

.list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
}

@media (max-width: 900px) {
  .list {
    grid-template-columns: 1fr;
  }
}

.script-item {
  padding: 15px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: opacity 0.15s;
}

.script-item.disabled {
  opacity: 0.55;
}

.script-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.script-title {
  font-weight: 650;
  font-size: 15px;
}

.script-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.play-badge {
  background: var(--surface-2);
  color: var(--text-muted);
}

.icon-btn {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: transparent;
  color: var(--text-muted);
}

.icon-btn:hover {
  color: var(--text);
  border-color: #35435a;
}

.icon-btn.danger:hover {
  color: var(--negative);
  border-color: rgba(248, 113, 113, 0.4);
}

.script-content {
  margin: 0;
  color: var(--text-muted);
  line-height: 1.65;
}

.script-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag {
  font-size: 12px;
  color: var(--text-faint);
  background: var(--surface-2);
  border-radius: 5px;
  padding: 1px 7px;
}

.toggle {
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--text-faint);
  padding: 3px 11px;
  font-size: 12.5px;
}

.toggle.on {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}
</style>
