import { describe, it, expect } from 'vitest'
import { generateMarkdownTable } from '../exportMarkdown'
import type { Node } from '@xyflow/react'
import type { NodeData } from '@/types'

const makeNode = (overrides: Partial<NodeData> = {}, id = '1'): Node<NodeData> => ({
  id,
  type: overrides.type ?? 'server',
  position: { x: 0, y: 0 },
  data: { label: 'Test', type: 'server', status: 'online', services: [], ...overrides },
})

describe('generateMarkdownTable', () => {
  it('returns empty string for empty node list', () => {
    expect(generateMarkdownTable([])).toBe('')
  })

  it('excludes groupRect nodes', () => {
    const nodes = [makeNode({ type: 'groupRect', label: 'Zone' })]
    expect(generateMarkdownTable(nodes)).toBe('')
  })

  it('generates header + separator + row', () => {
    const nodes = [makeNode({ label: 'Router', type: 'router', ip: '192.168.1.1', status: 'online' })]
    const md = generateMarkdownTable(nodes)
    const lines = md.split('\n')
    expect(lines[0]).toContain('Label')
    expect(lines[0]).toContain('IP')
    expect(lines[1]).toContain('---')
    expect(lines[2]).toContain('Router')
    expect(lines[2]).toContain('192.168.1.1')
  })

  it('uses — for missing fields', () => {
    const nodes = [makeNode({ label: 'Node', type: 'generic', ip: undefined, hostname: undefined })]
    const md = generateMarkdownTable(nodes)
    expect(md).toContain('—')
  })

  it('lists services as name:port pairs', () => {
    const nodes = [makeNode({
      label: 'Server',
      services: [{ port: 80, protocol: 'tcp', service_name: 'nginx' }, { port: 443, protocol: 'tcp', service_name: 'https' }],
    })]
    const md = generateMarkdownTable(nodes)
    expect(md).toContain('nginx:80')
    expect(md).toContain('https:443')
  })

  it('escapes pipe characters in cell values', () => {
    const nodes = [makeNode({ label: 'A|B' })]
    const md = generateMarkdownTable(nodes)
    expect(md).toContain('A\\|B')
  })

  it('generates one row per non-groupRect node', () => {
    const nodes = [
      makeNode({ type: 'server', label: 'A' }, '1'),
      makeNode({ type: 'router', label: 'B' }, '2'),
      makeNode({ type: 'groupRect', label: 'Zone' }, '3'),
    ]
    const lines = generateMarkdownTable(nodes).split('\n')
    // header + separator + 2 data rows
    expect(lines).toHaveLength(4)
  })
})
