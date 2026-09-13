import { Palette, Check, ChevronDown } from 'lucide-react'
import { useUI } from '@/store/ui'
import { SKINS } from '@/config/themes'
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel } from '@/components/ui/DropdownMenu'

export function ThemeSelector({ compact = false }) {
  const skin = useUI((s) => s.skin)
  const setSkin = useUI((s) => s.setSkin)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" className="skin-selector" aria-label="Choose theme">
          <Palette size={17} aria-hidden="true" />
          {!compact && <span className="skin-selector-name">{SKINS.find((s) => s.id === skin)?.name}</span>}
          <ChevronDown size={13} aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel>Panel theme</DropdownMenuLabel>
        {SKINS.map((item) => <DropdownMenuItem key={item.id} role="menuitemradio" aria-checked={skin === item.id} onSelect={() => setSkin(item.id)}>
          <span className="flex-1"><span className="block">{item.name}</span><span className="block text-xs text-muted-foreground">{item.layout}</span></span>
          {skin === item.id && <Check size={16} />}
        </DropdownMenuItem>)}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
