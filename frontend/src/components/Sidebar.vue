<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity,
  BookOpenText,
  FileBarChart,
  Library,
  LogOut,
  PenLine,
  RadioTower,
  Settings,
  ShieldCheck,
  Users,
} from '@lucide/vue'
import { clearSession, getUser } from '../api'

const route = useRoute()
const router = useRouter()
const user = computed(() => getUser())

const nav = computed(() => {
  const items = [
    { name: 'live', label: '直播控制台', icon: RadioTower },
    { name: 'content', label: '内容生成', icon: PenLine },
    { name: 'knowledge', label: '知识库', icon: BookOpenText },
    { name: 'scripts', label: '话术库', icon: Library },
    { name: 'rules', label: '场控规则', icon: ShieldCheck },
    { name: 'reports', label: '复盘报告', icon: FileBarChart },
  ]
  if (user.value?.role === 'admin') {
    items.push({ name: 'users', label: '权限管理', icon: Users })
    items.push({ name: 'settings', label: '系统设置', icon: Settings })
  }
  return items
})

function logout() {
  clearSession()
  router.push({ name: 'login' })
}
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-mark"><Activity :size="18" /></span>
      <span class="brand-name">LivePilot</span>
    </div>

    <nav class="nav">
      <button
        v-for="item in nav"
        :key="item.name"
        class="nav-item"
        :class="{ active: route.name === item.name }"
        @click="router.push({ name: item.name })"
      >
        <component :is="item.icon" :size="17" />
        <span>{{ item.label }}</span>
      </button>
    </nav>

    <div class="sidebar-foot">
      <div class="user-chip">
        <div class="avatar">{{ user?.display_name?.slice(0, 1) }}</div>
        <div class="user-meta">
          <div class="user-name">{{ user?.display_name }}</div>
          <div class="user-role">{{ user?.role }}</div>
        </div>
      </div>
      <button class="logout" title="退出登录" @click="logout">
        <LogOut :size="17" />
      </button>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 224px;
  flex-shrink: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border-right: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 20px 18px;
  border-bottom: 1px solid var(--border-soft);
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--accent);
  color: #06231f;
}

.brand-name {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 12px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 11px;
  height: 40px;
  padding: 0 12px;
  border: 0;
  border-radius: var(--radius);
  background: transparent;
  color: var(--text-muted);
  font-weight: 550;
  text-align: left;
  transition: background 0.15s, color 0.15s;
}

.nav-item:hover {
  background: var(--surface-2);
  color: var(--text);
}

.nav-item.active {
  background: var(--accent-soft);
  color: var(--accent);
}

.sidebar-foot {
  margin-top: auto;
  padding: 14px;
  border-top: 1px solid var(--border-soft);
  display: flex;
  align-items: center;
  gap: 8px;
}

.user-chip {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--surface-3);
  color: var(--accent);
  display: grid;
  place-items: center;
  font-weight: 700;
  flex-shrink: 0;
}

.user-meta {
  min-width: 0;
}

.user-name {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-role {
  font-size: 11.5px;
  color: var(--text-faint);
}

.logout {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: transparent;
  color: var(--text-muted);
}

.logout:hover {
  color: var(--negative);
  border-color: rgba(248, 113, 113, 0.4);
}
</style>
