// Phase 8 feature 13: Monaco editor, bundled fully locally (no CDN — the SPA's
// CSP is script-src 'self'). To keep the bundle and build memory sane we import
// only the editor API + the specific language *highlighters* (Monarch
// tokenizers run in the main thread — no per-language service worker needed for
// a file editor), and just the base editor worker. Lazy-loaded (React.lazy in
// Files.jsx) so Monaco only enters the bundle when the editor opens.
import { useUI } from '@/store/ui'
import Editor, { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'

// Syntax highlighting for the file types the goal names (+ a few common extras).
import 'monaco-editor/esm/vs/basic-languages/php/php.contribution'
import 'monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution'
import 'monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution'
import 'monaco-editor/esm/vs/basic-languages/css/css.contribution'
import 'monaco-editor/esm/vs/basic-languages/html/html.contribution'
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution'
import 'monaco-editor/esm/vs/basic-languages/xml/xml.contribution'
import 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution'
import 'monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution'
import 'monaco-editor/esm/vs/basic-languages/shell/shell.contribution'
import 'monaco-editor/esm/vs/basic-languages/sql/sql.contribution'
// JSON gets its own basic tokenizer via the language registration below (the
// full json language *service* worker is intentionally omitted).

self.MonacoEnvironment = {
  getWorker() {
    return new editorWorker()
  },
}

loader.config({ monaco })

const LANG = {
  php: 'php', js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript', css: 'css', scss: 'css', less: 'css',
  html: 'html', htm: 'html', json: 'json', py: 'python', env: 'ini', ini: 'ini',
  md: 'markdown', xml: 'xml', yml: 'yaml', yaml: 'yaml', sh: 'shell', sql: 'sql',
}

export function languageForName(name) {
  const lower = (name || '').toLowerCase()
  if (lower === '.env' || lower.endsWith('.env')) return 'ini'
  const dot = lower.lastIndexOf('.')
  const ext = dot >= 0 ? lower.slice(dot + 1) : ''
  return LANG[ext] || 'plaintext'
}

export default function CodeEditor({ value, onChange, filename, height = '60vh' }) {
  const prefersDark = useUI((s) => s.theme === 'dark')
  return (
    <Editor
      height={height}
      language={languageForName(filename)}
      value={value}
      onChange={(v) => onChange(v ?? '')}
      theme={prefersDark ? 'vs-dark' : 'light'}
      options={{
        fontSize: 13,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 2,
        wordWrap: 'on',
      }}
    />
  )
}
