import fs from 'node:fs'
import path from 'node:path'
import {
  DIALOG_TITLES,
  DYNAMIC_TABLES,
  FIELD_NAME_KEYS,
  NON_KEY_LITERALS,
  PROP_DEFAULTS,
  valuesOf,
} from '../dynamicTables'

// __dirname is src/i18n/__tests__, so `../..` is already `src`.
const SRC = path.resolve(__dirname, '../..')

/** Every key that has a zh-CN entry, across all parts. */
function dictionaryKeys(): Set<string> {
  const keys = new Set<string>()
  const dir = path.join(SRC, 'i18n/locales/parts')
  for (const f of fs.readdirSync(dir)) {
    if (!f.endsWith('.ts')) continue
    const src = fs.readFileSync(path.join(dir, f), 'utf8')
    for (const m of src.matchAll(/^\s*(['"])((?:\\.|(?!\1)[^\\])*)\1\s*:/gm)) {
      // Unescape so this matches the *runtime* key, not the source spelling:
      // the part file writes 'a\nb' but the lookup at runtime is a real newline.
      keys.add(
        m[2]
          .replace(/\\n/g, '\n')
          .replace(/\\t/g, '\t')
          .replace(/\\'/g, "'")
          .replace(/\\"/g, '"')
          .replace(/\\\\/g, '\\'),
      )
    }
  }
  return keys
}

describe('tables whose values reach t() at runtime', () => {
  const dict = dictionaryKeys()

  for (const table of DYNAMIC_TABLES) {
    it(`${table.what} all have a zh-CN entry`, () => {
      const src = fs.readFileSync(path.join(SRC, table.file), 'utf8')
      const all = valuesOf(src, table)
      // Guard against a stale selector making this pass vacuously.
      expect(all.length, `no values found in ${table.file} — the selector is stale`).toBeGreaterThan(0)
      const missing = all.filter((v) => !dict.has(v))
      expect(
        missing,
        `These ${table.what} fall back to English (from ${table.file}):\n` + missing.join('\n'),
      ).toEqual([])
    })
  }

  for (const key of FIELD_NAME_KEYS) {
    it(`the custom-colour field name ${JSON.stringify(key)} has a zh-CN entry`, () => {
      expect(dict.has(key), `${JSON.stringify(key)} is rendered as t(key) but has no entry`).toBe(true)
    })
  }

  for (const key of PROP_DEFAULTS) {
    it(`the PropertyList default ${JSON.stringify(key)} has a zh-CN entry`, () => {
      expect(
        dict.has(key),
        `${JSON.stringify(key)} is a prop default that PropertyList renders as t(…), but has no entry`,
      ).toBe(true)
    })
  }
})

describe('no English string literal reaches a render site untranslated', () => {
  // The sweep that found the first batch of misses only looked at JSX text and
  // the standard title/aria/placeholder attributes, so it missed custom props,
  // object-literal fields and default parameters. This is the wider net, kept
  // deliberately blunt: it flags anything prose-shaped that is not inside t().
  // A hit means "look at it by hand"; a hit that is deliberate belongs in
  // ALLOWED with the reason stated.
  const ALLOWED = new Map<string, string>([
    // Sample input, not copy. These have no dictionary entry on purpose.
    ['My Server', 'sample input'],
    ['Node host (app.example.com)', 'sample input'],
    ['Path (/admin)', 'sample input'],
    ['Debian 12', 'sample input'],
    // NOTE: do not list a hint here once it has been translated. ALLOWED makes
    // both sweeps skip the string, so adding the newly-fixed placeholders would
    // blind the check to exactly the regression it exists to catch — verified
    // by reverting a fix and watching the test stay green.
    // Font stacks offered by the pickers. A typeface name is a proper noun in
    // any locale, and the "(sans-serif)" suffix is the fallback it is grouped by.
    ['Inter (sans-serif)', 'font name, a proper noun in any locale'],
    ['JetBrains Mono', 'font name, a proper noun in any locale'],
    ['System Sans', 'font name, a proper noun in any locale'],
    // Developer-facing errors. These are thrown for a maintainer reading the
    // console, never rendered in the panel's UI.
    [
      'Add a corresponding WS command to custom_components/homelable/websocket.py.',
      'developer error text, thrown rather than rendered',
    ],
    [
      'Home Assistant connection not yet available. Did the panel mount?',
      'developer error text, thrown rather than rendered',
    ],
    // Sample data the demo seeds into a fresh canvas: an OS release, a product
    // name, a rack name, a cable label. They stand in for what a user would
    // type, so they behave like the other sample values.
    ['Debian 12', 'demo sample data, stands in for a user-entered value'],
    ['Proxmox VE 8.2', 'demo sample data: an OS release'],
    ['Synology DSM', 'demo sample data: a product name'],
    ['UniFi AP', 'demo sample data: a real product name'],
    ['TrueNAS SCALE', 'demo sample data: a product name'],
    ['Main rack', 'demo sample data, stands in for a user-entered value'],
    ['Cable manager', 'demo sample data, stands in for a user-entered value'],
    ['WAN uplink', 'demo sample data, stands in for a user-entered value'],
    ['VLAN 20', 'demo sample data: a technical value, not copy'],
    ['Proxmox VE', 'proper noun'],
    ['Zigbee Hub', 'demo sample data: a device name of the demo canvas'],
    ['Patch ref', 'demo sample data, stands in for a user-entered value'],
    ['Cable manager 1U', 'demo sample data, stands in for a user-entered value'],
    ['Freebox Ultra', 'demo sample data: a real product name'],
    ['Netgear GS308', 'demo sample data: a real product name'],
    ['TP-Link TL-SG108', 'demo sample data: a real product name'],
    // Property keys the importers mint. They become data, so a translated key
    // would name a property the importer never emits.
    ['CPU Model', 'property key minted by the importers, becomes data'],
    ['CPU Cores', 'property key minted by the importers, becomes data'],
    ['Z-Wave ID', 'property key minted by the Z-Wave importer, becomes data'],
  ])
  // The modal title defaults, each of which the modal renders as {t(title)}.
  for (const title of DIALOG_TITLES) {
    ALLOWED.set(title, 'dialog title default, rendered as {t(title)}')
  }
  // PropertyList's own prop defaults, which it translates at its render site.
  for (const key of PROP_DEFAULTS) {
    ALLOWED.set(key, 'PropertyList prop default, translated at the render site')
  }

  // Prose in a comment is documentation, not copy. Strip comments before
  // scanning rather than guessing from line prefixes — a block comment's
  // continuation lines do not start with `*`, and its opening line often
  // carries no marker at all. The block state carries across lines, since a
  // comment may open mid-line.
  function stripComments(line: string, inBlock: boolean): { code: string; inBlock: boolean } {
    let code = ''
    for (let i = 0; i < line.length; i++) {
      if (inBlock) {
        if (line[i] === '*' && line[i + 1] === '/') { inBlock = false; i++ }
        continue
      }
      if (line[i] === '/' && line[i + 1] === '*') { inBlock = true; i++; continue }
      if (line[i] === '/' && line[i + 1] === '/') break
      code += line[i]
    }
    return { code, inBlock }
  }

  const SKIP_LINE = [
    /^\s*import\b/,
    /^\s*export\s+.*from/,
    /^\s*$/,
    /className=/,
    /\bfrom ['"]/,
    /data-[a-z-]+=/,
    /console\.(log|warn|error|info)/,
    /@ts-ignore/,
    /@ts-expect-error/,
    /eslint/,
  ]

  const isProse = (s: string) => {
    if (!/^[A-Z]/.test(s) || !/\s/.test(s) || s.length < 6) return false
    if (/^[A-Z0-9_./:-]+$/.test(s)) return false
    // SVG path data. Interpolated coordinates are stripped first: their variable
    // names would never match a path-command character class.
    const bare = s.replace(/\$\{[^}]*\}/g, '')
    if (/^[MmLlHhVvCcSsQqTtAaZz][\d\s.,MHmLlHhVvCcSsQqTtAaZz-]*$/.test(bare)) return false
    if (/,\s*(monospace|serif|sans-serif|cursive|fantasy)$/.test(s)) return false
    if (/^(Bearer|Basic)\s/.test(s)) return false
    // An HTTP verb used as a terse note, e.g. "GET /nodes".
    if (/^(GET|POST|PUT|PATCH|DELETE|HEAD)\s+\//.test(s)) return false
    // Composite keys built by joining fields with a pipe — de-duplication keys,
    // cache keys, the like. Never shown to anyone.
    if (s.includes('|')) return false
    return true
  }

  it('finds nothing outside the allowlist', () => {
    // A string that has a dictionary entry is, by definition, translated — so it
    // is skipped here even when the `t()` is applied somewhere other than this
    // line. That covers the three shapes an earlier version of this sweep
    // false-positived on: values in a data table (`t(source.description)`),
    // component defaults (`title = 'Add Node'`, rendered as `{t(title)}`), and a
    // default sliced before lookup. A genuine miss has no entry, and that is
    // exactly what this test is here to catch.
    const dict = dictionaryKeys()
    const tableValues = new Set<string>()
    for (const table of DYNAMIC_TABLES) {
      for (const v of valuesOf(fs.readFileSync(path.join(SRC, table.file), 'utf8'), table)) {
        tableValues.add(v)
      }
    }

    const files: string[] = []
    ;(function walk(d: string) {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const full = path.join(d, e.name)
        if (e.isDirectory()) {
          if (e.name === 'node_modules' || e.name === 'i18n') continue
          // Fixtures, not app copy — a test's expected string is not something
          // a user ever sees rendered.
          if (e.name === 'test' || e.name === '__tests__') continue
          walk(full)
        } else if (/\.(ts|tsx)$/.test(e.name) && !/\.(test|spec)\.(ts|tsx)$/.test(e.name)) {
          files.push(full)
        }
      }
    })(SRC)

    expect(files.length).toBeGreaterThan(50)

    const hits: string[] = []
    for (const f of files) {
      let inBlock = false
      for (const [i, raw] of fs.readFileSync(f, 'utf8').split(/\r?\n/).entries()) {
        const { code: line, inBlock: stillInBlock } = stripComments(raw, inBlock)
        inBlock = stillInBlock
        if (SKIP_LINE.some((re) => re.test(line))) continue
        const translated = new Set(
          [...line.matchAll(/\bt\((['"`])((?:\\.|(?!\1)[^\\])*)\1/g)].map((m) => m[2]),
        )
        for (const m of line.matchAll(/(['"`])((?:\\.|(?!\1)[^\\])*)\1/g)) {
          const v = m[2]
          // A lookup fallback that is data rather than copy — see
          // NON_KEY_LITERALS. Prose-shaped rules would not catch these anyway,
          // but naming them here keeps one list as the single source of truth.
          if ((NON_KEY_LITERALS as readonly string[]).includes(v)) continue
          if (!isProse(v) || translated.has(v) || dict.has(v) || tableValues.has(v) || ALLOWED.has(v)) continue
          if (/^https?:|^\/|^\.|^#/.test(v)) continue
          hits.push(`${path.relative(SRC, f)}:${i + 1}  ${JSON.stringify(v)}`)
        }
      }
    }
    expect(
      hits,
      `Untranslated English literals — translate them, or add them to ALLOWED with a reason:\n${hits.join('\n')}`,
    ).toEqual([])
  })

  /**
   * Status ids are rendered straight from stored data, so they never appear as
   * a `t('…')` literal — `data.status` and `r.status` are variables. The sweep
   * above cannot see them twice over: it wants a leading capital *and* a
   * space, and these are single lower-case words.
   *
   * That is not hypothetical. `<span>{data.status}</span>` shipped and the
   * detail panel showed a green **Online** badge in a Chinese interface while
   * the sidebar next to it correctly said 在线. The helper that fixes it
   * (`statusLabel()`) already existed and simply was not called.
   *
   * So this asserts the *render* is translated, not that a key exists: a value
   * that passes through `t()`/`statusLabel()` is fine in any locale, and one
   * that does not is a miss. Keys reach t() through a variable, so the check
   * has to look at the JSX around the expression.
   */
  it('translates status ids rendered from stored data', () => {
    const PATTERNS: { re: RegExp; why: string }[] = [
      {
        re: /\{([a-zA-Z_$][\w$]*(?:\.[a-zA-Z_$][\w$]*)*\.status)\}/g,
        why: 'a stored status id rendered into the DOM',
      },
      {
        re: /\btitle=\{([a-zA-Z_$][\w$]*(?:\.[a-zA-Z_$][\w$]*)*\.status)\}/g,
        why: 'a stored status id used as a tooltip',
      },
    ]

    const files: string[] = []
    ;(function walk(d: string) {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const full = path.join(d, e.name)
        if (e.isDirectory()) {
          if (e.name === 'node_modules' || e.name === 'i18n' || e.name === '__tests__' || e.name === 'test') continue
          walk(full)
        } else if (/\.tsx$/.test(e.name) && !/\.test\.tsx$/.test(e.name)) {
          files.push(full)
        }
      }
    })(SRC)

    const hits: string[] = []
    for (const f of files) {
      let inBlock = false
      for (const [i, raw] of fs.readFileSync(f, 'utf8').split(/\r?\n/).entries()) {
        const { code: line, inBlock: stillInBlock } = stripComments(raw, inBlock)
        inBlock = stillInBlock
        for (const { re, why } of PATTERNS) {
          for (const m of line.matchAll(re)) {
            const expr = m[1]
            // Already routed through a translation helper.
            if (/\bt\(\s*[\w.]+\s*\)/.test(line) || /statusLabel\(/.test(line)) continue
            hits.push(`${path.relative(SRC, f)}:${i + 1}  ${JSON.stringify(expr)}  (${why})`)
          }
        }
      }
    }
    expect(
      hits,
      `Status ids reaching the screen untranslated — wrap them in t() or statusLabel():\n${hits.join('\n')}`,
    ).toEqual([])
  })

  it('states a reason for every allowlisted string', () => {
    for (const [text, reason] of ALLOWED) {
      expect(reason.length, `ALLOWED entry ${JSON.stringify(text)} has no reason`).toBeGreaterThan(10)
    }
  })

  /**
   * The sweep above needs a leading capital, which is what keeps Tailwind class
   * strings out — but it also means every hint that opens lower-case slips past.
   * That is not hypothetical: `placeholder="e.g. Uplink to core"` and
   * `<title>{' (click to select…)'}</title>` both shipped untranslated, and every
   * other test was green.
   *
   * Lowering the capital requirement globally buries the signal in a few hundred
   * `className` values, so this is a narrow, separate pass over the attributes
   * that actually hold a hint. `className` is excluded by construction rather
   * than by pattern-matching, which is why this stays quiet.
   *
   * Known limit: it reads one line at a time, so a JSX child written on the
   * line *after* its element — `<title>` on one line, `{' …'}` on the next —
   * is not seen. The CableLayer tooltip is in that shape. Widening this to a
   * whole-file scan is the fix if a second one ever shows up.
   */
  it('translates hints that open with a lower-case letter', () => {
    const HINT_PROPS = /\b(placeholder|title|hint|alt|aria-label)\s*=\s*(?:\{'([^']*)'\}|\{"([^"]*)"\}|"([^"]*)")/g
    // The same hint can arrive as a JSX child rather than an attribute:
    //   <title>{cable.label}{' (click to select, Delete to remove)'}</title>
    // That shape shipped untranslated too, so the attribute regex alone is not
    // enough to keep it fixed.
    const HINT_CHILD = /<(?:title|hint|caption)\b[^>]*>\s*\{'((?:[^'\\]|\\.)*)'/g

    const files: string[] = []
    ;(function walk(d: string) {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const full = path.join(d, e.name)
        if (e.isDirectory()) {
          if (e.name === 'node_modules' || e.name === 'i18n' || e.name === '__tests__' || e.name === 'test') continue
          walk(full)
        } else if (/\.tsx$/.test(e.name) && !/\.test\.tsx$/.test(e.name)) {
          files.push(full)
        }
      }
    })(SRC)

    // Sample input and data, not copy.
    const isData = (s: string) =>
      /^\d/.test(s) ||
      /^#[0-9a-f]{3,8}$/i.test(s) ||
      /^[a-z0-9_.-]+$/.test(s) ||
      /^[a-z0-9.-]+\.(com|net|org|io|lan|local)$/i.test(s)

    const dict = dictionaryKeys()
    const hits: string[] = []
    for (const f of files) {
      let inBlock = false
      for (const [i, raw] of fs.readFileSync(f, 'utf8').split(/\r?\n/).entries()) {
        const { code: line, inBlock: stillInBlock } = stripComments(raw, inBlock)
        inBlock = stillInBlock
        if (/\b(className|data-[\w-]+=|from ['"]|import\b)/.test(line)) continue
        // Already translated on this line.
        if (/\b(placeholder|title|hint|alt|aria-label)\s*=\s*\{?\s*t\(/.test(line)) continue
        if (/<(?:title|hint|caption)\b[^>]*>\s*\{t\(/.test(line)) continue

        const candidates: string[] = []
        for (const m of line.matchAll(HINT_PROPS)) {
          candidates.push(m[2] ?? m[3] ?? m[4] ?? '')
        }
        for (const m of line.matchAll(HINT_CHILD)) {
          candidates.push(m[1].replace(/\\'/g, "'").replace(/\\n/g, '\n'))
        }

        for (const raw of candidates) {
          const value = raw.replace(/\\n/g, ' ').trim()
          if (value.length < 4 || isData(value)) continue
          // More than one token. A single lowercase word is an identifier or a
          // data value; a hint is a phrase. Not "two consecutive letters"
          // either — "e.g. 20" is a real hint and has no adjacent pair.
          if (!/\s/.test(value)) continue
          if ((value.match(/[A-Za-z]/g) ?? []).length < 2) continue
          // Deliberately NOT skipped when the dictionary has an entry. Having
          // an entry is not the same as calling t() on it — a translation that
          // nothing looks up is the same dead entry that let a whole part file
          // ship unloaded. Only ALLOWED excuses a raw literal here.
          if (ALLOWED.has(value)) continue // sample input, declared above
          hits.push(`${path.relative(SRC, f)}:${i + 1}  ${JSON.stringify(value)}`)
        }
      }
    }
    expect(
      hits,
      `Lower-case English hints reaching the screen — wrap them in t():\n${hits.join('\n')}`,
    ).toEqual([])
  })
})
