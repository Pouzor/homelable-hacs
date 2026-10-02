import fs from 'node:fs'
import path from 'node:path'
import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import {
  t,
  getLocale,
  setLocaleForTest,
  registerDictionary,
  subscribe,
  syncLocaleFromHass,
  useLocale,
  DEFAULT_LOCALE,
  LOCALES,
} from '../index'
import zhCN from '../locales/zh-CN'
import root from '../locales/parts/root'
import common from '../locales/parts/common'
import componentsModals1 from '../locales/parts/components-modals-1'
import componentsModals2 from '../locales/parts/components-modals-2'
import componentsPanels from '../locales/parts/components-panels'
import componentsIntegrations from '../locales/parts/components-integrations'
import documentation from '../locales/parts/documentation'
import rack from '../locales/parts/rack'
import nodeTypes from '../locales/parts/node-types'
import icons from '../locales/parts/icons'
import panel from '../locales/parts/panel'
import designIcons from '../locales/parts/design-icons'
import {
  DIALOG_TITLES,
  DYNAMIC_TABLES,
  FIELD_NAME_KEYS,
  NON_KEY_LITERALS,
  PROP_DEFAULTS,
  RUNTIME_KEY_PARTS,
  valuesOf,
} from '../dynamicTables'

// __dirname is src/i18n/__tests__, so `../..` is already `src`.
const SRC_ROOT = path.resolve(__dirname, '../..')

// core.ts, index.ts and dynamicTables.ts document the API with `t('…')` examples
// in comments, so scanning them would invent keys that are never rendered. The
// dictionary is data and holds no call sites at all, only doc examples.
const SCAN_SKIP = new Set([
  path.join(SRC_ROOT, 'i18n/core.ts'),
  path.join(SRC_ROOT, 'i18n/index.ts'),
  path.join(SRC_ROOT, 'i18n/dynamicTables.ts'),
])
const SCAN_SKIP_DIRS = new Set([path.join(SRC_ROOT, 'i18n/locales')])

const PART_NAMES = [
  'root',
  'common',
  'components-modals-1',
  'components-modals-2',
  'components-panels',
  'components-integrations',
  'documentation',
  'rack',
  'node-types',
  'icons',
  'panel',
  'design-icons',
]

/** The raw part modules, keyed by the name PART_NAMES lists. */
const PARTS: Record<string, Record<string, string>> = {
  root,
  common,
  'components-modals-1': componentsModals1,
  'components-modals-2': componentsModals2,
  'components-panels': componentsPanels,
  'components-integrations': componentsIntegrations,
  documentation,
  rack,
  'node-types': nodeTypes,
  icons,
  panel,
  'design-icons': designIcons,
}

