<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { KeyRound, Trash2, UserPlus } from '@lucide/vue'
import { api } from '../api'
import { useToast } from '../composables/useToast'

const { toast } = useToast()
const users = ref<Awaited<ReturnType<typeof api.listUsers>>>([])
const loading = ref(true)

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  host: '主播',
  operator: '场控',
}

const showForm = ref(false)
const form = ref({ username: '', display_name: '', password: '', role: 'operator' })
const creating = ref(false)

async function load() {
  loading.value = true
  try {
    users.value = await api.listUsers()
  } catch (e: any) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}
onMounted(load)

async function create() {
  creating.value = true
  try {
    await api.createUser({ ...form.value })
    toast('用户已创建')
    showForm.value = false
    form.value = { username: '', display_name: '', password: '', role: 'operator' }
    await load()
  } catch (e: any) {
    toast(e.message, 'error')
  } finally {
    creating.value = false
  }
}

async function changeRole(id: number, role: string) {
  try {
    await api.updateUser(id, { role })
    toast('角色已更新')
    await load()
  } catch (e: any) {
    toast(e.message, 'error')
    await load()
  }
}

async function resetPassword(id: number, username: string) {
  const password = prompt(`为「${username}」设置新密码（至少 6 位）：`)
  if (!password) return
  if (password.length < 6) {
    toast('密码至少 6 位', 'error')
    return
  }
  try {
    await api.resetUserPassword(id, password)
    toast('密码已重置')
  } catch (e: any) {
    toast(e.message, 'error')
  }
}

async function remove(id: number, username: string) {
  if (!confirm(`确认删除用户「${username}」？该操作不可恢复。`)) return
  try {
    await api.deleteUser(id)
    toast('用户已删除')
    await load()
  } catch (e: any) {
    toast(e.message, 'error')
  }
}
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>权限管理</h1>
        <p class="page-sub">管理系统成员与角色：管理员（全部权限）/ 主播（开播与内容）/ 场控（只读看板）</p>
      </div>
      <button class="btn btn-primary" @click="showForm = !showForm">
        <UserPlus :size="15" /> 新建用户
      </button>
    </header>

    <form v-if="showForm" class="card form-card" @submit.prevent="create">
      <div class="grid-4">
        <div class="field">
          <label>用户名</label>
          <input v-model="form.username" class="input" placeholder="字母/数字/下划线" required />
        </div>
        <div class="field">
          <label>显示名</label>
          <input v-model="form.display_name" class="input" placeholder="留空同用户名" />
        </div>
        <div class="field">
          <label>初始密码</label>
          <input v-model="form.password" class="input" type="password" placeholder="至少 6 位" required />
        </div>
        <div class="field">
          <label>角色</label>
          <select v-model="form.role" class="select">
            <option value="operator">场控</option>
            <option value="host">主播</option>
            <option value="admin">管理员</option>
          </select>
        </div>
      </div>
      <div class="form-actions">
        <button type="submit" class="btn btn-primary" :disabled="creating">
          {{ creating ? '创建中…' : '创建' }}
        </button>
      </div>
    </form>

    <div class="card table-card">
      <table class="table">
        <thead>
          <tr>
            <th>用户名</th>
            <th>显示名</th>
            <th>角色</th>
            <th>创建时间</th>
            <th class="ops-col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="5" class="empty">加载中…</td>
          </tr>
          <tr v-else-if="!users.length">
            <td colspan="5" class="empty">暂无用户</td>
          </tr>
          <tr v-for="u in users" v-else :key="u.id">
            <td class="mono">{{ u.username }}</td>
            <td>{{ u.display_name }}</td>
            <td>
              <select
                class="select role-select"
                :value="u.role"
                @change="changeRole(u.id, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="(label, role) in ROLE_LABELS" :key="role" :value="role">
                  {{ label }}
                </option>
              </select>
            </td>
            <td class="dim">{{ new Date(u.created_at * 1000).toLocaleDateString() }}</td>
            <td class="ops">
              <button class="btn btn-sm" @click="resetPassword(u.id, u.username)">
                <KeyRound :size="13" /> 重置密码
              </button>
              <button class="btn btn-sm btn-danger" @click="remove(u.id, u.username)">
                <Trash2 :size="13" /> 删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.form-card {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-bottom: 14px;
}

.grid-4 {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field label {
  font-size: 13px;
  font-weight: 600;
}

.table-card {
  overflow-x: auto;
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13.5px;
}

.table th {
  text-align: left;
  padding: 10px 14px;
  font-size: 12px;
  color: var(--text-dim, #888);
  border-bottom: 1px solid var(--border-soft, #eee);
}

.table td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border-soft, #eee);
}

.role-select {
  min-width: 96px;
  padding: 5px 8px;
}

.ops {
  display: flex;
  gap: 8px;
}

.ops-col {
  width: 220px;
}

.mono {
  font-family: var(--mono, monospace);
}

.dim {
  color: var(--text-dim, #888);
}

.empty {
  text-align: center;
  color: var(--text-dim, #888);
  padding: 26px;
}
</style>
