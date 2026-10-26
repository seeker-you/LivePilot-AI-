<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Activity, ArrowRight, Lock, User } from '@lucide/vue'
import { api, saveSession } from '../api'

const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const res = await api.login(username.value, password.value)
    saveSession(res.access_token, res.user)
    router.push({ name: 'live' })
  } catch (e: any) {
    error.value = e.message || '登录失败'
  } finally {
    loading.value = false
  }
}

async function quick(u: string, p: string) {
  username.value = u
  password.value = p
  await submit()
}
</script>

<template>
  <div class="login">
    <div class="login-panel">
      <div class="login-brand">
        <span class="mark"><Activity :size="20" /></span>
        <div>
          <h1>LivePilot</h1>
          <p>直播 AI 运营中台</p>
        </div>
      </div>

      <form class="form" @submit.prevent="submit">
        <div class="field">
          <label>用户名</label>
          <div class="input-wrap">
            <User :size="16" class="lead-icon" />
            <input v-model="username" class="input pad" placeholder="请输入用户名" autocomplete="username" />
          </div>
        </div>
        <div class="field">
          <label>密码</label>
          <div class="input-wrap">
            <Lock :size="16" class="lead-icon" />
            <input
              v-model="password"
              class="input pad"
              type="password"
              placeholder="请输入密码"
              autocomplete="current-password"
            />
          </div>
        </div>

        <p v-if="error" class="error">{{ error }}</p>

        <button class="btn btn-primary submit" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登录' }}
          <ArrowRight v-if="!loading" :size="16" />
        </button>
      </form>

      <div class="quick">
        <div class="quick-label">演示账号</div>
        <div class="quick-list">
          <button class="quick-btn" @click="quick('admin', 'admin123')">管理员</button>
          <button class="quick-btn" @click="quick('host', 'host123')">主播</button>
          <button class="quick-btn" @click="quick('operator', 'op123')">场控</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login {
  min-height: 100%;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(900px 500px at 70% 10%, rgba(45, 212, 191, 0.08), transparent 55%),
    var(--bg);
}

.login-panel {
  width: 100%;
  max-width: 380px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 30px;
  box-shadow: var(--shadow);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 26px;
}

.mark {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: var(--accent);
  color: #06231f;
}

.login-brand h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}

.login-brand p {
  margin: 1px 0 0;
  color: var(--text-muted);
  font-size: 13px;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.input-wrap {
  position: relative;
}

.lead-icon {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-faint);
  pointer-events: none;
}

.input.pad {
  padding-left: 36px;
  height: 40px;
}

.error {
  margin: 0;
  color: var(--negative);
  font-size: 13px;
}

.submit {
  width: 100%;
  justify-content: center;
  height: 40px;
}

.quick {
  margin-top: 24px;
  padding-top: 18px;
  border-top: 1px solid var(--border-soft);
}

.quick-label {
  font-size: 12px;
  color: var(--text-faint);
  margin-bottom: 10px;
}

.quick-list {
  display: flex;
  gap: 8px;
}

.quick-btn {
  flex: 1;
  height: 32px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-muted);
}

.quick-btn:hover {
  color: var(--accent);
  border-color: var(--accent);
}
</style>
