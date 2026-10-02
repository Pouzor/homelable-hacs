/**
 * Simplified Chinese translations for the Home Assistant panel.
 *
 * Keys are the English source strings used at the call site (`t('Save')`), so a
 * missing key renders the English text rather than blank space — a gap degrades
 * to English instead of breaking the UI. See ../core.ts for the rationale.
 *
 * The dictionary is split into `parts/*` because the work was spread across a
 * main app whose wording this panel reuses verbatim, plus the surfaces that are
 * panel-only. A key defined in two parts with different values would be a
 * silent last-one-wins, so the test file asserts all parts agree.
 *
 * Conventions (the main app's GLOSSARY.md is the source of truth):
 *   - `{name}` placeholders must be kept; a placeholder may be *dropped* when
 *     Chinese has no use for it (this is how English `{plural}` suffixes, e.g.
 *     `device{plural}`, disappear from the rendered text).
 *   - Keep technical identifiers verbatim: Proxmox, Z-Wave, Zigbee, MQTT, IP,
 *     LQI, Markdown, VLAN, NAS, SFP.
 *   - A part file is a *copy* of the main app's part, trimmed to the keys this
 *     panel actually renders — shipping unused entries would fail the stale-key
 *     check that keeps the dictionary honest.
 *   - `__tests__/i18n.test.tsx` and `__tests__/coverage.test.ts` scan the
 *     source tree and fail on any key used in code that is missing here, on any
 *     English literal that reaches a render site without going through `t()`,
 *     and on any entry no call site uses. All of them must stay green.
 */
import root from './parts/root'
import common from './parts/common'
import componentsModals1 from './parts/components-modals-1'
import componentsModals2 from './parts/components-modals-2'
import componentsPanels from './parts/components-panels'
import componentsIntegrations from './parts/components-integrations'
import documentation from './parts/documentation'
import rack from './parts/rack'
// Copy the panel needs that the main app's (older) components do not have yet:
// the Lovelace card picker entry, the Zigbee / Z-Wave import modals,
// pending-device approval, scan configuration and the design editor.
import panel from './parts/panel'
// Values handed to t() at the render site rather than written as literals —
// see DYNAMIC_TABLES in ../dynamicTables.ts.
import designIcons from './parts/design-icons'
import icons from './parts/icons'

const zhCN: Record<string, string> = {
  ...root,
  ...common,
  ...componentsModals1,
  ...componentsModals2,
  ...componentsPanels,
  ...componentsIntegrations,
  ...documentation,
  ...rack,
  ...panel,
  ...designIcons,
  ...icons,
}

export default zhCN
