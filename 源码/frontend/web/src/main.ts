import { createApp } from 'vue'
import App from './App.vue'
import { createAuthManager } from './services/nstlAuth'
import NstlTrackerVue from './tracker/nstl-tracker-vue.esm.js'
import { trackerOptions } from './tracker/contentAssessmentTracker'
import { overflowTitle } from './directives/overflowTitle'
import './styles/fonts.css'
import './styles/main.css'
import './styles/app-sidebar.css'
import './styles/workspace-modern.css'
import './styles/home-landing.css'

createAuthManager()

const app = createApp(App)
app.use(NstlTrackerVue, trackerOptions())
app.directive('overflow-title', overflowTitle)
app.mount('#app')
