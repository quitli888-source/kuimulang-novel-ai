import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      redirect: '/works',
    },
    {
      path: '/works',
      component: () => import('../views/WorksList.vue'),
    },
    {
      // R4-P1-3: 会话管理
      path: '/works/:workId/sessions',
      component: () => import('../views/SessionManager.vue'),
    },
    {
      // R4-P1-5: 角色档案编辑
      path: '/works/:workId/characters',
      component: () => import('../views/CharacterEdit.vue'),
    },
    {
      path: '/config/:workId?',
      component: () => import('../views/ConfigPanel.vue'),
    },
    {
      path: '/writing/:workId',
      component: () => import('../views/WritingProgress.vue'),
    },
    {
      path: '/preview/:workId',
      component: () => import('../views/PreviewEdit.vue'),
    },
    {
      path: '/report/:workId',
      component: () => import('../views/Report.vue'),
    },
    {
      path: '/rewrite',
      component: () => import('../views/RewriteView.vue'),
    },
    {
      path: '/theme',
      component: () => import('../views/ThemeSettings.vue'),
    },
  ],
})

export default router
