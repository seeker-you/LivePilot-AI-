<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  Activity,
  Check,
  CircleStop,
  Gift,
  Heart,
  ListMusic,
  MessageSquare,
  Play,
  RadioTower,
  Sparkles,
  UserCheck,
  UserPlus,
  Users,
  Wifi,
  WifiOff,
  X,
} from '@lucide/vue'
import {
  api,
  getUser,
  openLiveSocket,
  streamAssist,
} from '../api'
import type { LiveAction, LiveEvent, LiveSession, QueueSnapshot } from '../types'
import MetricCard from '../components/MetricCard.vue'
import StreamChart from '../components/StreamChart.vue'
import { useToast } from '../composables/useToast'

const { toast } = useToast()
const user = getUser()
const canManage = computed(() => user?.role === 'admin' || user?.role === 'host')

const session = ref<LiveSession | null>(null)
const running = ref(false)
const connected = ref(false)
const title = ref('小鹿的美妆专场')
const rate = ref(2)
const platform = ref('mock')
const roomId = ref('')
let ws: WebSocket | null = null
let rateTimer: number | undefined
let eventCounter = 0

const events = ref<LiveEvent[]>([])
const actions = ref<LiveAction[]>([])
const queue = ref<QueueSnapshot>({ next: null, high: [], low: [] })
const online = ref(0)
const peakOnline = ref(0)
const chatCount = ref(0)
const giftTotal = ref(0)
const likeCount = ref(0)
const followCount = ref(0)
const actionCount = ref(0)
const rateSeries = ref<number[]>(Array.from({ length: 30 }, () => 0))
const lastRate = computed(() => rateSeries.value[rateSeries.value.length - 1] ?? 0)

const eventMeta: Record<string, { label: string; icon: any; tone: string }> = {
  chat: { label: '弹幕', icon: MessageSquare, tone: 'chat' },
  gift: { label: '礼物', icon: Gift, tone: 'gift' },
  user_enter: { label: '进场', icon: UserPlus, tone: 'enter' },
  like: { label: '点赞', icon: Heart, tone: 'like' },
  follow: { label: '关注', icon: UserCheck, tone: 'follow' },
}

const actionMeta: Record<string, { label: string; tone: string }> = {
  auto_reply: { label: '自动回复', tone: 'auto_reply' },
  welcome: { label: '欢迎', tone: 'welcome' },
  pin: { label: '置顶', tone: 'pin' },
  alert: { label: '告警', tone: 'alert' },
  escalate: { label: '升级', tone: 'escalate' },
}

onMounted(async () => {
  try {
    const sessions = await api.listSessions()
    const live = sessions.find((s) => s.status === 'live')
    if (live) await attach(live)
  } catch (e: any) {
    toast(e.message, 'error')
  }
})

onBeforeUnmount(() => {
  ws?.close()
  stopRateTimer()
})

const platformHint = computed(() => {
  if (platform.value === 'douyin')
    return '填直播间号、直播间链接或抖音分享短链（整段分享文案也可以），直连拉取真实弹幕'
  if (platform.value === 'douyin_grab')
    return '需在本机运行 DouyinBarrageGrab 并保持直播间页面打开'
  if (platform.value === 'wxlive') return '需运行 wxlivespy 并将转发地址指向本服务 /api/ingest/wxlive'
  return ''
})

