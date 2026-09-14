import { Check, Sun, Moon, Monitor, ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { SKINS } from '@/config/themes'

function ThemePreview({ skin }) {
  return <div className={`theme-preview preview-${skin}`} aria-hidden="true">
    <div className="preview-top"><b>B</b><i /><span /><span /></div>
    <div className="preview-layout"><div className="preview-tools"><div className="preview-search" />{[0, 1].map((n) => <div className="preview-group" key={n}><div className="preview-title" /><div className="preview-grid">{[0, 1, 2, 3, 4, 5].map((i) => <div className="preview-tool" key={i}><span className={`preview-icon preview-color-${i % 3}`} /><i /></div>)}</div></div>)}</div><div className="preview-stats">{[0, 1].map((n) => <div key={n}><b />{[0, 1, 2].map((i) => <span key={i}><i /></span>)}</div>)}</div></div>
  </div>
}
export default function Appearance() {
  const { skin, setSkin, theme, setTheme } = useUI()
  const role = useAuth((s) => s.role)
  return <div className="appearance-page">
    <header><span className="appearance-eyebrow">YOUR WORKSPACE</span><h1>Make yourself at home.</h1><p>Two familiar ways to manage your hosting. Choose the one that feels right for you.</p></header>
    <div className="theme-choices" role="group" aria-label="Panel theme">
      {SKINS.map((item) => <button type="button" key={item.id} className={`theme-choice ${skin === item.id ? 'selected' : ''}`} aria-pressed={skin === item.id} aria-label={`Use ${item.name} theme`} onClick={() => setSkin(item.id)}>
        <ThemePreview skin={item.id} />
        <div className="theme-choice-info"><div><h2>{item.name}</h2><span>{item.layout}</span></div><p>{item.description}</p><strong>{skin === item.id ? <><Check size={15} /> Active theme</> : <>Use this theme <ArrowRight size={15} /></>}</strong></div>
      </button>)}
    </div>
    <section className="appearance-mode"><div><h2>Color mode</h2><p>Keep it light or switch to a darker workspace.</p></div><div role="group" aria-label="Color mode">{[{ id: 'light', label: 'Light', icon: Sun }, { id: 'dark', label: 'Dark', icon: Moon }].map(({ id, label, icon: Icon }) => <button key={id} type="button" aria-pressed={theme === id} onClick={() => setTheme(id)}><Icon size={17} />{label}{theme === id && <Check size={14} />}</button>)}</div></section>
    <div className="appearance-note"><Monitor size={17} /><p>Your choice is saved in this browser and applies immediately. Your account, tools, and data stay the same.</p></div>
    <Link to={role === 'admin' ? '/overview' : role === 'reseller' ? '/reseller' : '/dashboard'} className="appearance-return">Back to dashboard <ArrowRight size={16} /></Link>
  </div>
}
