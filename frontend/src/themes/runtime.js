// The loaded theme: { id, Layout, Home, icons?, toolIcons? }. main.jsx sets it
// once before the first render, and it never changes without a page load, so
// plain module state is enough and components need no subscription.
let active = null

export const setActiveTheme = (theme) => { active = theme }
export const activeTheme = () => active
