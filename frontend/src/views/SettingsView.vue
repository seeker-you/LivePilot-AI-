<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { KeyRound, PlugZap, Save, SlidersHorizontal, Sparkles } from '@lucide/vue'
import { api } from '../api'
import { useToast } from '../composables/useToast'

const { toast } = useToast()
const tab = ref<'llm' | 'persona' | 'retrieval'>('llm')
const loading = ref(true)
const saving = ref(false)
const testing = ref(false)

// ── LLM 接入 ────────────────────────────────────────────────
const form = ref({
  llm_api_key: '',
  llm_base_url: '',
  llm_model: '',
  llm_embedding_model: '',
  llm_embedding_base_url: '',
  llm_embedding_api_key: '',
})
const keyMask = ref('')
const embeddingKeyMask = ref('')

// ── AI 风格（人设 → 系统提示词） ────────────────────────────
const persona = ref({
  persona_name: '',
  persona_personality: '',
  persona_style: '',
  persona_knowledge_scope: '',
  persona_forbidden_topics: '',
})

// ── 知识库检索（分块与 top-k） ───────────────────────────────
const retrieval = ref({
  chunk_size: 400,
  chunk_overlap: 50,
  top_k: 3,
})

const retrievalError = ref('')

const personaPrompt = computed(() => {
  const p = persona.value
  const parts = [
    `你是${p.persona_name || '小助手'}，一位正在直播的主播。`,
    `性格特点：${p.persona_personality || '…'}`,
    `说话风格：${p.persona_style || '…'}`,
    `知识范围：${p.persona_knowledge_scope || '…'}`,
  ]
  const topics = (p.persona_forbidden_topics || '')
    .split(/[，,]/)
    .map((t) => t.trim())
    .filter(Boolean)
  if (topics.length) parts.push(`禁止谈论：${topics.join('、')}`)
  return parts.join('\n')
})

onMounted(async () => {
  try {
    const [cfg, p] = await Promise.all([api.getLlmConfig(), api.getPersona()])
    keyMask.value = cfg.llm_api_key
    embeddingKeyMask.value = cfg.llm_embedding_api_key
    form.value = {
      llm_api_key: '',
      llm_base_url: cfg.llm_base_url,
      llm_model: cfg.llm_model,
      llm_embedding_model: cfg.llm_embedding_model,
      llm_embedding_base_url: cfg.llm_embedding_base_url ?? '',
      llm_embedding_api_key: '',
    }
    persona.value = {
      persona_name: p.persona_name ?? '',
      persona_personality: p.persona_personality ?? '',
      persona_style: p.persona_style ?? '',
      persona_knowledge_scope: p.persona_knowledge_scope ?? '',
      persona_forbidden_topics: p.persona_forbidden_topics ?? '',
    }
  } catch (e: any) {
    toast(e.message, 'error')
  }
  // 检索配置单独拉取：单个接口失败不影响 LLM/人设 tab
  try {
    const rc = await api.getRetrievalConfig()
    retrieval.value = {
      chunk_size: rc.chunk_size ?? 400,
      chunk_overlap: rc.chunk_overlap ?? 50,
      top_k: rc.top_k ?? 3,
    }
  } catch (e: any) {
    toast(e.message, 'error')
  }
  loading.value = false
})

