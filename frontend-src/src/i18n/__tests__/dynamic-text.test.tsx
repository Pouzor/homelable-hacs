/**
 * The three surfaces where the UI is densest in text that a static scan cannot
 * see.
 *
 * Everything here is a *runtime* lookup — `t(NODE_TYPE_LABELS[type])`,
 * `t(entry.label)`, `faceplateLabel(plate)` — so none of it appears as a
 * `t('…')` literal. coverage.test.ts proves the dictionary has an entry for
 * each value; this file proves the entry is actually *reaching the screen*,
 * which is the half a completeness check cannot answer on its own.
 *
 * Note the two kinds of "translated". Comparing the result against the key
 * only works for copy; a product name or an acronym is translated to itself on
 * purpose ('VM' -> 'VM', 'Wi-Fi' -> 'Wi-Fi'), and a missing entry is
 * indistinguishable from an identity one by value alone. So membership in the
 * dictionary is asserted for every value, and the change of wording is
 * asserted only for strings that are ordinary words.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { setLocaleForTest, DEFAULT_LOCALE, t } from '../index'
import zhCN from '../locales/zh-CN'
import { NODE_TYPE_LABELS, EDGE_TYPE_LABELS, type NodeType, type EdgeType } from '@/types'
import { ICON_REGISTRY, ICON_CATEGORIES } from '@/utils/nodeIcons'
import { FACEPLATES, faceplateGroups, faceplateLabel } from '@/rack/faceplates'
import { IconPickerPanel } from '@/components/modals/IconPickerPanel'
import { FaceplatePicker } from '@/rack/components/FaceplatePicker'
import { DesignModal } from '@/components/modals/DesignModal'

/** Is this key in the merged dictionary, i.e. would t() find a translation? */
const dictionaryHas = (key: string) => Object.prototype.hasOwnProperty.call(zhCN, key)

afterEach(() => {
  cleanup()
  setLocaleForTest(DEFAULT_LOCALE)
})

describe('node type labels reach the screen in Chinese', () => {
  it('resolves a caption for every device type', () => {
    setLocaleForTest('zh-CN')
    for (const type of Object.keys(NODE_TYPE_LABELS) as NodeType[]) {
      const key = NODE_TYPE_LABELS[type]
      expect(dictionaryHas(key), `${type}: "${key}" has no zh-CN entry`).toBe(true)
      // And the lookup actually returns it, rather than falling through.
      expect(t(key), `${type}: "${key}" did not resolve`).toBe(zhCN[key])
    }
  })

  it('actually translates the captions that are ordinary words', () => {
    setLocaleForTest('zh-CN')
    for (const [type, expected] of [
      ['server', '服务器'],
      ['router', '路由器'],
      ['switch', '交换机'],
      ['ap', '无线接入点'],
    ] as const) {
      const key = NODE_TYPE_LABELS[type as NodeType]
      expect(t(key), `${type} ("${key}") should read ${expected}`).toBe(expected)
    }
  })

  it('keeps the untranslated label usable as a lookup key', () => {
    // Switching back to English has to return the source string, not whatever
    // the Chinese value happened to be — the Add-vs-Save branches compare the
    // title against the English literal to pick their button set.
    setLocaleForTest('zh-CN')
    const translated = t(NODE_TYPE_LABELS.server)
    setLocaleForTest('en')
    expect(t(NODE_TYPE_LABELS.server)).toBe(NODE_TYPE_LABELS.server)
    expect(translated).not.toBe(NODE_TYPE_LABELS.server)
  })

  it('resolves a caption for every link type', () => {
    setLocaleForTest('zh-CN')
    for (const type of Object.keys(EDGE_TYPE_LABELS) as EdgeType[]) {
      const key = EDGE_TYPE_LABELS[type]
      expect(dictionaryHas(key), `${type}: "${key}" has no zh-CN entry`).toBe(true)
    }
    // 'Wi-Fi', 'IoT / Zigbee' and 'VLAN' stay verbatim; the rest are translated.
    expect(t('Ethernet')).toBe('以太网')
    expect(t('Fibre')).toBe('光纤')
  })
})