const STR = /(['"`])((?:\\.|(?!\1)[^\\])*)\1/g

/**
 * Index just past the top-level comma separating call arguments, or -1.
 *
 * String-aware on purpose. A comma inside a quoted key is not an argument
 * break: `t('Reads … Pending, where you …')` truncated at that comma leaves an
 * unterminated string, the literal regex finds no closing quote, and the whole
 * key silently disappears from the scan — reported as a stale entry and
 * deleted, when it was really a fully translated sentence.
 */
function firstArgEnd(body: string): number {
  let depth = 0
  let quote: string | null = null
  for (let i = 0; i < body.length; i++) {
    const c = body[i]
    if (quote) {
      if (c === '\\') { i++; continue }
      if (c === quote) quote = null
      continue
    }
    if (c === "'" || c === '"' || c === '`') { quote = c; continue }
    if (c === '(' || c === '[' || c === '{') depth++
    else if (c === ')' || c === ']' || c === '}') depth--
    else if (c === ',' && depth === 0) return i
  }
  return -1
}

/**
 * Every literal passed to `t()`, however it is written.
 *
 * A plain `/\bt\(\s*(')…/ regex is not enough, and the misses are silent:
 * `t(mode === 'pan' ? 'Pan and zoom' : 'Locked')` does not put the literal right
 * after the paren, and `t('Show on {where}', { where: t('node') })` defeats a
 * non-greedy match up to the first `)`. Both shapes are in this panel, and a
 * literal that is not collected is a key with no entry — English on screen with
 * every test green.
 *
 * Over-collecting is the same failure in the other direction, so this reads
 * only the first argument and skips operands of a comparison, which is how
 * `'pan'` in `mode === 'pan'` and `'en-US'` in `toLocaleString('en-US')` stay
 * out of the key set.
 */
function literalsInCalls(src: string): string[] {
  const found: string[] = []
  for (let i = 0; i < src.length; i++) {
    if (src[i] !== 't') continue
    const prev = src[i - 1]
    if (prev && /[A-Za-z0-9_$.]/.test(prev)) continue
    if (src[i + 1] !== '(') continue

    let depth = 0
    let end = -1
    for (let j = i + 1; j < src.length; j++) {
      const c = src[j]
      if (c === '(') depth++
      else if (c === ')') {
        depth--
        if (depth === 0) { end = j; break }
      }
    }
    if (end === -1) break

    const body = src.slice(i + 2, end)
    const cut = firstArgEnd(body)
    const arg = cut === -1 ? body : body.slice(0, cut)
    for (const m of arg.matchAll(STR)) {
      const before = arg.slice(0, m.index).trimEnd()
      const after = arg.slice(m.index + m[0].length).trimStart()
      if (/(?:===|!==|==|!=|>=|<=|[<>])$/.test(before)) continue
      if (/^(?:===|!==|==|!=|>=|<=|[<>])/.test(after)) continue
      const key = m[2].replace(/\\'/g, "'").replace(/\\"/g, '"')
      if (key) found.push(key)
    }
    i = end
  }
  return found
}

function resetLocale() {
  setLocaleForTest(DEFAULT_LOCALE)
}

afterEach(() => {
  resetLocale()
})

describe('i18n core', () => {
  it('falls back to the key itself, so English output never changes', () => {
    resetLocale()
    expect(getLocale()).toBe('en')
    // Nothing is registered for English: the source string *is* the translation.
    expect(t('Save')).toBe('Save')
    expect(t('A string with no translation at all')).toBe('A string with no translation at all')
  })

  it('translates a registered key in zh-CN', () => {
    registerDictionary('zh-CN', { Save: '保存' })
    setLocaleForTest('zh-CN')
    expect(t('Save')).toBe('保存')
  })

  it('falls back to English for a key the dictionary is missing', () => {
    setLocaleForTest('zh-CN')
    expect(t('Not translated yet')).toBe('Not translated yet')
  })

  it('never renders a blank string for an empty or whitespace-only entry', () => {
    registerDictionary('zh-CN', { 'Empty one': '', 'Blank one': '   ' })
    setLocaleForTest('zh-CN')
    expect(t('Empty one')).toBe('Empty one')
    expect(t('Blank one')).toBe('Blank one')
  })

  it('interpolates {name} placeholders', () => {
    registerDictionary('zh-CN', { 'Moved {count} devices into {zone}': '已将 {count} 台设备移入 {zone}' })
    setLocaleForTest('zh-CN')
    expect(t('Moved {count} devices into {zone}', { count: 3, zone: 'Rack 1' })).toBe(
      '已将 3 台设备移入 Rack 1',
    )
  })

  it('leaves a placeholder in place when no value is supplied for it', () => {
    resetLocale()
    expect(t('Moved {count} devices')).toBe('Moved {count} devices')
  })

  it('lets a translation drop a placeholder it has no use for', () => {
    // The English plural-suffix convention: Chinese has no plural form, so the
    // translation simply omits {plural} and the text reads correctly. This is
    // what `Approved {count} device{plural}{extra}` relies on.
    registerDictionary('zh-CN', { 'Imported {count} node{plural}': '已导入 {count} 个节点' })
    setLocaleForTest('zh-CN')
    expect(t('Imported {count} node{plural}', { count: 1, plural: '' })).toBe('已导入 1 个节点')
    expect(t('Imported {count} node{plural}', { count: 5, plural: 's' })).toBe('已导入 5 个节点')
  })

  it('leaves text with literal braces alone when no vars are passed', () => {
    resetLocale()
    // Guard against a JSON/YAML example being treated as a placeholder.
    expect(t('Example: {"a": 1}')).toBe('Example: {"a": 1}')
  })

  it('exposes English and Simplified Chinese', () => {
    expect([...LOCALES]).toEqual(['en', 'zh-CN'])
  })
})

/**
 * The panel has no language switcher: Home Assistant owns the setting and hands
 * it over on `hass`. These pin that contract down, because it is the only thing
 * that decides which language a user sees.
 */
describe('following the Home Assistant language', () => {
  it('falls back to English for a language it has no translation for', () => {
    setLocaleForTest('zh-CN')
    syncLocaleFromHass('fr')
    expect(getLocale()).toBe('en')
  })

  it('switches back to English when HA is set back to English', () => {
    setLocaleForTest('zh-CN')
    // The direction that matters: leaving zh-CN for 'en' has to actually happen,
    // or a user who reverts the setting in HA is stuck looking at Chinese.
    syncLocaleFromHass('en')
    expect(getLocale()).toBe('en')
  })

  it('tolerates every shape HA reports for Chinese', () => {
    for (const language of ['zh-Hans', 'zh-CN', 'zh', 'zh-Hant']) {
      resetLocale()
      syncLocaleFromHass(language)
      expect(getLocale(), `${language} should select zh-CN`).toBe('zh-CN')
    }
  })

  it('ignores an undefined language rather than resetting', () => {
    setLocaleForTest('zh-CN')
    // HA can hand over a partial object; that must not undo a locale already
    // resolved from a language it did report.
    syncLocaleFromHass(undefined)
    expect(getLocale()).toBe('zh-CN')
  })

  it('notifies subscribers only when the locale actually changes', () => {
    resetLocale()
    let calls = 0
    const off = subscribe(() => { calls++ })
    try {
      syncLocaleFromHass('en')
      expect(calls).toBe(0)
      syncLocaleFromHass('zh-Hans')
      expect(calls).toBe(1)
      // HA re-assigns `hass` many times a second; a repeated language must not
      // re-render the whole panel.
      syncLocaleFromHass('zh-Hans')
      expect(calls).toBe(1)
    } finally {
      off()
    }
  })
})

/**
 * Completeness gate. English-as-key means an untranslated key silently renders
 * English, so nothing in the suite would ever fail over a missing entry. This
 * test is the only thing standing between a partial translation and a UI that
 * is half Chinese, so it walks the real source tree rather than trusting a
 * hand-maintained list.
 */
describe('zh-CN dictionary completeness', () => {
  // The unit tests above call registerDictionary(), which mutates the imported
  // zh-CN object in place. Reload the module in a fresh registry so these checks
  // read the dictionary exactly as it exists in the file, not as the test run
  // left it.
  let fileDict: Record<string, string> = zhCN
  beforeAll(async () => {
    vi.resetModules()
    fileDict = (await import('../locales/zh-CN')).default
  })

  function walk(dir: string, out: string[] = []): string[] {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules' || entry.name === 'dist') continue
        if (SCAN_SKIP_DIRS.has(full)) continue
        // Fixtures and test helpers are not app copy, and a string that only
        // appears in a test is not a key the UI ever looks up.
        if (entry.name === 'test' || entry.name === '__tests__') continue
        walk(full, out)
      } else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.(ts|tsx)$/.test(entry.name)) {
        if (!SCAN_SKIP.has(full)) out.push(full)
      }
    }
    return out
  }

  /** Keys passed to t() anywhere in the source tree, minus the known non-keys. */
  function collectKeys(): { used: Set<string>; missing: string[] } {
    const used = new Set<string>()
    const missing: string[] = []
    for (const file of walk(SRC_ROOT)) {
      for (const key of literalsInCalls(fs.readFileSync(file, 'utf8'))) {
        if ((NON_KEY_LITERALS as readonly string[]).includes(key)) continue
        used.add(key)
        if (!Object.prototype.hasOwnProperty.call(fileDict, key)) {
          missing.push(`${path.relative(SRC_ROOT, file)}: ${key}`)
        }
      }
    }
    return { used, missing }
  }

  it('has no empty translation values', () => {
    const bad = Object.entries(fileDict)
      .filter(([, v]) => !v.trim())
      .map(([k]) => k)
    expect(bad).toEqual([])
  })

  it('scans a real source tree, not an empty one', () => {
    // Guards the two scans below: a broken path would report zero files and make
    // every assertion pass vacuously.
    expect(walk(SRC_ROOT).length).toBeGreaterThan(50)
  })

  it('defines a translation for every t() key used in the source', () => {
    const { missing } = collectKeys()
    expect(missing, `Untranslated t() keys:\n${missing.join('\n')}`).toEqual([])
  })

  it('has no translation for a key the code no longer uses', () => {
    // Guards the opposite drift: a stale entry is dead weight and hides the fact
    // that its source string was reworded, which would silently untranslate it.
    //
    // Exempt: keys that belong to a table or a prop default whose values reach
    // t() at runtime. Those never appear as a literal call site, so the scan
    // cannot see them being used — and declaring them stale would make this test
    // contradict the coverage test that exists precisely to guard them.
    const dynamicKeys = new Set<string>([
      ...DIALOG_TITLES,
      ...FIELD_NAME_KEYS,
      ...PROP_DEFAULTS,
    ])
    for (const table of DYNAMIC_TABLES) {
      const src = fs.readFileSync(path.join(SRC_ROOT, table.file), 'utf8')
      for (const v of valuesOf(src, table)) dynamicKeys.add(v)
    }
    for (const part of RUNTIME_KEY_PARTS) {
      const body = fs.readFileSync(
        path.join(SRC_ROOT, 'i18n/locales/parts', `${part}.ts`),
        'utf8',
      )
      for (const m of body.matchAll(/^\s*(['"])((?:\\.|(?!\1)[^\\])*)\1\s*:/gm)) {
        dynamicKeys.add(m[2].replace(/\\'/g, "'").replace(/\\"/g, '"'))
      }
    }

    const { used } = collectKeys()
    const stale = Object.keys(fileDict).filter((k) => !used.has(k) && !dynamicKeys.has(k))
    expect(stale, `Stale zh-CN entries:\n${stale.join('\n')}`).toEqual([])
  })

  it('translates a shared English string the same way in every part', () => {
    // The same source string turns up in several files, and the work was split
    // across parts. Spreading the parts would silently keep only the last
    // definition, so two parts disagreeing is a real (and invisible) defect.
    const seen = new Map<string, Map<string, string>>() // key -> value -> part
    for (const name of PART_NAMES) {
      for (const [key, value] of Object.entries(PARTS[name])) {
        if (!seen.has(key)) seen.set(key, new Map())
        seen.get(key)!.set(value, name)
      }
    }

    const conflicts: string[] = []
    for (const [key, byValue] of seen) {
      if (byValue.size > 1) {
        conflicts.push(
          `${key}\n    ${[...byValue].map(([v, p]) => `${p}: ${v}`).join('\n    ')}`,
        )
      }
    }
    expect(conflicts, `Parts disagree on a translation:\n${conflicts.join('\n')}`).toEqual([])
  })
})

describe('the app-wide usage pattern', () => {
  // Every component in the panel imports `t` at module scope and calls
  // `useLocale()` purely to repaint. That split exists so the React Compiler
  // keeps the hand-written memoization, so it is worth pinning down: a
  // module-level `t` must still repaint on its own.
  function ModuleScopedProbe() {
    useLocale()
    return <span data-testid="label">{t('Probe')}</span>
  }

  it('repaints a component that imports t at module scope', () => {
    registerDictionary('zh-CN', { Probe: '探针' })
    render(<ModuleScopedProbe />)
    expect(screen.getByTestId('label').textContent).toBe('Probe')

    act(() => syncLocaleFromHass('zh-Hans'))
    expect(screen.getByTestId('label').textContent).toBe('探针')

    act(() => syncLocaleFromHass('en'))
    expect(screen.getByTestId('label').textContent).toBe('Probe')
  })
})
