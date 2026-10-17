import { Check, Sun, Moon, Monitor, ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { SKINS } from '@/config/themes'
import { Select } from '@/components/ui/Select'
import { Button } from '@/components/ui/Button'
import { PageHeader } from '@/components/ui/PageHeader'

function ThemePreview({ skin }) {
  const appearance = useUI(s => s.appearance)
  return <div style={{ '--preview-accent': appearance.colors[skin] || window.BoronAppearance.defaults[skin] }} className={`theme-preview preview-${skin}`} aria-hidden="true">
    <div className="preview-top"><b>B</b><i /><span /><span /></div>
    <div className="preview-layout"><div className="preview-tools"><div className="preview-search" />{[0, 1].map((n) => <div className="preview-group" key={n}><div className="preview-title" /><div className="preview-grid">{[0, 1, 2, 3, 4, 5].map((i) => <div className="preview-tool" key={i}><span className={`preview-icon preview-color-${i % 3}`} /><i /></div>)}</div></div>)}</div><div className="preview-stats">{[0, 1].map((n) => <div key={n}><b />{[0, 1, 2].map((i) => <span key={i}><i /></span>)}</div>)}</div></div>
  </div>
}
export default function Appearance() {
  const { skin, setSkin, theme, setTheme, appearance, setAppearance, resetAppearance } = useUI()
  const accent = appearance.colors[skin] || window.BoronAppearance.defaults[skin]
  const setAccent = color => setAppearance({ colors: { ...appearance.colors, [skin]: color } })
  const role = useAuth((s) => s.role)
  return <div className="appearance-page">
    <PageHeader title="Appearance" description="Choose the panel theme and color mode for this browser." />
    <div className="theme-choices" role="group" aria-label="Panel theme">
      {SKINS.map((item) => <button type="button" key={item.id} className={`theme-choice ${skin === item.id ? 'selected' : ''}`} aria-pressed={skin === item.id} aria-label={`Use ${item.name} theme`} onClick={() => setSkin(item.id)}>
        <ThemePreview skin={item.id} />
        <div className="theme-choice-info"><div><h2>{item.name}</h2><span>{item.layout}</span></div><p>{item.description}</p><strong>{skin === item.id ? <><Check size={15} /> Active theme</> : <>Use this theme <ArrowRight size={15} /></>}</strong></div>
      </button>)}
    </div>
    <section className="appearance-mode"><div><h2>Color mode</h2><p>Keep it light or switch to a darker workspace.</p></div><div role="group" aria-label="Color mode">{[{ id: 'light', label: 'Light', icon: Sun }, { id: 'dark', label: 'Dark', icon: Moon }].map(({ id, label, icon: Icon }) => <button key={id} type="button" aria-pressed={theme === id} onClick={() => setTheme(id)}><Icon size={17} />{label}{theme === id && <Check size={14} />}</button>)}</div></section>
    <section className="appearance-customization" aria-labelledby="personalize-title">
      <h2 id="personalize-title">Personalise your workspace</h2>
      <p>Adjust the accent for each theme and choose the reading style that suits you.</p>
      <div className="appearance-settings-grid">
        <div className="appearance-setting">
          <label htmlFor="accent-color">{skin === 'evolution' ? 'Evo' : 'Paper'} accent colour</label>
          <input id="accent-color" type="color" value={accent} onChange={e => setAccent(e.target.value)} />
          <p>{accent.toUpperCase()} · Text and focus colours adjust for readability.</p>
          <div className="accent-presets" role="group" aria-label="Accent presets">
            {[['Gold', '#bc942c'], ['Blue', '#076fd5'], ['Teal', '#087f8c'], ['Green', '#287b42'], ['Rose', '#b43c62']].map(([name, color]) =>
              <button key={name} type="button" aria-pressed={accent === color} onClick={() => setAccent(color)}><i style={{ backgroundColor: color }} aria-hidden="true" />{name}</button>)}
          </div>
        </div>
        <div className="appearance-setting"><label htmlFor="appearance-font">Font</label>
          <Select id="appearance-font" value={appearance.font} onChange={e => setAppearance({ font: e.target.value })}>
            {Object.entries(window.BoronAppearance.fonts).map(([id, font]) => <option key={id} value={id}>{font.label}</option>)}
          </Select><p>Fonts are served by this panel, with no external font requests.</p>
        </div>
        <div className="appearance-setting"><label htmlFor="appearance-text">Text size</label>
          <Select id="appearance-text" value={appearance.textSize} onChange={e => setAppearance({ textSize: e.target.value })}><option value="default">Theme default</option><option value="large">Larger text</option></Select>
        </div>
        <div className="appearance-setting"><label htmlFor="appearance-density">Spacing</label>
          <Select id="appearance-density" value={appearance.density} onChange={e => setAppearance({ density: e.target.value })}><option value="default">Theme default</option><option value="compact">Compact</option><option value="comfortable">Comfortable</option></Select>
        </div>
        <div className="appearance-setting"><label htmlFor="appearance-motion">Motion</label>
          <Select id="appearance-motion" value={appearance.motion} onChange={e => setAppearance({ motion: e.target.value })}><option value="system">Follow device preference</option><option value="reduced">Reduce motion</option></Select>
        </div>
      </div>
      <div className="appearance-sample">Your workspace, your style. Domains, email and databases — 0123456789.</div>
      <div className="appearance-settings-actions"><Button variant="secondary" onClick={resetAppearance}>Reset appearance</Button></div>
    </section>
    <div className="appearance-note"><Monitor size={17} /><p>Your choice is saved in this browser and applies immediately. Your account, tools, and data stay the same.</p></div>
    <Link to={role === 'admin' ? '/overview' : role === 'reseller' ? '/reseller' : '/dashboard'} className="appearance-return">Back to dashboard <ArrowRight size={16} /></Link>
  </div>
}
