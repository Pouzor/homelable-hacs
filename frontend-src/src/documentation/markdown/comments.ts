/**
 * Drop HTML comments from the rendered page.
 *
 * The device template writes a provenance marker above migrated notes
 * (`<!-- Migrated verbatim from this device's Notes field … -->`). The renderer
 * does not run raw HTML, so react-markdown printed the marker as literal text.
 * A comment is meant for whoever reads the source, so it is removed from the
 * tree here and stays in the body, the editor and the `.md` export.
 *
 * Only a node that is nothing but comments goes; any other raw HTML keeps the
 * renderer's usual treatment.
 */
interface MdNode {
  type: string
  value?: string
  children?: MdNode[]
}

/**
 * True when `value` is one or more `<!-- … -->` comments separated only by
 * whitespace. A linear scan rather than a regex: the equivalent pattern
 * backtracks exponentially on input like `<!----><!----><!--…`.
 */
function onlyComments(value: string): boolean {
  let i = 0
  let seen = false
  for (;;) {
    while (i < value.length && /\s/.test(value[i])) i++
    if (i === value.length) return seen
    if (!value.startsWith('<!--', i)) return false
    const end = value.indexOf('-->', i + 4)
    if (end === -1) return false
    i = end + 3
    seen = true
  }
}

export function isCommentOnly(node: MdNode): boolean {
  return node.type === 'html' && typeof node.value === 'string' && onlyComments(node.value)
}

function strip(node: MdNode): void {
  if (!node.children) return
  node.children = node.children.filter((child) => !isCommentOnly(child))
  node.children.forEach(strip)
}

/** A remark plugin: `remarkPlugins={[remarkGfm, remarkDropComments]}`. */
export function remarkDropComments() {
  return (tree: MdNode) => strip(tree)
}
