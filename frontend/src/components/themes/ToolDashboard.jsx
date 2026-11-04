import { activeTheme } from '@/themes/runtime'

// Both home routes render the home screen of the server's theme.
export default function ToolDashboard() {
  const { Home } = activeTheme()
  return <Home />
}
