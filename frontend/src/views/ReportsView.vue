<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { FileBarChart, FileText, RefreshCw } from '@lucide/vue'
import { api } from '../api'
import type { LiveSession, ReportMetrics, TimelinePoint } from '../types'
import { useToast } from '../composables/useToast'

const { toast } = useToast()
const sessions = ref<LiveSession[]>([])
const selectedId = ref('')
const metrics = ref<ReportMetrics | null>(null)
const report = ref('')
const loading = ref(false)

const selected = computed(() =>
  sessions.value.find((s) => s.id === selectedId.value) ?? null,
)

onMounted(loadSessions)

async function loadSessions() {
  try {
    sessions.value = await api.listSessions()
    if (sessions.value.length && !selectedId.value) {
      selectedId.value = sessions.value[0].id
      await loadMetrics()
    }
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

async function loadMetrics() {
  if (!selectedId.value) return
  try {
    metrics.value = await api.getMetrics(selectedId.value)
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

async function generate() {
  if (!selectedId.value) return
  loading.value = true
  report.value = ''
  try {
    const res = await api.generateReport(selectedId.value)
    metrics.value = res.metrics
    report.value = res.report
  } catch (e: any) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

function markdownToHtml(md: string): string {
  const escape = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const lines = md.split(/\r?\n/)
  const html: string[] = []
  let listOpen = false
  const closeList = () => {
    if (listOpen) {
      html.push('</ul>')
      listOpen = false
    }
  }
  for (const raw of lines) {
    const line = raw.trimEnd()
    if (/^### /.test(line)) {
      closeList()
      html.push(`<h3>${inline(escape(line.slice(4)))}</h3>`)
    } else if (/^## /.test(line)) {
      closeList()
      html.push(`<h2>${inline(escape(line.slice(3)))}</h2>`)
    } else if (/^- /.test(line)) {
      if (!listOpen) {
        html.push('<ul>')
        listOpen = true
      }
      html.push(`<li>${inline(escape(line.slice(2)))}</li>`)
    } else if (line.trim()) {
      closeList()
      html.push(`<p>${inline(escape(line))}</p>`)
    }
  }
  closeList()
  return html.join('')
}

function inline(text: string): string {
  return text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}

function pct(n: number) {
  if (!metrics.value) return '0%'
  const total = Math.max(
    1,
    metrics.value.sentiment.positive +
      metrics.value.sentiment.neutral +
      metrics.value.sentiment.negative,
  )
  return `${Math.round((n / total) * 100)}%`
}

function minuteTip(p: TimelinePoint): string {
  const parts = [`总互动 ${p.total}`]
  if (p.chat) parts.push(`弹幕 ${p.chat}`)
  if (p.gift_count) parts.push(`礼物 ${p.gift_count} 笔/价值 ${p.gift_value}`)
  if (p.likes) parts.push(`点赞 ${p.likes}`)
  if (p.enters) parts.push(`进场 ${p.enters}`)
  return `第 ${p.minute + 1} 分钟：${parts.join('、')}`
}

const trendTip = ref<{ text: string; x: number; y: number; below: boolean } | null>(null)

function onTrendMove(e: MouseEvent, text: string) {
  const wrap = (e.currentTarget as HTMLElement).closest('.trend-wrap') as HTMLElement | null
  if (!wrap) return
  const rect = wrap.getBoundingClientRect()
  const x = Math.min(Math.max(e.clientX - rect.left, 80), Math.max(80, rect.width - 80))
  const y = e.clientY - rect.top
  const below = y < 70
  trendTip.value = { text, x, y, below }
}

function hideTrendTip() {
  trendTip.value = null
}

const hasInsights = computed(() => {
  const i = metrics.value?.insights
  if (!i) return false
  return Boolean(i.peak) || i.cold_spots.length > 0 || i.highlights.length > 0
})

const chart = computed(() => {
  const t = metrics.value?.timeline ?? []
  if (t.length < 2) return null
  const W = 720
  const H = 190
  const padL = 46
  const padR = 12
  const padT = 14
  const padB = 24
  const innerW = W - padL - padR
  const innerH = H - padT - padB
  const maxTotal = Math.max(1, ...t.map((p) => p.total))
  const maxEnters = Math.max(1, ...t.map((p) => p.enters))
  const x = (i: number) => padL + (i / (t.length - 1)) * innerW
  const yTotal = (v: number) => padT + (1 - v / maxTotal) * innerH
  const yEnters = (v: number) => padT + (1 - v / maxEnters) * innerH
  const pts = t.map((p, i) => `${x(i).toFixed(1)},${yTotal(p.total).toFixed(1)}`)
  const enterPts = t.map((p, i) => `${x(i).toFixed(1)},${yEnters(p.enters).toFixed(1)}`).join(' ')
  const baseY = padT + innerH
  const areaPath = `M ${padL},${baseY} L ${pts.join(' L ')} L ${x(t.length - 1).toFixed(1)},${baseY} Z`
  const step = Math.max(1, Math.ceil(t.length / 12))
  const labels = t
    .map((p, i) => (i % step === 0 ? { x: +x(i).toFixed(1), text: `${p.minute + 1}m` } : null))
    .filter((l): l is { x: number; text: string } => l !== null)
  const grid = [0.25, 0.5, 0.75].map((r) => ({
    y: +yTotal(maxTotal * r).toFixed(1),
    v: Math.round(maxTotal * r),
  }))
  const yAxis = [
    { y: +baseY.toFixed(1), v: 0 },
    { y: +yTotal(maxTotal / 2).toFixed(1), v: Math.round(maxTotal / 2) },
    { y: +yTotal(maxTotal).toFixed(1), v: maxTotal },
  ]
  const clampIdx = (n: number) => Math.max(0, Math.min(n, t.length - 1))
  const halfStep = (x(1) - x(0)) / 2
  const ins = metrics.value?.insights
  const coldRects = (ins?.cold_spots ?? []).map((c) => {
    const x1 = Math.max(padL, x(clampIdx(c.from)) - halfStep)
    const x2 = Math.min(W - padR, x(clampIdx(c.to)) + halfStep)
    return { x: +x1.toFixed(1), w: +Math.max(2, x2 - x1).toFixed(1) }
  })
  const peak = ins?.peak
  const peakDot =
    peak && peak.minute >= 0 && peak.minute < t.length
      ? { cx: +x(peak.minute).toFixed(1), cy: +yTotal(peak.total).toFixed(1) }
      : null
  const peakLabel =
    peakDot && peak
      ? { x: peakDot.cx, y: +Math.min(H - padB, peakDot.cy + 16).toFixed(1), text: `峰值 ${peak.total}` }
      : null
  const hlDots = (ins?.highlights ?? [])
    .filter((h) => h.minute >= 0 && h.minute < t.length)
    .map((h) => ({ cx: +x(h.minute).toFixed(1), cy: +yTotal(t[h.minute].total).toFixed(1) }))
  const cellW = innerW / t.length
  const hoverCells = t.map((p, i) => ({
    x: +(padL + i * cellW).toFixed(1),
    w: +Math.max(2, cellW).toFixed(1),
    tip: minuteTip(p),
  }))
  return {
    W,
    H,
    padT,
    padL,
    innerH,
    totalPts: pts.join(' '),
    enterPts,
    areaPath,
    labels,
    grid,
    yAxis,
    coldRects,
    peakDot,
    peakLabel,
    hlDots,
    hoverCells,
  }
})

const convRate = computed(() => {
  if (!metrics.value || !metrics.value.chat_count) return null
  return Math.round((metrics.value.follow_count / metrics.value.chat_count) * 1000) / 10
})

const maxTagCount = computed(() =>
  Math.max(1, ...(metrics.value?.tag_hits ?? []).map((t) => t.count)),
)

const maxFunnelValue = computed(() =>
  Math.max(1, ...(metrics.value?.funnel ?? []).map((f) => f.value)),
)

const tagPct = (n: number) => Math.round((n / maxTagCount.value) * 100) + '%'
const funnelPct = (n: number) => Math.round((n / maxFunnelValue.value) * 100) + '%'
</script>

<template>
  <div class="reports">
    <div class="page-title">
      <div>
        <h1>复盘报告</h1>
        <p>聚合一场直播的数据，自动生成可执行的运营建议</p>
      </div>
      <div class="actions">
        <select v-model="selectedId" class="select session-select" @change="loadMetrics">
          <option v-if="!sessions.length" value="">暂无直播</option>
          <option v-for="s in sessions" :key="s.id" :value="s.id">
            {{ s.title }} · {{ s.status === 'live' ? '直播中' : '已结束' }}
          </option>
        </select>
        <button class="btn btn-primary" :disabled="!selected || loading" @click="generate">
          <RefreshCw v-if="loading" :size="15" class="spin" />
          <FileText v-else :size="15" />
          {{ loading ? '生成中…' : '生成复盘' }}
        </button>
      </div>
    </div>

    <div v-if="!selected" class="card empty-state">
      <FileBarChart :size="30" />
      <div>还没有直播记录，先去控制台开一场直播</div>
    </div>

    <template v-else>
      <div v-if="metrics" class="metric-grid">
        <div class="metric-box">
          <span>总弹幕</span>
          <strong>{{ metrics.chat_count }}</strong>
        </div>
        <div class="metric-box">
          <span>礼物价值</span>
          <strong>{{ metrics.gift_total }}</strong>
        </div>
        <div class="metric-box">
          <span>峰值在线</span>
          <strong>{{ metrics.peak_online }}</strong>
        </div>
        <div class="metric-box">
          <span>独立观众</span>
          <strong>{{ metrics.unique_users }}</strong>
        </div>
        <div class="metric-box">
          <span>新增关注</span>
          <strong>{{ metrics.follow_count }}</strong>
        </div>
        <div class="metric-box">
          <span>AI 动作</span>
          <strong>{{ metrics.action_count }}</strong>
        </div>
      </div>

      <section v-if="chart" class="card panel trend-panel">
        <header class="panel-head">节奏趋势（分钟级）</header>
        <div class="trend-wrap" @mouseleave="hideTrendTip">
          <svg class="trend-svg" :viewBox="`0 0 ${chart.W} ${chart.H}`" role="img" aria-label="分钟级互动趋势图">
            <line
              v-for="(g, i) in chart.grid"
              :key="'grid' + i"
              class="grid-line"
              :x1="chart.padL"
              :y1="g.y"
              :x2="chart.W - 12"
              :y2="g.y"
            />
            <text
              v-for="(a, i) in chart.yAxis"
              :key="'ya' + i"
              class="axis-label y-label"
              :x="chart.padL - 6"
              :y="a.y + 3"
              text-anchor="end"
            >{{ a.v }}</text>
            <rect
              v-for="(r, i) in chart.coldRects"
              :key="'cold' + i"
              class="cold-band"
              :x="r.x"
              :y="chart.padT"
              :width="r.w"
              :height="chart.innerH"
            />
            <path class="total-area" :d="chart.areaPath" />
            <polyline class="enter-line" :points="chart.enterPts" />
            <polyline class="total-line" :points="chart.totalPts" />
            <circle v-if="chart.peakDot" class="peak-dot" :cx="chart.peakDot.cx" :cy="chart.peakDot.cy" r="4" />
            <text v-if="chart.peakLabel" class="peak-label" :x="chart.peakLabel.x" :y="chart.peakLabel.y" text-anchor="middle">{{ chart.peakLabel.text }}</text>
            <circle
              v-for="(d, i) in chart.hlDots"
              :key="'hl' + i"
              class="hl-dot"
              :cx="d.cx"
              :cy="d.cy"
              r="3.5"
            />
            <text
              v-for="(l, i) in chart.labels"
              :key="'lab' + i"
              class="axis-label"
              :x="l.x"
              :y="chart.H - 6"
              text-anchor="middle"
            >{{ l.text }}</text>
            <rect
              v-for="(c, i) in chart.hoverCells"
              :key="'hov' + i"
              class="hover-cell"
              :x="c.x"
              y="0"
              :width="c.w"
              :height="chart.H"
              @mousemove="onTrendMove($event, c.tip)"
            />
          </svg>
          <div
            v-if="trendTip"
            class="trend-tip"
            :class="{ below: trendTip.below }"
            :style="{ left: trendTip.x + 'px', top: trendTip.y + 'px' }"
          >{{ trendTip.text }}</div>
        </div>
        <div class="legend">
          <span><i class="lg lg-total" />总互动</span>
          <span><i class="lg lg-enter" />进场</span>
          <span><i class="lg lg-peak" />峰值</span>
          <span><i class="lg lg-hl" />高光</span>
          <span><i class="lg lg-cold" />冷场</span>
        </div>
      </section>

      <section v-if="metrics" class="card panel insight-panel">
        <header class="panel-head">答疑覆盖与信息盲区</header>
        <div class="coverage-row">
          <span class="coverage-label">知识库答疑覆盖率</span>
          <b class="coverage-value">{{ metrics.coverage != null ? metrics.coverage + '%' : '—' }}</b>
          <span class="coverage-sub">{{ metrics.answered_total ?? 0 }} / {{ metrics.question_total ?? 0 }} 条咨询</span>
        </div>

        <template v-if="metrics.tag_hits?.length">
          <div class="subhead">观众命中较多的信息点</div>
          <div class="tag-bars">
            <div v-for="t in metrics.tag_hits" :key="t.tag" class="tag-row">
              <span class="tag-name">{{ t.tag }}</span>
              <div class="tag-track"><i class="tag-fill" :style="{ width: tagPct(t.count) }" /></div>
              <b class="tag-count">{{ t.count }}</b>
            </div>
          </div>
        </template>

        <template v-if="metrics.blind_spots?.length">
          <div class="subhead">信息盲区（知识库答不上来的问题）</div>
          <ul class="blind-list">
            <li v-for="b in metrics.blind_spots" :key="b.text" class="blind-item">
              <span class="blind-text">{{ b.text }}</span>
              <span class="blind-count">{{ b.count }}</span>
            </li>
          </ul>
        </template>
      </section>

      <section v-if="metrics && metrics.funnel?.length" class="card panel funnel-panel">
        <header class="panel-head">购买转化漏斗</header>
        <div class="funnel">
          <div v-for="f in metrics.funnel" :key="f.label" class="funnel-step">
            <span class="funnel-label">{{ f.label }}</span>
            <div class="funnel-track"><i class="funnel-fill" :style="{ width: funnelPct(f.value) }" /></div>
            <b class="funnel-value">{{ f.value }}</b>
          </div>
        </div>
      </section>

      <div v-if="metrics" class="report-layout">
        <section class="card panel">
          <header class="panel-head">数据洞察</header>
          <div class="sentiment">
            <div class="bar-row">
              <span>正面</span>
              <div class="bar"><i class="fill pos" :style="{ width: pct(metrics.sentiment.positive) }" /></div>
              <b>{{ metrics.sentiment.positive }}</b>
            </div>
            <div class="bar-row">
              <span>中性</span>
              <div class="bar"><i class="fill neu" :style="{ width: pct(metrics.sentiment.neutral) }" /></div>
              <b>{{ metrics.sentiment.neutral }}</b>
            </div>
            <div class="bar-row">
              <span>负面</span>
              <div class="bar"><i class="fill neg" :style="{ width: pct(metrics.sentiment.negative) }" /></div>
              <b>{{ metrics.sentiment.negative }}</b>
            </div>
          </div>
          <div class="conv-metrics">
            <div class="conv-item">
              <span>弹幕→关注转化率</span>
              <b>{{ convRate !== null ? convRate + '%' : '—' }}</b>
            </div>
          </div>
          <div v-if="metrics.top_questions.length" class="questions">
            <div class="subhead">高频提问</div>
            <div v-for="q in metrics.top_questions" :key="q.text" class="q-row">
              <span class="q-text">{{ q.text }}</span>
              <span class="q-count">{{ q.count }}</span>
            </div>
          </div>
          <div v-if="hasInsights" class="insights">
            <div class="subhead">节奏洞察</div>
            <div v-if="metrics.insights.peak" class="ins-row">
              <span class="ins-badge peak">峰值</span>
              <span>第 {{ metrics.insights.peak.minute + 1 }} 分钟 · {{ metrics.insights.peak.detail }}</span>
            </div>
            <div v-for="(c, i) in metrics.insights.cold_spots" :key="'cs' + i" class="ins-row">
              <span class="ins-badge cold">冷场</span>
              <span>第 {{ c.from + 1 }}–{{ c.to + 1 }} 分钟，互动明显低于均值</span>
            </div>
            <div v-for="(h, i) in metrics.insights.highlights" :key="'hi' + i" class="ins-row">
              <span class="ins-badge hl">高光</span>
              <span>第 {{ h.minute + 1 }} 分钟 · {{ h.detail }}</span>
            </div>
          </div>
        </section>

        <section class="card panel report-panel">
          <header class="panel-head">自动复盘报告</header>
          <div v-if="report" class="markdown" v-html="markdownToHtml(report)" />
          <div v-else class="empty">选择一场直播并点击「生成复盘」</div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 局部修复：标题行空间不足时按钮被压窄，文字会一字一行竖排；
   禁止换行并禁止收缩，让「生成复盘」始终横排显示 */
.actions .btn {
  white-space: nowrap;
  flex-shrink: 0;
}

.session-select {
  min-width: 260px;
}

.spin {
  animation: spin 0.9s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 56px;
  color: var(--text-faint);
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

@media (max-width: 1000px) {
  .metric-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

.metric-box {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 13px 15px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.metric-box span {
  color: var(--text-muted);
  font-size: 12.5px;
}

.metric-box strong {
  font-size: 22px;
  font-variant-numeric: tabular-nums;
}

.report-layout {
  display: grid;
  grid-template-columns: 0.8fr 1.2fr;
  gap: 16px;
  align-items: start;
}

@media (max-width: 1000px) {
  .report-layout {
    grid-template-columns: 1fr;
  }
}

.panel {
  padding: 16px;
}

.panel-head {
  font-weight: 650;
  margin-bottom: 14px;
  padding-bottom: 11px;
  border-bottom: 1px solid var(--border-soft);
}

.sentiment {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.bar-row {
  display: grid;
  grid-template-columns: 36px 1fr 28px;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--text-muted);
}

.bar {
  height: 8px;
  border-radius: 6px;
  background: var(--surface-3);
  overflow: hidden;
}

.fill {
  display: block;
  height: 100%;
  border-radius: 6px;
}

.fill.pos {
  background: var(--positive);
}

.fill.neu {
  background: var(--text-faint);
}

.fill.neg {
  background: var(--negative);
}

.questions {
  margin-top: 18px;
}

.subhead {
  font-size: 13px;
  color: var(--text-muted);
  margin-bottom: 9px;
}

.q-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid var(--border-soft);
  font-size: 13px;
}

.q-text {
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.q-count {
  color: var(--accent);
  font-weight: 650;
}

.report-panel {
  min-height: 320px;
}

.markdown {
  line-height: 1.75;
  font-size: 14px;
}

.markdown :deep(h2) {
  font-size: 17px;
  margin: 0 0 8px;
}

.markdown :deep(h3) {
  font-size: 15px;
  margin: 16px 0 6px;
}

.markdown :deep(p) {
  margin: 0 0 10px;
  color: var(--text-muted);
}

.markdown :deep(ul) {
  margin: 0 0 10px;
  padding-left: 20px;
}

.markdown :deep(li) {
  margin: 4px 0;
  color: var(--text-muted);
}

.markdown :deep(strong) {
  color: var(--text);
}

.trend-panel {
  margin-bottom: 16px;
}

.trend-svg {
  width: 100%;
  height: auto;
  display: block;
}

.total-area {
  fill: var(--accent);
  fill-opacity: 0.12;
}

.total-line {
  fill: none;
  stroke: var(--accent);
  stroke-width: 2;
  stroke-linejoin: round;
  stroke-linecap: round;
}

.enter-line {
  fill: none;
  stroke: var(--positive);
  stroke-width: 1.5;
  stroke-dasharray: 4 3;
  opacity: 0.9;
}

.grid-line {
  stroke: var(--border-soft);
  stroke-width: 1;
  stroke-dasharray: 3 4;
}

.y-label {
  fill: var(--text-faint);
  font-size: 10px;
}

.peak-label {
  fill: var(--negative);
  font-size: 11px;
  font-weight: 650;
}

.conv-metrics {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  margin-top: 16px;
  padding: 12px;
  background: var(--surface-2);
  border-radius: var(--radius);
  border: 1px solid var(--border-soft);
}

.conv-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.conv-item span {
  font-size: 12px;
  color: var(--text-muted);
}

.conv-item b {
  font-size: 18px;
  font-variant-numeric: tabular-nums;
}

.peak-dot {
  fill: var(--negative);
  stroke: var(--surface);
  stroke-width: 1.5;
}

.hl-dot {
  fill: #e6a23c;
  stroke: var(--surface);
  stroke-width: 1.5;
}

.cold-band {
  fill: var(--text-faint);
  opacity: 0.14;
}

.hover-cell {
  fill: transparent;
  pointer-events: all;
}

.trend-wrap {
  position: relative;
}

.trend-tip {
  position: absolute;
  transform: translate(-50%, calc(-100% - 12px));
  padding: 8px 12px;
  border-radius: var(--radius);
  background: var(--surface-3);
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
  color: var(--text);
  font-size: 12.5px;
  line-height: 1.5;
  white-space: normal;
  pointer-events: none;
  z-index: 30;
  max-width: 320px;
  min-width: 120px;
}

.trend-tip::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 100%;
  margin-left: -6px;
  border: 6px solid transparent;
  border-top-color: var(--surface-3);
}

.trend-tip.below {
  transform: translate(-50%, 14px);
}

.trend-tip.below::after {
  top: auto;
  bottom: 100%;
  border-top-color: transparent;
  border-bottom-color: var(--surface-3);
}

.axis-label {
  fill: var(--text-faint);
  font-size: 10px;
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 10px;
  font-size: 12px;
  color: var(--text-muted);
}

.legend .lg {
  display: inline-block;
  width: 14px;
  height: 3px;
  border-radius: 2px;
  margin-right: 5px;
  vertical-align: middle;
}

.lg-total {
  background: var(--accent);
}

.lg-enter {
  background: repeating-linear-gradient(to right, var(--positive) 0 4px, transparent 4px 7px);
}

.lg-peak,
.lg-hl {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.lg-peak {
  background: var(--negative);
}

.lg-hl {
  background: #e6a23c;
}

.lg-cold {
  height: 10px;
  background: var(--text-faint);
  opacity: 0.3;
}

.insights {
  margin-top: 18px;
}

.ins-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid var(--border-soft);
  font-size: 13px;
  color: var(--text-muted);
}

.ins-badge {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 999px;
}

.ins-badge.peak {
  background: rgba(245, 108, 108, 0.12);
  color: var(--negative);
}

.ins-badge.cold {
  background: var(--surface-3);
  color: var(--text-muted);
}

.ins-badge.hl {
  background: rgba(230, 162, 60, 0.16);
  color: #b88230;
}

.insight-panel,
.funnel-panel {
  margin-bottom: 16px;
}

.coverage-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 14px;
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius);
  margin-bottom: 18px;
}

.coverage-label {
  font-size: 13px;
  color: var(--text-muted);
}

.coverage-value {
  font-size: 26px;
  font-variant-numeric: tabular-nums;
  color: var(--accent);
}

.coverage-sub {
  font-size: 12px;
  color: var(--text-faint);
}

.tag-bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 18px;
}

.tag-row {
  display: grid;
  grid-template-columns: 72px 1fr 32px;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.tag-name {
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tag-track {
  height: 8px;
  border-radius: 6px;
  background: var(--surface-3);
  overflow: hidden;
}

.tag-fill {
  display: block;
  height: 100%;
  border-radius: 6px;
  background: var(--accent);
}

.tag-count {
  color: var(--accent);
  font-weight: 650;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.blind-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.blind-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid var(--border-soft);
  font-size: 13px;
}

.blind-text {
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.blind-count {
  color: var(--negative);
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}

.funnel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.funnel-step {
  display: grid;
  grid-template-columns: 72px 1fr 44px;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.funnel-label {
  color: var(--text-muted);
}

.funnel-track {
  height: 16px;
  border-radius: 8px;
  background: var(--surface-3);
  overflow: hidden;
}

.funnel-fill {
  display: block;
  height: 100%;
  border-radius: 8px;
  background: var(--accent);
  min-width: 4px;
}

.funnel-step:nth-child(1) .funnel-fill {
  opacity: 1;
}

.funnel-step:nth-child(2) .funnel-fill {
  opacity: 0.82;
}

.funnel-step:nth-child(3) .funnel-fill {
  opacity: 0.64;
}

.funnel-step:nth-child(4) .funnel-fill {
  opacity: 0.46;
}

.funnel-value {
  color: var(--text);
  font-weight: 650;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
</style>
