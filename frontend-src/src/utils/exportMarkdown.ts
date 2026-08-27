import type { Node } from '@xyflow/react'
import type { NodeData } from '@/types'

const EMPTY = '—'

// Escape backslashes first, then pipes, so an input backslash can never
// combine with the escape we add and turn a pipe back into a cell separator.
// Newlines are collapsed to spaces: a raw one ends the table row.
function escapeCell(v: string): string {
  return v
    .replace(/\\/g, '\\\\')
    .replace(/\|/g, '\\|')
    .replace(/[\r\n]+/g, ' ')
}

function cell(v: string | null | undefined): string {
  if (!v) return EMPTY
  return escapeCell(v)
}

export function generateMarkdownTable(nodes: Node<NodeData>[]): string {
  const rows = nodes
    .filter((n) => n.data.type !== 'groupRect')
    .map((n) => {
      const d = n.data
      const services = d.services?.length
        ? d.services.map((s) => {
          const port = s.port != null ? `:${s.port}` : ''
          const path = s.path?.trim() ? s.path.trim() : ''
          return escapeCell(`${s.service_name ?? ''}${port}${path}`)
        }).join(', ')
        : EMPTY
      return [
        cell(d.label),
        cell(d.type),
        cell(d.ip),
        cell(d.hostname),
        cell(d.status),
        services,
      ]
    })

  if (rows.length === 0) return ''

  const headers = ['Label', 'Type', 'IP', 'Hostname', 'Status', 'Services']
  const separator = headers.map(() => '---')

  const lines = [
    `| ${headers.join(' | ')} |`,
    `| ${separator.join(' | ')} |`,
    ...rows.map((r) => `| ${r.join(' | ')} |`),
  ]

  return lines.join('\n')
}
