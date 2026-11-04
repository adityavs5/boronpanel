import { createElement } from 'react'
import '@fontsource/roboto/latin-400.css'
import '@fontsource/roboto/latin-500.css'
import '@fontsource/roboto/latin-600.css'
import { ToolsHome } from '../shared/ToolsHome'
import { Layout } from './Layout'
import icons, { toolIcons } from './icons.gen'
import './grid.css'

const Home = () => createElement(ToolsHome, { heading: 'Your tools', filter: true })

export default { id: 'grid', Layout, Home, icons, toolIcons }