async function start() {
  if (platform.value === 'douyin' && !roomId.value.trim()) {
    toast('请填写抖音直播间号 / 直播间链接 / 分享短链', 'error')
    return
  }
  try {
    const s = await api.startSession(
      title.value || '直播间',
      rate.value,
      platform.value,
      roomId.value.trim(),
    )
    await attach(s)
    toast(
      platform.value === 'mock'
        ? '直播已开始'
        : `直播已开始，等待${
            platform.value === 'wxlive' ? '视频号' : '抖音'
          }事件接入`,
    )
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

async function stop() {
  if (!session.value) return
  try {
    await api.stopSession(session.value.id)
    ws?.close()
    running.value = false
    stopRateTimer()
    toast('直播已结束，可在复盘报告查看数据')
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

async function attach(s: LiveSession) {
  session.value = s
  running.value = true
  events.value = []
  actions.value = []
  resetCounters()
  ws?.close()
  stopRateTimer()
  connected.value = false
  startRateTimer()
  ws = openLiveSocket(s.id, {
    onEvent: handleEvent,
    onAction: handleAction,
    onQueue: (q) => (queue.value = q),
    onSession: (data) => {
      if (data.status === 'ended') {
        running.value = false
        stopRateTimer()
        ws?.close()
      }
    },
    onOpen: () => (connected.value = true),
    onClose: () => (connected.value = false),
    onError: () => (connected.value = false),
    onHello: (snapshot) => {
      online.value = snapshot.metrics?.online ?? 0
      peakOnline.value = snapshot.metrics?.peak_online ?? 0
      events.value = snapshot.recent_events ?? []
      actions.value = snapshot.recent_actions ?? []
      void loadCounters()
      void loadQueue()
    },
  })
}

function resetCounters() {
  online.value = 0
  peakOnline.value = 0
  chatCount.value = 0
  giftTotal.value = 0
  likeCount.value = 0
  followCount.value = 0
  actionCount.value = 0
  eventCounter = 0
  rateSeries.value = Array.from({ length: 30 }, () => 0)
}

async function loadCounters() {
  if (!session.value) return
  try {
    const m = await api.getMetrics(session.value.id)
    chatCount.value = m.chat_count
    giftTotal.value = m.gift_total
    likeCount.value = m.like_total
    followCount.value = m.follow_count
    actionCount.value = m.action_count
    peakOnline.value = Math.max(peakOnline.value, m.peak_online)
  } catch {
    /* snapshot counters are best-effort */
  }
}

async function loadQueue() {
  if (!session.value) return
  try {
    queue.value = await api.getQueue(session.value.id)
  } catch {
    /* queue snapshot is best-effort */
  }
}

async function popNext() {
  if (!session.value) return
  try {
    await api.popQueue(session.value.id)
    await loadQueue()
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

function handleEvent(e: LiveEvent) {
  eventCounter++
  events.value.unshift(e)
  if (events.value.length > 200) events.value.length = 200
  if (e.type === 'chat') chatCount.value++
  else if (e.type === 'gift') giftTotal.value += Number(e.payload?.value ?? 0)
  else if (e.type === 'like') likeCount.value += Number(e.payload?.count ?? 0)
  else if (e.type === 'follow') followCount.value++
  else if (e.type === 'user_enter') {
    online.value++
    peakOnline.value = Math.max(peakOnline.value, online.value)
  }
}

function startRateTimer() {
  stopRateTimer()
  rateTimer = window.setInterval(() => {
    rateSeries.value = [...rateSeries.value.slice(1), eventCounter]
    eventCounter = 0
  }, 1000)
}

function stopRateTimer() {
  if (rateTimer !== undefined) {
    window.clearInterval(rateTimer)
    rateTimer = undefined
  }
}

function handleAction(a: LiveAction) {
  actions.value.unshift(a)
  if (actions.value.length > 200) actions.value.length = 200
  actionCount.value++
}

async function review(action: LiveAction, status: 'approved' | 'rejected') {
  if (!session.value) return
  try {
    await api.reviewAction(session.value.id, action.id, status)
    action.status = status
    toast(status === 'approved' ? '已确认执行' : '已驳回')
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

function formatTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}

function eventText(e: LiveEvent) {
  const p = e.payload
  if (e.type === 'chat') return p?.text ?? ''
  if (e.type === 'gift') return `${p?.user} 送出「${p?.gift}」`
  if (e.type === 'user_enter') return `${p?.user} 进入直播间${p?.is_vip ? ' · VIP' : ''}`
  if (e.type === 'like') return `${p?.user} 点赞 ×${p?.count}`
  if (e.type === 'follow') return `${p?.user} 关注了主播`
  return ''
}

function sentimentDot(e: LiveEvent) {
  if (e.type !== 'chat') return ''
  const s = e.payload?.sentiment
  if (s === 'positive') return 'positive'
  if (s === 'negative') return 'negative'
  return 'neutral'
}

function sourceLabel(source: string) {
  const map: Record<string, string> = {
    danmaku: '弹幕回复',
    gift: '礼物感谢',
    welcome: '进场欢迎',
    script: '话术补位',
  }
  return map[source] ?? source
}

// Assist panel
const query = ref('')
const assistOutput = ref('')
const assistSources = ref<string[]>([])
const generating = ref(false)

async function generateAssist() {
  if (!query.value.trim() || generating.value) return
  generating.value = true
  assistOutput.value = ''
  assistSources.value = []
  try {
    await streamAssist(
      { query: query.value, session_id: session.value?.id, context: events.value.slice(0, 10) },
      (delta) => (assistOutput.value += delta),
      (sources) => (assistSources.value = sources),
    )
  } catch (e: any) {
    toast(e.message, 'error')
  } finally {
    generating.value = false
  }
}
</script>

<template>
  <div class="live">
    <div class="page-title">
      <div>
        <h1>直播控制台</h1>
        <p>实时弹幕、礼物与 AI 场控动作，自动话术辅助</p>
      </div>
      <div class="controls">
        <span v-if="running" class="badge live-badge">
          <span class="pulse" /> 直播中
        </span>
        <span v-else class="badge idle-badge">未开播</span>
        <span v-if="running" class="badge conn-badge" :class="connected ? 'conn-ok' : 'conn-down'">
          <Wifi v-if="connected" :size="13" />
          <WifiOff v-else :size="13" />
          {{ connected ? '已连接' : '重连中' }}
        </span>
        <template v-if="!running">
          <input v-model="title" class="input title-input" placeholder="直播标题" />
          <select v-model="platform" class="select rate-select">
            <option value="mock">模拟数据</option>
            <option value="douyin">抖音直播（直连）</option>
            <option value="douyin_grab">抖音（外部工具）</option>
            <option value="wxlive">视频号直播</option>
          </select>
          <input
            v-if="platform !== 'mock'"
            v-model="roomId"
            class="input title-input"
            :placeholder="
              platform === 'douyin'
                ? '直播间号 / 直播间链接 / 抖音分享短链'
                : platform === 'douyin_grab'
                  ? '抖音房间 GroupId（选填，过滤其它房间）'
                  : '视频号备注（选填）'
            "
          />
          <select v-if="platform === 'mock'" v-model.number="rate" class="select rate-select">
            <option :value="1">慢速弹幕</option>
            <option :value="2">正常弹幕</option>
            <option :value="5">快速弹幕</option>
            <option :value="10">极速弹幕</option>
          </select>
          <button class="btn btn-primary" :disabled="!canManage" @click="start">
            <Play :size="15" /> 开始直播
          </button>
        </template>
        <template v-else>
          <button class="btn btn-danger" :disabled="!canManage" @click="stop">
            <CircleStop :size="15" /> 结束直播
          </button>
        </template>
      </div>
    </div>

    <div class="metrics">
      <MetricCard label="在线人数" :value="online" :icon="Users" />
      <MetricCard label="峰值在线" :value="peakOnline" :icon="Users" />
      <MetricCard label="弹幕数" :value="chatCount" :icon="MessageSquare" />
      <MetricCard label="礼物价值" :value="giftTotal" :icon="Gift" tone="positive" />
      <MetricCard label="点赞" :value="likeCount" :icon="Heart" />
      <MetricCard label="新增关注" :value="followCount" :icon="UserCheck" />
      <MetricCard label="AI 动作" :value="actionCount" :icon="Sparkles" tone="warning" />
    </div>

    <section v-if="running" class="card chart-card">
      <header class="chart-head">
        <div class="chart-title">
          <Activity :size="15" />
          <span>实时事件速率</span>
        </div>
        <div class="chart-value num">{{ lastRate }} <em>次/秒</em></div>
      </header>
      <StreamChart :series="rateSeries" color="#2dd4bf" :height="110" />
    </section>

    <section v-if="running" class="card queue-card">
      <header class="queue-head">
        <div class="queue-title">
          <ListMusic :size="16" />
          <span>话术队列</span>
        </div>
        <div class="queue-counts">
          <span class="badge q-high">即时回复 {{ queue.high.length }}</span>
          <span class="badge q-low">话术补位 {{ queue.low.length }}</span>
        </div>
      </header>
      <div class="queue-body">
        <div class="queue-next">
          <div class="queue-next-label">下一条口播</div>
          <div class="queue-next-content">
            <span v-if="queue.next" class="source-badge" :class="queue.next.source">
              {{ sourceLabel(queue.next.source) }}
            </span>
            <span v-if="queue.next" class="queue-text">{{ queue.next.content }}</span>
            <span v-else class="queue-empty">队列为空，正在从话术库补位…</span>
          </div>
        </div>
        <button class="btn" :disabled="!canManage || !queue.next" @click="popNext">
          <Check :size="15" /> 已播，下一条
        </button>
      </div>
    </section>

    <div v-if="!running" class="placeholder card">
      <RadioTower :size="34" />
      <div class="placeholder-title">尚未开始直播</div>
      <div class="placeholder-text">
        {{ platformHint || '点击「开始直播」会接入模拟弹幕源，实时演示 AI 场控闭环' }}
      </div>
    </div>

    <template v-else>
      <div class="grid">
        <section class="card feed">
          <header class="feed-head">
            <span>实时弹幕流</span>
            <span class="muted">{{ events.length }} 条</span>
          </header>
          <div class="feed-body scroll-area">
            <div v-if="!events.length" class="empty">等待弹幕…</div>
            <div v-for="e in events" :key="e.id" class="event-row">
              <component
                :is="eventMeta[e.type]?.icon"
                :size="15"
                class="event-icon"
                :class="eventMeta[e.type]?.tone"
              />
              <div class="event-main">
                <div class="event-line">
                  <span class="event-user">{{ e.payload?.user ?? '观众' }}</span>
                  <span class="event-type">{{ eventMeta[e.type]?.label }}</span>
                  <span v-if="e.type === 'chat'" class="sentiment" :class="sentimentDot(e)" />
                </div>
                <div class="event-text">{{ eventText(e) }}</div>
              </div>
              <span class="event-time">{{ formatTime(e.ts) }}</span>
            </div>
          </div>
        </section>

        <section class="card feed">
          <header class="feed-head">
            <span>AI 场控动作</span>
            <span class="muted">{{ actions.length }} 条</span>
          </header>
          <div class="feed-body scroll-area">
            <div v-if="!actions.length" class="empty">等待 AI 识别…</div>
            <div v-for="a in actions" :key="a.id" class="action-row">
              <span class="badge action-badge" :class="actionMeta[a.type]?.tone">
                {{ actionMeta[a.type]?.label ?? a.type }}
              </span>
              <div class="action-content">{{ a.content }}</div>
              <div class="action-foot">
                <span class="event-time">{{ formatTime(a.ts) }}</span>
                <div v-if="a.status === 'pending'" class="action-btns">
                  <button
                    class="mini ok"
                    :disabled="!canManage"
                    title="确认执行"
                    @click="review(a, 'approved')"
                  >
                    <Check :size="13" />
                  </button>
                  <button
                    class="mini no"
                    :disabled="!canManage"
                    title="驳回"
                    @click="review(a, 'rejected')"
                  >
                    <X :size="13" />
                  </button>
                </div>
                <span v-else class="badge status-badge" :class="a.status">
                  {{ a.status === 'approved' ? '已确认' : a.status === 'rejected' ? '已驳回' : '已执行' }}
                </span>
              </div>
            </div>
          </div>
        </section>
      </div>

      <section class="card assist">
        <header class="assist-head">
          <span class="assist-title"><Sparkles :size="16" /> 实时话术助手</span>
          <span class="muted">结合知识库与实时弹幕生成主播话术</span>
        </header>
        <div class="assist-body">
          <div class="assist-input-row">
            <input
              v-model="query"
              class="input"
              placeholder="例如：有人问油皮能不能用，下一句怎么说？"
              @keydown.enter="generateAssist"
            />
            <button class="btn btn-primary" :disabled="generating || !query.trim()" @click="generateAssist">
              <Sparkles :size="15" />
              {{ generating ? '生成中…' : '生成话术' }}
            </button>
          </div>
          <div v-if="assistOutput" class="assist-output">
            {{ assistOutput }}
            <span v-if="generating" class="cursor">▍</span>
          </div>
          <div v-if="assistSources.length" class="assist-sources">
            参考知识：<span v-for="s in assistSources" :key="s" class="source-chip">{{ s }}</span>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.title-input {
  width: 200px;
}

.rate-select {
  width: 130px;
}

.live-badge {
  background: rgba(52, 211, 153, 0.15);
  color: var(--positive);
}

.idle-badge {
  background: var(--surface-2);
  color: var(--text-muted);
}

.conn-badge.conn-ok {
  background: rgba(59, 130, 246, 0.14);
  color: var(--info);
}

.conn-badge.conn-down {
  background: rgba(245, 158, 11, 0.14);
  color: var(--warning);
}

.pulse {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--positive);
  animation: pulse 1.4s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
    box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.5);
  }
  50% {
    opacity: 0.6;
    box-shadow: 0 0 0 5px rgba(52, 211, 153, 0);
  }
}

.metrics {
  display: grid;
  grid-template-columns: repeat(7, minmax(110px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

@media (max-width: 1200px) {
  .metrics {
    grid-template-columns: repeat(4, 1fr);
  }
}

.chart-card {
  padding: 14px 16px 10px;
  margin-bottom: 16px;
}

.chart-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.chart-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 13.5px;
}

.chart-title svg {
  color: var(--accent);
}

.chart-value {
  font-size: 15px;
  font-weight: 600;
  color: var(--accent);
}

.chart-value em {
  font-style: normal;
  font-size: 12px;
  color: var(--text-faint);
  margin-left: 3px;
}

.queue-card {
  padding: 14px 16px;
  margin-bottom: 16px;
}

.queue-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.queue-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 650;
  font-size: 13.5px;
}

.queue-title svg {
  color: var(--accent);
}

.queue-counts {
  display: flex;
  gap: 6px;
}

.q-high {
  background: rgba(248, 113, 113, 0.14);
  color: var(--negative);
}

.q-low {
  background: var(--surface-2);
  color: var(--text-muted);
}

.queue-body {
  display: flex;
  align-items: center;
  gap: 16px;
}

.queue-next {
  flex: 1;
  min-width: 0;
}

.queue-next-label {
  font-size: 12px;
  color: var(--text-faint);
  margin-bottom: 5px;
}

.queue-next-content {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 26px;
}

.source-badge {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 5px;
  font-size: 12px;
  font-weight: 600;
  background: var(--surface-3);
  color: var(--text-muted);
}

.source-badge.danmaku {
  background: rgba(96, 165, 250, 0.16);
  color: var(--info);
}

.source-badge.gift {
  background: rgba(251, 191, 36, 0.16);
  color: var(--warning);
}

.source-badge.welcome {
  background: rgba(45, 212, 191, 0.16);
  color: var(--accent);
}

.source-badge.script {
  background: var(--surface-2);
  color: var(--text-muted);
}

.queue-text {
  color: var(--text);
  word-break: break-word;
}

.queue-empty {
  color: var(--text-faint);
}

.placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 320px;
  color: var(--text-faint);
}

.placeholder-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-muted);
}

.placeholder-text {
  font-size: 13px;
}

.grid {
  display: grid;
  grid-template-columns: 1.15fr 0.85fr;
  gap: 16px;
  margin-bottom: 16px;
}

.feed {
  display: flex;
  flex-direction: column;
  min-height: 420px;
  max-height: 520px;
}

.feed-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 16px;
  border-bottom: 1px solid var(--border-soft);
  font-weight: 600;
  font-size: 13.5px;
}

.feed-body {
  flex: 1;
  padding: 8px;
}

.event-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 9px 10px;
  border-bottom: 1px solid var(--border-soft);
}

