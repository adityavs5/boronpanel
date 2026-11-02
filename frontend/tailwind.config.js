/** @type {import('tailwindcss').Config} */
// Boron design tokens (frontend/DESIGN.md). Every colour resolves to a CSS
// variable in src/styles/tokens.css, so one class set renders correctly in
// both skins, both colour modes and with any user-chosen accent.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        accent: {
          DEFAULT: token('accent'),
          ...Object.fromEntries([50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((n) => [n, token(`accent-${n}`)])),
          foreground: token('accent-fg'),
          hover: token('accent-hover'),
          soft: token('accent-soft'),
        },
        navy: token('navy'),
        scrim: 'rgb(var(--scrim))',
        danger: { DEFAULT: token('danger'), solid: token('danger-solid'), foreground: '#FFFFFF' },
        warning: { DEFAULT: token('warning'), solid: token('warning-solid'), foreground: '#FFFFFF' },
        success: { DEFAULT: token('success'), solid: token('success-solid'), foreground: '#FFFFFF' },
        info: { DEFAULT: token('info'), solid: token('info-solid'), foreground: '#FFFFFF' },
        chrome: { DEFAULT: token('chrome-bg'), foreground: token('chrome-fg') },
        background: token('bg'),
        surface: token('surface'),
        card: token('card'),
        border: token('border'),
        input: token('input'),
        'input-surface': token('input-surface'),
        ring: token('ring'),
        link: token('link'),
        foreground: token('fg'),
        heading: token('heading'),
        muted: { DEFAULT: token('muted'), foreground: token('muted-fg') },
      },
      // Accent text always uses the readable --link shade (raw --accent can sit
      // below 4.5:1 on dark cards), and the low/high ramp steps that are tints
      // as backgrounds become readable text shades too.
      textColor: {
        accent: Object.fromEntries([['DEFAULT', token('link')], ...[50, 100, 200, 900, 950].map((n) => [n, token('link')])]),
      },
      fontFamily: {
        sans: ['var(--font-ui)', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      borderRadius: {
        card: 'var(--radius-card)',
        btn: 'var(--radius-btn)',
        control: 'var(--radius-btn)',
        badge: 'var(--radius-badge)',
        chip: 'var(--radius-chip)',
        lg: 'var(--radius-card)',
        md: 'var(--radius-btn)',
        sm: 'var(--radius-badge)',
      },
      boxShadow: {
        card: 'var(--shadow-card)',
        'card-hover': 'var(--shadow-pop)',
        dropdown: 'var(--shadow-pop)',
      },
      keyframes: {
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'slide-in-right': { from: { transform: 'translateX(100%)', opacity: '0' }, to: { transform: 'translateX(0)', opacity: '1' } },
        // Animates the individual `scale` property so it composes with the centring translate on dialogs.
        'scale-in': { from: { scale: '0.97', opacity: '0' }, to: { scale: '1', opacity: '1' } },
      },
      animation: {
        'fade-in': 'fade-in 0.14s ease-out',
        'slide-in-right': 'slide-in-right 0.16s ease-out',
        'scale-in': 'scale-in 0.12s ease-out',
      },
    },
  },
  plugins: [],
}
