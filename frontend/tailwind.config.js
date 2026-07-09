/** @type {import('tailwindcss').Config} */
// Forgehost design tokens (Cloudways-inspired). Surfaces use CSS variables
// (see src/index.css) so the same class set renders in both light and dark
// mode; brand + semantic colors are fixed hex per the goal's token spec.
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Brand accent (teal) — full ramp derived from #1FBED6.
        accent: {
          DEFAULT: '#1FBED6',
          50: '#ECFBFD',
          100: '#D0F5FA',
          200: '#A6ECF4',
          300: '#6FDCEB',
          400: '#3DCADF',
          500: '#1FBED6',
          600: '#1697AC',
          700: '#15788A',
          800: '#176272',
          900: '#185260',
          950: '#0A3742',
          foreground: '#04222A',
        },
        // Semantic status colors (goal spec).
        danger: { DEFAULT: '#EF4444', foreground: '#FFFFFF' },
        warning: { DEFAULT: '#F59E0B', foreground: '#3A2A05' },
        success: { DEFAULT: '#10B981', foreground: '#03291E' },
        info: { DEFAULT: '#3B82F6', foreground: '#FFFFFF' },
        // Sidebar is always the dark slate (#111827 = gray-900) in both themes.
        sidebar: {
          DEFAULT: '#111827',
          hover: '#1F2937',
          active: '#1FBED6',
          muted: '#9CA3AF',
          border: '#1F2937',
        },
        // Themeable surfaces via CSS vars.
        background: 'rgb(var(--bg) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        card: 'rgb(var(--card) / <alpha-value>)',
        border: 'rgb(var(--border) / <alpha-value>)',
        input: 'rgb(var(--input) / <alpha-value>)',
        ring: 'rgb(var(--ring) / <alpha-value>)',
        foreground: 'rgb(var(--fg) / <alpha-value>)',
        muted: {
          DEFAULT: 'rgb(var(--muted) / <alpha-value>)',
          foreground: 'rgb(var(--muted-fg) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['Inter Variable', 'Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      borderRadius: {
        // 8px cards, 6px buttons/inputs per the token spec.
        card: '8px',
        btn: '6px',
        lg: '8px',
        md: '6px',
        sm: '4px',
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(0 0 0 / 0.04), 0 1px 3px 0 rgb(0 0 0 / 0.06)',
        'card-hover': '0 4px 12px -2px rgb(0 0 0 / 0.10)',
        dropdown: '0 8px 24px -4px rgb(0 0 0 / 0.14)',
      },
      fontSize: {
        xs: ['0.75rem', { lineHeight: '1rem' }],
        sm: ['0.8125rem', { lineHeight: '1.25rem' }],
        base: ['0.875rem', { lineHeight: '1.5rem' }],
      },
      keyframes: {
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'slide-in-right': { from: { transform: 'translateX(100%)', opacity: '0' }, to: { transform: 'translateX(0)', opacity: '1' } },
        'scale-in': { from: { transform: 'scale(0.96)', opacity: '0' }, to: { transform: 'scale(1)', opacity: '1' } },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: {
        'fade-in': 'fade-in 0.15s ease-out',
        'slide-in-right': 'slide-in-right 0.2s ease-out',
        'scale-in': 'scale-in 0.12s ease-out',
      },
    },
  },
  plugins: [],
}