.event-row:last-child {
  border-bottom: 0;
}

.event-icon {
  margin-top: 3px;
  flex-shrink: 0;
  color: var(--text-muted);
}

.event-icon.gift {
  color: var(--warning);
}

.event-icon.enter {
  color: var(--info);
}

.event-icon.follow {
  color: var(--positive);
}

.event-main {
  flex: 1;
  min-width: 0;
}

.event-line {
  display: flex;
  align-items: center;
  gap: 8px;
}

.event-user {
  font-weight: 600;
  color: var(--text);
}

.event-type {
  font-size: 11px;
  color: var(--text-faint);
  background: var(--surface-2);
  padding: 0 6px;
  border-radius: 4px;
}

.event-text {
  color: var(--text-muted);
  word-break: break-word;
}

.event-time {
  color: var(--text-faint);
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.sentiment {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.sentiment.positive {
  background: var(--positive);
}

.sentiment.negative {
  background: var(--negative);
}

.sentiment.neutral {
  background: var(--text-faint);
}

.action-row {
  padding: 11px 10px;
  border-bottom: 1px solid var(--border-soft);
}

.action-row:last-child {
  border-bottom: 0;
}

.action-badge {
  background: var(--surface-3);
  color: var(--text-muted);
  margin-bottom: 7px;
}

.action-badge.auto_reply {
  background: rgba(96, 165, 250, 0.16);
  color: var(--info);
}

.action-badge.welcome {
  background: rgba(45, 212, 191, 0.16);
  color: var(--accent);
}

.action-badge.alert {
  background: rgba(251, 191, 36, 0.16);
  color: var(--warning);
}

.action-badge.escalate {
  background: rgba(248, 113, 113, 0.16);
  color: var(--negative);
}

.action-content {
  color: var(--text);
  margin-bottom: 7px;
  word-break: break-word;
}

.action-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.action-btns {
  display: flex;
  gap: 6px;
}

.mini {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: transparent;
}

.mini.ok {
  color: var(--positive);
}

.mini.no {
  color: var(--negative);
}

.status-badge.approved {
  background: rgba(52, 211, 153, 0.14);
  color: var(--positive);
}

.status-badge.rejected {
  background: rgba(248, 113, 113, 0.14);
  color: var(--negative);
}

.status-badge.executed {
  background: var(--surface-2);
  color: var(--text-muted);
}

.assist {
  padding: 16px;
}

.assist-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 13px;
}

.assist-title {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-weight: 650;
}

.assist-title svg {
  color: var(--accent);
}

.assist-input-row {
  display: flex;
  gap: 10px;
}

.assist-output {
  margin-top: 13px;
  padding: 13px 14px;
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius);
  line-height: 1.7;
  min-height: 44px;
}

.cursor {
  color: var(--accent);
  animation: pulse 1s infinite;
}

.assist-sources {
  margin-top: 10px;
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
