// Synthetic QA only; imported through the isolated Vite config, never the app.
export const requirePlatformLogin = async () => 'isolated-research-stage-qa'
export const getAuthManager = () => ({ checkAuth: async () => true, isAuthenticated: () => true, showLoginModal: () => {}, getUserInfo: () => ({}) })
