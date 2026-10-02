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
      // Protocol names are exempt by *value*, not by table: a table-level
      // exemption would cover every string later added to the same table.
      const verbatim = new Set<string>(table.verbatim ?? [])
      const missing = all.filter((v) => !verbatim.has(v) && !dict.has(v))
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
  const STR_LITERAL = /(['"`])((?:\\.|(?!\1)[^\\])*)\1/g

  // shadcn primitives: class tables and structural markup only, no copy.
  const SKIP_DIRS = new Set(['node_modules', 'i18n', 'test', '__tests__', 'ui'])

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
    ['Vendor', 'property key minted by the importers, becomes data'],
    ['Model', 'property key minted by the importers, becomes data'],
    ['MemoryStick', 'sample device_type in importYaml fixtures, becomes data'],
    ['HardDrive', 'sample device_type in importYaml fixtures, becomes data'],
    // Key chords. ShortcutsModal holds them verbatim on purpose: "Ctrl" is the
    // key the user presses, and translating it would make the help wrong.
    ['Ctrl', 'a key binding, shown as the key the user presses'],
    ['Scroll', 'a key binding, shown as the gesture the user performs'],
    ['Drag', 'a key binding, shown as the gesture the user performs'],
    // Developer-facing errors: thrown for a maintainer reading the console,
    // never rendered in the panel's UI.
    ['authApi.login (HA handles auth)', 'developer note, not user-facing copy'],
    ['useHass must be used inside <HassProvider>', 'developer error text, thrown rather than rendered'],
    ['configuration must be a mapping', 'developer error text, thrown rather than rendered'],
    ['`height` must be a positive number of pixels', 'developer error text, thrown rather than rendered'],
    ['upload failed', 'developer error text, rethrown after a translated toast'],
    // Property-name suggestions offered in the rack editor. Accepting one
    // creates a property *by that name*, so it is data the user will read back
    // on the canvas, not chrome.
    ['Length', 'a property-name suggestion; picking it creates a property with this name'],
    ['Speed', 'a property-name suggestion; picking it creates a property with this name'],
    ['Category', 'a property-name suggestion; picking it creates a property with this name'],
  ])

  /**
   * Exemptions that only hold in one file.
   *
   * ALLOWED above is keyed by the string alone, so excusing `Status` for the
   * Markdown exporter would also excuse an untranslated `<span>Status</span>`
   * written in any other component later — a hole opened in the whole sweep to
   * close one line. These words are common enough that the blast radius is
   * real, so the exemption is pinned to the file that earned it.
   *
   * The markdown table is left English on purpose, together with its body: the
   * body is raw data (`server`, `online`), so Chinese headings over English
   * values would be a half-translated artifact, and the table is copied out as
   * a data contract. See the comment in exportMarkdown.ts.
   */
  const ALLOWED_BY_FILE = new Map<string, Set<string>>([
    ['utils/exportMarkdown.ts', new Set(['Label', 'Type', 'IP', 'Hostname', 'Status', 'Services'])],
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
    /\bfrom ['"]/,
    /console\.(log|warn|error|info)/,
    /@ts-ignore/,
    /@ts-expect-error/,
    /eslint/,
  ]

  /**
   * Blank out the parts of a line that are structurally not copy, instead of
   * skipping whole lines.
   *
   * Skipping the line was wrong twice over: `<DialogTitle className="…">Export
   * Canvas</DialogTitle>` carries its title on a line that also has a
   * className, so the whole line — title included — was skipped and
   * ExportModal shipped in English. The same happened with a t() call sharing
   * a line with a data-attribute. Masking the className value keeps the copy.
   */
  function maskNonCopy(line: string): string {
    return line
      .replace(/\bclassName\s*=\s*"[^"]*"/g, 'className=""')
      .replace(/\bclassName\s*=\s*\{[^{}]*\}/g, 'className={}')
      .replace(/\bdata-[\w-]+\s*=\s*"[^"]*"/g, 'data-x=""')
      .replace(/\bstyle\s*=\s*\{\{[^}]*\}\}/g, 'style={{}}')
  }

  // Proper nouns and protocol names. Kept verbatim in every locale, so a
  // scanner that cannot recognise them produces noise nobody acts on.
  const PROPER_NOUNS = new Set([
    'Proxmox', 'Zigbee', 'Z-Wave', 'Zigbee2MQTT', 'Prometheus', 'Ping', 'Health',
    'Serial', 'UDP', 'TCP', 'SSH', 'HTTP', 'HTTPS', 'MQTT', 'Mattermost', 'Ntfy',
    'OpnSense', 'Portainer', 'MemoryStick', 'HardDrive', 'Disk', 'Unraid', 'Synology',
    'Home Assistant', 'InfluxDB', 'Grafana', 'WireGuard', 'OpenVPN', 'Netdata',
    'Homelable', 'PVEAuditor', 'Serif', 'Sans', 'Mono', 'Inter', 'Georgia',
  ])

  // Not copy, and in bulk: CSS values, path data, key names. This list is the
  // difference between a check that reports six real misses and one that
  // reports three hundred Tailwind class strings, which is a check nobody reads.
  const NOT_COPY = [
    // An interpolated value, not a fixed phrase: a CSS length, a coordinate, an id.
    /\$\{/,
    // SVG path data, including the multi-command form.
    /^[MmLlHhVvCcSsQqTtAaZz][\d\s.,MLHVCSQTAZmlhvcsqtaz-]*$/,
    // A fragment of a template literal or a TS type, not a phrase.
    /^\s*[:}]/,
    /\bas\s+[A-Z][\w<>[\]]*$/,
    /=>|\)\s*\.|\bas\s+Node\b/,
    /^\(?[\w.]*\)?\s*[=!]==?|\)\)$/,
    /\b(event|props|state)\s*:\s*[A-Z]\w*Props/,
    /^\(?\w+\)?:\s*!/,
    // A TypeScript type or expression caught by the JSX-text pass, which cannot
    // tell `Promise<() => void>` from a sentence because both contain `>`.
    /\b(Promise|Record|Partial|Map|Set|Omit|Pick|Node|Edge|ReactNode|KeyboardEvent)\b/,
    /[[\]<>]|\b(edges|types|id)\s*:/,
    /^\s*[=(,]|[,:=]\s*$/,
    // An inline style declaration on a custom element.
    /(^|[\s{])(width|height|display|margin|padding|border|background|color|font|position|top|left|right|bottom)\s*:\s*[\w#(]/,
    /\b\d+px\b.*\b\d+px\b|\d+%/,
    // A font stack — ends in a generic family.
    /,\s*(monospace|serif|sans-serif|cursive|fantasy|system-ui|ui-sans-serif|ui-monospace|ui-serif)$/,
    /^(var|calc|rgba?|hsla?|linear-gradient|radial-gradient|repeating-linear-gradient)\b/,
    /^\d+(\.\d+)?(px|rem|em|%|vh|vw|ms|s|fr|deg|ch|ex|pt)$/,
    /^\d/,                                        // a bare number or "12px 16px"
    /#[0-9a-f]{3,8}\b/i,                           // a colour
    /\b(solid|dashed|dotted|double|groove|ridge|inset|outset)\b/,
    /^(translate|scale|rotate|transform|opacity|filter|drop-shadow|all|transition|animation|animate-in|fade-in|zoom-in)\b/,
    /cubic-bezier|steps\(|ease-in|ease-out|infinite|forwards/,
    // Tailwind arbitrary values and state prefixes; no sentence has these.
    /^\[?[a-z-]*:[\w-]+[/:\]]/,                     // hover:, dark:, [a]:
    /\[[&>:~*]|\[[a-z]/,                            // [&_svg], [&>svg], [data-…
    /\b(focus-visible|hover|active|disabled|group-hover|peer-checked|data-\[)/,
    /(w-|h-|px-|py-|mt-|mb-|ml-|mr-|pt-|pb-|gap-|flex|grid|rounded|items-|justify-|shrink|overflow|truncate|inline-)/,
    /(text-(xs|sm|base|lg|xl|foreground|muted|destructive|primary|secondary|white))/, 
    /^(bg-|border-|from-|to-|via-|ring-|shadow-|fill-|stroke-|divide-|outline-)/,
    // HTML attribute keywords and framework directives.
    /^noopener\b/,
    /^noreferrer\b/,
    /^use client$/,
    /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s/,
    // KeyboardEvent.key values: compared against, never displayed.
    /^(Enter|Escape|Delete|Backspace|Space|Shift|Control|ControlLeft|ControlRight|Meta|Alt|ShiftLeft|ShiftRight|Tab|Arrow\w+|Home|End|PageUp|PageDown)$/,
  ]

  const isProse = (s: string) => {
    if (s.length < 4) return false
    if (!/[A-Za-z]/.test(s)) return false
    if (PROPER_NOUNS.has(s)) return false
    // Two or more letters, and either a space or a starting capital. Requiring
    // both used to mean a single-word button label — "Cancel", "Download",
    // "Exporting…" — was treated as data, and ExportModal shipped with all
    // three in English plus a title and two option captions.
    const hasUpper = /^[A-Z]/.test(s)
    if (!hasUpper && !/\s/.test(s)) return false
    if ((s.match(/[A-Za-z]/g) ?? []).length < 2) return false
    if (/^[A-Z0-9_./:-]+$/.test(s)) return false
    if (NOT_COPY.some((re) => re.test(s))) return false
    return true
  }

  /**
   * Literals handed to `t()` on this line.
   *
   * A plain /\bt\('…'\)/ misses `t(cond ? 'A' : 'B')`, which is how the card
   * editor and the scan-status filter both pass their two branches. Reading
   * only the ternary's left side would report a translated string as a miss.
   */
  function literalsPassedToT(line: string): string[] {
    const out: string[] = []
    for (let i = 0; i < line.length; i++) {
      if (line[i] !== 't' || line[i + 1] !== '(') continue
      if (i > 0 && /[A-Za-z0-9_$.]/.test(line[i - 1])) continue
      // Balance within the line, or to its end when the call continues below —
      // `toast.success(t('Found {count} devices', {` is the common shape, and
      // stopping at the first line would miss the key that matters most.
      let depth = 0
      let end = line.length
      for (let j = i + 1; j < line.length; j++) {
        if (line[j] === '(') depth++
        else if (line[j] === ')') {
          depth--
          if (depth === 0) {
            end = j
            break
          }
        }
      }
      for (const m of line.slice(i + 2, end).matchAll(STR_LITERAL)) {
        // Trimmed to match what `report()` compares. Without this, every
        // fragment key that deliberately keeps its English join space —
        // 'Ranges are managed in ', 'Gateway: ' — is reported as a miss
        // even though the t() call is right there on the line.
        out.push(
          m[2]
            .replace(/\\'/g, "'")
            .replace(/\\"/g, '"')
            .trim(),
        )
      }
    }
    return out
  }

  /** Net paren depth of a line, ignoring anything inside quotes. */
  function parenDelta(line: string): number {
    let depth = 0
    let quote: string | null = null
    for (let i = 0; i < line.length; i++) {
      const c = line[i]
      if (quote) {
        if (c === '\\') i++
        else if (c === quote) quote = null
        continue
      }
      if (c === "'" || c === '"' || c === '`') quote = c
      else if (c === '(') depth++
      else if (c === ')') depth--
    }
    return depth
  }

  it('finds nothing outside the allowlist', () => {
    // Having a dictionary entry is deliberately NOT enough to excuse a literal.
    // An earlier version of this sweep skipped anything `dict.has(v)`, on the
    // reasoning that an entry proves the string is translated. It does not —
    // it proves someone *wrote* a translation, not that any call site reads
    // it. ExportModal carried 'Standard'/'High'/'Ultra' in the dictionary the
    // whole time and rendered all three in English, because it printed
    // `opt.label` rather than `t(opt.label)`. See the note on `report` below.
    //
    // What *is* skipped is a value that provably reaches `t()` some other way:
    // a DYNAMIC_TABLES entry read at a render site, or a declared exemption.
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
          if (SKIP_DIRS.has(e.name)) continue
          walk(full)
        } else if (/\.(ts|tsx)$/.test(e.name) && !/\.(test|spec)\.(ts|tsx)$/.test(e.name)) {
          files.push(full)
        }
      }
    })(SRC)

    expect(files.length).toBeGreaterThan(50)

    const hits: string[] = []
    // A t() call may open on one line and close several lines later, as in
    // `t('{nodes} nodes · …', { nodes: d.node_count ?? 0, })`. Scanning line by
    // line then misses the key, and the body — object keys, expressions — gets
    // read as copy. So once a line opens an unclosed `t(`, the lines up to its
    // close are skipped. The trigger is specifically `t(`: tracking *every*
    // unbalanced paren would swallow ordinary multi-line JSX instead.
    let inTCall = false
    let tDepth = 0
    for (const f of files) {
      const rel = path.relative(SRC, f).split(path.sep).join('/')
      // Only the file that earned the exemption gets it — see ALLOWED_BY_FILE.
      const exemptHere = ALLOWED_BY_FILE.get(rel)
      let inBlock = false
      for (const [i, raw] of fs.readFileSync(f, 'utf8').split(/\r?\n/).entries()) {
        const { code: masked, inBlock: stillInBlock } = stripComments(raw, inBlock)
        inBlock = stillInBlock
        const line = maskNonCopy(masked)
        if (SKIP_LINE.some((re) => re.test(line))) continue
        if (inTCall) {
          tDepth += parenDelta(line)
          if (tDepth <= 0) inTCall = false
          continue
        }
        const translated = new Set(literalsPassedToT(line))
        if (/(?:^|[^A-Za-z0-9_$.])t\(/.test(line)) {
          const d = parenDelta(line)
          if (d > 0) {
            inTCall = true
            tDepth = d
          }
        }
        const seen = new Set<string>()

        // `dict.has(v)` is deliberately NOT a reason to skip. A dictionary
        // entry only proves a translation was *written*; nothing proves a call
        // site reads it. ExportModal had 'Standard'/'High'/'Ultra' in the
        // dictionary and still rendered English, because it printed
        // `opt.label` instead of `t(opt.label)` — a dead entry, invisible to
        // every check that asked "is this key translated?" rather than
        // "is this literal routed through t()?". Membership is not wiring.
        const report = (raw: string) => {
          const v = raw.trim()
          if (!v || seen.has(v)) return
          seen.add(v)
          // A lookup fallback that is data rather than copy — see
          // NON_KEY_LITERALS. Naming them here keeps one list authoritative.
          if ((NON_KEY_LITERALS as readonly string[]).includes(v)) return
          if (!isProse(v) || translated.has(v) || tableValues.has(v)) return
          if (exemptHere?.has(v) || ALLOWED.has(v)) return
          if (/^https?:|^\/|^\.|^#/.test(v)) return
          hits.push(`${path.relative(SRC, f)}:${i + 1}  ${JSON.stringify(v)}`)
        }

        for (const m of line.matchAll(STR_LITERAL)) report(m[2])

        // JSX text. Not a string literal — `<DialogTitle>Export Canvas</…>`
        // has no quotes anywhere, so a sweep that only reads quoted strings
        // never sees it. That is how ExportModal shipped a title, a Cancel
        // button, a Download button and an "Exporting…" spinner in English
        // while every quoted string around them was translated.
        for (const m of line.matchAll(/>\s*([^<>{}]*[A-Za-z][^<>{}]*?)\s*</g)) report(m[1])
      }
    }
    expect(
      hits,
      `Untranslated English literals — translate them, or add them to ALLOWED with a reason:\n${hits.join('\n')}`,
    ).toEqual([])
  })

  /**
   * The exemptions above are the only way a real miss gets to hide, so each one
   * is pinned to the thing that earned it. Without these the lists rot in the
   * one direction that matters: a file gets renamed, or a new English string
   * lands in an exempted file, and the exemption quietly becomes a permanent
   * green light for that file.
   */
  it('the file-scoped allowlist covers only what it declares', () => {
    for (const [rel, exempt] of ALLOWED_BY_FILE) {
      const full = path.join(SRC, rel)
      expect(fs.existsSync(full), `${rel} is allowlisted but no longer exists`).toBe(true)

      // Nothing may be downgraded to the global list: a bare ALLOWED entry is
      // keyed by string alone and would excuse the same word in every file.
      for (const v of exempt) {
        expect(ALLOWED.has(v), `${JSON.stringify(v)} is in both ALLOWED and ALLOWED_BY_FILE`).toBe(false)
      }

      // Every prose literal in the file must be declared. A new heading added
      // next to the existing ones is exactly the regression this catches.
      // Presence is tracked separately from prose-ness: 'IP' is two characters
      // and so never reaches the sweep at all, but it is still a string the
      // exemption names and must not be reported as stale.
      const src = fs.readFileSync(full, 'utf8')
      const present = new Set<string>()
      const inFile = new Set<string>()
      for (const m of src.matchAll(STR_LITERAL)) {
        const v = m[2].trim()
        if (!v) continue
        present.add(v)
        if (isProse(v)) inFile.add(v)
      }
      const undeclared = [...inFile].filter((v) => !exempt.has(v))
      expect(
        undeclared,
        `${rel} has English prose the allowlist does not declare: ${undeclared.join(', ')}\n` +
          'Either translate it, or add it to ALLOWED_BY_FILE with a reason.',
      ).toEqual([])

      // And no entry may outlive the literal it was written for.
      const stale = [...exempt].filter((v) => !present.has(v))
      expect(stale, `${rel} is allowlisted for strings it no longer contains`).toEqual([])
    }
  })

  it('no dynamic table waives its whole value set', () => {
    // `verbatim` exists for protocol names. If it ever covered a whole table
    // the entry would assert nothing at all while still reading as coverage.
    for (const table of DYNAMIC_TABLES) {
      if (!table.verbatim?.length) continue
      const src = fs.readFileSync(path.join(SRC, table.file), 'utf8')
      const all = valuesOf(src, table)
      const waived = all.filter((v) => (table.verbatim ?? []).includes(v))
      expect(waived.length, `${table.what} waives every value it has`).toBeLessThan(all.length)
    }
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

  /**
   * Every read of the check-method table must be wrapped in `t()`.
   *
   * This is the one gap the other checks cannot close. The literal sweep skips
   * table values (they are data), the dictionary test only asks whether `Ping`
   * *has* an entry, and neither notices that a render site stopped calling
   * `t()` — the value is still translated, it just is not being read any more.
   * That is exactly how PendingDeviceModal's picker shipped a bare English
   * `Ping` next to a fully-Chinese form: NodeModal had been fixed, the second
   * copy of the table had not, and all 2071 tests were green.
   *
   * The tables are now one, so this asserts that neither caller can quietly
   * drop the wrapper again, and a re-introduced local copy would fail too.
   */
  it('routes every check-method caption read through t()', () => {
    const CALLERS = [
      'components/modals/NodeModal.tsx',
      'components/modals/PendingDeviceModal.tsx',
    ]
    const hits: string[] = []
    for (const rel of CALLERS) {
      const src = fs.readFileSync(path.join(SRC, rel), 'utf8')
      let inBlock = false
      for (const [i, raw] of src.split(/\r?\n/).entries()) {
        const { code: line, inBlock: stillInBlock } = stripComments(raw, inBlock)
        inBlock = stillInBlock
        // The import is the one legitimate bare mention.
        if (/^\s*import\b/.test(line)) continue
        for (const m of line.matchAll(/\bCHECK_METHOD_LABELS\b/g)) {
          const before = line.slice(0, m.index)
          // `t(CHECK_METHOD_LABELS[…])` is the only acceptable shape.
          if (/\bt\(\s*$/.test(before)) continue
          hits.push(`${rel}:${i + 1}  CHECK_METHOD_LABELS read without t()`)
        }
      }
    }
    expect(
      hits,
      `Check-method captions reaching the screen untranslated:\n${hits.join('\n')}`,
    ).toEqual([])
  })
})
