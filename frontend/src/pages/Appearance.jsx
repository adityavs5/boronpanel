import { Sun, Moon, Check, SlidersHorizontal } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { activeTheme } from '@/themes/runtime'
import { themeInfo } from '@/themes/registry'
import { Select } from '@/components/ui/Select'
import { Button } from '@/components/ui/Button'
import { PageHeader } from '@/components/ui/PageHeader'

// Personal comfort settings only. The panel theme belongs to the server
// administrator (Panel theme page).
export default function Appearance() {
  const { theme, setTheme, appearance, setAppearance, resetAppearance } = useUI()
  const role = useAuth((s) => s.role)
  const info = themeInfo(activeTheme()?.id)
  return <div className="appearance-page">
    <PageHeader title="Display preferences" description="Adjust text size, spacing and motion for this browser." icon={SlidersHorizontal} />
    {info.darkMode && <section className="appearance-mode"><div><h2>Colour mode</h2><p>Keep it light or switch to a darker workspace.</p></div><div role="group" aria-label="Colour mode">{[{ id: 'light', label: 'Light', icon: Sun }, { id: 'dark', label: 'Dark', icon: Moon }].map(({ id, label, icon: Icon }) => <button key={id} type="button" aria-pressed={theme === id} onClick={() => setTheme(id)}><Icon size={17} />{label}{theme === id && <Check size={14} />}</button>)}</div></section>}
    <section className="appearance-customization" aria-labelledby="display-title">
      <h2 id="display-title">Reading and comfort</h2>
      <div className="appearance-settings-grid">
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
      <div className="appearance-settings-actions"><Button variant="secondary" onClick={resetAppearance}>Reset preferences</Button></div>
    </section>
    <p className="appearance-note-text">This panel uses the {info.name} theme, set by the server administrator.{role === 'admin' && <> <Link to="/panel-theme">Change the panel theme</Link></>}</p>
  </div>
}
