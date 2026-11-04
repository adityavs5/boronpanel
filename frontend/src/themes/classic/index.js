import { createElement } from 'react'
import '@fontsource/open-sans/latin-400.css'
import '@fontsource/open-sans/latin-600.css'
import '@fontsource/open-sans/latin-300.css'
import { ToolsHome } from '../shared/ToolsHome'
import { Layout } from './Layout'
import icons from './icons.gen'
import './classic.css'

const Home = () => createElement(ToolsHome, { heading: 'Tools' })

export default { id: 'classic', Layout, Home, icons, sidebar: { tone: 'navy', homeLabel: 'Tools', collapsible: false }, labels: { account: 'General information', resources: 'Statistics' }, account: { nav: 'tabs', overview: 'table' } }
