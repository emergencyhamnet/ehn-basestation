const LIVE_REFRESH_MILLIS = 15000;

const DIRECTORY_ENDPOINT_PRESETS = [
  { network: "MESHCORE", placeholder: "public key or node address", defaultPriority: 100, capabilities: "group,direct" },
  { network: "MESHTASTIC", placeholder: "node id or channel address", defaultPriority: 90, capabilities: "group,direct" },
  { network: "SMS", placeholder: "+441234567890", defaultPriority: 80, capabilities: "direct" },
  { network: "APRS", placeholder: "M0ABC-7", defaultPriority: 70, capabilities: "broadcast,direct" },
  { network: "EMAIL", placeholder: "operator@example.org", defaultPriority: 60, capabilities: "direct" },
];

const fallbackScreenContent = {
  directory: {
    eyebrow: "Directory",
    title: "Authority directory and user records",
    intro: "The main EHN database should feel like a user and member console, not a transport debug view.",
    actions: '<button class="button button-secondary" type="button" disabled>Refresh users</button><button class="button" type="button" disabled>Add user</button>',
    bodyClass: "workspace-body",
    body: '<section class="panel roster-panel"><div class="panel-head"><h3>User Directory</h3><span class="panel-meta">preview</span></div><div class="search-pill">Waiting for authority-directory data.</div></section><section class="panel panel-form"><div class="panel-head"><h3>User Details</h3><span class="panel-meta">preview</span></div></section>',
    inspectorPrimary: { title: "Authority View", meta: "Preview", content: '<dl class="inspector-list"><div><dt>Purpose</dt><dd>Review and edit the main EHN database.</dd></div></dl>' },
    inspectorSecondary: { title: "Next Steps", meta: "Directory", content: '<ul class="next-list"><li>Populate the authority DB with real users and gateway endpoints.</li></ul>' },
  },
  messages: {
    eyebrow: "Messages",
    title: "Cross-gateway message activity",
    intro: "Recent activity across MeshCore and SMS gateways, with registered users kept visible for operator review.",
    actions: '<button class="button button-secondary" type="button" disabled>Refresh</button>',
    bodyClass: "workspace-body",
    body: '<section class="panel"><div class="panel-head"><h3>Messages</h3><span class="panel-meta">preview</span></div><div class="search-pill">Waiting for gateway message data.</div></section>',
    inspectorPrimary: { title: "Conversation Info", meta: "Preview", content: '<dl class="inspector-list"><div><dt>Mode</dt><dd>Disconnected</dd></div></dl>' },
    inspectorSecondary: { title: "MeshCore Notes", meta: "Preview", content: '<ul class="next-list"><li>Waiting for API connection.</li></ul>' },
  },
  meshcoreGateway: {
    eyebrow: "MeshCore Gateway",
    title: "MeshCore gateway operations",
    intro: "Use a dedicated gateway panel for MeshCore status, private channels, observed nodes, and private-channel test sends.",
    actions: '<button class="button button-secondary" type="button" disabled>Refresh</button>',
    bodyClass: "workspace-stack",
    body: '<section class="panel"><div class="panel-head"><h3>Gateway Status</h3><span class="panel-meta">preview</span></div><div class="search-pill">Waiting for MeshCore gateway data.</div></section>',
    inspectorPrimary: { title: "Gateway Status", meta: "Preview", content: '<dl class="inspector-list"><div><dt>State</dt><dd>Unavailable</dd></div></dl>' },
    inspectorSecondary: { title: "Next Steps", meta: "MeshCore", content: '<ul class="next-list"><li>Connect the MeshCore runtime and refresh this panel.</li></ul>' },
  },
  smsGateway: {
    eyebrow: "SMS Gateway",
    title: "Android SMS gateway operations",
    intro: "Phone-side SMS and RCS activity should be managed from a dedicated gateway panel, not inferred from shared message history.",
    actions: '<button class="button button-secondary" type="button" disabled>Refresh</button>',
    bodyClass: "workspace-stack",
    body: '<section class="panel"><div class="panel-head"><h3>Gateway Status</h3><span class="panel-meta">preview</span></div><div class="search-pill">Waiting for SMS gateway data.</div></section>',
    inspectorPrimary: { title: "Gateway Status", meta: "Preview", content: '<dl class="inspector-list"><div><dt>State</dt><dd>Unavailable</dd></div></dl>' },
    inspectorSecondary: { title: "Next Steps", meta: "SMS", content: '<ul class="next-list"><li>Connect the Android app and refresh this panel.</li></ul>' },
  },
  basestation: {
    eyebrow: "Basestation",
    title: "Basestation status and management",
    intro: "Station-level health, database state, gateway registrations, and logs belong here.",
    actions: '<button class="button button-secondary" type="button" disabled>Refresh basestation</button>',
    bodyClass: "workspace-single",
    body: '<section class="panel"><div class="panel-head"><h3>Basestation</h3><span class="panel-meta">preview</span></div><div class="search-pill">Waiting for basestation status data.</div></section>',
    inspectorPrimary: { title: "Station", meta: "Preview", content: '<dl class="inspector-list"><div><dt>Status</dt><dd>Disconnected</dd></div></dl>' },
    inspectorSecondary: { title: "Scope", meta: "Basestation", content: '<ul class="next-list"><li>Station config, databases, links, and logs live here.</li></ul>' },
  },
  channels: {
    eyebrow: "Channels",
    title: "Channel configuration",
    intro: "Channels are projected from the Windows-side gateway contract.",
    actions: '<button class="button button-secondary" type="button" disabled>Refresh</button>',
    bodyClass: "workspace-body",
    body: '<section class="panel"><div class="panel-head"><h3>Channel Slots</h3><span class="panel-meta">preview</span></div><div class="search-pill">Waiting for channel data.</div></section>',
    inspectorPrimary: { title: "Channel Info", meta: "Preview", content: '<dl class="inspector-list"><div><dt>Total</dt><dd>0</dd></div></dl>' },
    inspectorSecondary: { title: "Next Steps", meta: "Preview", content: '<ul class="next-list"><li>Channel management is read-only for now.</li></ul>' },
  },
  contacts: {
    eyebrow: "Contacts",
    title: "Observed nodes and contact database",
    intro: "Observed nodes from the basestation gateway link.",
    actions: '<button class="button button-secondary" type="button" disabled>Refresh</button>',
    bodyClass: "workspace-body",
    body: '<section class="panel"><div class="panel-head"><h3>Contacts</h3><span class="panel-meta">preview</span></div><div class="search-pill">Waiting for contact data.</div></section>',
    inspectorPrimary: { title: "Contacts", meta: "Preview", content: '<dl class="inspector-list"><div><dt>Total</dt><dd>0</dd></div></dl>' },
    inspectorSecondary: { title: "Next Steps", meta: "Preview", content: '<ul class="next-list"><li>Observed nodes will appear here when available.</li></ul>' },
  },
  node: {
    eyebrow: "Node",
    title: "Device status and configuration",
    intro: "Review current node metrics and preferences from the MeshCore runtime.",
    actions: '<button class="button button-secondary" type="button" disabled>Refresh</button>',
    bodyClass: "workspace-body",
    body: '<section class="panel"><div class="panel-head"><h3>Status</h3><span class="panel-meta">preview</span></div><div class="search-pill">Waiting for node data.</div></section>',
    inspectorPrimary: { title: "Node Info", meta: "Preview", content: '<dl class="inspector-list"><div><dt>Status</dt><dd>Disconnected</dd></div></dl>' },
    inspectorSecondary: { title: "Next Steps", meta: "Preview", content: '<ul class="next-list"><li>Connect to the runtime to view node state.</li></ul>' },
  },
  diagnostics: {
    eyebrow: "Diagnostics",
    title: "Device and gateway diagnostics",
    intro: "System metrics and debug logs for troubleshooting.",
    actions: '<button class="button button-secondary" type="button" disabled>Refresh</button>',
    bodyClass: "workspace-body",
    body: '<section class="panel"><div class="panel-head"><h3>Diagnostics</h3><span class="panel-meta">preview</span></div><div class="search-pill">Waiting for diagnostics data.</div></section>',
    inspectorPrimary: { title: "Device Info", meta: "Preview", content: '<dl class="inspector-list"><div><dt>Status</dt><dd>Disconnected</dd></div></dl>' },
    inspectorSecondary: { title: "Next Steps", meta: "Preview", content: '<ul class="next-list"><li>Connect to view device diagnostics.</li></ul>' },
  },
};

function buildLocalPreviewDirectoryState() {
  return {
    is_preview: true,
    source: "preview",
    database_path: "memory://preview",
    station_name: "Windows authority preview",
    station_role: "authority",
    user_count: 2,
    promotion_counts: {
      PROMOTED: 1,
      PENDING_AUTHORITY_REVIEW: 1,
    },
    users: [
      {
        user: {
          user_id: "user-8f2c1d73a4be",
          display_name: "Jane Example",
          status: "active",
          home_region: "East Sector",
          is_local_to_station: "False",
          retention_class: "TRUSTED_STATION",
          promotion_status: "PROMOTED",
          created_at: "2026-08-28T10:15:00+00:00",
          updated_at: "2026-09-01T14:22:00+00:00",
        },
        identity: {
          identity_id: "identity-jane-example",
          user_id: "user-8f2c1d73a4be",
          identity_type: "USER",
          display_name: "jane-1234",
          messaging_mode: "BOTH",
          policy_id: "standard-member",
          status: "active",
          created_at: "2026-08-28T10:15:00+00:00",
          updated_at: "2026-09-01T14:22:00+00:00",
        },
        endpoints: [
          {
            endpoint_id: "endpoint-jane-meshcore",
            identity_id: "identity-jane-example",
            network: "MESHCORE",
            native_address: "9af3d182...b6cc77d2",
            verified: "True",
            enabled: "True",
            priority: 100,
            trust_level: "verified",
            replication_class: "TRUSTED_STATION",
            last_verified_at: "2026-08-31T18:10:00+00:00",
            capability_tags: "group,direct",
            updated_at: "2026-09-01T14:15:00+00:00",
          },
          {
            endpoint_id: "endpoint-jane-meshtastic",
            identity_id: "identity-jane-example",
            network: "MESHTASTIC",
            native_address: "!c0ffee12",
            verified: "True",
            enabled: "True",
            priority: 90,
            trust_level: "verified",
            replication_class: "TRUSTED_STATION",
            last_verified_at: "2026-08-31T18:00:00+00:00",
            capability_tags: "group,direct",
            updated_at: "2026-09-01T13:58:00+00:00",
          },
          {
            endpoint_id: "endpoint-jane-aprs",
            identity_id: "identity-jane-example",
            network: "APRS",
            native_address: "M0ABC-7",
            verified: "False",
            enabled: "False",
            priority: 70,
            trust_level: "pending",
            replication_class: "FIELD_OPERATIONAL",
            last_verified_at: "",
            capability_tags: "broadcast,direct",
            updated_at: "2026-08-30T09:02:00+00:00",
          },
        ],
      },
      {
        user: {
          user_id: "user-b1a72d04321e",
          display_name: "Unknown Medic 2",
          status: "active",
          home_region: "Forward Team",
          is_local_to_station: "True",
          retention_class: "FIELD_OPERATIONAL",
          promotion_status: "PENDING_AUTHORITY_REVIEW",
          created_at: "2026-09-01T11:07:00+00:00",
          updated_at: "2026-09-01T11:07:00+00:00",
        },
        identity: {
          identity_id: "identity-unknown-medic-2",
          user_id: "user-b1a72d04321e",
          identity_type: "USER",
          display_name: "medic-2041",
          messaging_mode: "DIRECT",
          policy_id: "",
          status: "active",
          created_at: "2026-09-01T11:07:00+00:00",
          updated_at: "2026-09-01T11:07:00+00:00",
        },
        endpoints: [
          {
            endpoint_id: "endpoint-unknown-medic-2-meshcore",
            identity_id: "identity-unknown-medic-2",
            network: "MESHCORE",
            native_address: "ab12cd34ef56",
            verified: "False",
            enabled: "True",
            priority: 100,
            trust_level: "local",
            replication_class: "FIELD_OPERATIONAL",
            last_verified_at: "",
            capability_tags: "direct",
            updated_at: "2026-09-01T11:07:00+00:00",
          },
        ],
      },
    ],
  };
}