async function save() {
  saving.value = true
  retrievalError.value = ''
  try {
    if (tab.value === 'llm') {
      const payload: Record<string, string> = {
        llm_base_url: form.value.llm_base_url,
        llm_model: form.value.llm_model,
        llm_embedding_model: form.value.llm_embedding_model,
        llm_embedding_base_url: form.value.llm_embedding_base_url,
      }
      // 留空表示沿用现有 Key；输入新值则覆盖
      if (form.value.llm_api_key) payload.llm_api_key = form.value.llm_api_key
      if (form.value.llm_embedding_api_key)
        payload.llm_embedding_api_key = form.value.llm_embedding_api_key
      const cfg = await api.updateLlmConfig(payload)
      keyMask.value = cfg.llm_api_key
      embeddingKeyMask.value = cfg.llm_embedding_api_key
      form.value.llm_api_key = ''
      form.value.llm_embedding_api_key = ''
      toast('配置已保存')
    } else if (tab.value === 'retrieval') {
      if (retrieval.value.chunk_overlap >= retrieval.value.chunk_size) {
        retrievalError.value = '重叠长度需小于分块大小'
        return
      }
      await api.updateRetrievalConfig({
        chunk_size: retrieval.value.chunk_size,
        chunk_overlap: retrieval.value.chunk_overlap,
        top_k: retrieval.value.top_k,
      })
      toast('检索配置已保存，下次弹幕检索即时生效')
    } else {
      await api.updatePersona({ ...persona.value })
      toast('AI 风格已保存，话术/复盘生成即时生效')
    }
  } catch (e: any) {
    toast(e.message, 'error')
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  testing.value = true
  toast('正在测试连通（本地模型冷启动可能需 1–3 分钟）…')
  try {
    // 直接测试当前表单（未保存也能测），空字段沿用已保存配置
    const payload: Record<string, string> = {
      llm_base_url: form.value.llm_base_url,
      llm_model: form.value.llm_model,
      llm_embedding_model: form.value.llm_embedding_model,
      llm_embedding_base_url: form.value.llm_embedding_base_url,
    }
    if (form.value.llm_api_key) payload.llm_api_key = form.value.llm_api_key
    if (form.value.llm_embedding_api_key)
      payload.llm_embedding_api_key = form.value.llm_embedding_api_key
    const r = await api.testLlmConfig(payload)
    const emb = r.embedding
    if (emb && !emb.ok) toast(`对话连通（${r.model}），向量失败：${emb.error}`, 'error')
    else if (emb) toast(`连通成功（对话 ${r.model} / 向量 ${emb.model}）`)
    else toast(`连通成功（${r.model}）`)
  } catch (e: any) {
    const text = (e && e.message) || '测试失败，请检查接口地址与模型名'
    toast(`测试失败：${text}`, 'error')
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>系统设置</h1>
        <p class="page-sub">LLM 接入与 AI 人设风格，保存后立即生效</p>
      </div>
    </header>

    <div class="tabs">
      <button class="tab" :class="{ active: tab === 'llm' }" @click="tab = 'llm'">
        <KeyRound :size="14" /> LLM 接入
      </button>
      <button class="tab" :class="{ active: tab === 'retrieval' }" @click="tab = 'retrieval'">
        <SlidersHorizontal :size="14" /> 知识库检索
      </button>
      <button class="tab" :class="{ active: tab === 'persona' }" @click="tab = 'persona'">
        <Sparkles :size="14" /> AI 风格
      </button>
    </div>

    <div v-if="loading" class="card placeholder-card">加载中…</div>

    <!-- LLM 接入 -->
    <form v-else-if="tab === 'llm'" class="card form-card" @submit.prevent="save">
      <div class="group-title">对话模型</div>
      <div class="grid-2">
        <div class="field">
          <label>API Key
            <span class="hint">{{ keyMask ? `当前：${keyMask}` : '本地 Ollama 可留空' }}</span>
          </label>
          <div class="key-row">
            <KeyRound :size="16" class="field-icon" />
            <input
              v-model="form.llm_api_key"
              class="input"
              type="password"
              autocomplete="off"
              :placeholder="keyMask ? '留空保持不变，输入则覆盖' : 'sk-...'"
            />
          </div>
        </div>
        <div class="field">
          <label>接口地址（OpenAI 兼容）</label>
          <input
            v-model="form.llm_base_url"
            class="input"
            placeholder="https://api.openai.com/v1 或 http://localhost:11434/v1"
          />
        </div>
      </div>

      <div class="grid-2">
        <div class="field">
          <label>对话模型</label>
          <input v-model="form.llm_model" class="input" placeholder="gpt-4o-mini / qwen2.5:7b" />
        </div>
        <div class="field">
          <label>向量模型</label>
          <input v-model="form.llm_embedding_model" class="input" placeholder="text-embedding-3-small / nomic-embed-text" />
        </div>
      </div>

      <div class="group-title">向量接口（选填，留空复用对话）</div>
      <div class="grid-2">
        <div class="field">
          <label>向量 API Key
            <span class="hint">
              {{ embeddingKeyMask ? `当前：${embeddingKeyMask}` : '留空复用对话 Key' }}
            </span>
          </label>
          <div class="key-row">
            <KeyRound :size="16" class="field-icon" />
            <input
              v-model="form.llm_embedding_api_key"
              class="input"
              type="password"
              autocomplete="off"
              :placeholder="embeddingKeyMask ? '留空保持不变，输入则覆盖' : '本地 Ollama 可留空'"
            />
          </div>
        </div>
        <div class="field">
          <label>向量接口地址</label>
          <input
            v-model="form.llm_embedding_base_url"
            class="input"
            placeholder="http://localhost:11434/v1（例如向量走本地 Ollama）"
          />
        </div>
      </div>

      <div class="form-actions">
        <button type="submit" class="btn btn-primary" :disabled="saving">
          <Save :size="15" /> {{ saving ? '保存中…' : '保存配置' }}
        </button>
        <button type="button" class="btn" :disabled="testing" @click="testConnection">
          <PlugZap :size="15" /> {{ testing ? '测试中…' : '测试连通' }}
        </button>
      </div>

      <p class="tip">
        未配置 Key 时系统以离线启发式模式运行（分类、辅助、复盘均可工作）；
        配置后弹幕分类、话术生成、知识库向量检索将走真实模型。<br />
        兼容本地 Ollama：接口地址填 <code>http://localhost:11434/v1</code>，对话模型如
        <code>qwen2.5:7b</code>，Key 留空即可；向量模型如 <code>nomic-embed-text</code>，
        向量地址留空则复用本地接口。先执行 <code>ollama serve</code> 并
        <code>ollama pull &lt;模型&gt;</code>。
      </p>
    </form>

    <!-- 知识库检索 -->
    <form v-else-if="tab === 'retrieval'" class="card form-card" @submit.prevent="save">
      <div class="group-title">分块策略（对知识库检索即时生效）</div>
      <div class="grid-2">
        <div class="field">
          <label>分块大小（字符）</label>
          <input v-model.number="retrieval.chunk_size" class="input" type="number" min="50" max="4000" step="50" />
          <span class="field-desc">每条知识按多少个字符切一块，越长上下文越全、粒度越粗</span>
        </div>
        <div class="field">
          <label>块间重叠（字符）</label>
          <input v-model.number="retrieval.chunk_overlap" class="input" type="number" min="0" max="800" step="10" />
          <span class="field-desc">相邻块保留的重叠内容，避免关键信息恰好落在切缝处</span>
        </div>
      </div>

      <div class="group-title">召回设置</div>
      <div class="field">
        <label>Top-K（召回条数）</label>
        <input v-model.number="retrieval.top_k" class="input" type="number" min="1" max="50" step="1" />
        <span class="field-desc">命中后最多取几条知识用于自动回复 / 辅助回答</span>
      </div>

      <p v-if="retrievalError" class="form-error">{{ retrievalError }}</p>

      <div class="form-actions">
        <button type="submit" class="btn btn-primary" :disabled="saving">
          <Save :size="15" /> {{ saving ? '保存中…' : '保存配置' }}
        </button>
      </div>

      <p class="tip">
        保存后即时生效：检索时按当前分块大小/重叠对知识库内容切块匹配，
        再按 Top-K 取回最优条目。较小的分块对「弹幕短问题」命中更准，过小会丢上下文。
      </p>
    </form>

    <!-- AI 风格 -->
    <form v-else class="card form-card" @submit.prevent="save">
      <div class="grid-2">
        <div class="field">
          <label>主播名称</label>
          <input v-model="persona.persona_name" class="input" placeholder="如：小鹿" />
        </div>
        <div class="field">
          <label>性格特点</label>
          <input v-model="persona.persona_personality" class="input" placeholder="如：热情友好、幽默风趣" />
        </div>
      </div>

      <div class="field">
        <label>说话风格</label>
        <input v-model="persona.persona_style" class="input" placeholder="如：轻松活泼，喜欢用简短的句子" />
      </div>

      <div class="field">
        <label>知识范围</label>
        <input v-model="persona.persona_knowledge_scope" class="input" placeholder="如：美妆护肤、直播间商品卖点" />
      </div>

      <div class="field">
        <label>禁止话题（逗号分隔）</label>
        <input v-model="persona.persona_forbidden_topics" class="input" placeholder="如：政治、医疗诊断、绝对化疗效承诺" />
      </div>

      <div class="form-actions">
        <button type="submit" class="btn btn-primary" :disabled="saving">
          <Save :size="15" /> {{ saving ? '保存中…' : '保存人设' }}
        </button>
      </div>

      <div class="preview">
        <div class="preview-title">System Prompt 预览（注入话术助手 / 内容生成 / 复盘报告）</div>
        <pre class="preview-body">{{ personaPrompt }}</pre>
      </div>

      <p class="tip">
        人设会作为系统提示词前缀注入所有文本生成场景；弹幕意图分类为结构化任务，不受人设影响。
      </p>
    </form>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}

.tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: 1px solid var(--border-soft, #eee);
  border-radius: var(--radius, 8px);
  background: transparent;
  color: var(--text-dim, #888);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.tab.active {
  background: var(--accent-soft, #eef);
  border-color: var(--accent, #66f);
  color: var(--accent, #66f);
}

.form-card {
  max-width: 640px;
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.field .hint {
  font-weight: 400;
  color: var(--text-dim, #888);
  margin-left: 6px;
}

.field-desc {
  font-size: 12px;
  color: var(--text-dim, #888);
  line-height: 1.5;
}

.form-error {
  font-size: 12.5px;
  color: var(--negative, #e5484d);
  margin: 0;
}

.key-row {
  position: relative;
  display: flex;
  align-items: center;
}

.field-icon {
  position: absolute;
  left: 10px;
  color: var(--text-dim, #888);
}

.key-row .input {
  padding-left: 32px;
}

.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

@media (max-width: 640px) {
  .grid-2 {
    grid-template-columns: 1fr;
  }
}

.group-title {
  font-size: 12.5px;
  font-weight: 650;
  color: var(--text-muted);
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-soft, #e5e5e5);
}

.form-actions {
  display: flex;
  gap: 10px;
}

.preview {
  border: 1px dashed var(--border-soft, #ddd);
  border-radius: var(--radius, 8px);
  padding: 12px;
}

.preview-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-dim, #888);
  margin-bottom: 8px;
}

.preview-body {
  margin: 0;
  white-space: pre-wrap;
  font-size: 12px;
  line-height: 1.7;
  font-family: var(--mono, monospace);
}

.placeholder-card {
  max-width: 640px;
  padding: 30px;
  color: var(--text-dim, #888);
  text-align: center;
}

.tip {
  font-size: 12px;
  color: var(--text-dim, #888);
  line-height: 1.6;
  margin: 0;
}
</style>
