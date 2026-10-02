/**
 * Copy that only the Home Assistant panel needs.
 *
 * Everything else is reused verbatim from the main app's parts, because it is
 * the same product and the same wording. The strings here are all panel-only
 * surfaces the main app's (older) components don't have yet: the Lovelace card
 * picker entry, the Zigbee / Z-Wave import modals, pending-device approval,
 * scan configuration and the canvas design editor.
 *
 * Conventions (see the main app's GLOSSARY.md):
 *   - `{name}` placeholders must be kept; a placeholder may be *dropped* when
 *     Chinese has no use for it — that is how English `{plural}` suffixes
 *     disappear, leaving `已批准 5 台设备` rather than `已批准 5 台设备s`.
 *   - Keep technical identifiers verbatim: Proxmox, Z-Wave, Zigbee, MQTT, IP,
 *     LQI, Markdown, base topic, the `→` menu path separator.
 *   - A key that is only part of a sentence keeps the spacing it needs in
 *     English and drops it in Chinese. `{t('Gateway: ')}` is a case in point:
 *     the trailing space joins the value to the <span> that follows, and
 *     `'网关：'` supplies its own full-width colon instead.
 */
const part: Record<string, string> = {
  // --- card picker entry (ha-card.ts) ------------------------------------
  'Homelable Canvas': 'Homelable 画布',
  'Read-only view of a Homelable network canvas.': 'Homelable 网络画布的只读视图。',

  // --- pending devices: approval -----------------------------------------
  'Approve': '批准',
  'Approve ({count})': '批准（{count}）',
  // {label} is the device's own user-supplied name, and {extra} is an already
  // translated "（+3 条连线）" fragment — both pass straight through.
  'Approved {label}{extra}': '已批准 {label}{extra}',
  'Approved {count} device{plural}{extra}': '已批准 {count} 台设备{extra}',
  'This device is on a canvas — delete its node or hide it instead':
    '该设备已在某个画布上 —— 请改为删除它的节点或将其隐藏',

  // --- scan configuration -------------------------------------------------
  'A scan is already running': '已有扫描正在进行',
  'A scan is already running — wait for it or stop it first':
    '已有扫描正在进行 —— 请等待其完成，或先停止它',
  'No ranges configured.': '未配置任何范围。',
  'No IP ranges configured — set them in the integration options':
    '未配置 IP 范围 —— 请在集成选项中设置',
  'Ranges are managed in ': 'IP 段在',
  'Settings → Devices & services → Homelable → Configure':
    '设置 → 设备与服务 → Homelable → 配置',
  '. Status check interval is configured there too.': '。状态检查间隔也在那里配置。',
  'Scan extra ports and probe HTTP services to identify apps on custom ports. Applies to this scan only.':
    '扫描额外端口并探测 HTTP 服务，以识别运行在自定义端口上的应用。仅对本次扫描生效。',
  'Scan started — track progress in History': '扫描已开始 —— 在扫描历史中查看进度',
  'Show IPs': '显示 IP',
  'Hide IPs': '隐藏 IP',

  // --- Zigbee import ------------------------------------------------------
  'Zigbee Import': 'Zigbee 导入',
  'Import Zigbee': '导入 Zigbee',
  'Import Z-Wave': '导入 Z-Wave',
  'Import Proxmox': '导入 Proxmox',
  'Z-Wave JS UI Import': 'Z-Wave JS UI 导入',
  'Start Zigbee scan': '开始 Zigbee 扫描',
  'Start Z-Wave scan': '开始 Z-Wave 扫描',
  'Zigbee scan started — check Scan History for results':
    'Zigbee 扫描已开始 —— 结果请查看扫描历史',
  'Z-Wave scan started — check Scan History for results':
    'Z-Wave 扫描已开始 —— 结果请查看扫描历史',
  'Failed to start Zigbee scan': '无法启动 Zigbee 扫描',
  'Failed to start Z-Wave scan': '无法启动 Z-Wave 扫描',
  'Gateway: ': '网关：',
  'checking…': '检测中…',
  '(auto-detected)': '（自动检测）',
  'Homelable picks the gateway for you: ZHA when its integration is set up, otherwise Zigbee2MQTT. Nothing to configure for ZHA.':
    'Homelable 会自动替你选择网关：已配置 ZHA 集成时使用 ZHA，否则使用 Zigbee2MQTT。ZHA 无需任何配置。',
  "ZHA is read straight from the integration — no MQTT broker, no re-pairing, and it returns almost instantly. Routers, end devices and LQI come from the radio's neighbour tables. Results show in Scan History.":
    'ZHA 数据直接从集成读取 —— 无需 MQTT broker，无需重新配对，几乎即时返回。路由器、终端设备和 LQI 来自无线模块的邻居表。结果显示在扫描历史中。',
  'Zigbee2MQTT is fetched over the MQTT broker HA already uses (base topic set in the integration options). The scan runs in the background — it can take a few minutes on large meshes as the coordinator polls every router. Progress shows in Scan History; you can keep working meanwhile.':
    'Zigbee2MQTT 通过 HA 已有的 MQTT broker 获取（base topic 在集成选项中设置）。扫描在后台运行 —— 在大型 Mesh 上，协调器需要轮询每个路由器，可能要花几分钟。进度显示在扫描历史中，你可以同时继续其他操作。',
  "Fetches the Z-Wave node list via Home Assistant's MQTT integration and adds discovered devices to Pending, where you can approve them onto the canvas.":
    '通过 Home Assistant 的 MQTT 集成获取 Z-Wave 节点列表，并把发现的设备加入待处理列表，你可以在那里批准它们进入画布。',
  'The scan runs in the background — Z-Wave JS UI can take a few minutes on large meshes as the gateway polls every node. Progress shows in Scan History; you can keep working meanwhile.':
    '扫描在后台运行 —— 在大型 Mesh 上，网关需要轮询每个节点，Z-Wave JS UI 可能要花几分钟。进度显示在扫描历史中，你可以同时继续其他操作。',

  // --- sentence fragments -------------------------------------------------
  // These are stitched around a <strong> that carries a brand or menu path, so
  // the Chinese drops the English joiner spaces and supplies its own punctuation.
  'Reads your Zigbee mesh and adds every device to Pending, where you approve them onto the canvas. Works with ':
    '读取你的 Zigbee Mesh，并把每台设备加入待处理列表，由你批准后放入画布。支持 ',
  ' or ': ' 或 ',
  '.': '。',
  'Running the other one? Change ': '正在运行另一个？想改用',
  ' in Settings → Devices & services → Homelable → Configure.':
    '，请在 设置 → 设备与服务 → Homelable → 配置 中切换。',
  'Zigbee gateway': 'Zigbee 网关',

  // --- form placeholder hints ---------------------------------------------
  // "e.g." is kept in Latin script because that is how the form reads on an
  // English keyboard, but the examples are Chinese so the hint matches the
  // field it sits under. Sibling: 'e.g. router' in components-panels.ts.
  'e.g. Home Network, Rack Power': '例如：家庭网络、机柜供电',
  'e.g. 20': '例如：20',
  'e.g. 1G, trunk...\nsecond line': '例如：1G、主干…\n第二行',
  'e.g. Uplink to core': '例如：上联到核心',
  // The host part is an example address and stays as-is; only the explanation
  // is copy. CHECK_TARGET_PLACEHOLDERS stays in English and is translated at
  // the render site — a module-level table would freeze the locale at import.
  '192.168.1.10 (defaults to node IP)': '192.168.1.10（默认为节点 IP）',
  // Cable tooltip. The leading space is part of the string: it is appended to
  // "<label> — <type>" and English needs the separator.
  ' (click to select, Delete to remove)': '（点击选中，Delete 键删除）',

  // --- relative time (keys match the main app so both share one set) ------
  'just now': '刚刚',
  '{n}m ago': '{n} 分钟前',
  '{n}h ago': '{n} 小时前',
  '{n}d ago': '{n} 天前',
  '{n}w ago': '{n} 周前',
  '{n}mo ago': '{n} 个月前',
  '{n}y ago': '{n} 年前',

  // --- status ids ----------------------------------------------------------
  // A node's status is rendered straight from its stored id, so the ids are
  // keys in their own right. The capitalised spellings are the filter
  // dropdown's labels and are separate entries; both are needed.
  'running': '进行中',
  'done': '已完成',
  'cancelled': '已取消',
  'error': '错误',

  // --- card / panel chrome ------------------------------------------------
  'Loading rack…': '正在加载机柜…',
  'Canvas unavailable. Is the Homelable integration still set up?':
    '画布不可用。Homelable 集成是否仍然配置正确？',
  'Loading canvas…': '正在加载画布…',
  'Markdown table copied to clipboard': 'Markdown 表格已复制到剪贴板',
  'Copy inventory as Markdown table': '复制设备清单为 Markdown 表格',
  'Search nodes, pending devices by IP or service…': '搜索节点、按 IP 或服务搜索待处理设备…',
  // Note the "and": the main app's caption is "…, pending devices…", a
  // different string and a different dictionary key.
  'Type to search nodes and pending devices…': '输入内容以搜索节点和待处理设备…',
  'This design has no devices yet. Open the Homelable panel to build it.':
    '该设计还没有设备。打开 Homelable 面板来搭建。',
  'Add Floorplan': '添加平面图',
  'Edges': '连线',
  'Derive patches from the links already drawn on the logical canvases':
    '根据逻辑画布上已绘制的连线推导配线关系',
  'Drag a port to place it, or nudge the selected one with the arrow keys. Ports snap to the row or column of their neighbours.':
    '拖动端口即可放置，或用方向键微调选中的端口。端口会吸附到相邻端口所在的行或列。',

  // --- integration options (token) ---------------------------------------
  'Leave the token blank to use the token configured in the integration options.':
    '令牌留空则使用集成选项中配置的令牌。',
  'Set a token here, or configure one in the integration options for reuse and auto-sync.':
    '在此处设置令牌，或在集成选项中配置以便复用和自动同步。',
  // One sentence split around <span>PVEAuditor</span>, so it has to join with
  // 'role is enough.' — reusing that half from the main app's own phrasing.
  'A read-only': '只读',

  // --- Lovelace card editor ----------------------------------------------
  // Captions for the card's <ha-form> fields, read by computeLabel(). These are
  // form labels, so they stay short.
  'Design': '设计',
  'Title': '标题',
  'Fit the canvas on load': '加载时适配画布',
  'Interaction': '交互',
  'Pan and zoom': '平移和缩放',
  'Locked': '锁定',
  'Open http://<ip> when a node is clicked': '点击节点时打开 http://<ip>',
}

export default part