const liveState = {
  activeScreen: "directory",
  apiAvailable: false,
  lastError: null,
  lastLoadedAt: null,
  basestationRefreshPending: false,
  basestationProcesses: null,
  workbenchIdentity: null,
  basestationProcessState: {
    pendingProcessId: null,
    message: "",
    level: "info",
  },
  runtime: null,
  node: null,
  contacts: null,
  messages: null,
  meshcoreGateway: null,
  smsGateway: null,
  deliveryQueue: null,
  logs: null,
  device: null,
  directory: buildLocalPreviewDirectoryState(),
  directoryUi: {
    selectedUserId: null,
    formMode: "view",
    message: "",
  },
  sendState: {
    meshcore: {
      pending: false,
      level: "info",
      message: "",
    },
    sms: {
      pending: false,
      level: "info",
      message: "",
    },
  },
  smsComposer: {
    toNumber: "",
    body: "",
  },
  meshcoreComposer: {
    channelIndex: "",
    body: "",
  },
  nodeWriteState: {
    pending: false,
    level: "info",
    message: "",
  },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function valueOrDefault(value, fallback) {
  return value === undefined || value === null || value === "" ? fallback : value;
}

function normalizeEndpointFlag(value) {
  return value === true || value === "True" || value === "true" || value === 1 || value === "1";
}

function formatIsoShort(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatRelativeTime(value) {
  if (!value) return "unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const deltaSeconds = Math.round((date.getTime() - Date.now()) / 1000);
  const absSeconds = Math.abs(deltaSeconds);
  if (absSeconds < 60) return deltaSeconds >= 0 ? "in moments" : "moments ago";
  const units = [
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
  ];
  for (const [seconds, label] of units) {
    if (absSeconds >= seconds) {
      const amount = Math.round(deltaSeconds / seconds);
      const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
      return formatter.format(amount, label);
    }
  }
  return formatIsoShort(value);
}

function deliveryEndpointStatusText(item) {
  return item?.has_target_endpoint ? "captured" : "missing";
}

function shortModuleName(path) {
  const parts = String(path || "").split(/[/\\]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "unknown";
}

function metricCard(label, value) {
  return `<div class="metric-card"><span class="detail-label">${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "-")}</strong></div>`;
}

function boolText(value) {
  if (value === true || value === 1 || value === "1" || String(value).toLowerCase() === "true") return "Yes";
  if (value === false || value === 0 || value === "0" || String(value).toLowerCase() === "false") return "No";
  return "-";
}

function statusBadge(label, tone = "neutral") {
  return `<span class="tag tag-${escapeHtml(tone || "neutral")}">${escapeHtml(label ?? "-")}</span>`;
}

function summaryRow(label, value) {
  return `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value ?? "-")}</dd></div>`;
}

function formatPromotionLabel(value) {
  const text = String(value || "LOCAL_ONLY").trim();
  return text.toLowerCase().replace(/_/g, " ");
}

function summarizeNetworks(endpoints) {
  if (!Array.isArray(endpoints) || !endpoints.length) return "No endpoints";
  const networks = [...new Set(endpoints.map((endpoint) => String(endpoint?.network || "").trim().toUpperCase()).filter(Boolean))];
  return networks.length ? networks.join(", ") : "No endpoints";
}

function formatGps(lat, lon) {
  const latitude = Number(lat);
  const longitude = Number(lon);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || (latitude === 0 && longitude === 0)) return "-";
  return `${latitude.toFixed(5)}, ${longitude.toFixed(5)}`;
}

function describeGpsMode(prefs) {
  if (!prefs) return "-";
  if (prefs.gps_mode === "not_supported" || prefs.gps_supported === false) return "not supported";
  if (prefs.gps_enabled === true) return prefs.gps_interval_s ? `on · ${prefs.gps_interval_s}s` : "on";
  if (prefs.gps_enabled === false) return "off";
  return prefs.gps_mode || "unknown";
}

function describeAdvertPolicy(value) {
  const policy = Number(value);
  if (policy === 0) return "no location";
  if (policy === 1) return "share location";
  if (Number.isFinite(policy)) return `policy ${policy}`;
  return "-";
}

function basestationProcessTone(process) {
  if (process?.status === "running") return "ok";
  if (process?.status === "running_unmanaged") return "warn";
  if (process?.status === "stopped") return "neutral";
  return "neutral";
}

function basestationProcessActionMarkup() {
  const state = liveState.basestationProcessState || {};
  if (!state.message) return "";
  const tone = state.level === "error" ? "danger" : (state.level === "success" ? "ok" : "neutral");
  return `<div class="search-pill search-pill-${escapeHtml(tone)}">${escapeHtml(state.message)}</div>`;
}

function buildBasestationProcessRows(processes) {
  if (!Array.isArray(processes) || !processes.length) {
    return '<div class="search-pill">No local basestation process data is available yet.</div>';
  }
  return `<div class="table-card"><table class="data-table"><thead><tr><th>Process</th><th>Status</th><th>Scope</th><th>Port</th><th>Control</th><th>Action</th></tr></thead><tbody>${processes.map((process) => {
    const isPending = liveState.basestationProcessState?.pendingProcessId === process.id;
    const canStart = process.control_available && process.status === "stopped";
    const canStop = process.control_available && process.status === "running";
    const actionButton = canStart
      ? `<button class="button" type="button" data-basestation-process-action="start" data-basestation-process-id="${escapeHtml(process.id)}" ${isPending ? "disabled" : ""}>${isPending ? "Starting..." : "Start"}</button>`
      : (canStop
        ? `<button class="button button-secondary" type="button" data-basestation-process-action="stop" data-basestation-process-id="${escapeHtml(process.id)}" ${isPending ? "disabled" : ""}>${isPending ? "Stopping..." : "Stop"}</button>`
        : escapeHtml(process.control_reason || "Observe only"));
    return `<tr><td><strong>${escapeHtml(process.label || process.id)}</strong><div class="table-subtext">${escapeHtml(process.kind || "process")}</div></td><td>${escapeHtml(process.status_label || process.status || "unknown")}</td><td>${escapeHtml(process.host_scope || "local")}<div class="table-subtext">${escapeHtml(process.host || "-")}</div></td><td>${escapeHtml(process.port || "-")}${process.pid ? `<div class="table-subtext">PID ${escapeHtml(String(process.pid))}</div>` : ""}</td><td>${escapeHtml(process.control_available ? "local managed" : "observe only")}</td><td>${actionButton}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}

function buildBasestationScreen() {
  if (!liveState.apiAvailable && !liveState.directory) return fallbackScreenContent.basestation;
  const runtime = liveState.runtime || {};
  const directory = liveState.directory || buildLocalPreviewDirectoryState();
  const basestationProcesses = Array.isArray(liveState.basestationProcesses?.processes) ? liveState.basestationProcesses.processes : [];
  const deliveryQueue = liveState.deliveryQueue || { messages: [], route_counts: {}, state_counts: {}, message_count: 0, claimed_message_count: 0, ready_message_count: 0 };
  const logs = Array.isArray(liveState.logs?.lines) ? liveState.logs.lines : (Array.isArray(liveState.logs?.entries) ? liveState.logs.entries : []);
  const logText = logs.slice(0, 20).map((entry) => {
    if (typeof entry === "string") return entry;
    return `${formatIsoShort(entry.timestamp || entry.event_utc)} ${entry.level || "info"} ${entry.message || entry.text || ""}`.trim();
  }).join("\n");
  const routeSummary = Object.entries(deliveryQueue.route_counts || {}).map(([name, count]) => `${name}: ${count}`).join(" · ") || "No delivery routes recorded";
  const stateSummary = Object.entries(deliveryQueue.state_counts || {}).map(([name, count]) => `${name}: ${count}`).join(" · ") || "No delivery states recorded";
  const queueRows = Array.isArray(deliveryQueue.messages) ? deliveryQueue.messages : [];
  const refreshLabel = liveState.basestationRefreshPending ? "Refreshing basestation..." : "Refresh basestation";
  const refreshNote = liveState.basestationRefreshPending
    ? '<div class="search-pill">Refreshing basestation state from the current host and gateway links...</div>'
    : `<div class="search-pill">Last basestation refresh ${escapeHtml(formatRelativeTime(liveState.lastLoadedAt))}.</div>`;
  const runningProcesses = basestationProcesses.filter((process) => process?.status === "running").length;
  const managedProcesses = basestationProcesses.filter((process) => process?.control_available).length;
  const queueMarkup = queueRows.length
    ? `<div class="table-card"><table class="data-table"><thead><tr><th>Target</th><th>Route</th><th>State</th><th>Endpoint</th><th>Updated</th></tr></thead><tbody>${queueRows.map((item) => `<tr><td>${escapeHtml(item.target_user_id || "-")}</td><td>${escapeHtml(item.selected_route || item.target_endpoint_network || "unassigned")}</td><td>${escapeHtml(item.delivery_state || "unknown")}</td><td>${escapeHtml(deliveryEndpointStatusText(item))}</td><td>${escapeHtml(formatIsoShort(item.updated_at || item.created_at))}</td></tr>`).join("")}</tbody></table></div>`
    : '<div class="search-pill">No delivery records are available in this station view.</div>';

  const body = [
    '<section class="stats-band panel">',
    `<div class="stat-block"><span class="detail-label">Station</span><strong>${escapeHtml(directory.station_name || runtime.host?.hostname || "windows-basestation")}</strong></div>`,
    `<div class="stat-block"><span class="detail-label">Role</span><strong>${escapeHtml(directory.station_role || "field")}</strong></div>`,
    `<div class="stat-block"><span class="detail-label">Directory Users</span><strong>${escapeHtml(String(directory.user_count || 0))}</strong></div>`,
    `<div class="stat-block"><span class="detail-label">Delivery Records</span><strong>${escapeHtml(String(deliveryQueue.message_count || 0))}</strong></div>`,
    '</section>',
    refreshNote,
    '<section class="summary-grid summary-grid-single">',
    '<section class="panel">',
    '<div class="panel-head"><h3>Station And Link</h3><span class="panel-meta">live state</span></div>',
    `<div class="detail-grid detail-grid-wide">${metricCard("Basestation", liveState.apiAvailable ? "Connected" : "Preview")}${metricCard("Session", runtime.service?.ActiveState || "unknown")}${metricCard("Link", runtime.service?.SubState || runtime.interface_mode || "unknown")}${metricCard("Mode", runtime.interface_mode || "-")}${metricCard("Platform", runtime.host?.platform || "-")}${metricCard("Storage Root", runtime.storage_root || "-")}${metricCard("Last Refresh", formatIsoShort(liveState.lastLoadedAt))}</div>`,
    '</section>',
    '<section class="panel">',
    '<div class="panel-head"><h3>Directory Storage And Review</h3><span class="panel-meta">authority state</span></div>',
    `<div class="detail-grid detail-grid-wide">${metricCard("Directory Source", directory.source || "-")}${metricCard("Preview Mode", directory.is_preview ? "Yes" : "No")}${metricCard("Pending Review", directory.promotion_counts?.PENDING_AUTHORITY_REVIEW || 0)}${metricCard("Promoted", directory.promotion_counts?.PROMOTED || 0)}${metricCard("Local Records", Array.isArray(directory.users) ? directory.users.filter((entry) => entry?.user?.is_local_to_station === true || entry?.user?.is_local_to_station === "True").length : 0)}${metricCard("Directory Path", directory.database_path || "-")}</div>`,
    '</section>',
    '<section class="panel">',
    '<div class="panel-head"><h3>Gateway Registrations</h3><span class="panel-meta">current view</span></div>',
    `<div class="detail-grid detail-grid-wide">${metricCard("MeshCore", meshcoreGatewayStatusText())}${metricCard("SMS", smsGatewayStatusText())}${metricCard("Endpoint Captured", deliveryQueue.ready_message_count || 0)}${metricCard("Delivery Claims", deliveryQueue.claimed_message_count || 0)}${metricCard("Routes", routeSummary)}${metricCard("States", stateSummary)}</div>`,
    '</section>',
    '<section class="panel">',
    '<div class="panel-head"><h3>Local Basestation Processes</h3><span class="panel-meta">gateways remain observe only</span></div>',
    `<div class="detail-grid detail-grid-wide">${metricCard("Running", runningProcesses)}${metricCard("Locally Managed", managedProcesses)}${metricCard("Observed Only", Math.max(0, basestationProcesses.length - managedProcesses))}${metricCard("Policy", "Basestation only")}</div>`,
    basestationProcessActionMarkup(),
    buildBasestationProcessRows(basestationProcesses),
    '</section>',
    '<section class="panel">',
    '<div class="panel-head"><h3>Delivery Records</h3><span class="panel-meta">redacted</span></div>',
    queueMarkup,
    '</section>',
    '<section class="panel">',
    `<div class="panel-head"><h3>Recent Logs</h3><span class="panel-meta">${logs.length} entries</span></div><div class="log-viewer"><pre>${escapeHtml(logText || "No log entries available.")}</pre></div>`,
    '</section>',
    '</section>',
  ].join("");

  return {
    eyebrow: "Basestation",
    title: "Basestation status and management",
    intro: "This panel owns station-level management: link state, directory storage, gateway registrations, review state, and logs.",
    actions: `<button class="button button-secondary" type="button" id="refreshBasestationButton" ${liveState.basestationRefreshPending ? "disabled" : ""}>${escapeHtml(refreshLabel)}</button>`,
    bodyClass: "workspace-stack",
    body,
    inspectorPrimary: { title: "Station Summary", meta: directory.station_role || "station", content: `<dl class="inspector-list"><div><dt>Station</dt><dd>${escapeHtml(directory.station_name || runtime.host?.hostname || "windows-basestation")}</dd></div><div><dt>Link</dt><dd>${escapeHtml(runtime.service?.SubState || runtime.interface_mode || "-")}</dd></div><div><dt>Directory</dt><dd>${escapeHtml(directory.source || "-")}</dd></div><div><dt>Delivery Records</dt><dd>${escapeHtml(String(deliveryQueue.message_count || 0))}</dd></div><div><dt>Managed Processes</dt><dd>${escapeHtml(String(managedProcesses))}</dd></div><div><dt>Last Refresh</dt><dd>${escapeHtml(formatIsoShort(liveState.lastLoadedAt))}</dd></div></dl>` },
    inspectorSecondary: { title: "Delivery State", meta: "Redacted", content: `<ul class="next-list"><li>${escapeHtml(routeSummary)}</li><li>${escapeHtml(stateSummary)}</li><li>Endpoint status here only means an address was captured for routing, not that the user is connected or currently reachable.</li><li>Gateway panels stay observe-only here even when the gateway is colocated with this basestation.</li></ul>` },
  };
}
function promotionTone(value) {
  if (value === "PROMOTED" || value === "MERGED") return "ok";
  if (value === "PENDING_AUTHORITY_REVIEW") return "warn";
  if (value === "REJECTED_DUPLICATE") return "danger";
  return "neutral";
}

function pickPrimaryEndpoint(endpoints) {
  if (!Array.isArray(endpoints) || !endpoints.length) return null;
  return [...endpoints].sort((left, right) => Number(right.priority || 0) - Number(left.priority || 0))[0];
}

function splitDisplayName(displayName) {
  const parts = String(displayName || "").trim().split(/\s+/).filter(Boolean);
  return {
    givenName: parts[0] || "",
    familyName: parts.slice(1).join(" "),
  };
}

function formatChannelType(channel) {
  if (!channel || channel.is_empty) return "empty";
  if (Number(channel.index) === 0) return "public";
  const name = String(channel.name || "").toLowerCase();
  if (name.includes("public")) return "public";
  return "private";
}

function messageSendAllowed() {
  const parsed = liveState.runtime?.status?.parsed || {};
  return Boolean(parsed.gateway_tx_allowed || parsed.browser_send_allowed || parsed.helper_tx_allowed || parsed.send_allowed);
}

function composerStatusMarkup() {
  if (!liveState.sendState.message) return '<p class="composer-status"></p>';
  const statusClass = liveState.sendState.level === "error" ? "composer-status composer-status-error" : "composer-status composer-status-success";
  return `<p class="${statusClass}">${escapeHtml(liveState.sendState.message)}</p>`;
}

function gatewayComposerStatusMarkup(state) {
  if (!state || !state.message) return '<p class="composer-status"></p>';
  const statusClass = state.level === "error" ? "composer-status composer-status-error" : "composer-status composer-status-success";
  return `<p class="${statusClass}">${escapeHtml(state.message)}</p>`;
}

function nodeWriteStatusMarkup() {
  if (!liveState.nodeWriteState.message) return '<p class="composer-status"></p>';
  const statusClass = liveState.nodeWriteState.level === "error" ? "composer-status composer-status-error" : "composer-status composer-status-success";
  return `<p class="${statusClass}">${escapeHtml(liveState.nodeWriteState.message)}</p>`;
}

function deriveDirectoryGroups(entry) {
  const source = entry || {};
  const user = source.user || {};
  const identity = source.identity || {};
  const profile = source.profile && typeof source.profile === "object" ? source.profile : {};
  const custom = profile;
  const endpoints = Array.isArray(source.endpoints) ? source.endpoints : [];
  const primaryEndpoint = pickPrimaryEndpoint(endpoints);
  const displayName = String(user.display_name || identity.display_name || custom.personal?.display_name || "").trim();
  const splitName = splitDisplayName(displayName || user.user_id || "");
  const personal = custom.personal || {
    display_name: displayName || user.display_name || identity.display_name || "Unnamed user",
    given_name: splitName.givenName,
    family_name: splitName.familyName,
    email: "",
    phone_primary: "",
    home_region: "",
    home_location_text: "",
    lat: null,
    lon: null,
    member_status: user.status || "",
    general_status: user.status || "",
    last_active_at: user.updated_at || user.created_at || "",
  };
  const identityGroup = custom.identity || {
    primary_identity_label: identity.display_name || user.user_id || "",
    identity_type: "USER",
    messaging_mode: "BOTH",
    policy_profile: "",
    rcs_name: "",
    routing_notes: "",
  };
  const teams = custom.teams || {
    teams: [],
    roles: [],
    active_group_member: false,
  };
  const presence = custom.presence || {
    current_best_route: primaryEndpoint?.network || "No route",
    last_seen_by_network: Object.fromEntries(endpoints.map((endpoint) => [endpoint.network, endpoint.updated_at || endpoint.last_verified_at || ""])),
    presence_state: primaryEndpoint ? "Configured" : "No current observations",
  };
  const review = custom.review || {
    record_origin: source.source_station || (user.is_local_to_station === "True" ? "field-station" : "windows-authority"),
    replication_class: primaryEndpoint?.replication_class || user.retention_class || "FIELD_OPERATIONAL",
    promotion_status: user.promotion_status || "LOCAL_ONLY",
    conflict_flag: false,
  };
  const notes = custom.notes || {
    operator_notes: "",
  };
  return { personal, identityGroup, teams, presence, review, notes };
}

function getDirectoryUsers() {
  return Array.isArray(liveState.directory?.users) ? liveState.directory.users : [];
}

function getSelectedDirectoryUser() {
  const users = getDirectoryUsers();
  if (!users.length) return null;
  const selected = users.find((entry) => entry?.user?.user_id === liveState.directoryUi.selectedUserId);
  if (selected) return selected;
  liveState.directoryUi.selectedUserId = users[0]?.user?.user_id || null;
  return users[0] || null;
}

function recomputeDirectorySummary() {
  const users = getDirectoryUsers();
  const promotionCounts = {};
  users.forEach((entry) => {
    const status = String(entry?.user?.promotion_status || "UNKNOWN");
    promotionCounts[status] = (promotionCounts[status] || 0) + 1;
  });
  if (liveState.directory) {
    liveState.directory.user_count = users.length;
    liveState.directory.promotion_counts = promotionCounts;
  }
}

function upsertDirectoryEntry(entry) {
  if (!entry?.user?.user_id) return;
  if (!liveState.directory) {
    liveState.directory = buildLocalPreviewDirectoryState();
    liveState.directory.is_preview = false;
  }
  const users = Array.isArray(liveState.directory.users) ? [...liveState.directory.users] : [];
  const existingIndex = users.findIndex((candidate) => candidate?.user?.user_id === entry.user.user_id);
  if (existingIndex >= 0) users[existingIndex] = entry;
  else users.unshift(entry);
  liveState.directory.users = users;
  recomputeDirectorySummary();
}

function buildEndpointDraftRows(selectedUser, isAdd) {
  const endpointsByNetwork = new Map(
    (selectedUser?.endpoints || []).map((endpoint) => [String(endpoint.network || "").toUpperCase(), endpoint]),
  );
  return DIRECTORY_ENDPOINT_PRESETS.map((preset) => {
    const endpoint = endpointsByNetwork.get(preset.network) || null;
    return {
      network: preset.network,
      placeholder: preset.placeholder,
      endpointId: endpoint?.endpoint_id || "",
      nativeAddress: endpoint?.native_address || "",
      enabled: endpoint ? normalizeEndpointFlag(endpoint.enabled) : preset.network === "MESHCORE" && isAdd,
      verified: endpoint ? normalizeEndpointFlag(endpoint.verified) : false,
      priority: endpoint?.priority ?? preset.defaultPriority,
      trustLevel: endpoint?.trust_level || (isAdd ? "draft" : "local"),
      replicationClass: endpoint?.replication_class || "TRUSTED_STATION",
      capabilityTags: endpoint?.capability_tags || preset.capabilities,
    };
  });
}

function buildDirectoryEditor(selectedUser) {
  if (liveState.directoryUi.formMode === "view") return "";
  const isAdd = liveState.directoryUi.formMode === "add";
  const sourceUser = isAdd ? null : selectedUser;
  const groups = deriveDirectoryGroups(sourceUser || {});
  const endpointRows = buildEndpointDraftRows(sourceUser, isAdd);
  return `
    <section class="record-section">
      <div class="panel-head">
        <h4>${isAdd ? "Add New User" : "Edit Selected User"}</h4>
        <span class="panel-meta">directory store</span>
      </div>
      <p class="editor-note">Populate whichever gateway endpoints this user actually has. Any endpoint row with an address will be saved into the directory.</p>
      <form id="directoryEditorForm" class="form-grid compact-form">
        <div class="record-subgrid">
          <label>Display Name<input name="display_name" value="${escapeHtml(isAdd ? "" : groups.personal.display_name || "")}"></label>
          <label>Given Name<input name="given_name" value="${escapeHtml(isAdd ? "" : groups.personal.given_name || "")}"></label>
          <label>Family Name<input name="family_name" value="${escapeHtml(isAdd ? "" : groups.personal.family_name || "")}"></label>
          <label>Email<input name="email" value="${escapeHtml(isAdd ? "" : groups.personal.email || "")}"></label>
          <label>Phone<input name="phone_primary" value="${escapeHtml(isAdd ? "" : groups.personal.phone_primary || "")}"></label>
          <label>Home Region<input name="home_region" value="${escapeHtml(isAdd ? "" : groups.personal.home_region || "")}"></label>
          <label>Home Location<input name="home_location_text" value="${escapeHtml(isAdd ? "" : groups.personal.home_location_text || "")}"></label>
          <label>Member Status<input name="member_status" value="${escapeHtml(isAdd ? "Pending" : groups.personal.member_status || "")}"></label>
          <label>General Status<input name="general_status" value="${escapeHtml(isAdd ? "active" : groups.personal.general_status || "")}"></label>
          <label>EHN Identity<input name="primary_identity_label" value="${escapeHtml(isAdd ? "jane-1234" : groups.identityGroup.primary_identity_label || "")}"></label>
          <label>Identity Type<input name="identity_type" value="${escapeHtml(isAdd ? "USER" : groups.identityGroup.identity_type || "USER")}"></label>
          <label>Policy Profile<input name="policy_profile" value="${escapeHtml(isAdd ? "" : groups.identityGroup.policy_profile || "")}"></label>
          <label>Messaging Mode<select name="messaging_mode"><option value="DIRECT" ${!isAdd && groups.identityGroup.messaging_mode === "DIRECT" ? "selected" : ""}>Individual</option><option value="GROUP" ${!isAdd && groups.identityGroup.messaging_mode === "GROUP" ? "selected" : ""}>Group</option><option value="BOTH" ${isAdd || groups.identityGroup.messaging_mode === "BOTH" ? "selected" : ""}>Both</option></select></label>
          <label>Notes<textarea name="operator_notes" rows="4">${escapeHtml(isAdd ? "" : groups.notes.operator_notes || "")}</textarea></label>
        </div>
        <section class="record-section">
          <h4>Gateway Endpoints</h4>
          <p class="panel-meta">For phone-linked users, set the visible RCS sender string only on the SMS row.</p>
          <div class="endpoint-card-grid">
            ${endpointRows.map((endpoint) => `
              <section class="panel endpoint-card">
                <div class="panel-head">
                  <h4>${escapeHtml(endpoint.network)}</h4>
                  <span class="panel-meta">directory row</span>
                </div>
                <div class="record-subgrid">
                  <input type="hidden" name="endpoint_id_${endpoint.network}" value="${escapeHtml(endpoint.endpointId)}">
                  <label>${escapeHtml(endpoint.network)} Address<input name="endpoint_address_${endpoint.network}" placeholder="${escapeHtml(endpoint.placeholder)}" value="${escapeHtml(endpoint.nativeAddress)}"></label>
                  ${endpoint.network === "SMS" ? `<label>RCS Visible Name<input name="rcs_name" value="${escapeHtml(isAdd ? "" : groups.identityGroup.rcs_name || "")}"></label>` : ""}
                  <label>Priority<input name="endpoint_priority_${endpoint.network}" type="number" value="${escapeHtml(String(endpoint.priority))}"></label>
                  <label>Capability Tags<input name="endpoint_capabilities_${endpoint.network}" value="${escapeHtml(endpoint.capabilityTags)}"></label>
                  <label>Trust Level<input name="endpoint_trust_${endpoint.network}" value="${escapeHtml(endpoint.trustLevel)}"></label>
                  <label>Replication Class<input name="endpoint_replication_${endpoint.network}" value="${escapeHtml(endpoint.replicationClass)}"></label>
                  <label><input type="checkbox" name="endpoint_enabled_${endpoint.network}" ${endpoint.enabled ? "checked" : ""}> Enabled</label>
                  <label><input type="checkbox" name="endpoint_verified_${endpoint.network}" ${endpoint.verified ? "checked" : ""}> Verified</label>
                </div>
              </section>
            `).join("")}
          </div>
        </section>
        <div class="form-actions header-actions">
          <button class="button button-secondary" type="button" id="directoryCancelButton">Cancel</button>
          <button class="button" type="submit">Save User</button>
        </div>
      </form>
    </section>`;
}

function buildDirectoryRecordSections(selectedUser) {
  if (!selectedUser) return '<div class="search-pill">No user records available yet.</div>';
  const groups = deriveDirectoryGroups(selectedUser);
  const selectedEndpoints = selectedUser.endpoints || [];
  const primaryEndpoint = pickPrimaryEndpoint(selectedEndpoints);
  const endpointCards = selectedEndpoints.length
    ? selectedEndpoints.map((endpoint) => `
        <section class="panel endpoint-card">
          <div class="panel-head">
            <h4>${escapeHtml(endpoint.network || "Endpoint")}</h4>
            <span class="panel-meta">priority ${escapeHtml(String(valueOrDefault(endpoint.priority, "-")))}</span>
          </div>
          <div class="detail-grid detail-grid-wide">
            ${metricCard("Address", endpoint.native_address || "-")}
            ${metricCard("Verified", boolText(endpoint.verified))}
            ${metricCard("Enabled", boolText(endpoint.enabled))}
            ${metricCard("Trust", endpoint.trust_level || "-")}
            ${metricCard("Replication", endpoint.replication_class || "-")}
            ${metricCard("Last Verified", formatIsoShort(endpoint.last_verified_at))}
          </div>
        </section>`).join("")
    : '<div class="search-pill">No endpoint rows recorded yet.</div>';
  const lastSeenCards = Object.entries(groups.presence.last_seen_by_network || {}).map(([network, value]) => metricCard(`Last Seen ${network}`, formatIsoShort(value))).join("");
  return `
    <div class="record-banner">
      ${statusBadge(selectedUser.user?.status || "unknown", "neutral")}
      ${statusBadge(formatPromotionLabel(selectedUser.user?.promotion_status || "LOCAL_ONLY"), promotionTone(selectedUser.user?.promotion_status))}
      ${selectedUser.user?.is_local_to_station === "True" ? statusBadge("local to station", "warn") : statusBadge("shared authority record", "ok")}
    </div>
    <section class="record-section"><h4>Personal</h4><div class="detail-grid detail-grid-wide">${metricCard("Display Name", groups.personal.display_name)}${metricCard("Given Name", groups.personal.given_name || "-")}${metricCard("Family Name", groups.personal.family_name || "-")}${metricCard("Email", groups.personal.email || "-")}${metricCard("Phone", groups.personal.phone_primary || "-")}${metricCard("Home Region", groups.personal.home_region || "-")}${metricCard("Home Location", groups.personal.home_location_text || "-")}${metricCard("Map Location", formatGps(groups.personal.lat, groups.personal.lon))}${metricCard("Member Status", groups.personal.member_status || "-")}${metricCard("General Status", groups.personal.general_status || "-")}${metricCard("Last Active", formatIsoShort(groups.personal.last_active_at))}${metricCard("Retention", selectedUser.user?.retention_class || "-")}</div></section>
    <section class="record-section"><h4>Identity</h4><div class="detail-grid detail-grid-wide">${metricCard("Identity Label", groups.identityGroup.primary_identity_label || "-")}${metricCard("Identity Type", groups.identityGroup.identity_type || "-")}${metricCard("Messaging Mode", groups.identityGroup.messaging_mode || "-")}${metricCard("Policy", groups.identityGroup.policy_profile || "Not assigned")}${metricCard("RCS Visible Name", groups.identityGroup.rcs_name || "-")}${metricCard("Routing Notes", groups.identityGroup.routing_notes || "-")}${metricCard("Primary Endpoint", primaryEndpoint?.network || "No endpoint")}</div></section>
    <section class="record-section"><h4>Gateway Endpoints</h4><div class="endpoint-card-grid">${endpointCards}</div></section>
    <section class="record-section"><h4>Teams And Roles</h4><div class="detail-grid detail-grid-wide">${metricCard("Teams", (groups.teams.teams || []).join(", ") || "-")}${metricCard("Roles", (groups.teams.roles || []).join(", ") || "-")}${metricCard("Active Group Member", boolText(groups.teams.active_group_member))}${metricCard("User ID", selectedUser.user?.user_id || "-")}</div></section>
    <section class="record-section"><h4>Presence And Activity</h4><div class="detail-grid detail-grid-wide">${metricCard("Current Best Route", groups.presence.current_best_route || "-")}${metricCard("Presence State", groups.presence.presence_state || "-")}${lastSeenCards || metricCard("Last Seen", "-")}</div></section>
    <section class="record-section"><h4>Review And Replication</h4><div class="detail-grid detail-grid-wide">${metricCard("Record Origin", groups.review.record_origin || "-")}${metricCard("Replication Class", groups.review.replication_class || "-")}${metricCard("Promotion Status", groups.review.promotion_status || "-")}${metricCard("Conflict Flag", boolText(groups.review.conflict_flag))}</div></section>
    <section class="record-section"><h4>Notes</h4><div class="detail-grid detail-grid-wide">${metricCard("Operator Notes", groups.notes.operator_notes || "-")}</div></section>`;
}

function buildDirectoryScreen() {
  if (!liveState.directory) return fallbackScreenContent.directory;
  const users = liveState.directory.users || [];
  const selectedUser = getSelectedDirectoryUser();
  const deleteDisabled = liveState.directory.is_preview || !selectedUser ? "disabled" : "";
  const detailsContent = liveState.directoryUi.formMode === "view" ? buildDirectoryRecordSections(selectedUser) : buildDirectoryEditor(selectedUser);
  const detailsTitle = liveState.directoryUi.formMode === "view" ? "User Details" : (liveState.directoryUi.formMode === "add" ? "Add User" : "Edit User");
  const detailsMeta = liveState.directoryUi.formMode === "view" ? (selectedUser ? escapeHtml(selectedUser.user?.user_id || "selected") : "none") : "directory store";
  const primaryEndpoint = pickPrimaryEndpoint(selectedUser?.endpoints || []);
  const promotionEntries = Object.entries(liveState.directory.promotion_counts || {});
  return {
    eyebrow: "Directory",
    title: liveState.directory.station_role === "authority" ? "Main database user directory" : "Field station user directory",
    intro: liveState.directory.is_preview ? "This screen is using preview records so you can review the authority layout before loading real user data. When you add records to the database, the same screen will render those rows instead." : "This screen is reading user records from the current basestation SQLite directory store. It separates personal user data, identity behavior, and per-gateway endpoints so the main DB stays understandable to operators.",
    actions: `<button class="button button-secondary" type="button" id="refreshDirectoryButton">Refresh users</button><button class="button button-secondary" type="button" id="editDirectoryButton" ${selectedUser ? "" : "disabled"}>Edit selected</button><button class="button button-danger" type="button" id="deleteDirectoryButton" ${deleteDisabled}>Delete selected</button><button class="button" type="button" id="addDirectoryButton">Add new</button>`,
    bodyClass: "workspace-body",
    body: `<section class="panel roster-panel"><div class="panel-head"><h3>User Directory</h3><span class="panel-meta">${escapeHtml(String(liveState.directory.user_count || 0))} records</span></div><dl class="summary-list summary-list-compact">${summaryRow("Data Source", liveState.directory.source)}${summaryRow("Station", liveState.directory.station_name)}${summaryRow("Station Role", liveState.directory.station_role)}${summaryRow("Database", liveState.directory.database_path)}${summaryRow("Endpoints", summarizeNetworks(selectedUser?.endpoints || []))}</dl>${liveState.directoryUi.message ? `<p class="editor-note">${escapeHtml(liveState.directoryUi.message)}</p>` : ""}${users.map((entry) => `<div class="thread-item ${selectedUser?.user?.user_id === entry?.user?.user_id ? "thread-item-active" : ""}" data-directory-select="${escapeHtml(entry?.user?.user_id || "")}"><div><strong>${escapeHtml(entry.user?.display_name || "Unnamed user")}</strong><p>${escapeHtml(summarizeNetworks(entry.endpoints))}</p></div><span>${statusBadge(formatPromotionLabel(entry.user?.promotion_status), promotionTone(entry.user?.promotion_status))}</span></div>`).join("")}</section><section class="panel panel-form"><div class="panel-head"><h3>${detailsTitle}</h3><span class="panel-meta">${detailsMeta}</span></div>${detailsContent}</section>`,
    inspectorPrimary: { title: "Directory Summary", meta: liveState.directory.is_preview ? "Preview" : "Live", content: `<dl class="inspector-list"><div><dt>Users</dt><dd>${escapeHtml(String(liveState.directory.user_count || 0))}</dd></div><div><dt>Source</dt><dd>${escapeHtml(liveState.directory.source || "-")}</dd></div><div><dt>Role</dt><dd>${escapeHtml(liveState.directory.station_role || "-")}</dd></div><div><dt>Primary Endpoint</dt><dd>${escapeHtml(primaryEndpoint?.network || "No endpoint")}</dd></div></dl>` },
    inspectorSecondary: { title: "Review State", meta: "Counts", content: `<ul class="next-list">${promotionEntries.length ? promotionEntries.map(([name, count]) => `<li>${escapeHtml(formatPromotionLabel(name))}: ${escapeHtml(String(count))}</li>`).join("") : '<li>No promotion-state rows yet.</li>'}</ul>` },
  };
}

function buildMessagesScreen() {
  if (!liveState.apiAvailable || !liveState.messages) return fallbackScreenContent.messages;
  const interfaceMode = liveState.runtime?.interface_mode || "preview";
  const messageEntries = liveState.messages.messages || [];
  const registeredUsers = Array.isArray(liveState.messages.registered_users) ? liveState.messages.registered_users : (Array.isArray(liveState.messages.active_users) ? liveState.messages.active_users : []);
  const timeline = messageEntries.length ? messageEntries.slice(0, 50).map((entry) => {
    const inbound = entry.direction === "received" || entry.direction === "advert";
    const messageClass = inbound ? "message-row-inbound" : "message-row-outbound";
    const origin = entry.direction === "advert" ? "advert" : (entry.source_label || entry.source_id || entry.recipient_prefix || entry.target_id || entry.message_scope || entry.direction || "event");
    const meta = [entry.network || "event", entry.direction || "unknown", entry.target_id && entry.target_id !== origin ? `to ${entry.target_id}` : entry.message_scope || ""].filter(Boolean).join(" · ");
    return `<article class="message-row ${messageClass}"><p class="message-origin">${escapeHtml(origin)}</p><p class="message-meta">${escapeHtml(meta)}</p><p class="message-body">${escapeHtml(entry.text || entry.detail || "(no text)")}</p><time>${escapeHtml(formatIsoShort(entry.event_utc))}</time></article>`;
  }).join("") : '<article class="message-row message-row-inbound"><p class="message-origin">history</p><p class="message-body">No gateway-linked message activity recorded yet.</p><time>-</time></article>';
  return {
    eyebrow: "Messages",
    title: "Cross-gateway message activity",
    intro: "This overall panel shows recent activity across gateways, while gateway-specific testing stays in the dedicated MeshCore and SMS panels.",
    actions: '<button class="button button-secondary" type="button" id="refreshMessagesButton">Refresh</button>',
    bodyClass: "workspace-body",
    body: `<section class="thread-list panel"><div class="panel-head"><h3>Registered Users</h3><span class="panel-meta">${registeredUsers.length} visible</span></div>${registeredUsers.length ? registeredUsers.map((item, index) => `<div class="thread-item ${index === 0 ? "thread-item-active" : ""}"><div><strong>${escapeHtml(item.label || "Unknown")}</strong><p>${escapeHtml([(item.state || item.detail || item.network || "registered"), item.last_heard_utc ? `last heard ${formatRelativeTime(item.last_heard_utc)}` : ""].filter(Boolean).join(" · "))}</p></div><span>${escapeHtml(item.network || "-")}</span></div>`).join("") : '<div class="thread-item"><div><strong>No registered users</strong><p>Resolved presence and messaging identities will appear here.</p></div><span>-</span></div>'}</section><section class="timeline panel"><div class="panel-head"><h3>Messages</h3><span class="panel-meta">${Math.min(liveState.messages.message_count || 0, 50)} recent events</span></div>${timeline}</section>`,
    inspectorPrimary: { title: "Message Feed", meta: interfaceMode, content: `<dl class="inspector-list"><div><dt>Mode</dt><dd>${escapeHtml(interfaceMode)}</dd></div><div><dt>History path</dt><dd>${escapeHtml(liveState.messages.history_meta?.path || "-")}</dd></div><div><dt>Last message event</dt><dd>${escapeHtml(formatRelativeTime(messageEntries[0]?.event_utc))}</dd></div><div><dt>Limit</dt><dd>50 events</dd></div></dl>` },
    inspectorSecondary: { title: "Screen Role", meta: "Overall", content: '<ul class="next-list"><li>Use this screen for cross-gateway awareness.</li><li>Use MeshCore Gateway and SMS Gateway for transport-specific testing and sends.</li></ul>' },
  };
}

function buildMeshcoreGatewayScreen() {
  if (!liveState.apiAvailable || !liveState.meshcoreGateway?.ok) return fallbackScreenContent.meshcoreGateway;
  const gateway = liveState.meshcoreGateway;
  const runtime = liveState.runtime || {};
  const node = liveState.node || {};
  const device = liveState.device || {};
  const prefs = node.prefs || {};
  const logs = Array.isArray(liveState.logs?.lines) ? liveState.logs.lines : (Array.isArray(liveState.logs?.entries) ? liveState.logs.entries : []);
  const privateChannels = Array.isArray(gateway.private_channels) ? gateway.private_channels : [];
  const contacts = Array.isArray(gateway.contacts) ? gateway.contacts : [];
  const history = Array.isArray(gateway.history) ? gateway.history : [];
  const sendAllowed = meshcoreGatewayConnected() && meshcoreGatewayTxAllowed() && privateChannels.length > 0;
  const logText = logs.slice(0, 12).map((entry) => {
    if (typeof entry === "string") return entry;
    return `${formatIsoShort(entry.timestamp || entry.event_utc)} ${entry.level || "info"} ${entry.message || entry.text || ""}`.trim();
  }).join("\n");
  const historyMarkup = history.length
    ? history.slice(0, 12).map((entry) => `<article class="message-row ${entry.direction === "received" ? "message-row-inbound" : "message-row-outbound"}"><p class="message-origin">${escapeHtml(entry.source_label || entry.source_id || entry.recipient_prefix || entry.target_id || entry.message_scope || "event")}</p><p class="message-meta">${escapeHtml([entry.network || "meshcore", entry.direction || "unknown", entry.detail || ""].filter(Boolean).join(" · "))}</p><p class="message-body">${escapeHtml(entry.text || entry.detail || "")}</p><time>${escapeHtml(formatIsoShort(entry.event_utc))}</time></article>`).join("")
    : '<article class="message-row message-row-inbound"><p class="message-origin">history</p><p class="message-body">No MeshCore gateway activity recorded yet.</p><time>-</time></article>';
  return {
    eyebrow: "MeshCore Gateway",
    title: "MeshCore gateway operations",
    intro: "This panel owns MeshCore gateway operations, including channels, observed contacts and nodes, runtime/device visibility, and limited test sending through private channels.",
    actions: '<button class="button button-secondary" type="button" id="refreshMeshcoreGatewayButton">Refresh MeshCore gateway</button>',
    bodyClass: "workspace-stack",
    body: `<section class="stats-band panel"><div class="stat-block"><span class="detail-label">Gateway</span><strong>${escapeHtml(gateway.gateway_id || "-")}</strong></div><div class="stat-block"><span class="detail-label">Status</span><strong>${escapeHtml(meshcoreGatewayStatusText())}</strong></div><div class="stat-block"><span class="detail-label">Private Channels</span><strong>${escapeHtml(String(gateway.private_channel_count || 0))}</strong></div><div class="stat-block"><span class="detail-label">Observed Contacts</span><strong>${escapeHtml(String(contacts.length))}</strong></div></section><section class="dual-panels"><section class="panel"><div class="panel-head"><h3>Private Channels</h3><span class="panel-meta">${escapeHtml(String(privateChannels.length))} usable</span></div><div class="table-card"><table class="data-table"><thead><tr><th>Idx</th><th>Name</th><th>Type</th><th>Send</th></tr></thead><tbody>${privateChannels.length ? privateChannels.map((channel) => `<tr><td>${escapeHtml(String(channel.index))}</td><td>${escapeHtml(channel.name || "-")}</td><td>${escapeHtml(formatChannelType(channel))}</td><td>${channel.browser_send_allowed ? "allowed" : "runtime"}</td></tr>`).join("") : '<tr><td colspan="4">No private channels are available for basestation send tests.</td></tr>'}</tbody></table></div></section><section class="panel panel-form"><div class="panel-head"><h3>Send To Private Channel</h3><span class="panel-meta">test path</span></div><form id="meshcoreChannelSendForm" class="form-grid compact-form"><label for="meshcoreChannelSelect">Private channel</label><select id="meshcoreChannelSelect" ${sendAllowed ? "" : "disabled"}>${privateChannels.map((channel, index) => `<option value="${escapeHtml(String(channel.index))}" data-channel-name="${escapeHtml(channel.name || "")}" ${index === 0 ? "selected" : ""}>${escapeHtml((channel.name || `channel-${channel.index}`) + ` · slot ${channel.index}`)}</option>`).join("") || '<option value="">No private channels</option>'}</select><label for="meshcoreChannelMessage">Message</label><textarea id="meshcoreChannelMessage" rows="4" placeholder="Write a MeshCore private-channel test message" ${sendAllowed ? "" : "disabled"}></textarea>${gatewayComposerStatusMarkup(liveState.sendState.meshcore)}<div class="composer-actions"><button class="button button-secondary" type="button" id="reloadMeshcoreGatewayButton">Reload</button><button class="button" type="submit" ${sendAllowed && !liveState.sendState.meshcore?.pending ? "" : "disabled"}>${liveState.sendState.meshcore?.pending ? "Sending..." : "Send"}</button></div></form></section></section><section class="summary-grid"><section class="panel"><div class="panel-head"><h3>Observed Contacts And Nodes</h3><span class="panel-meta">${escapeHtml(String(contacts.length))} recent</span></div>${contacts.length ? contacts.slice(0, 8).map((contact) => `<div class="thread-item"><div><strong>${escapeHtml(contact.name || contact.pub_key_short || "Unknown")}</strong><p>${escapeHtml(contact.pub_key_short || contact.pub_key || "")}</p></div><span>${escapeHtml(contact.out_path_len ? `${contact.out_path_len} hops` : "observed")}</span></div>`).join("") : '<div class="thread-item"><div><strong>No contacts</strong><p>Observed nodes will appear here when available.</p></div><span>-</span></div>'}</section><section class="panel"><div class="panel-head"><h3>Runtime And Device</h3><span class="panel-meta">read only</span></div><div class="detail-grid detail-grid-wide">${metricCard("Interface", runtime.interface_mode || gateway.runtime?.transport || "-")}${metricCard("Service", runtime.service?.SubState || runtime.service?.ActiveState || "-")}${metricCard("Endpoint", gateway.runtime?.link_endpoint || gateway.runtime?.runtime_endpoint || "-")}${metricCard("Node Name", prefs.node_name || "-")}${metricCard("Frequency", prefs.freq_mhz ? `${prefs.freq_mhz} MHz` : "-")}${metricCard("TX Power", valueOrDefault(prefs.tx_power_dbm, "-"))}${metricCard("Battery", device.battery?.battery_percent ? `${device.battery.battery_percent}%` : valueOrDefault(device.battery?.voltage_mv, valueOrDefault(device.battery?.battery_mv, "-")))}${metricCard("Contract", device.runtime?.contract_version || "-")}</div></section></section><section class="dual-panels"><section class="panel"><div class="panel-head"><h3>Recent Mesh Activity</h3><span class="panel-meta">${escapeHtml(String(history.length))} recent</span></div>${historyMarkup}</section><section class="panel"><div class="panel-head"><h3>Gateway Log Tail</h3><span class="panel-meta">${escapeHtml(String(logs.length))} entries</span></div><div class="log-viewer"><pre>${escapeHtml(logText || "No log entries available.")}</pre></div></section></section>`,
    inspectorPrimary: { title: "MeshCore Status", meta: gateway.runtime?.transport || "meshcore", content: `<dl class="inspector-list"><div><dt>Connected</dt><dd>${gateway.connected ? "Yes" : "No"}</dd></div><div><dt>Interface Mode</dt><dd>${escapeHtml(gateway.runtime?.interface_mode || "-")}</dd></div><div><dt>Endpoint</dt><dd>${escapeHtml(gateway.runtime?.link_endpoint || gateway.runtime?.runtime_endpoint || "-")}</dd></div><div><dt>Gateway ID</dt><dd>${escapeHtml(gateway.gateway_id || "-")}</dd></div></dl>` },
    inspectorSecondary: { title: "Boundary", meta: "MeshCore", content: '<ul class="next-list"><li>This panel owns gateway integration, channels, contacts, nodes, and debug visibility.</li><li>Actual node configuration should stay in the native MeshCore UI.</li><li>Basestation send is intentionally limited to private channels here.</li></ul>' },
  };
}

function buildSmsGatewayScreen() {
  if (!liveState.apiAvailable || !liveState.smsGateway?.available) return fallbackScreenContent.smsGateway;
  const gateway = liveState.smsGateway.gateway || {};
  const inbound = Array.isArray(liveState.smsGateway.inbound_events) ? liveState.smsGateway.inbound_events : [];
  const outbound = Array.isArray(liveState.smsGateway.outbound_messages) ? liveState.smsGateway.outbound_messages : [];
  const basestationEvents = Array.isArray(liveState.smsGateway.basestation_events) ? liveState.smsGateway.basestation_events : [];
  const pendingOutbound = Number(gateway.queued_outbound || 0) + Number(gateway.claimed_outbound || 0);
  const statusText = smsGatewayStatusText();
  const phoneLinkText = smsGatewayPhoneLinkText();
  const inboundMarkup = inbound.length
    ? inbound.map((event) => `<article class="message-row message-row-inbound"><p class="message-origin">${escapeHtml(event.sender_label || event.sender_user_id || event.from_number || event.source_endpoint || "unknown sender")}</p><p class="message-meta">${escapeHtml([event.network || "sms", event.target_id ? `to ${event.target_id}` : "inbound"].filter(Boolean).join(" · "))}</p><p class="message-body">${escapeHtml(event.body || event.text || "")}</p><time>${escapeHtml(formatIsoShort(event.observed_at_utc || event.event_ts_utc))}</time></article>`).join("")
    : '<article class="message-row message-row-inbound"><p class="message-origin">inbound</p><p class="message-body">No Android-posted SMS or RCS events recorded in this server process yet.</p><time>-</time></article>';
  const outboundRows = outbound.length
    ? outbound.map((item) => `<tr><td>${escapeHtml(item.to_number || "-")}</td><td>${escapeHtml(item.state || "queued")}</td><td>${escapeHtml(item.content_visibility || "redacted")}</td><td>${escapeHtml(formatIsoShort(item.updated_at || item.claimed_at || item.requested_at))}</td></tr>`).join("")
    : '<tr><td colspan="4">No outbound SMS items recorded yet.</td></tr>';
  const basestationMarkup = basestationEvents.length
    ? basestationEvents.slice().reverse().map((event) => `<div class="thread-item"><div><strong>${escapeHtml((event.network || "sms").toUpperCase())} ${escapeHtml(event.status || event.event_type || "event")}</strong><p>${escapeHtml(event.summary || "")}</p></div><span>${escapeHtml(formatIsoShort(event.timestamp))}</span></div>`).join("")
    : '<div class="thread-item"><div><strong>No basestation SMS events</strong><p>No SMS or RCS events have been recorded by the basestation event store in this process yet.</p></div><span>-</span></div>';
  return {
    eyebrow: "SMS Gateway",
    title: "Android SMS gateway operations",
    intro: "This panel is the authoritative place to test the Android SMS edge. It shows raw inbound posts, outbound queue state, and the basestation actions taken for SMS and RCS traffic.",
    actions: '<button class="button button-secondary" type="button" id="refreshSmsGatewayButton">Refresh SMS gateway</button>',
    bodyClass: "workspace-stack",
    body: `<section class="stats-band panel"><div class="stat-block"><span class="detail-label">Gateway</span><strong>${escapeHtml(gateway.gateway_id || "-")}</strong></div><div class="stat-block"><span class="detail-label">Gateway Status</span><strong>${escapeHtml(statusText)}</strong></div><div class="stat-block"><span class="detail-label">Phone Link</span><strong>${escapeHtml(phoneLinkText)}</strong></div><div class="stat-block"><span class="detail-label">Last Android Seen</span><strong>${escapeHtml(formatRelativeTime(gateway.last_android_seen_at))}</strong></div></section><section class="dual-panels"><section class="panel"><div class="panel-head"><h3>Phone Link And Inbound</h3><span class="panel-meta">live state</span></div><div class="detail-grid detail-grid-wide">${metricCard("Link Status", phoneLinkText)}${metricCard("Gateway State", statusText)}${metricCard("Inbound Seen", String(liveState.smsGateway.inbound_count || 0))}${metricCard("Outbound Pending", String(pendingOutbound))}${metricCard("Last Link Check", formatRelativeTime(gateway.last_link_status_at))}${metricCard("Last Inbound", formatRelativeTime(gateway.last_inbound_received_at))}</div><div class="panel-head"><h3>Raw Inbound From Phone</h3><span class="panel-meta">${escapeHtml(String(inbound.length))} recent</span></div>${inboundMarkup}</section><section class="panel panel-form"><div class="panel-head"><h3>Send Test SMS</h3><span class="panel-meta">queue outbound</span></div><form id="smsSendForm" class="form-grid compact-form"><label for="smsToNumber">To number</label><input id="smsToNumber" type="text" placeholder="+15551234567"><label for="smsBody">Message</label><textarea id="smsBody" rows="4" placeholder="Write an outbound SMS test message"></textarea>${gatewayComposerStatusMarkup(liveState.sendState.sms)}<div class="composer-actions"><button class="button button-secondary" type="button" id="reloadSmsGatewayButton">Reload</button><button class="button" type="submit" ${!liveState.sendState.sms?.pending ? "" : "disabled"}>${liveState.sendState.sms?.pending ? "Queueing..." : "Queue SMS"}</button></div></form></section></section><section class="dual-panels"><section class="panel"><div class="panel-head"><h3>Outbound Queue</h3><span class="panel-meta">${escapeHtml(String(outbound.length))} recent</span></div><div class="table-card"><table class="data-table"><thead><tr><th>To</th><th>State</th><th>Content</th><th>Updated</th></tr></thead><tbody>${outboundRows}</tbody></table></div></section><section class="panel"><div class="panel-head"><h3>Basestation SMS Events</h3><span class="panel-meta">${escapeHtml(String(basestationEvents.length))} recent</span></div>${basestationMarkup}</section></section>`,
    inspectorPrimary: { title: "Gateway Status", meta: gateway.transport || "sms", content: `<dl class="inspector-list"><div><dt>Display Name</dt><dd>${escapeHtml(gateway.display_name || "-")}</dd></div><div><dt>Status</dt><dd>${escapeHtml(statusText)}</dd></div><div><dt>Phone Link</dt><dd>${escapeHtml(phoneLinkText)}</dd></div><div><dt>Queued</dt><dd>${escapeHtml(String(gateway.queued_outbound || 0))}</dd></div><div><dt>Claimed</dt><dd>${escapeHtml(String(gateway.claimed_outbound || 0))}</dd></div><div><dt>Sent</dt><dd>${escapeHtml(String(gateway.sent_outbound || 0))}</dd></div><div><dt>Failed</dt><dd>${escapeHtml(String(gateway.failed_outbound || 0))}</dd></div></dl>` },
    inspectorSecondary: { title: "Test Guidance", meta: "SMS", content: '<ul class="next-list"><li>A phone-posted SMS should appear first under Raw Inbound From Phone.</li><li>The basestation should then record an SMS or RCS event after classification.</li><li>Presence-only `EHN:` tests should usually show raw inbound plus outbound queue activity.</li></ul>' },
  };
}

/*
function buildBasestationScreenLegacy() {
  if (!liveState.apiAvailable && !liveState.directory) return fallbackScreenContent.basestation;
  const runtime = liveState.runtime || {};
  const directory = liveState.directory || buildLocalPreviewDirectoryState();
  const meshGateway = liveState.meshcoreGateway || {};
  const smsGateway = liveState.smsGateway || {};
  const deliveryQueue = liveState.deliveryQueue || { messages: [], route_counts: {}, state_counts: {}, message_count: 0, claimed_message_count: 0, ready_message_count: 0 };
  const logs = Array.isArray(liveState.logs?.lines) ? liveState.logs.lines : (Array.isArray(liveState.logs?.entries) ? liveState.logs.entries : []);
  const logText = logs.slice(0, 20).map((entry) => {
    if (typeof entry === "string") return entry;
    return `${formatIsoShort(entry.timestamp || entry.event_utc)} ${entry.level || "info"} ${entry.message || entry.text || ""}`.trim();
  }).join("\n");
  const routeSummary = Object.entries(deliveryQueue.route_counts || {}).map(([name, count]) => `${name}: ${count}`).join(" · ") || "No delivery routes recorded";
  const stateSummary = Object.entries(deliveryQueue.state_counts || {}).map(([name, count]) => `${name}: ${count}`).join(" · ") || "No delivery states recorded";
  const queueRows = Array.isArray(deliveryQueue.messages) ? deliveryQueue.messages : [];
  const queueMarkup = queueRows.length
    ? `<div class="table-card"><table class="data-table"><thead><tr><th>Target</th><th>Route</th><th>State</th><th>Endpoint</th><th>Updated</th></tr></thead><tbody>${queueRows.map((item) => `<tr><td>${escapeHtml(item.target_user_id || "-")}</td><td>${escapeHtml(item.selected_route || item.target_endpoint_network || "unassigned")}</td><td>${escapeHtml(item.delivery_state || "unknown")}</td><td>${escapeHtml(deliveryEndpointStatusText(item))}</td><td>${escapeHtml(formatIsoShort(item.updated_at || item.created_at))}</td></tr>`).join("")}</tbody></table></div>`
    : '<div class="search-pill">No delivery records are available in this station view.</div>';
  return {
    eyebrow: "Basestation",
    title: "Basestation status and management",
    intro: "This panel owns station-level management: link state, directory storage, gateway registrations, review state, and logs.",
    actions: '<button class="button button-secondary" type="button" id="refreshBasestationButton">Refresh basestation</button>',
    bodyClass: "workspace-stack",
    body: `<section class="stats-band panel"><div class="stat-block"><span class="detail-label">Station</span><strong>${escapeHtml(directory.station_name || runtime.host?.hostname || "windows-basestation")}</strong></div><div class="stat-block"><span class="detail-label">Role</span><strong>${escapeHtml(directory.station_role || "field")}</strong></div><div class="stat-block"><span class="detail-label">Directory Users</span><strong>${escapeHtml(String(directory.user_count || 0))}</strong></div><div class="stat-block"><span class="detail-label">Queued Deliveries</span><strong>${escapeHtml(String(deliveryQueue.message_count || 0))}</strong></div></section><section class="summary-grid"><section class="panel"><div class="panel-head"><h3>Station And Link</h3><span class="panel-meta">live state</span></div><div class="detail-grid detail-grid-wide">${metricCard("Basestation", liveState.apiAvailable ? "Connected" : "Preview")}${metricCard("Session", runtime.service?.ActiveState || "unknown")}${metricCard("Link", runtime.service?.SubState || runtime.interface_mode || "unknown")}${metricCard("Mode", runtime.interface_mode || "-")}${metricCard("Platform", runtime.host?.platform || "-")}${metricCard("Storage Root", runtime.storage_root || "-")}</div></section><section class="panel"><div class="panel-head"><h3>Directory Storage And Review</h3><span class="panel-meta">authority state</span></div><div class="detail-grid detail-grid-wide">${metricCard("Directory Source", directory.source || "-")}${metricCard("Preview Mode", directory.is_preview ? "Yes" : "No")}${metricCard("Pending Review", directory.promotion_counts?.PENDING_AUTHORITY_REVIEW || 0)}${metricCard("Promoted", directory.promotion_counts?.PROMOTED || 0)}${metricCard("Local Records", Array.isArray(directory.users) ? directory.users.filter((entry) => entry?.user?.is_local_to_station === true || entry?.user?.is_local_to_station === "True").length : 0)}${metricCard("Directory Path", directory.database_path || "-")}</div></section><section class="panel"><div class="panel-head"><h3>Gateway Registrations</h3><span class="panel-meta">transport state</span></div><div class="detail-grid detail-grid-wide">${metricCard("MeshCore", meshcoreGatewayStatusText())}${metricCard("SMS", smsGatewayStatusText())}${metricCard("SMS Phone Link", smsGatewayPhoneLinkText())}${metricCard("SMS Pending", String((smsGateway.gateway?.queued_outbound || 0) + (smsGateway.gateway?.claimed_outbound || 0)))}${metricCard("Queued Ready", String(deliveryQueue.ready_message_count || 0))}${metricCard("Last Android Seen", formatRelativeTime(smsGateway.gateway?.last_android_seen_at))}</div></section><section class="panel"><div class="panel-head"><h3>Queued Delivery Overview</h3><span class="panel-meta">redacted</span></div><div class="detail-grid detail-grid-wide">${metricCard("Queue Rows", String(deliveryQueue.message_count || 0))}${metricCard("Claimed", String(deliveryQueue.claimed_message_count || 0))}${metricCard("Endpoint Ready", String(deliveryQueue.ready_message_count || 0))}${metricCard("Routes", routeSummary)}${metricCard("States", stateSummary)}${metricCard("Delivery DB", deliveryQueue.database_path || "-")}</div>${queueMarkup}</section><section class="panel"><div class="panel-head"><h3>Recent Logs</h3><span class="panel-meta">${logs.length} entries</span></div><div class="log-viewer"><pre>${escapeHtml(logText || "No log entries available.")}</pre></div></section></section>`,
    inspectorPrimary: { title: "Station Summary", meta: directory.station_role || "station", content: `<dl class="inspector-list"><div><dt>Station</dt><dd>${escapeHtml(directory.station_name || runtime.host?.hostname || "windows-basestation")}</dd></div><div><dt>Link</dt><dd>${escapeHtml(runtime.service?.SubState || runtime.interface_mode || "-")}</dd></div><div><dt>Directory</dt><dd>${escapeHtml(directory.source || "-")}</dd></div><div><dt>Delivery Records</dt><dd>${escapeHtml(String(deliveryQueue.message_count || 0))}</dd></div></dl>` },
    inspectorSecondary: { title: "Delivery State", meta: "Redacted", content: `<ul class="next-list"><li>${escapeHtml(routeSummary)}</li><li>${escapeHtml(stateSummary)}</li><li>Endpoint status here only means an address was captured for routing, not that the user is connected or currently reachable.</li></ul>` },
  };
}

*/

function buildNodeScreen() {
  if (!liveState.apiAvailable || !liveState.node) return fallbackScreenContent.node;
  const prefs = liveState.node.prefs || {};
  const txPowerDbm = valueOrDefault(prefs.tx_power_dbm, valueOrDefault(prefs.tx_power, ""));
  return {
    eyebrow: "Node",
    title: "Device status and configuration",
    intro: "Node facts are projected from the MeshCore gateway snapshot.",
    actions: '<button class="button button-secondary" type="button" id="refreshNodeButton">Refresh</button>',
    bodyClass: "workspace-body",
    body: `<section class="stats-band panel"><div class="stat-block"><span class="detail-label">Name</span><strong>${escapeHtml(prefs.node_name || prefs.node_long_name || "-")}</strong></div><div class="stat-block"><span class="detail-label">Mode</span><strong>${escapeHtml(liveState.runtime?.interface_mode || "gateway-link")}</strong></div><div class="stat-block"><span class="detail-label">GPS</span><strong>${escapeHtml(describeGpsMode(prefs))}</strong></div><div class="stat-block"><span class="detail-label">Advert</span><strong>${escapeHtml(describeAdvertPolicy(prefs.advert_loc_policy || prefs.advert_loc_poli))}</strong></div></section><section class="dual-panels"><section class="panel"><div class="panel-head"><h3>Radio</h3><span class="panel-meta">projection</span></div><div class="detail-grid detail-grid-wide">${metricCard("Frequency", prefs.freq_mhz ? `${prefs.freq_mhz} MHz` : "-")}${metricCard("Bandwidth", prefs.bw_khz ? `${prefs.bw_khz} kHz` : "-")}${metricCard("SF", valueOrDefault(prefs.sf, "-"))}${metricCard("CR", valueOrDefault(prefs.cr, "-"))}</div></section><section class="panel panel-form"><div class="panel-head"><h3>Write TX Power</h3><span class="panel-meta">live</span></div><form id="nodeTxPowerForm" class="form-grid compact-form"><label>TX Power (dBm)<input id="nodeTxPowerInput" type="number" value="${escapeHtml(String(txPowerDbm))}"></label>${nodeWriteStatusMarkup()}<div class="form-actions"><button class="button" type="submit">Save TX Power</button></div></form></section></section>`,
    inspectorPrimary: { title: "Node Info", meta: "Live", content: `<dl class="inspector-list"><div><dt>Node ID</dt><dd>${escapeHtml(String(liveState.node.node_id || "-"))}</dd></div><div><dt>Channels</dt><dd>${escapeHtml(String((liveState.node.channels || []).filter((channel) => !channel.is_empty).length))}</dd></div></dl>` },
    inspectorSecondary: { title: "Prefs", meta: "Current", content: `<ul class="next-list"><li>Default scope: ${escapeHtml(prefs.default_scope_name || "-")}</li><li>BLE pin: ${escapeHtml(String(valueOrDefault(prefs.ble_pin, "-")))}</li></ul>` },
  };
}

async function refreshBasestationScreen() {
  liveState.basestationRefreshPending = true;
  if (liveState.activeScreen === "basestation") {
    renderScreen("basestation");
  }
  try {
    await loadLiveState();
  } finally {
    liveState.basestationRefreshPending = false;
    if (liveState.activeScreen === "basestation") {
      renderScreen("basestation");
    }
  }
}

function buildDiagnosticsScreen() {
  if (!liveState.apiAvailable || !liveState.runtime || !liveState.device) return fallbackScreenContent.diagnostics;
  const runtime = liveState.runtime;
  const status = runtime.status || {};
  const parsed = status.parsed || {};
  const device = liveState.device;
  const logs = Array.isArray(liveState.logs?.lines) ? liveState.logs.lines : (Array.isArray(liveState.logs?.entries) ? liveState.logs.entries : []);
  const logText = logs.slice(0, 20).map((entry) => {
    if (typeof entry === "string") return entry;
    return `${formatIsoShort(entry.timestamp || entry.event_utc)} ${entry.level || "info"} ${entry.message || entry.text || ""}`.trim();
  }).join("\n");
  return {
    eyebrow: "Diagnostics",
    title: "Device and gateway diagnostics",
    intro: "This screen surfaces runtime and device status without inventing transport state that the gateway does not expose.",
    actions: '<button class="button button-secondary" type="button" id="refreshDeviceButton">Refresh status</button>',
    bodyClass: "workspace-stack",
    body: `<section class="stats-band panel"><div class="stat-block"><span class="detail-label">Service</span><strong>${escapeHtml(runtime.service?.ActiveState || "unknown")}</strong></div><div class="stat-block"><span class="detail-label">SubState</span><strong>${escapeHtml(runtime.service?.SubState || "unknown")}</strong></div><div class="stat-block"><span class="detail-label">Interface</span><strong>${escapeHtml(runtime.interface_mode || "-")}</strong></div><div class="stat-block"><span class="detail-label">Bridge Ready</span><strong>${parsed.bridge_ready === true ? "yes" : "no"}</strong></div></section><section class="dual-panels"><section class="panel"><div class="panel-head"><h3>Runtime Status</h3><span class="panel-meta">live</span></div><div class="detail-grid detail-grid-wide">${metricCard("Host", runtime.host?.hostname || "-")}${metricCard("Platform", runtime.host?.platform || "-")}${metricCard("Transport Path", device.runtime?.endpoint || parsed.link_endpoint || status.path || "-")}${metricCard("Contract", device.runtime?.contract_version || "-")}</div></section><section class="panel"><div class="panel-head"><h3>Log Tail</h3><span class="panel-meta">${logs.length} entries</span></div><div class="log-viewer"><pre>${escapeHtml(logText || "No log entries available.")}</pre></div></section></section>`,
    inspectorPrimary: { title: "Live Device Surface", meta: "Windows", content: '<dl class="inspector-list"><div><dt>Available now</dt><dd>Gateway snapshot, observed nodes, inbound events, outbound status, and device metrics when exposed.</dd></div></dl>' },
    inspectorSecondary: { title: "Architecture", meta: "Persistent link", content: '<ul class="next-list"><li>Windows remains the UI host.</li><li>The gateway contributes transport-facing helper data only.</li></ul>' },
  };
}

function getScreenContent(screenName) {
  if (screenName === "directory") return buildDirectoryScreen();
  if (screenName === "messages") return buildMessagesScreen();
  if (screenName === "meshcoreGateway") return buildMeshcoreGatewayScreen();
  if (screenName === "smsGateway") return buildSmsGatewayScreen();
  if (screenName === "basestation") return buildBasestationScreen();
  if (screenName === "node") return buildNodeScreen();
  if (screenName === "diagnostics") return buildDiagnosticsScreen();
  return fallbackScreenContent[screenName] || fallbackScreenContent.directory;
}

async function fetchJson(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload.error || payload.message || `${path} returned ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

async function sendDirectedPrivateMessage(recipientPublicKey, text) {
  return fetchJson("/api/messages/directed-private", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recipient_public_key: recipientPublicKey, text }),
  });
}

async function sendMeshcorePrivateChannelMessage(channelIndex, channelName, text) {
  return fetchJson("/api/gateways/meshcore/private-channel-send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channel_index: Number(channelIndex), channel_name: channelName, text }),
  });
}

async function sendSmsGatewayMessage(toNumber, body) {
  return fetchJson("/api/gateways/sms/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ to_number: toNumber, body }),
  });
}

async function setNodeTxPower(txPowerDbm) {
  return fetchJson("/api/node/tx-power", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tx_power_dbm: txPowerDbm }),
  });
}

async function saveDirectoryUser(payload) {
  return fetchJson("/api/directory/user", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function deleteDirectoryUser(userId) {
  const encodedUserId = encodeURIComponent(userId);
  let response = await fetch(`/api/directory/user/${encodedUserId}/delete`, {
    method: "POST",
    cache: "no-store",
  });
  if (response.status === 404 || response.status === 405) {
    response = await fetch(`/api/directory/user/${encodedUserId}`, {
      method: "DELETE",
      cache: "no-store",
    });
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || payload.message || `Delete failed with ${response.status}`);
  }
  return payload;
}

async function controlBasestationProcess(processId, action) {
  return fetchJson("/api/basestation/process-control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ process_id: processId, action }),
  });
}

function scrollDirectoryEditorIntoView() {
  const form = document.getElementById("directoryEditorForm");
  if (!form) return;
  form.scrollIntoView({ behavior: "auto", block: "start" });
  const firstInput = form.querySelector("input, select, textarea");
  if (firstInput instanceof HTMLElement) firstInput.focus();
}

function meshcoreGatewayTxAllowed() {
  return liveState.meshcoreGateway?.runtime?.gateway_tx_allowed === true;
}

function meshcoreGatewayConnected() {
  return liveState.meshcoreGateway?.connected === true;
}

function meshcoreGatewayStatusText() {
  if (!meshcoreGatewayConnected()) return liveState.meshcoreGateway?.status || "disconnected";
  if (!meshcoreGatewayTxAllowed()) return "connected, writes disabled";
  return liveState.meshcoreGateway?.status || "connected";
}

function smsGatewayPhoneLinkText() {
  const gateway = liveState.smsGateway?.gateway || {};
  let state = gateway.phone_link_state;
  if (!state) {
    const lastSeen = gateway.last_android_seen_at ? new Date(gateway.last_android_seen_at) : null;
    if (!lastSeen || Number.isNaN(lastSeen.getTime())) {
      state = "never_seen";
    } else {
      const diffSeconds = Math.max(0, Math.round((Date.now() - lastSeen.getTime()) / 1000));
      if (diffSeconds <= 90) state = "connected";
      else if (diffSeconds <= 300) state = "stale";
      else state = "offline";
    }
  }
  if (state === "connected") return "connected";
  if (state === "stale") return "stale";
  if (state === "offline") return "offline";
  if (state === "never_seen") return "not seen yet";
  return gateway.last_android_seen_at ? "seen" : "unknown";
}

function smsGatewayStatusText() {
  const gateway = liveState.smsGateway?.gateway || {};
  let status = gateway.status;
  if (!status || status === "idle") {
    const phoneLinkText = smsGatewayPhoneLinkText();
    const queued = Number(gateway.queued_outbound || 0);
    const claimed = Number(gateway.claimed_outbound || 0);
    const failed = Number(gateway.failed_outbound || 0);
    if (phoneLinkText === "not seen yet") status = queued > 0 ? "waiting_for_phone" : "idle";
    else if ((phoneLinkText === "stale" || phoneLinkText === "offline") && queued > 0) status = "queue_waiting_for_phone";
    else if (phoneLinkText === "stale" || phoneLinkText === "offline") status = "phone_stale";
    else if (claimed > 0) status = "phone_sending";
    else if (queued > 0) status = "queue_pending";
    else if (failed > 0) status = "delivery_failed";
    else if (phoneLinkText === "connected") status = "connected_idle";
  }
  if (status === "waiting_for_phone") return "waiting for phone";
  if (status === "queue_waiting_for_phone") return "queued, waiting for phone";
  if (status === "phone_stale") return "phone stale";
  if (status === "phone_sending") return "phone sending";
  if (status === "queue_pending") return "queue pending";
  if (status === "delivery_failed") return "delivery failed";
  if (status === "connected_idle") return "connected idle";
  return status === "idle" ? "idle" : (status || (liveState.smsGateway?.available ? "ready" : "unknown"));
}

function updateEventStrip() {
  const sourceChip = document.getElementById("eventSourceChip");
  const sessionChip = document.getElementById("eventSessionChip");
  const unreadChip = document.getElementById("eventUnreadChip");
  const syncChip = document.getElementById("eventSyncChip");
  const modeChip = document.getElementById("eventModeChip");
  const processChip = document.getElementById("eventProcessChip");
  const warningChip = document.getElementById("eventWarningChip");
  if (!sourceChip || !sessionChip || !unreadChip || !syncChip || !modeChip || !processChip || !warningChip) return;
  if (!liveState.apiAvailable) {
    sourceChip.textContent = "Preview data";
    sessionChip.textContent = "Session: preview only";
    unreadChip.textContent = "Messages: mock";
    syncChip.textContent = "Last refresh: unavailable";
    modeChip.textContent = "Mode: preview";
    processChip.textContent = "Process: unknown";
    warningChip.textContent = liveState.lastError || "Windows workbench not connected yet";
    return;
  }
  const identity = liveState.workbenchIdentity || {};
  sourceChip.textContent = "Live Windows link";
  sessionChip.textContent = `Session: ${liveState.runtime?.service?.ActiveState || "unknown"}`;
  unreadChip.textContent = `Messages: ${liveState.messages?.message_count || 0}`;
  syncChip.textContent = `Last refresh: ${formatRelativeTime(liveState.lastLoadedAt)}`;
  modeChip.textContent = `Mode: ${liveState.runtime?.interface_mode || "unknown"}`;
  processChip.textContent = `Process: PID ${identity.pid || "?"} @${identity.listen_port || "?"} ${identity.station_role || "unknown"} ${shortModuleName(identity.module_path)}`;
  if (!messageSendAllowed() && liveState.activeScreen === "directory") {
    warningChip.textContent = "Gateway projection is read-only; directory records remain editable";
    return;
  }
  if (meshcoreGatewayConnected() && !meshcoreGatewayTxAllowed()) {
    warningChip.textContent = "Mesh gateway connected, writes disabled";
    return;
  }
  warningChip.textContent = messageSendAllowed() ? "Gateway transmit enabled" : "Gateway panels control transport-specific send";
}

function updateRailSummary() {
  const statusPill = document.getElementById("railStatusPill");
  const basestation = document.getElementById("railBasestationStatus");
  const link = document.getElementById("railLinkStatus");
  const directory = document.getElementById("railDirectoryStatus");
  const mainDb = document.getElementById("railMainDbStatus");
  const meshcore = document.getElementById("railMeshcoreStatus");
  const sms = document.getElementById("railSmsStatus");
  if (!statusPill || !basestation || !link || !directory || !mainDb || !meshcore || !sms) return;
  if (!liveState.apiAvailable) {
    statusPill.textContent = "Preview only";
    basestation.textContent = "Offline preview";
    link.textContent = "Unavailable";
    directory.textContent = "Preview cache";
    mainDb.textContent = liveState.directory?.database_path || "Not connected";
    meshcore.textContent = "Unknown";
    sms.textContent = "Unknown";
    return;
  }
  statusPill.textContent = liveState.runtime?.service?.active ? "Live linked" : "Degraded";
  basestation.textContent = liveState.runtime?.host?.hostname || liveState.directory?.station_name || "Windows basestation";
  link.textContent = liveState.runtime?.service?.SubState || liveState.runtime?.interface_mode || "unknown";
  directory.textContent = `${liveState.directory?.user_count || 0} users · ${liveState.directory?.source || "unknown source"}`;
  mainDb.textContent = liveState.directory?.database_path || "Not reported";
  meshcore.textContent = meshcoreGatewayStatusText();
  sms.textContent = smsGatewayStatusText();
}

function renderRailDetails(screen) {
  const container = document.getElementById("railDetailStack");
  if (!container || !screen) return;
  container.innerHTML = [screen.inspectorPrimary, screen.inspectorSecondary]
    .filter(Boolean)
    .map((config) => `<section class="inspector-panel"><div class="panel-head"><h3>${escapeHtml(config.title)}</h3>${config.meta ? `<span class="panel-meta">${escapeHtml(config.meta)}</span>` : ""}</div>${config.content}</section>`)
    .join("");
}

function attachScreenHandlers(screenName) {
  if (screenName === "messages") {
    [document.getElementById("refreshMessagesButton")].filter(Boolean).forEach((button) => {
      button.addEventListener("click", () => loadLiveState());
    });
  }
  if (screenName === "basestation") {
    const button = document.getElementById("refreshBasestationButton");
    if (button) button.addEventListener("click", () => refreshBasestationScreen());
    document.querySelectorAll("[data-basestation-process-action]").forEach((element) => {
      element.addEventListener("click", async () => {
        const processId = element.getAttribute("data-basestation-process-id") || "";
        const action = element.getAttribute("data-basestation-process-action") || "";
        if (!processId || !action) return;
        liveState.basestationProcessState = {
          pendingProcessId: processId,
          message: `${action === "start" ? "Starting" : "Stopping"} ${processId}...`,
          level: "info",
        };
        renderScreen("basestation");
        try {
          const response = await controlBasestationProcess(processId, action);
          liveState.basestationProcessState = {
            pendingProcessId: null,
            message: response.message || `${processId} ${action} completed.`,
            level: "success",
          };
          await loadLiveState();
          renderScreen("basestation");
        } catch (error) {
          liveState.basestationProcessState = {
            pendingProcessId: null,
            message: error && error.message ? error.message : String(error),
            level: "error",
          };
          renderScreen("basestation");
        }
      });
    });
  }
  if (screenName === "meshcoreGateway") {
    [document.getElementById("refreshMeshcoreGatewayButton"), document.getElementById("reloadMeshcoreGatewayButton")].filter(Boolean).forEach((button) => {
      button.addEventListener("click", () => loadLiveState());
    });
    const channelField = document.getElementById("meshcoreChannelSelect");
    if (channelField) {
      if (liveState.meshcoreComposer.channelIndex) {
        channelField.value = liveState.meshcoreComposer.channelIndex;
      }
      if (!channelField.value && channelField.options.length) {
        channelField.selectedIndex = 0;
        liveState.meshcoreComposer.channelIndex = channelField.value;
      }
      channelField.addEventListener("change", () => {
        liveState.meshcoreComposer.channelIndex = channelField.value;
      });
    }
    const messageField = document.getElementById("meshcoreChannelMessage");
    if (messageField) {
      messageField.value = liveState.meshcoreComposer.body || "";
      messageField.addEventListener("input", () => {
        liveState.meshcoreComposer.body = messageField.value;
      });
    }
    const form = document.getElementById("meshcoreChannelSendForm");
    if (form) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!meshcoreGatewayTxAllowed()) {
          liveState.sendState.meshcore = { pending: false, level: "error", message: "MeshCore transmit is disabled for this gateway." };
          renderScreen("meshcoreGateway");
          return;
        }
        const channelIndex = channelField ? channelField.value.trim() : "";
        const channelName = channelField && channelField.selectedOptions.length ? channelField.selectedOptions[0].dataset.channelName || "" : "";
        const text = messageField ? messageField.value.trim() : "";
        liveState.meshcoreComposer.channelIndex = channelField ? channelField.value : "";
        liveState.meshcoreComposer.body = messageField ? messageField.value : "";
        if (!channelIndex || !text) {
          liveState.sendState.meshcore = { pending: false, level: "error", message: "Private channel and message text are required." };
          renderScreen("meshcoreGateway");
          return;
        }
        liveState.sendState.meshcore = { pending: true, level: "info", message: "Sending MeshCore private-channel message..." };
        renderScreen("meshcoreGateway");
        try {
          const response = await sendMeshcorePrivateChannelMessage(channelIndex, channelName, text);
          const acceptedMessage = response.message || "MeshCore private-channel message accepted by gateway link.";
          liveState.sendState.meshcore = { pending: false, level: "success", message: `${acceptedMessage} RF delivery is not independently confirmed here.` };
          liveState.meshcoreComposer.channelIndex = channelField ? channelField.value : "";
          liveState.meshcoreComposer.body = "";
          await loadLiveState();
          renderScreen("meshcoreGateway");
        } catch (error) {
          liveState.sendState.meshcore = { pending: false, level: "error", message: error && error.message ? error.message : String(error) };
          renderScreen("meshcoreGateway");
        }
      });
    }
  }
  if (screenName === "channels") {
    const button = document.getElementById("refreshChannelsButton");
    if (button) button.addEventListener("click", () => loadLiveState());
  }
  if (screenName === "contacts") {
    const button = document.getElementById("refreshContactsButton");
    if (button) button.addEventListener("click", () => loadLiveState());
  }
  if (screenName === "node") {
    const refreshButton = document.getElementById("refreshNodeButton");
    if (refreshButton) refreshButton.addEventListener("click", () => loadLiveState());
    const form = document.getElementById("nodeTxPowerForm");
    if (form) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const input = document.getElementById("nodeTxPowerInput");
        const txPowerDbm = input ? Number(input.value) : NaN;
        if (!Number.isInteger(txPowerDbm)) {
          liveState.nodeWriteState = { pending: false, level: "error", message: "TX power must be an integer dBm value." };
          renderScreen("node");
          return;
        }
        liveState.nodeWriteState = { pending: true, level: "info", message: "Saving TX power..." };
        renderScreen("node");
        try {
          const response = await setNodeTxPower(txPowerDbm);
          liveState.nodeWriteState = { pending: false, level: "success", message: response.message || "TX power updated." };
          await loadLiveState();
          renderScreen("node");
        } catch (error) {
          liveState.nodeWriteState = { pending: false, level: "error", message: error && error.message ? error.message : String(error) };
          renderScreen("node");
        }
      });
    }
  }
  if (screenName === "diagnostics") {
    const button = document.getElementById("refreshDeviceButton");
    if (button) button.addEventListener("click", () => loadLiveState());
  }
  if (screenName === "smsGateway") {
    [document.getElementById("refreshSmsGatewayButton"), document.getElementById("reloadSmsGatewayButton")].filter(Boolean).forEach((button) => {
      button.addEventListener("click", () => loadLiveState());
    });
    const numberField = document.getElementById("smsToNumber");
    if (numberField) {
      numberField.value = liveState.smsComposer.toNumber || "";
      numberField.addEventListener("input", () => {
        liveState.smsComposer.toNumber = numberField.value;
      });
    }
    const bodyField = document.getElementById("smsBody");
    if (bodyField) {
      bodyField.value = liveState.smsComposer.body || "";
      bodyField.addEventListener("input", () => {
        liveState.smsComposer.body = bodyField.value;
      });
    }
    const form = document.getElementById("smsSendForm");
    if (form) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const toNumber = numberField ? numberField.value.trim() : "";
        const body = bodyField ? bodyField.value.trim() : "";
        liveState.smsComposer.toNumber = numberField ? numberField.value : "";
        liveState.smsComposer.body = bodyField ? bodyField.value : "";
        if (!toNumber || !body) {
          liveState.sendState.sms = { pending: false, level: "error", message: "Destination number and message text are required." };
          renderScreen("smsGateway");
          return;
        }
        liveState.sendState.sms = { pending: true, level: "info", message: "Queueing outbound SMS..." };
        renderScreen("smsGateway");
        try {
          const response = await sendSmsGatewayMessage(toNumber, body);
          liveState.sendState.sms = { pending: false, level: "success", message: response.message || "Outbound SMS queued." };
          liveState.smsComposer.toNumber = "";
          liveState.smsComposer.body = "";
          await loadLiveState();
          renderScreen("smsGateway");
        } catch (error) {
          liveState.sendState.sms = { pending: false, level: "error", message: error && error.message ? error.message : String(error) };
          renderScreen("smsGateway");
        }
      });
    }
  }
  if (screenName === "directory") {
    const button = document.getElementById("refreshDirectoryButton");
    if (button) button.addEventListener("click", () => loadLiveState());
    const editButton = document.getElementById("editDirectoryButton");
    if (editButton) {
      editButton.addEventListener("click", () => {
        liveState.directoryUi.formMode = "edit";
        liveState.directoryUi.message = "";
        renderScreen("directory");
        scrollDirectoryEditorIntoView();
      });
    }
    const addButton = document.getElementById("addDirectoryButton");
    if (addButton) {
      addButton.addEventListener("click", () => {
        liveState.directoryUi.formMode = "add";
        liveState.directoryUi.message = "";
        renderScreen("directory");
        scrollDirectoryEditorIntoView();
      });
    }
    const deleteButton = document.getElementById("deleteDirectoryButton");
    if (deleteButton) {
      deleteButton.addEventListener("click", async () => {
        const selected = getSelectedDirectoryUser();
        const userId = selected?.user?.user_id || "";
        if (!userId || liveState.directory?.is_preview) {
          liveState.directoryUi.message = "Select a saved basestation user before deleting.";
          renderScreen("directory");
          return;
        }
        const displayName = selected?.user?.display_name || selected?.identity?.display_name || userId;
        if (!window.confirm(`Delete ${displayName} from the basestation directory database?`)) {
          return;
        }
        liveState.directoryUi.message = `Deleting ${displayName}...`;
        renderScreen("directory");
        try {
          const response = await deleteDirectoryUser(userId);
          liveState.directoryUi.formMode = "view";
          liveState.directoryUi.selectedUserId = null;
          await loadLiveState();
          liveState.directoryUi.message = response.message || "Directory user deleted.";
          renderScreen("directory");
        } catch (error) {
          liveState.directoryUi.message = error && error.message ? error.message : String(error);
          renderScreen("directory");
        }
      });
    }
    document.querySelectorAll("[data-directory-select]").forEach((element) => {
      element.addEventListener("click", () => {
        liveState.directoryUi.selectedUserId = element.getAttribute("data-directory-select");
        liveState.directoryUi.formMode = "view";
        liveState.directoryUi.message = "";
        renderScreen("directory");
      });
    });
    const cancelButton = document.getElementById("directoryCancelButton");
    if (cancelButton) {
      cancelButton.addEventListener("click", () => {
        liveState.directoryUi.formMode = "view";
        liveState.directoryUi.message = "Edit cancelled.";
        renderScreen("directory");
      });
    }
    const form = document.getElementById("directoryEditorForm");
    if (form) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const formData = new FormData(form);
        const formMode = liveState.directoryUi.formMode;
        const selected = formMode === "edit" ? getSelectedDirectoryUser() : null;
        const displayName = String(formData.get("display_name") || "").trim() || "Unnamed user";
        const homeRegion = String(formData.get("home_region") || "").trim();
        const generalStatus = String(formData.get("general_status") || "active").trim() || "active";
        const promotionStatus = formMode === "add" ? "PENDING_AUTHORITY_REVIEW" : (selected?.user?.promotion_status || "LOCAL_ONLY");
        const endpointPayloads = DIRECTORY_ENDPOINT_PRESETS.map((preset) => {
          const network = preset.network;
          const nativeAddress = String(formData.get(`endpoint_address_${network}`) || "").trim();
          if (!nativeAddress) return null;
          const existingEndpoint = (selected?.endpoints || []).find((endpoint) => String(endpoint.network || "").toUpperCase() === network);
          const priority = Number(formData.get(`endpoint_priority_${network}`) || preset.defaultPriority);
          const verified = formData.get(`endpoint_verified_${network}`) !== null;
          const enabled = formData.get(`endpoint_enabled_${network}`) !== null;
          return {
            endpoint_id: String(formData.get(`endpoint_id_${network}`) || existingEndpoint?.endpoint_id || "").trim() || undefined,
            network,
            native_address: nativeAddress,
            verified,
            enabled,
            priority: Number.isFinite(priority) ? priority : preset.defaultPriority,
            trust_level: String(formData.get(`endpoint_trust_${network}`) || existingEndpoint?.trust_level || "draft").trim() || "draft",
            replication_class: String(formData.get(`endpoint_replication_${network}`) || existingEndpoint?.replication_class || "TRUSTED_STATION").trim() || "TRUSTED_STATION",
            last_verified_at: verified ? (existingEndpoint?.last_verified_at || new Date().toISOString()) : "",
            capability_tags: String(formData.get(`endpoint_capabilities_${network}`) || existingEndpoint?.capability_tags || preset.capabilities).trim(),
            metadata: {},
          };
        }).filter(Boolean);
        const primaryEndpoint = endpointPayloads[0] || null;
        const profile = {
          personal: {
            display_name: displayName,
            given_name: String(formData.get("given_name") || "").trim(),
            family_name: String(formData.get("family_name") || "").trim(),
            email: String(formData.get("email") || "").trim(),
            phone_primary: String(formData.get("phone_primary") || "").trim(),
            home_region: homeRegion,
            home_location_text: String(formData.get("home_location_text") || "").trim(),
            lat: null,
            lon: null,
            member_status: String(formData.get("member_status") || "").trim(),
            general_status: generalStatus,
            last_active_at: new Date().toISOString(),
          },
          identity: {
            primary_identity_label: String(formData.get("primary_identity_label") || "").trim(),
            identity_type: String(formData.get("identity_type") || "USER").trim(),
            messaging_mode: String(formData.get("messaging_mode") || "BOTH").trim(),
            policy_profile: String(formData.get("policy_profile") || "").trim(),
            rcs_name: String(formData.get("rcs_name") || "").trim(),
            routing_notes: formMode === "add" ? "draft authority user" : "edited in browser demo",
          },
          teams: { teams: [], roles: [], active_group_member: false },
          presence: {
            current_best_route: primaryEndpoint?.network || "No route",
            last_seen_by_network: Object.fromEntries(endpointPayloads.map((endpoint) => [endpoint.network, new Date().toISOString()])),
            presence_state: endpointPayloads.length ? "Configured" : "No current observations",
          },
          review: {
            record_origin: liveState.directory?.station_name || "windows-authority",
            replication_class: primaryEndpoint?.replication_class || "FIELD_OPERATIONAL",
            promotion_status: promotionStatus,
            conflict_flag: false,
          },
          notes: {
            operator_notes: String(formData.get("operator_notes") || "").trim(),
          },
        };
        const payload = {
          user: {
            user_id: formMode === "edit" ? selected?.user?.user_id : undefined,
            display_name: displayName,
            status: generalStatus,
            home_region: homeRegion,
            is_local_to_station: formMode === "edit" ? selected?.user?.is_local_to_station : false,
            retention_class: formMode === "edit" ? (selected?.user?.retention_class || "TRUSTED_STATION") : "TRUSTED_STATION",
            promotion_status: promotionStatus,
          },
          identity: {
            identity_id: formMode === "edit" ? selected?.identity?.identity_id : undefined,
            display_name: profile.identity.primary_identity_label,
            identity_type: profile.identity.identity_type,
            messaging_mode: profile.identity.messaging_mode,
            policy_id: profile.identity.policy_profile,
          },
          profile,
          endpoints: endpointPayloads,
        };
        try {
          const response = await saveDirectoryUser(payload);
          const savedEntry = response.entry;
          liveState.directoryUi.selectedUserId = savedEntry?.user?.user_id || liveState.directoryUi.selectedUserId;
          liveState.directoryUi.message = response.message || "Directory user saved.";
          liveState.directoryUi.formMode = "view";
          upsertDirectoryEntry(savedEntry);
          try {
            await loadLiveState();
          } catch (error) {
            const refreshMessage = error && error.message ? error.message : String(error);
            liveState.directoryUi.message = `${response.message || "Directory user saved."} Refresh warning: ${refreshMessage}`;
            renderScreen("directory");
          }
        } catch (error) {
          liveState.directoryUi.message = error && error.message ? error.message : String(error);
          renderScreen("directory");
        }
      });
    }
  }
}

function settledValue(result) {
  return result && result.status === "fulfilled" ? result.value : null;
}

function settledReason(result, label) {
  if (!result || result.status === "fulfilled") return null;
  const reason = result.reason && result.reason.message ? result.reason.message : String(result.reason);
  return `${label}: ${reason}`;
}

async function loadLiveState() {
  const [runtimeResult, nodeResult, contactsResult, messagesResult, meshcoreGatewayResult, smsGatewayResult, deliveryQueueResult, logsResult, deviceResult, directoryResult, basestationProcessesResult, workbenchIdentityResult] = await Promise.allSettled([
    fetchJson("/api/runtime"),
    fetchJson("/api/node"),
    fetchJson("/api/contacts"),
    fetchJson("/api/messages"),
    fetchJson("/api/gateways/meshcore"),
    fetchJson("/api/gateways/sms"),
    fetchJson("/api/delivery/queue"),
    fetchJson("/api/logs?limit=20"),
    fetchJson("/api/device"),
    fetchJson("/api/directory"),
    fetchJson("/api/basestation/processes"),
    fetchJson("/api/workbench/identity"),
  ]);

  liveState.runtime = settledValue(runtimeResult);
  liveState.node = settledValue(nodeResult);
  liveState.contacts = settledValue(contactsResult);
  liveState.messages = settledValue(messagesResult);
  liveState.meshcoreGateway = settledValue(meshcoreGatewayResult);
  liveState.smsGateway = settledValue(smsGatewayResult);
  liveState.deliveryQueue = settledValue(deliveryQueueResult);
  liveState.logs = settledValue(logsResult);
  liveState.device = settledValue(deviceResult);
  liveState.basestationProcesses = settledValue(basestationProcessesResult);
  liveState.workbenchIdentity = settledValue(workbenchIdentityResult);

  const directory = settledValue(directoryResult);
  if (directory) {
    liveState.directory = directory;
    if (!liveState.directoryUi.selectedUserId && Array.isArray(directory.users) && directory.users.length) {
      liveState.directoryUi.selectedUserId = directory.users[0]?.user?.user_id || null;
    }
  } else if (!liveState.directory) {
    liveState.directory = buildLocalPreviewDirectoryState();
  }

  const errors = [
    settledReason(runtimeResult, "/api/runtime"),
    settledReason(nodeResult, "/api/node"),
    settledReason(contactsResult, "/api/contacts"),
    settledReason(messagesResult, "/api/messages"),
    settledReason(meshcoreGatewayResult, "/api/gateways/meshcore"),
    settledReason(smsGatewayResult, "/api/gateways/sms"),
    settledReason(deliveryQueueResult, "/api/delivery/queue"),
    settledReason(logsResult, "/api/logs"),
    settledReason(deviceResult, "/api/device"),
    settledReason(directoryResult, "/api/directory"),
    settledReason(basestationProcessesResult, "/api/basestation/processes"),
    settledReason(workbenchIdentityResult, "/api/workbench/identity"),
  ].filter(Boolean);

  liveState.apiAvailable = Boolean(
    liveState.runtime ||
    liveState.node ||
    liveState.contacts ||
    liveState.messages ||
    liveState.meshcoreGateway ||
    liveState.smsGateway ||
    liveState.deliveryQueue ||
    liveState.logs ||
    liveState.device ||
    liveState.basestationProcesses ||
    liveState.workbenchIdentity ||
    directory
  );
  if (liveState.apiAvailable) {
    liveState.lastLoadedAt = new Date().toISOString();
  }
  liveState.lastError = errors.length ? errors.join(" | ") : null;

  updateEventStrip();
  updateRailSummary();
  if (liveState.activeScreen === "directory" && liveState.directoryUi.formMode !== "view") {
    return;
  }
  renderScreen(liveState.activeScreen);
}

function renderScreen(screenName) {
  liveState.activeScreen = screenName;
  const screen = getScreenContent(screenName);
  if (!screen) return;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("nav-item-active", button.dataset.screen === screenName);
  });
  document.getElementById("workspaceEyebrow").textContent = screen.eyebrow;
  document.getElementById("workspaceTitle").textContent = screen.title;
  document.getElementById("workspaceIntro").textContent = screen.intro;
  document.getElementById("workspaceActions").innerHTML = screen.actions;
  const body = document.getElementById("workspaceBody");
  body.className = screen.bodyClass;
  body.innerHTML = screen.body;
  renderRailDetails(screen);
  updateRailSummary();
  attachScreenHandlers(screenName);
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => renderScreen(button.dataset.screen));
});

renderScreen("messages");
updateEventStrip();
updateRailSummary();
loadLiveState();
window.setInterval(() => {
  if (!document.hidden) loadLiveState();
}, LIVE_REFRESH_MILLIS);