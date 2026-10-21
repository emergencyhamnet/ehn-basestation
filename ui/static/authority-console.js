const state = {
  users: [],
  selectedUserId: null,
  delivery: {
    messages: [],
    routeCounts: {},
    stateCounts: {},
    messageCount: 0,
    claimedMessageCount: 0,
  },
};

const ENDPOINT_NETWORKS = ["MESHCORE", "SMS", "RCS", "EMAIL", "APRS", "MESHTASTIC"];

function supportsRcsVisibleName(network) {
  return String(network || "").toUpperCase() === "SMS";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  if (!response.ok) {
    let details = "";
    try {
      const payload = await response.json();
      details = payload.error || payload.message || "";
    } catch {
      details = "";
    }
    throw new Error(details ? `${details} (${response.status})` : `${url} failed with ${response.status}`);
  }
  return response.json();
}

async function parseJsonResponse(response, fallbackMessage) {
  if (!response.ok) {
    let details = "";
    try {
      const payload = await response.json();
      details = payload.error || payload.message || "";
    } catch {
      details = "";
    }
    throw new Error(details ? `${details} (${response.status})` : fallbackMessage || `Request failed with ${response.status}`);
  }
  return response.json();
}

async function loadOptionalJson(url, fallbackValue, options = {}) {
  try {
    return await loadJson(url, options);
  } catch (error) {
    return {
      ...fallbackValue,
      ok: false,
      unavailable: true,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function cloneRecord(record) {
  return JSON.parse(JSON.stringify(record));
}

function buildBlankRecord() {
  return {
    user: {
      user_id: "",
      display_name: "",
      status: "active",
      home_region: "",
      is_local_to_station: false,
      retention_class: "TRUSTED_STATION",
      promotion_status: "PENDING_AUTHORITY_REVIEW",
    },
    identity: {
      identity_id: "",
      display_name: "",
      identity_type: "USER",
      messaging_mode: "BOTH",
      policy_id: "",
      status: "active",
    },
    profile: {
      personal: {
        given_name: "",
        family_name: "",
        email: "",
        phone_primary: "",
        home_location_text: "",
        member_status: "",
      },
      identity: {
        routing_notes: "",
        rcs_name: "",
      },
      teams: {
        teams: [],
        roles: [],
        active_group_member: false,
      },
      notes: {
        operator_notes: "",
      },
    },
    endpoints: [],
  };
}

function selectedRecord() {
  const record = state.users.find((item) => item.user && item.user.user_id === state.selectedUserId);
  return record ? cloneRecord(record) : buildBlankRecord();
}

function setEditorStatus(message) {
  document.getElementById("editorStatus").textContent = message;
}

function mainStandingLabel(value) {
  if (value === "PROMOTED" || value === "MERGED") return "Central";
  if (value === "PENDING_AUTHORITY_REVIEW") return "Pending Main";
  if (value === "LOCAL_ONLY") return "Not submitted";
  return String(value || "Unknown").replace(/_/g, " ");
}

function syncDeleteButton(userId) {
  const button = document.getElementById("deleteUserButton");
  if (!button) {
    return;
  }
  button.disabled = !userId;
}

function renderSummary(payload) {
  const root = document.getElementById("summaryGrid");
  const authority = payload.authority || {};
  const counts = authority.promotion_counts || {};
  const items = [
    ["Station", authority.station_name || "-"],
    ["Role", authority.station_role || "-"],
    ["Users", String(authority.user_count || 0)],
    ["Audit events", String(authority.audit_event_count || 0)],
    ["DB path", authority.database_path || "-"],
    ["Main standing", Object.entries(counts).map(([key, value]) => `${mainStandingLabel(key)}: ${value}`).join(", ") || "-"],
  ];
  root.innerHTML = items.map(([label, value]) => `<div class="summary-item"><strong>${escapeHtml(label)}</strong><div class="meta">${escapeHtml(value)}</div></div>`).join("");
}

function endpointRowMarkup(endpoint, index, rcsName = "") {
  const rcsVisibleNameEnabled = supportsRcsVisibleName(endpoint.network);
  return `
    <div class="endpoint-row" data-endpoint-index="${index}">
      <label>
        <span>Network</span>
        <select data-field="network">
          ${ENDPOINT_NETWORKS.map((network) => `<option value="${network}" ${endpoint.network === network ? "selected" : ""}>${network}</option>`).join("")}
        </select>
      </label>
      <label>
        <span>Address</span>
        <input data-field="native_address" type="text" value="${escapeHtml(endpoint.native_address || "")}">
      </label>
      ${rcsVisibleNameEnabled ? `
      <label>
        <span>RCS Visible Name</span>
        <input data-field="rcs_name" type="text" value="${escapeHtml(rcsName)}">
      </label>
      ` : ""}
      <label>
        <span>Priority</span>
        <input data-field="priority" type="number" value="${escapeHtml(String(endpoint.priority ?? 100))}">
      </label>
      <label>
        <span>Capability Tags</span>
        <input data-field="capability_tags" type="text" value="${escapeHtml(endpoint.capability_tags || "")}">
      </label>
      <label>
        <span>Trust Level</span>
        <input data-field="trust_level" type="text" value="${escapeHtml(endpoint.trust_level || "draft")}">
      </label>
      <label>
        <span>Replication Class</span>
        <input data-field="replication_class" type="text" value="${escapeHtml(endpoint.replication_class || "TRUSTED_STATION")}">
      </label>
      <label class="checkbox-label">
        <input data-field="enabled" type="checkbox" ${endpoint.enabled === "False" || endpoint.enabled === false ? "" : "checked"}>
        <span>Enabled for outbound</span>
      </label>
      <label class="checkbox-label">
        <input data-field="verified" type="checkbox" ${endpoint.verified === "True" || endpoint.verified === true ? "checked" : ""}>
        <span>Verified (also enables)</span>
      </label>
      <button type="button" class="secondary-button" data-action="remove-endpoint">Remove</button>
    </div>
  `;
}

function renderUsers() {
  const root = document.getElementById("usersList");
  if (!state.users.length) {
    root.innerHTML = '<div class="empty">No members yet.</div>';
    return;
  }
  root.innerHTML = state.users.map((row) => {
    const user = row.user || {};
    const identity = row.identity || {};
    const userId = user.user_id || "";
    const selectedClass = userId === state.selectedUserId ? " is-selected" : "";
    const networks = Array.isArray(row.endpoints)
      ? row.endpoints.map((endpoint) => endpoint.network).filter(Boolean).join(", ")
      : "";
    return `<div class="list-item user-card${selectedClass}" data-user-id="${escapeHtml(userId)}"><div class="user-actions"><div><strong>${escapeHtml(user.display_name || identity.display_name || "Unnamed user")}</strong><div class="meta">${escapeHtml(identity.display_name || "")}</div><div class="meta">${escapeHtml(user.promotion_status || "")} • ${escapeHtml(networks || "no endpoints")}</div></div><button type="button" class="secondary-button" data-action="edit-user" data-user-id="${escapeHtml(userId)}">Edit</button></div></div>`;
  }).join("");
}

function renderAudit(payload) {
  const root = document.getElementById("auditList");
  const events = Array.isArray(payload.audit_events) ? payload.audit_events : [];
  if (!events.length) {
    root.innerHTML = '<div class="empty">No audit activity yet.</div>';
    return;
  }
  root.innerHTML = events.map((event) => `<div class="list-item"><strong>${escapeHtml(event.action)}</strong><div class="meta">${escapeHtml(event.event_ts)}</div><div class="meta">${escapeHtml(event.actor)} • ${escapeHtml(event.subject_type)} ${escapeHtml(event.subject_id)}</div><div class="meta">${escapeHtml(event.summary || "")}</div></div>`).join("");
}

function renderDelivery(payload) {
  const summaryRoot = document.getElementById("deliverySummary");
  const listRoot = document.getElementById("deliveryList");
  const statusRoot = document.getElementById("deliveryStatus");
  if (payload && payload.unavailable) {
    summaryRoot.innerHTML = '<div class="delivery-pill"><strong>Delivery Queue</strong><div class="meta">Unavailable</div></div>';
    listRoot.innerHTML = '<div class="empty">Delivery queue API is unavailable on this authority server.</div>';
    statusRoot.textContent = payload.error || "Delivery queue API is unavailable on this authority server.";
    return;
  }
  const routeCounts = payload.route_counts || {};
  const stateCounts = payload.state_counts || {};
  const summaryItems = [
    ["Queued records", String(payload.message_count || 0)],
    ["Claimed now", String(payload.claimed_message_count || 0)],
    ["Routes", Object.entries(routeCounts).map(([key, value]) => `${key}: ${value}`).join(", ") || "-"],
    ["States", Object.entries(stateCounts).map(([key, value]) => `${key}: ${value}`).join(", ") || "-"],
  ];
  summaryRoot.innerHTML = summaryItems.map(([label, value]) => `<div class="delivery-pill"><strong>${escapeHtml(label)}</strong><div class="meta">${escapeHtml(value)}</div></div>`).join("");

  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  if (!messages.length) {
    listRoot.innerHTML = '<div class="empty">No queued delivery work.</div>';
    statusRoot.textContent = "Redacted queue state for operators.";
    return;
  }
  listRoot.innerHTML = messages.map((message) => {
    const route = message.selected_route || "unassigned";
    const stateLabel = message.delivery_state || "unknown";
    const target = message.target_user_id || "unbound";
    const claim = message.claim_station_id ? `claimed by ${message.claim_station_id}` : "unclaimed";
    const nextAttempt = message.next_attempt_after ? `retry after ${message.next_attempt_after}` : "ready now";
    const endpoint = message.has_target_endpoint ? (message.target_endpoint_network || "route-bound") : "no endpoint captured";
    return `<div class="list-item"><strong>${escapeHtml(target)}</strong><div class="meta">${escapeHtml(route)} • ${escapeHtml(stateLabel)} • ${escapeHtml(claim)}</div><div class="meta">${escapeHtml(endpoint)} • ${escapeHtml(nextAttempt)}</div><div class="meta">accepted ${escapeHtml(message.accepted_station_id || "-")} • owner ${escapeHtml(message.owning_station_id || "-")}</div></div>`;
  }).join("");
  statusRoot.textContent = `${messages.length} redacted queue entries loaded.`;
}

function renderEditor(record) {
  const user = record.user || {};
  const identity = record.identity || {};
  const personal = (record.profile && record.profile.personal) || {};
  const identityProfile = (record.profile && record.profile.identity) || {};
  const notes = (record.profile && record.profile.notes) || {};
  document.getElementById("editorTitle").textContent = user.user_id ? `Edit ${user.display_name || identity.display_name || "Member"}` : "Add Member";
  document.getElementById("userIdInput").value = user.user_id || "";
  document.getElementById("identityIdInput").value = identity.identity_id || "";
  document.getElementById("displayNameInput").value = user.display_name || personal.display_name || "";
  document.getElementById("identityDisplayNameInput").value = identity.display_name || identityProfile.primary_identity_label || "";
  document.getElementById("statusInput").value = user.status || "active";
  document.getElementById("promotionStatusInput").value = user.promotion_status || "PENDING_AUTHORITY_REVIEW";
  document.getElementById("homeRegionInput").value = user.home_region || personal.home_region || "";
  document.getElementById("messagingModeInput").value = identity.messaging_mode || identityProfile.messaging_mode || "BOTH";
  document.getElementById("emailInput").value = personal.email || "";
  document.getElementById("phoneInput").value = personal.phone_primary || "";
  document.getElementById("givenNameInput").value = personal.given_name || "";
  document.getElementById("familyNameInput").value = personal.family_name || "";
  document.getElementById("locationInput").value = personal.home_location_text || "";
  document.getElementById("routingNotesInput").value = identityProfile.routing_notes || "";
  document.getElementById("operatorNotesInput").value = notes.operator_notes || "";

  const endpoints = Array.isArray(record.endpoints) ? record.endpoints : [];
  document.getElementById("endpointsList").innerHTML = endpoints.length
    ? endpoints.map((endpoint, index) => endpointRowMarkup(endpoint, index, identityProfile.rcs_name || "")).join("")
    : '<div class="empty">No endpoints yet.</div>';
  syncDeleteButton(user.user_id || "");
}

function readRcsVisibleName() {
  const activeInput = document.querySelector('.endpoint-row input[data-field="rcs_name"]:not([disabled])');
  if (activeInput instanceof HTMLInputElement) {
    return activeInput.value.trim();
  }
  const fallbackInput = document.querySelector('.endpoint-row input[data-field="rcs_name"]');
  return fallbackInput instanceof HTMLInputElement ? fallbackInput.value.trim() : "";
}

function syncEndpointRowFields(row) {
  if (!(row instanceof HTMLElement)) {
    return;
  }
  const networkSelect = row.querySelector('select[data-field="network"]');
  if (!(networkSelect instanceof HTMLSelectElement)) {
    return;
  }
  const nextMarkup = endpointRowMarkup({
    network: networkSelect.value,
    native_address: row.querySelector('input[data-field="native_address"]')?.value || "",
    priority: row.querySelector('input[data-field="priority"]')?.value || 100,
    capability_tags: row.querySelector('input[data-field="capability_tags"]')?.value || "",
    trust_level: row.querySelector('input[data-field="trust_level"]')?.value || "draft",
    replication_class: row.querySelector('input[data-field="replication_class"]')?.value || "TRUSTED_STATION",
    enabled: row.querySelector('input[data-field="enabled"]')?.checked !== false,
    verified: row.querySelector('input[data-field="verified"]')?.checked === true,
  }, row.dataset.endpointIndex || "0", readRcsVisibleName());
  row.outerHTML = nextMarkup.trim();
}

function collectEndpoints() {
  const rows = Array.from(document.querySelectorAll(".endpoint-row"));
  return rows.map((row) => ({
    network: row.querySelector('[data-field="network"]').value,
    native_address: row.querySelector('[data-field="native_address"]').value.trim(),
    enabled: row.querySelector('[data-field="enabled"]').checked,
    verified: row.querySelector('[data-field="verified"]').checked,
    priority: Number(row.querySelector('[data-field="priority"]').value || 100),
    trust_level: row.querySelector('[data-field="trust_level"]').value.trim() || (row.querySelector('[data-field="verified"]').checked ? "verified" : "draft"),
    replication_class: row.querySelector('[data-field="replication_class"]').value.trim() || "TRUSTED_STATION",
    capability_tags: row.querySelector('[data-field="capability_tags"]').value.trim(),
  })).filter((endpoint) => endpoint.network && endpoint.native_address);
}

function buildPayloadFromForm() {
  const base = selectedRecord();
  const displayName = document.getElementById("displayNameInput").value.trim();
  const identityDisplayName = document.getElementById("identityDisplayNameInput").value.trim();
  const status = document.getElementById("statusInput").value;
  const promotionStatus = document.getElementById("promotionStatusInput").value;
  const homeRegion = document.getElementById("homeRegionInput").value.trim();
  const messagingMode = document.getElementById("messagingModeInput").value;
  const payload = cloneRecord(base);

  payload.user = {
    ...(payload.user || {}),
    user_id: document.getElementById("userIdInput").value.trim(),
    display_name: displayName,
    status,
    home_region: homeRegion,
    is_local_to_station: false,
    retention_class: payload.user?.retention_class || "TRUSTED_STATION",
    promotion_status: promotionStatus,
  };
  payload.identity = {
    ...(payload.identity || {}),
    identity_id: document.getElementById("identityIdInput").value.trim(),
    display_name: identityDisplayName || displayName,
    identity_type: payload.identity?.identity_type || "USER",
    messaging_mode: messagingMode,
    policy_id: payload.identity?.policy_id || "",
    status,
  };
  payload.profile = {
    ...(payload.profile || {}),
    personal: {
      ...((payload.profile && payload.profile.personal) || {}),
      display_name: displayName,
      given_name: document.getElementById("givenNameInput").value.trim(),
      family_name: document.getElementById("familyNameInput").value.trim(),
      email: document.getElementById("emailInput").value.trim(),
      phone_primary: document.getElementById("phoneInput").value.trim(),
      home_region: homeRegion,
      home_location_text: document.getElementById("locationInput").value.trim(),
      general_status: status,
      member_status: ((payload.profile && payload.profile.personal) || {}).member_status || "",
    },
    identity: {
      ...((payload.profile && payload.profile.identity) || {}),
      primary_identity_label: identityDisplayName || displayName,
      messaging_mode: messagingMode,
      identity_type: payload.identity?.identity_type || "USER",
      routing_notes: document.getElementById("routingNotesInput").value.trim(),
      rcs_name: readRcsVisibleName(),
      policy_profile: payload.identity?.policy_id || "",
    },
    notes: {
      ...((payload.profile && payload.profile.notes) || {}),
      operator_notes: document.getElementById("operatorNotesInput").value.trim(),
    },
    teams: {
      ...((payload.profile && payload.profile.teams) || {}),
      teams: Array.isArray(payload.profile?.teams?.teams) ? payload.profile.teams.teams : [],
      roles: Array.isArray(payload.profile?.teams?.roles) ? payload.profile.teams.roles : [],
      active_group_member: Boolean(payload.profile?.teams?.active_group_member),
    },
  };
  payload.endpoints = collectEndpoints();
  return payload;
}

function startNewUser() {
  state.selectedUserId = null;
  renderUsers();
  renderEditor(buildBlankRecord());
  setEditorStatus("Creating a new member record.");
}

function selectUser(userId) {
  state.selectedUserId = userId;
  renderUsers();
  renderEditor(selectedRecord());
  setEditorStatus(`Editing ${userId}.`);
}

function addEndpointRow() {
  const root = document.getElementById("endpointsList");
  const empty = root.querySelector(".empty");
  if (empty) {
    root.innerHTML = "";
  }
  root.insertAdjacentHTML("beforeend", endpointRowMarkup({
    network: "MESHCORE",
    native_address: "",
    enabled: true,
    verified: false,
    priority: 100,
    trust_level: "draft",
    replication_class: "TRUSTED_STATION",
    capability_tags: "",
  }, root.querySelectorAll(".endpoint-row").length, readRcsVisibleName()));
  syncEndpointRowFields(root.lastElementChild);
}

async function saveUser(event) {
  event.preventDefault();
  const payload = buildPayloadFromForm();
  if (!payload.user.display_name) {
    setEditorStatus("Display name is required.");
    return;
  }
  setEditorStatus("Saving member...");
  const response = await loadJson("/api/authority/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  state.selectedUserId = response.entry?.user?.user_id || payload.user.user_id || null;
  await refreshAuthorityView();
  setEditorStatus(response.message || "Member saved.");
}

async function deleteUser() {
  const record = selectedRecord();
  const userId = record.user && record.user.user_id ? record.user.user_id : "";
  if (!userId) {
    setEditorStatus("Select a saved member before deleting.");
    return;
  }
  const displayName = record.user?.display_name || record.identity?.display_name || userId;
  if (!window.confirm(`Delete ${displayName} completely from the authority database?`)) {
    return;
  }
  setEditorStatus(`Deleting ${displayName}...`);
  const encodedUserId = encodeURIComponent(userId);
  let response = await fetch(`/api/authority/users/${encodedUserId}/delete`, {
    method: "POST",
    cache: "no-store",
  });
  if (response.status === 404 || response.status === 405) {
    response = await fetch(`/api/authority/users/${encodedUserId}`, {
      method: "DELETE",
      cache: "no-store",
    });
  }
  const payload = await parseJsonResponse(response, `Delete request failed for ${displayName}.`);
  state.selectedUserId = null;
  await refreshAuthorityView();
  setEditorStatus(payload.message || "Member deleted.");
}

async function refreshAuthorityView() {
  const [summary, users, audit, delivery] = await Promise.all([
    loadJson(`/api/authority/summary?t=${Date.now()}`),
    loadJson(`/api/authority/users?t=${Date.now()}`),
    loadJson(`/api/authority/audit?limit=25&t=${Date.now()}`),
    loadOptionalJson(`/api/authority/delivery/messages?t=${Date.now()}`, {
      messages: [],
      route_counts: {},
      state_counts: {},
      message_count: 0,
      claimed_message_count: 0,
    }),
  ]);
  state.users = Array.isArray(users.users) ? users.users : [];
  state.delivery = {
    messages: Array.isArray(delivery.messages) ? delivery.messages : [],
    routeCounts: delivery.route_counts || {},
    stateCounts: delivery.state_counts || {},
    messageCount: Number(delivery.message_count || 0),
    claimedMessageCount: Number(delivery.claimed_message_count || 0),
  };
  if (state.selectedUserId && !state.users.some((row) => row.user && row.user.user_id === state.selectedUserId)) {
    state.selectedUserId = null;
  }
  renderSummary(summary);
  renderUsers();
  renderAudit(audit);
  renderDelivery(delivery);
  renderEditor(selectedRecord());
}

document.getElementById("refreshButton").addEventListener("click", () => {
  refreshAuthorityView().then(() => setEditorStatus("Authority data refreshed.")).catch((error) => setEditorStatus(error.message));
});

document.getElementById("newUserButton").addEventListener("click", () => {
  startNewUser();
});

document.getElementById("resetEditorButton").addEventListener("click", () => {
  renderEditor(selectedRecord());
  setEditorStatus(state.selectedUserId ? `Reset changes for ${state.selectedUserId}.` : "Cleared unsaved changes.");
});

document.getElementById("addEndpointButton").addEventListener("click", () => {
  addEndpointRow();
  setEditorStatus("Added a blank endpoint row.");
});

document.getElementById("deleteUserButton").addEventListener("click", () => {
  deleteUser().catch((error) => setEditorStatus(error.message));
});

document.getElementById("userEditorForm").addEventListener("submit", (event) => {
  saveUser(event).catch((error) => setEditorStatus(error.message));
});

document.getElementById("usersList").addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  const editButton = target.closest('[data-action="edit-user"]');
  if (editButton instanceof HTMLElement) {
    selectUser(editButton.dataset.userId || "");
    return;
  }
  const card = target.closest(".user-card");
  if (card instanceof HTMLElement) {
    selectUser(card.dataset.userId || "");
  }
});

document.getElementById("endpointsList").addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  const removeButton = target.closest('[data-action="remove-endpoint"]');
  if (removeButton instanceof HTMLElement) {
    removeButton.closest(".endpoint-row")?.remove();
    if (!document.querySelector(".endpoint-row")) {
      document.getElementById("endpointsList").innerHTML = '<div class="empty">No endpoints yet.</div>';
    }
    setEditorStatus("Removed endpoint row.");
  }
});

document.getElementById("endpointsList").addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  const row = target.closest(".endpoint-row");
  if (row) {
    const verified = row.querySelector('input[data-field="verified"]');
    const enabled = row.querySelector('input[data-field="enabled"]');
    if (target === verified && verified.checked) enabled.checked = true;
    if (target === enabled && !enabled.checked && verified.checked) enabled.checked = true;
    syncEndpointRowFields(row);
  }
});

refreshAuthorityView()
  .then(() => setEditorStatus("Authority data loaded."))
  .catch((error) => setEditorStatus(error.message));