/**
 * Design icon labels — the `aria-label` / tooltip on the canvas-design icon grid.
 *
 * Panel-only: the main app reaches these through the same runtime `t(entry.label)`
 * lookup but had no separate dictionary file for them. Seven of the sixteen
 * labels already exist elsewhere in the dictionary and are reused from there;
 * this file holds the nine that the panel introduced with its own icon set.
 *
 * The register is deliberately terse — these are tooltips next to a glyph, not
 * sentences, so they stay nouns rather than the longer "… / …" captions the
 * node-icon picker uses.
 */
const part: Record<string, string> = {
  'Dashboard': '仪表盘',
  'Compute': '算力',
  'Wireless': '无线',
  'Database': '数据库',
  'Cloud': '云端',
  'Home': '家庭',
  'Internet': '互联网',
  'Lighting': '照明',
  'Industrial': '工业',
}

export default part
