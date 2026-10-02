
/**
 * Tables of English strings that reach the UI through a *runtime* `t()` call
 * rather than a literal one.
 *
 * The completeness test scans for `t('…')` literals, so it cannot see these: the
 * call site is `t(entry.label)` or `t(EDGE_TYPE_LABELS[type])`, and a missing
 * entry degrades to English silently with every test still green. In the main
 * app that is exactly how ~180 strings were missed the first time round.
 *
 * Both test files need this list, and a test file cannot be imported from
 * another test file without executing its suites — hence this module.
 */
export interface DynamicTable {
  /** What the table is, for the failure message. */
  what: string
  /** Path under `src`. */
  file: string
  /**
   * How to pull the values out. `block` names an exported const and reads to its
   * closing brace, which is exact; `pattern` is for tables that are one big array
   * with no per-entry key to anchor on.
   */
  block?: string
  fields?: string[]
  pattern?: string
}

export const DYNAMIC_TABLES: DynamicTable[] = [
  {
    what: 'icon picker labels (rendered as t(entry.label))',
    file: 'utils/nodeIcons.ts',
    pattern: "label:\\s*'((?:[^'\\\\]|\\\\.)*)'",
  },
  {
    what: 'icon categories (the picker filter tabs)',
    file: 'utils/nodeIcons.ts',
    pattern: "category:\\s*'((?:[^'\\\\]|\\\\.)*)'",
  },
  {
    // Panel-only table: the main app has no separate icon set for canvas designs.
    what: 'design icon labels (DesignModal grid)',
    file: 'utils/designIcons.ts',
    pattern: "label:\\s*'((?:[^'\\\\]|\\\\.)*)'",
  },
  {
    what: 'theme names and descriptions (rendered as t(preset.…))',
    file: 'utils/themes.ts',
    pattern: "(?:label|description):\\s*'((?:[^'\\\\]|\\\\.)*)'",
  },
  {
    what: 'device type labels (rendered as t(NODE_TYPE_LABELS[…]))',
    file: 'types/index.ts',
    block: 'NODE_TYPE_LABELS',
  },
  {
    // EdgeModal also builds its select from `Object.entries(EDGE_TYPE_LABELS)`,
    // so the same values are reached twice — once as a lookup, once as a table.
    what: 'edge type labels (EDGE_TYPE_LABELS)',
    file: 'types/index.ts',
    block: 'EDGE_TYPE_LABELS',
  },
  {
    what: 'export dialog option captions and hints',
    file: 'utils/export.ts',
    pattern: "(?:label|hint):\\s*'((?:[^'\\\\]|\\\\.)*)'",
  },
  {
    what: 'rack faceplate labels (faceplateLabel())',
    file: 'rack/faceplates.ts',
    pattern: "label:\\s*'((?:[^'\\\\]|\\\\.)*)'",
  },
  {
    // The card editor's <ha-form> field captions, read by computeLabel() as
    // t(LABELS[schema.name]). Panel-only — the main app has no card editor.
    what: 'card editor field labels (computeLabel())',
    file: 'lib/cardEditorForm.ts',
    block: 'LABELS',
  },
]

/** Read the display fields inside `export const <name> = { … }` or `[ … ]`. */
export function valuesInBlock(source: string, name: string, fields?: string[]): string[] {
  const start = source.indexOf(name)
  if (start === -1) return []
  // The table may be an object or an array of objects. Find the first opener
  // that actually contains something: a type annotation can carry an empty `[]`
  // just before the real literal.
  let open = -1
  for (let i = start; i < source.length; i++) {
    const c = source[i]
    if (c !== '{' && c !== '[') continue
    if (source[i + 1] === (c === '{' ? '}' : ']')) { i += 1; continue }
    open = i
    break
  }
  if (open === -1) return []
  const closer = source[open] === '{' ? '}' : ']'
  const opener = source[open]

  let depth = 0
  for (let i = open; i < source.length; i++) {
    if (source[i] === opener) depth++
    else if (source[i] === closer) {
      depth--
      if (depth === 0) {
        const body = source.slice(open, i)
        const quoted = (s: string) => s.replace(/\\'/g, "'").replace(/\\"/g, '"')
        const found: string[] = []
        if (!fields) {
          for (const m of body.matchAll(/:\s*'((?:[^'\\]|\\')*)'/g)) found.push(m[1])
          for (const m of body.matchAll(/\[([^\]]*)\]/g)) {
            for (const n of m[1].matchAll(/'((?:[^'\\]|\\')*)'/g)) found.push(n[1])
          }
        } else {
          for (const field of fields) {
            const scalar = new RegExp(`\\b${field}:\\s*'((?:[^'\\\\]|\\\\.)*)'`, 'g')
            for (const m of body.matchAll(scalar)) found.push(m[1])
            const arr = new RegExp(`\\b${field}:\\s*\\[([^\\]]*)\\]`, 'g')
            for (const m of body.matchAll(arr)) {
              for (const n of m[1].matchAll(/'((?:[^'\\]|\\')*)'/g)) found.push(n[1])
            }
          }
        }
        return [...new Set(found.map(quoted))]
      }
    }
  }
  return []
}