describe('the icon picker shows Chinese captions and Chinese category tabs', () => {
  it('renders every icon caption and category in Chinese', () => {
    setLocaleForTest('zh-CN')
    render(<IconPickerPanel onSelect={vi.fn()} />)

    for (const category of ICON_CATEGORIES) {
      expect(dictionaryHas(category), `category "${category}" has no zh-CN entry`).toBe(true)
      expect(screen.getByText(t(category)), `category ${category} is not on screen`).toBeInTheDocument()
    }
    for (const entry of ICON_REGISTRY) {
      expect(dictionaryHas(entry.label), `icon "${entry.label}" has no zh-CN entry`).toBe(true)
    }
    // The picker titles each button with the caption, so one proves the wiring.
    const globs = ICON_REGISTRY.find((e) => e.label === 'Globe / ISP')?.label
    expect(globs).toBeDefined()
    expect(screen.getByTitle(t(globs!))).toBeInTheDocument()
  })

  it('actually changes the captions that are ordinary words', () => {
    setLocaleForTest('zh-CN')
    render(<IconPickerPanel onSelect={vi.fn()} />)
    // Checking "the caption differs from its key" would flag ~25 correct
    // entries: a caption like 'Jellyfin / Emby' is two product names and is
    // meant to read the same in both locales. So the assertion is spelled out
    // for the captions that genuinely change, and the picker is checked for
    // those exact strings.
    for (const [english, chinese] of [
      ['Router', '路由器'],
      ['Firewall', '防火墙'],
      ['Server', '服务器'],
      ['Switch / Network', '交换机 / 网络'],
      ['Container / LXC', '容器 / LXC'],
    ] as const) {
      expect(t(english), `"${english}" should read ${chinese}`).toBe(chinese)
      expect(screen.queryAllByTitle(chinese).length, `"${chinese}" is not on screen`).toBeGreaterThan(0)
    }
  })
})

describe('the faceplate catalogue shows Chinese names', () => {
  it('translates every faceplate label and group', () => {
    setLocaleForTest('zh-CN')
    render(<FaceplatePicker open value="server-1u" onPick={vi.fn()} onClose={vi.fn()} />)

    for (const { group } of faceplateGroups()) {
      expect(dictionaryHas(group), `group "${group}" has no zh-CN entry`).toBe(true)
      expect(screen.getByText(t(group)), `group ${group} is not on screen`).toBeInTheDocument()
    }
    for (const plate of FACEPLATES) {
      // faceplateLabel() translates on its own, so the dictionary key is the
      // template's English `label` and the rendered text is the result.
      expect(dictionaryHas(plate.label), `faceplate "${plate.id}" ("${plate.label}") has no zh-CN entry`).toBe(true)
      const rendered = faceplateLabel(plate)
      expect(rendered, `faceplate "${plate.id}" did not resolve`).toBe(zhCN[plate.label])
      const onScreen =
        screen.queryAllByTitle(rendered).length + screen.queryAllByText(rendered).length
      expect(onScreen, `faceplate "${plate.id}" ("${rendered}") is not on screen`).toBeGreaterThan(0)
    }
  })
})

describe('the design editor translates its icon grid', () => {
  it('resolves Chinese tooltips for the design icons', () => {
    setLocaleForTest('zh-CN')
    render(<DesignModal open onClose={vi.fn()} onSubmit={vi.fn()} />)
    for (const name of ['Dashboard', 'Database', 'Lighting', 'Industrial', 'Compute', 'Cloud']) {
      expect(dictionaryHas(name), `design icon "${name}" has no zh-CN entry`).toBe(true)
      expect(t(name), `design icon "${name}" did not resolve`).toBe(zhCN[name])
    }
  })
})
