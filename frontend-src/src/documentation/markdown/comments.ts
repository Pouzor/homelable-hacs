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

const ONLY_COMMENTS = /^(?:\s*<!--[\s\S]*?-->)+\s*$/

export function isCommentOnly(node: MdNode): boolean {
  return node.type === 'html' && typeof node.value === 'string' && ONLY_COMMENTS.test(node.value)
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
