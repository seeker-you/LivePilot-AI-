import { createRouter, createWebHistory } from 'vue-router'
import { getToken, getUser } from './api'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue') },
    { path: '/', redirect: '/live' },
    { path: '/live', name: 'live', component: () => import('./views/LiveView.vue') },
    { path: '/knowledge', name: 'knowledge', component: () => import('./views/KnowledgeView.vue') },
    { path: '/content', name: 'content', component: () => import('./views/ContentView.vue') },
    { path: '/rules', name: 'rules', component: () => import('./views/RulesView.vue') },
    { path: '/scripts', name: 'scripts', component: () => import('./views/ScriptsView.vue') },
    { path: '/reports', name: 'reports', component: () => import('./views/ReportsView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
    { path: '/users', name: 'users', component: () => import('./views/UsersView.vue') },
  ],
})

router.beforeEach((to) => {
  if (to.name !== 'login' && !getToken()) return { name: 'login' }
  if (to.name === 'login' && getToken()) return { name: 'live' }
  const adminOnly = to.name === 'settings' || to.name === 'users'
  if (adminOnly && getUser()?.role !== 'admin') return { name: 'live' }
  return true
})

export default router
