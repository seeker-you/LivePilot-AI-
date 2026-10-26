<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import Sidebar from './components/Sidebar.vue'
import { useToast } from './composables/useToast'

const route = useRoute()
const isLogin = computed(() => route.name === 'login')
const { toasts } = useToast()
</script>

<template>
  <div v-if="isLogin" class="login-shell">
    <router-view />
  </div>
  <div v-else class="app-shell">
    <Sidebar />
    <main class="app-main">
      <router-view />
    </main>
  </div>
  <div class="toast">
    <div v-for="t in toasts" :key="t.id" class="toast-item" :class="t.kind">
      {{ t.text }}
    </div>
  </div>
</template>

