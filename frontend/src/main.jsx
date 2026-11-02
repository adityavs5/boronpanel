import '@fontsource/lato/latin-700.css'
import '@fontsource/lato/latin-400.css'
import '@fontsource/roboto/latin-600.css'
import '@fontsource/roboto/latin-500.css'
import '@fontsource/roboto/latin-400.css'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { router } from './routes'
import { queryClient } from './lib/queryClient'
import { registerAuthHandlers } from './lib/api'
import { useAuth } from './store/auth'
import { useUI, applyTheme, syncUIPreferences } from './store/ui'
import { TooltipProvider } from './components/ui/Tooltip'
import { Toaster } from './components/ui/Toast'
import { BrandingBootstrap } from './components/layout/BrandingBootstrap'
import './styles/base.css'
import './styles/tokens.css'
import './styles/shell.css'
import './styles/components.css'
import './styles/home.css'
import './styles/pages.css'

// Apply persisted theme before first paint.
applyTheme(useUI.getState().theme, useUI.getState().skin)
window.addEventListener('storage', syncUIPreferences)

// Wire global auth/maintenance handling into the axios interceptor.
registerAuthHandlers({
  unauthorized: () => {
    useAuth.getState().clear()
    if (!window.location.pathname.endsWith('/login')) {
      router.navigate('/login')
    }
  },
  maintenance: () => router.navigate('/maintenance'),
})

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={200}>
        <BrandingBootstrap />
        <RouterProvider router={router} />
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
