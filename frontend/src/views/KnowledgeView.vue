<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { BookOpenText, FileUp, Plus, Search, Trash2, Wand2, X } from '@lucide/vue'
import { api, getUser } from '../api'
import type { KnowledgeDraft, KnowledgeItem } from '../types'
import { useToast } from '../composables/useToast'

const { toast } = useToast()
const items = ref<KnowledgeItem[]>([])
const search = ref('')
const showingForm = ref(false)
const showingOrganize = ref(false)
const organizing = ref(false)
const enriching = ref(false)
const user = getUser()
const canWrite = computed(() => user?.role === 'admin' || user?.role === 'host')

// AI 批量整理
const organizeText = ref('')
const organizeProduct = ref('')
const organizeCategory = ref('')
const drafts = ref<(KnowledgeDraft & { checked: boolean })[]>([])

const form = ref({
  title: '',
  product: '',
  category: 'product',
  content: '',
  tags: '',
})

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter((i) =>
    [i.title, i.product, i.content, i.tags.join(' ')].join(' ').toLowerCase().includes(q),
  )
})

onMounted(load)

async function load() {
  try {
    items.value = await api.listKnowledge()
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

async function submit() {
  if (!form.value.title.trim() || !form.value.content.trim()) {
    toast('请填写标题和内容', 'error')
    return
  }
  try {
    await api.createKnowledge({
      title: form.value.title,
      product: form.value.product || '通用',
      category: form.value.category,
      content: form.value.content,
      tags: form.value.tags
        .split(/[,，]/)
        .map((t) => t.trim())
        .filter(Boolean),
    })
    form.value = { title: '', product: '', category: 'product', content: '', tags: '' }
    showingForm.value = false
    await load()
    toast('知识条目已添加')
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

async function remove(item: KnowledgeItem) {
  try {
    await api.deleteKnowledge(item.id)
    await load()
    toast('已删除')
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

function readFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    organizeText.value = String(reader.result ?? '')
    toast(`已读取 ${file.name}，点「开始整理」拆条`)
  }
  reader.readAsText(file)
  input.value = ''
}

async function organize() {
  if (!organizeText.value.trim()) {
    toast('请先粘贴素材或读取文件', 'error')
    return
  }
  organizing.value = true
  try {
    const { items: parsed } = await api.organizeKnowledge({
      text: organizeText.value,
      product: organizeProduct.value.trim(),
      category: organizeCategory.value,
    })
    drafts.value = parsed.map((d) => ({ ...d, checked: true }))
    if (!drafts.value.length) toast('没有整理出条目，请检查素材', 'error')
    else toast(`已整理出 ${drafts.value.length} 条，可编辑后入库`)
  } catch (e: any) {
    toast(e.message, 'error')
  } finally {
    organizing.value = false
  }
}

function setDraftTags(d: KnowledgeDraft & { checked: boolean }, raw: string) {
  d.tags = raw
    .split(/[,，]/)
    .map((t) => t.trim())
    .filter(Boolean)
}

function removeDraft(idx: number) {
  drafts.value.splice(idx, 1)
}

async function importDrafts() {
  const selected = drafts.value.filter((d) => d.checked)
  if (!selected.length) {
    toast('请至少勾选一条', 'error')
    return
  }
  try {
    await Promise.all(
      selected.map((d) =>
        api.createKnowledge({
          title: d.title,
          product: d.product || '通用',
          category: d.category,
          content: d.content,
          tags: d.tags,
        }),
      ),
    )
    drafts.value = []
    organizeText.value = ''
    showingOrganize.value = false
    await load()
    toast(`已入库 ${selected.length} 条`)
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

async function enrich() {
  if (!form.value.content.trim()) {
    toast('请先填写内容', 'error')
    return
  }
  enriching.value = true
  try {
    const { item } = await api.enrichKnowledge({
      title: form.value.title,
      content: form.value.content,
      product: form.value.product,
      category: form.value.category,
    })
    form.value.title = item.title
    form.value.product = item.product
    form.value.category = item.category
    form.value.content = item.content
    form.value.tags = item.tags.join(', ')
    toast('已用 AI 补全，请确认后保存')
  } catch (e: any) {
    toast(e.message, 'error')
  } finally {
    enriching.value = false
  }
}
</script>

<template>
  <div class="knowledge">
    <div class="page-title">
      <div>
        <h1>知识库</h1>
        <p>商品卖点、物流售后与优惠政策的检索知识，驱动自动回复与话术生成</p>
      </div>
      <div class="actions">
        <div class="search-wrap">
          <Search :size="15" class="search-icon" />
          <input v-model="search" class="input search-input" placeholder="搜索知识…" />
        </div>
        <button v-if="canWrite" class="btn" @click="showingOrganize = !showingOrganize">
          <X v-if="showingOrganize" :size="15" />
          <Wand2 v-else :size="15" />
          {{ showingOrganize ? '收起' : 'AI 批量整理' }}
        </button>
        <button v-if="canWrite" class="btn btn-primary" @click="showingForm = !showingForm">
          <X v-if="showingForm" :size="15" />
          <Plus v-else :size="15" />
          {{ showingForm ? '取消' : '新增知识' }}
        </button>
      </div>
    </div>

    <!-- AI 批量整理 -->
    <section v-if="showingOrganize" class="card form-card">
      <div class="organize-head">
        <div>
          <div class="organize-title">AI 批量整理</div>
          <p class="organize-sub">粘贴零散规则/素材或读取文件，AI 拆成多条结构化知识，预览确认后入库</p>
        </div>
        <label class="btn">
          <FileUp :size="15" />
          读取文件（txt / csv / md）
          <input type="file" accept=".txt,.csv,.md,text/plain" class="hidden-file" @change="readFile" />
        </label>
      </div>
      <div class="field">
        <textarea
          v-model="organizeText"
          class="textarea organize-area"
          placeholder="例如：&#10;商品：熬夜修护面霜，玻色因+烟酰胺双效修护，28 天改善暗沉，敏感肌可用，今晚 199 元买一送一。&#10;发货：现货 48 小时内发货，中通/圆通，偏远顺丰补差，7 天无理由退换。&#10;优惠：关注主播领 10 元券，满 399 减 40。"
        ></textarea>
      </div>
      <div class="form-grid">
        <div class="field">
          <label>商品 / 主题（提示 AI）</label>
          <input v-model="organizeProduct" class="input" placeholder="选填，例如：熬夜修护面霜" />
        </div>
        <div class="field">
          <label>分类（提示 AI）</label>
          <select v-model="organizeCategory" class="select">
            <option value="">自动判断</option>
            <option value="product">商品</option>
            <option value="policy">政策</option>
            <option value="script">话术</option>
            <option value="general">通用</option>
          </select>
        </div>
      </div>
      <div class="form-foot">
        <button class="btn btn-primary" :disabled="organizing" @click="organize">
          <Wand2 :size="15" />
          {{ organizing ? '整理中…' : '开始整理' }}
        </button>
      </div>

      <template v-if="drafts.length">
        <div class="draft-bar">
          <span>整理结果（{{ drafts.length }} 条，勾选后可编辑）</span>
          <button class="btn btn-primary" @click="importDrafts">
            确认入库（{{ drafts.filter((d) => d.checked).length }} 条）
          </button>
        </div>
        <div class="draft-list">
          <article v-for="(d, idx) in drafts" :key="idx" class="card draft-item">
            <div class="draft-row">
              <input v-model="d.checked" type="checkbox" class="draft-check" />
              <input v-model="d.title" class="input" />
              <input v-model="d.product" class="input draft-product" placeholder="通用" />
              <select v-model="d.category" class="select draft-cat">
                <option value="product">商品</option>
                <option value="policy">政策</option>
                <option value="script">话术</option>
                <option value="general">通用</option>
              </select>
              <button class="icon-btn danger" title="移除" @click="removeDraft(idx)">
                <Trash2 :size="15" />
              </button>
            </div>
            <textarea v-model="d.content" class="textarea draft-content"></textarea>
            <div class="draft-tags">
              标签：
              <input
                class="input draft-tags-input"
                :value="d.tags.join(', ')"
                @input="setDraftTags(d, ($event.target as HTMLInputElement).value)"
                placeholder="逗号分隔，覆盖观众问法"
              />
            </div>
          </article>
        </div>
      </template>
    </section>

    <form v-if="showingForm" class="card form-card" @submit.prevent="submit">
      <div class="form-grid">
        <div class="field">
          <label>标题</label>
          <input v-model="form.title" class="input" placeholder="例如：主推商品卖点" />
        </div>
        <div class="field">
          <label>商品 / 主题</label>
          <input v-model="form.product" class="input" placeholder="例如：熬夜修护面霜" />
        </div>
        <div class="field">
          <label>分类</label>
          <select v-model="form.category" class="select">
            <option value="product">商品</option>
            <option value="policy">政策</option>
            <option value="script">话术</option>
            <option value="general">通用</option>
          </select>
        </div>
        <div class="field">
          <label>标签（逗号分隔）</label>
          <input v-model="form.tags" class="input" placeholder="面霜, 修护, 敏感肌" />
        </div>
      </div>
      <div class="field">
        <label>内容</label>
        <textarea v-model="form.content" class="textarea" placeholder="写清楚卖点、政策或话术，供 AI 检索引用" />
      </div>
      <div class="form-foot">
        <button type="button" class="btn" :disabled="enriching || !form.content.trim()" @click="enrich">
          <Wand2 :size="15" />
          {{ enriching ? '补全中…' : 'AI 补全标签' }}
        </button>
        <button type="submit" class="btn btn-primary">保存知识</button>
      </div>
    </form>

    <div v-if="!filtered.length" class="card empty-state">
      <BookOpenText :size="30" />
      <div>暂无知识条目</div>
    </div>

    <div v-else class="list">
      <article v-for="item in filtered" :key="item.id" class="card kb-item">
        <div class="kb-head">
          <div class="kb-title">{{ item.title }}</div>
          <div class="kb-meta">
            <span class="badge cat-badge">{{ item.category }}</span>
            <span class="product">{{ item.product }}</span>
          </div>
        </div>
        <p class="kb-content">{{ item.content }}</p>
        <div class="kb-foot">
          <div class="tags">
            <span v-for="t in item.tags" :key="t" class="tag"># {{ t }}</span>
          </div>
          <button
            v-if="canWrite"
            class="icon-btn danger"
            title="删除"
            @click="remove(item)"
          >
            <Trash2 :size="15" />
          </button>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.search-wrap {
  position: relative;
}

.search-icon {
  position: absolute;
  left: 11px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-faint);
}

.search-input {
  width: 230px;
  padding-left: 34px;
}

.form-card {
  padding: 18px;
  margin-bottom: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

@media (max-width: 1000px) {
  .form-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

.form-foot {
  display: flex;
  justify-content: flex-end;
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

.kb-item {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kb-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.kb-title {
  font-weight: 650;
  font-size: 15px;
}

.kb-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.cat-badge {
  background: var(--accent-soft);
  color: var(--accent);
}

.product {
  font-size: 12.5px;
  color: var(--text-muted);
}

.kb-content {
  margin: 0;
  color: var(--text-muted);
  line-height: 1.65;
}

.kb-foot {
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

.icon-btn {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: transparent;
  color: var(--text-muted);
}

.icon-btn.danger:hover {
  color: var(--negative);
  border-color: rgba(248, 113, 113, 0.4);
}

/* AI 批量整理 */
.organize-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.organize-title {
  font-weight: 650;
  font-size: 15px;
}

.organize-sub {
  margin: 3px 0 0;
  color: var(--text-muted);
  font-size: 12.5px;
}

.hidden-file {
  display: none;
}

.organize-area {
  min-height: 120px;
}

.draft-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border-soft);
  color: var(--text-muted);
  font-size: 13px;
}

.draft-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.draft-item {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.draft-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.draft-check {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
  flex-shrink: 0;
}

.draft-product {
  max-width: 160px;
  flex-shrink: 1;
}

.draft-cat {
  max-width: 110px;
  flex-shrink: 0;
}

.draft-content {
  min-height: 66px;
}

.draft-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--text-muted);
}

.draft-tags-input {
  flex: 1;
}

@media (max-width: 720px) {
  .draft-product,
  .draft-cat {
    display: none;
  }
}
</style>
