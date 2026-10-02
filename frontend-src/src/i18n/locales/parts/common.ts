/**
 * Short labels that appear in more than one area of the app.
 *
 * Split out from the per-area parts by a deterministic rule: a key used by more
 * than one source file lives here, everything else lives with the file that uses
 * it. That way a caption like "Cancel" is written once and can never drift into
 * two different Chinese words in two different modals.
 *
 * Sourced from ../GLOSSARY.md.
 */
const part: Record<string, string> = {
  'Close': '关闭',
  'Send devices to': '把设备发送到',
  // Lowercase because that is exactly what the UI renders — the visual capital
  // came from a `capitalize` CSS class. Keying on anything else would change the
  // English output, which the test suite pins.
  'background': '背景',
  'border': '边框',
  'icon': '图标',
  'offline': '离线',
  'online': '在线',
  'unknown': '未知',
}

export default part
