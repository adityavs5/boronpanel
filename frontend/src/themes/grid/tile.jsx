// One tool pictogram: a solid glyph on a coloured tile. grid.css draws the tones.
export function tile(Glyph, tone) {
  return function ToolTile() {
    return <span className="grid-tile" data-tone={tone}><Glyph aria-hidden="true" /></span>
  }
}