/**
 * Part files whose keys are resolved at runtime rather than by a literal
 * `t('…')` call site. The stale-key scan would otherwise declare all of them
 * unused, which would contradict the tests that exist precisely to guard them.
 *
 * Only `design-icons` qualifies here. The icon, node-type, edge-type, export
 * and faceplate tables are all covered too, but the main app's parts already
 * define those keys — mostly inside the modal parts, since a caption like
 * "Server" is written once and shared — so they are reachable by a literal scan
 * and need no exemption.
 */
export const RUNTIME_KEY_PARTS = ['design-icons', 'icons'] as const

/**
 * Modal title/label defaults. These modals render `{t(title)}`, so the default
 * literal is a key rather than a bare string, and several are additionally
 * compared with `===` to pick the Add-vs-Save button set — a translated title
 * would silently take the wrong branch. Listed explicitly because they are
 * scattered across component signatures rather than one table.
 */
export const DIALOG_TITLES = [
  'Add Node',
  'Add Service',
  'Add Text',
  'Add Zone',
  'Connect Nodes',
  'Edit Link',
  'Edit Node',
  'Edit Text',
  'Edit Zone',
  'New Canvas',
  'Properties',
] as const

/**
 * Field names rendered through a variable key. The colour swatches iterate the
 * `custom_colors` keys and call `t(key)`, so the literal scan sees a variable
 * rather than a string. They are lower-case in English too — the capital the
 * user sees comes from a `capitalize` class.
 */
export const FIELD_NAME_KEYS = ['background', 'border', 'icon'] as const

/**
 * Default prop values on PropertyList / PropertyForm, which the component
 * translates at its own render site (`t(keyPlaceholder)`, `t(visibleLabel)`, …).
 * They are neither `t()` literals nor a table, so the literal scan cannot see
 * them and a default would silently stay English while every test passed.
 *
 * `'node'` belongs here too: PropertyBadge receives the surface word *after*
 * PropertyList slices the `Show on ` prefix off, and interpolates it into
 * `t('Show on {where}')`. It has to be a key in its own right or the tooltip
 * reads 「在 node 上显示」.
 */
export const PROP_DEFAULTS = [
  'Properties',
  'Show on node',
  'Show on canvas',
  'node',
  'Label (e.g. CPU Model)',
  'Value — optional (e.g. i7-12700K)',
  'No properties — click Add to define one.',
] as const

/**
 * Literals that the `t()` scan collects but that are not dictionary keys.
 *
 * Both are the *fallback* of a lookup whose table already covers the real
 * values, so a missing entry changes nothing — the key renders as itself and
 * the English is what the user would have seen anyway. Translating them would
 * be wrong: one is a computed rack dimension, the other a type enum.
 *
 *   `t(WIDTH_LABEL[plate.colSpan] ?? \`${plate.colSpan}/${RACK_COLUMNS}\`)`
 *   `t(NODE_TYPE_LABELS[(form.type ?? 'server') as NodeType])`
 */
export const NON_KEY_LITERALS = [
  '${plate.colSpan}/${RACK_COLUMNS}',
  'server',
] as const

/** Every value this table can put in front of a user. */
export function valuesOf(source: string, table: DynamicTable): string[] {
  if (table.block) return valuesInBlock(source, table.block, table.fields)
  const re = new RegExp(table.pattern!, 'g')
  return [...new Set([...source.matchAll(re)].map((m) => m[1].replace(/\\'/g, "'")))]
}
