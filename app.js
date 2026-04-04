/* ============================================================
   tado-planning configurator — Frontend logic
   ============================================================ */

// ── STATE ──────────────────────────────────────────────────
const state = {
  zones: [],           // [{id, name, type}] from Tado API
  configs: [],         // config names from schedules/
  currentConfig: null, // name of config being edited
  configData: {},      // {zoneName: {timetable, week, weekend, ...}}
  planningEvents: [],  // [{day, time, week, level, config}]
  exceptions: [],      // [{name, description, period}]
  currentException: null,
  exceptionData: null,
  zoneModalZone: null, // zone name being edited in modal
  zoneModalSlots: {},  // {MONDAY_TO_FRIDAY: [...], SATURDAY: [...], SUNDAY: [...]}
};

const DAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"];
const WEEKS = ["odd","even","both"];
const TIMETABLES = ["ONE_DAY","THREE_DAY","SEVEN_DAY"];
const PREHEATS = ["off","ECO","BALANCE","COMFORT"];

// ── INIT ───────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  // Navigation
  document.querySelectorAll(".nav-item").forEach(item => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach(i => i.classList.remove("active"));
      document.querySelectorAll(".section").forEach(s => s.classList.remove("active"));
      item.classList.add("active");
      document.getElementById(`section-${item.dataset.section}`).classList.add("active");
      if (item.dataset.section === "status") loadStatus();
      if (item.dataset.section === "configs") loadConfigs();
      if (item.dataset.section === "planning") loadPlanning();
      if (item.dataset.section === "exceptions") loadExceptions();
    });
  });

  // Initial load
  loadStatus();
  loadZones();
  document.getElementById("schedulesPath").textContent = "schedules/";
});

// ── API HELPERS ────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(`/api${path}`, opts);
  return r.json();
}

// ── TOAST ──────────────────────────────────────────────────
let toastTimer;
function toast(msg, type = "") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = `toast show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2500);
}

// ── STATUS ─────────────────────────────────────────────────
async function loadStatus() {
  const data = await api("GET", "/status");
  const el = document.getElementById("statusContent");

  if (data.error) {
    el.innerHTML = `<div class="status-card"><div class="status-card-title">Error</div>
      <div class="status-value" style="color:var(--red)">${data.error}</div></div>`;
    return;
  }

  let html = `
    <div class="status-card">
      <div class="status-card-title">Current time</div>
      <div class="status-value" style="font-size:14px;font-family:var(--font-mono)">${data.now}</div>
      <div class="status-sub">ISO week ${data.iso_week} — ${data.parity}</div>
    </div>`;

  if (data.level1) {
    html += `<div class="status-card status-active">
      <div class="status-card-title">Level 1 — active config</div>
      <div class="status-value">${data.level1.config}</div>
      <div class="status-sub">since ${data.level1.since}</div>
    </div>`;
  }

  if (data.level2) {
    html += `<div class="status-card status-active" style="border-color:var(--l2);background:rgba(155,127,213,0.08)">
      <div class="status-card-title" style="color:var(--l2)">Level 2 — active config</div>
      <div class="status-value" style="color:var(--l2)">${data.level2.config}</div>
      <div class="status-sub">since ${data.level2.since}</div>
    </div>`;
  }

  if (data.exceptions && data.exceptions.length > 0) {
    for (const exc of data.exceptions) {
      html += `<div class="status-card status-exception">
        <div class="status-card-title">Exception active</div>
        <div class="status-value" style="font-size:14px">${exc.description}</div>
        <div class="status-sub">${exc.period}</div>
      </div>`;
    }
  }

  el.innerHTML = html;
}

// ── ZONES ──────────────────────────────────────────────────
async function loadZones() {
  try {
    const data = await api("GET", "/zones");
    if (data.error) { toast("Tado: " + data.error, "error"); return; }
    state.zones = data.zones;
  } catch(e) {
    toast("Could not load zones", "error");
  }
}

// ── CONFIGS ────────────────────────────────────────────────
async function loadConfigs() {
  const data = await api("GET", "/configs");
  state.configs = data.configs || [];
  renderConfigList();
}

function renderConfigList() {
  const el = document.getElementById("configList");
  if (state.configs.length === 0) {
    el.innerHTML = `<div class="empty-state">No configs yet</div>`;
    return;
  }
  el.innerHTML = state.configs.map(name =>
    `<div class="item-row ${name === state.currentConfig ? 'active' : ''}"
          onclick="openConfig('${name}')">${name}</div>`
  ).join("");
}

async function openConfig(name) {
  state.currentConfig = name;
  renderConfigList();
  const data = await api("GET", `/configs/${name}`);
  state.configData = data;
  document.getElementById("configName").value = name;
  renderZonesEditor();
  document.getElementById("configEditorPanel").style.display = "block";
}

function newConfig() {
  state.currentConfig = null;
  state.configData = {};
  document.getElementById("configName").value = "";
  renderZonesEditor();
  document.getElementById("configEditorPanel").style.display = "block";
  renderConfigList();
}

function renderZonesEditor() {
  const el = document.getElementById("zonesEditor");
  if (state.zones.length === 0) {
    el.innerHTML = `<div class="empty-state">No zones loaded — click "Reload zones"</div>`;
    return;
  }

  el.innerHTML = state.zones.map(zone => {
    const key = zone.name.toLowerCase().replace(/\s+/g, "_");
    const cfg = state.configData[key];
    const active = !!cfg;
    const timetable = cfg?.timetable || "THREE_DAY";
    const slots = cfg?.week || [];

    const slotsPreview = slots.slice(0,4).map(s =>
      `<span class="slot-chip">${s.start} ${s.temp}°</span>`
    ).join("") + (slots.length > 4 ? `<span class="slot-chip">+${slots.length-4}</span>` : "");

    return `<div class="zone-card ${active ? 'active' : 'inactive'}" id="zcard-${key}">
      <div class="zone-card-header">
        <input type="checkbox" class="zone-checkbox" ${active ? 'checked' : ''}
               onchange="toggleZone('${key}', '${zone.name}', this.checked)" />
        <span class="zone-name">${zone.name}</span>
        <span class="zone-timetable-badge">${timetable}</span>
        ${active ? `<button class="zone-edit-btn" onclick="openZoneModal('${key}', '${zone.name}')">Edit ›</button>` : ''}
      </div>
      ${active ? `<div class="zone-card-body">
        <div class="slots-preview">${slotsPreview || '<span style="color:var(--text3);font-size:11px">No slots defined</span>'}</div>
      </div>` : ''}
    </div>`;
  }).join("");
}

function toggleZone(key, name, checked) {
  if (checked) {
    if (!state.configData[key]) {
      state.configData[key] = {
        timetable: "THREE_DAY",
        week: [
          {start: "00:00", temp: 15},
          {start: "07:00", temp: 20},
          {start: "22:00", temp: 15}
        ],
        away_temp: 15.0,
        away_enabled: true,
        preheat: "ECO",
        early_start: true
      };
    }
  } else {
    delete state.configData[key];
  }
  renderZonesEditor();
}

async function saveConfig() {
  const name = document.getElementById("configName").value.trim();
  if (!name) { toast("Please enter a config name", "error"); return; }
  await api("POST", `/configs/${name}`, state.configData);
  state.currentConfig = name;
  await loadConfigs();
  toast(`Saved: ${name}`, "success");
}

async function deleteConfig() {
  if (!state.currentConfig) return;
  if (!confirm(`Delete config "${state.currentConfig}"?`)) return;
  await api("DELETE", `/configs/${state.currentConfig}`);
  state.currentConfig = null;
  state.configData = {};
  document.getElementById("configEditorPanel").style.display = "none";
  await loadConfigs();
  toast("Deleted", "success");
}

// ── ZONE MODAL ─────────────────────────────────────────────
function openZoneModal(key, name) {
  state.zoneModalZone = key;
  const cfg = state.configData[key] || {};
  document.getElementById("zoneModalTitle").textContent = `Edit zone: ${name}`;

  const timetable = cfg.timetable || "THREE_DAY";
  const week      = cfg.week || [{start:"00:00",temp:15}];
  const weekend   = cfg.weekend || null;
  const awayTemp  = cfg.away_temp ?? 15.0;
  const awayEn    = cfg.away_enabled !== false;
  const preheat   = cfg.preheat || "ECO";
  const earlyStart = cfg.early_start !== false;

  // Store modal state
  state.zoneModalData = {
    timetable,
    slots: { MONDAY_TO_FRIDAY: [...week], SATURDAY: weekend ? [...weekend] : [...week], SUNDAY: weekend ? [...weekend] : [...week] },
    away_temp: awayTemp,
    away_enabled: awayEn,
    preheat,
    early_start: earlyStart
  };

  renderZoneModal();
  document.getElementById("zoneModal").style.display = "flex";
}

function renderZoneModal() {
  const d = state.zoneModalData;
  const body = document.getElementById("zoneModalBody");

  const timetableButtons = TIMETABLES.map(t =>
    `<button class="timetable-btn ${d.timetable === t ? 'active' : ''}"
             onclick="setModalTimetable('${t}')">${t}</button>`
  ).join("");

  const slotsForTimetable = () => {
    if (d.timetable === "ONE_DAY") return renderSlotSection("MONDAY_TO_SUNDAY", d.slots.MONDAY_TO_FRIDAY);
    if (d.timetable === "THREE_DAY") return `
      <div style="margin-bottom:12px"><div class="modal-section-title">Weekdays (Mon–Fri)</div>${renderSlotSection("MONDAY_TO_FRIDAY", d.slots.MONDAY_TO_FRIDAY)}</div>
      <div style="margin-bottom:12px"><div class="modal-section-title">Saturday</div>${renderSlotSection("SATURDAY", d.slots.SATURDAY)}</div>
      <div><div class="modal-section-title">Sunday</div>${renderSlotSection("SUNDAY", d.slots.SUNDAY)}</div>`;
    if (d.timetable === "SEVEN_DAY") {
      const days7 = ["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"];
      return days7.map(day => {
        if (!d.slots[day]) d.slots[day] = [...d.slots.MONDAY_TO_FRIDAY];
        return `<div style="margin-bottom:12px"><div class="modal-section-title">${day}</div>${renderSlotSection(day, d.slots[day])}</div>`;
      }).join("");
    }
    return "";
  };

  body.innerHTML = `
    <div class="modal-section">
      <div class="modal-section-title">Timetable type</div>
      <div class="timetable-select">${timetableButtons}</div>
    </div>
    <div class="modal-section">
      <div class="modal-section-title">Time slots</div>
      ${slotsForTimetable()}
    </div>
    <div class="modal-section">
      <div class="modal-section-title">Away & preheat</div>
      <div class="away-grid">
        <div><label class="form-row" style="margin:0"><span style="font-family:var(--font-mono);font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:0.08em;display:block;margin-bottom:6px">Away temp (°C)</span>
          <input class="input mono" type="number" id="modal-away-temp" value="${d.away_temp}" min="5" max="25" step="0.5" style="width:100%"/></label></div>
        <div><label class="form-row" style="margin:0"><span style="font-family:var(--font-mono);font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:0.08em;display:block;margin-bottom:6px">Away enabled</span>
          <select class="input" id="modal-away-enabled">
            <option value="true" ${awayEn ? 'selected' : ''}>Yes</option>
            <option value="false" ${!awayEn ? 'selected' : ''}>No</option>
          </select></label></div>
        <div><label class="form-row" style="margin:0"><span style="font-family:var(--font-mono);font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:0.08em;display:block;margin-bottom:6px">Preheat</span>
          <select class="input" id="modal-preheat">
            ${PREHEATS.map(p => `<option value="${p}" ${d.preheat === p ? 'selected' : ''}>${p}</option>`).join("")}
          </select></label></div>
      </div>
      <div style="margin-top:12px">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px;color:var(--text2)">
          <input type="checkbox" id="modal-early-start" ${d.early_start ? 'checked' : ''} style="accent-color:var(--accent)"/>
          Early start (Tado pre-heating feature)
        </label>
      </div>
    </div>`;
}

function renderSlotSection(dayType, slots) {
  const rows = slots.map((s, i) => `
    <div class="slot-row">
      <input type="time" value="${s.start}" onchange="updateModalSlot('${dayType}', ${i}, 'start', this.value)"/>
      <input type="number" value="${s.temp}" min="5" max="30" step="0.5"
             onchange="updateModalSlot('${dayType}', ${i}, 'temp', parseFloat(this.value))"/>
      <span style="font-family:var(--font-mono);font-size:10px;color:var(--text3)">
        → ${slots[i+1] ? slots[i+1].start : '00:00'}
      </span>
      <button class="slot-delete" onclick="deleteModalSlot('${dayType}', ${i})">✕</button>
    </div>`).join("");

  return `<div class="slot-editor">
    <div class="slot-header">
      <span>Start</span><span>Temp °C</span><span>Until</span><span></span>
    </div>
    ${rows}
    <button class="add-slot-btn" onclick="addModalSlot('${dayType}')">+ Add slot</button>
  </div>`;
}

function setModalTimetable(t) {
  state.zoneModalData.timetable = t;
  renderZoneModal();
}

function updateModalSlot(dayType, idx, field, val) {
  const key = dayType === "MONDAY_TO_SUNDAY" ? "MONDAY_TO_FRIDAY" : dayType;
  state.zoneModalData.slots[key][idx][field] = val;
}

function addModalSlot(dayType) {
  const key = dayType === "MONDAY_TO_SUNDAY" ? "MONDAY_TO_FRIDAY" : dayType;
  const slots = state.zoneModalData.slots[key];
  slots.push({start: "12:00", temp: 18});
  slots.sort((a,b) => a.start.localeCompare(b.start));
  renderZoneModal();
}

function deleteModalSlot(dayType, idx) {
  const key = dayType === "MONDAY_TO_SUNDAY" ? "MONDAY_TO_FRIDAY" : dayType;
  state.zoneModalData.slots[key].splice(idx, 1);
  renderZoneModal();
}

function applyZoneEdit() {
  const key = state.zoneModalZone;
  const d   = state.zoneModalData;

  const away_temp    = parseFloat(document.getElementById("modal-away-temp").value);
  const away_enabled = document.getElementById("modal-away-enabled").value === "true";
  const preheat      = document.getElementById("modal-preheat").value;
  const early_start  = document.getElementById("modal-early-start").checked;

  const slots = d.slots;
  let cfg = { timetable: d.timetable, preheat, early_start, away_temp, away_enabled };

  if (d.timetable === "ONE_DAY") {
    cfg.week = slots.MONDAY_TO_FRIDAY;
  } else if (d.timetable === "THREE_DAY") {
    cfg.week = slots.MONDAY_TO_FRIDAY;
    cfg.weekend = slots.SATURDAY;
  } else if (d.timetable === "SEVEN_DAY") {
    cfg.week = slots.MONDAY_TO_FRIDAY;
    for (const day of ["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"]) {
      if (slots[day]) cfg[day.toLowerCase()] = slots[day];
    }
  }

  state.configData[key] = cfg;
  closeZoneModal();
  renderZonesEditor();
}

function closeZoneModal() {
  document.getElementById("zoneModal").style.display = "none";
}

// ── PLANNING STANDARD ──────────────────────────────────────
async function loadPlanning() {
  const data = await api("GET", "/planning/standard");
  state.planningEvents = (data.events || []).filter(e => e.day); // skip _comment entries
  renderPlanningEvents();
}

function renderPlanningEvents() {
  const el = document.getElementById("planningEvents");
  if (state.planningEvents.length === 0) {
    el.innerHTML = `<div class="empty-state">No events — click "+ Add event"</div>`;
    return;
  }

  const configOptions = state.configs.map(c => `<option value="${c}">${c}</option>`).join("");

  el.innerHTML = state.planningEvents.map((ev, i) => `
    <div class="event-row">
      <select onchange="updateEvent(${i}, 'day', this.value)">
        ${DAYS.map(d => `<option value="${d}" ${ev.day===d?'selected':''}>${d.slice(0,3)}</option>`).join("")}
      </select>
      <input type="time" value="${ev.time || '00:00'}"
             onchange="updateEvent(${i}, 'time', this.value)" />
      <select onchange="updateEvent(${i}, 'week', this.value)">
        ${WEEKS.map(w => `<option value="${w}" ${ev.week===w?'selected':''}>${w}</option>`).join("")}
      </select>
      <div class="event-level-toggle">
        <button class="level-btn ${ev.level===1?'active-l1':''}" onclick="setEventLevel(${i},1)">L1</button>
        <button class="level-btn ${ev.level===2?'active-l2':''}" onclick="setEventLevel(${i},2)">L2</button>
      </div>
      <select onchange="updateEvent(${i}, 'config', this.value)">
        ${configOptions}
        ${!state.configs.includes(ev.config) && ev.config ?
          `<option value="${ev.config}" selected>${ev.config}</option>` : ''}
      </select>
      <button class="event-delete" onclick="deleteEvent(${i})">✕</button>
    </div>`).join("");
}

function updateEvent(i, field, val) {
  state.planningEvents[i][field] = val;
}

function setEventLevel(i, level) {
  state.planningEvents[i].level = level;
  renderPlanningEvents();
}

function addPlanningEvent() {
  state.planningEvents.push({
    day: "monday", time: "00:00", week: "both",
    level: 1, config: state.configs[0] || ""
  });
  renderPlanningEvents();
}

function deleteEvent(i) {
  state.planningEvents.splice(i, 1);
  renderPlanningEvents();
}

async function savePlanning() {
  const data = { events: state.planningEvents };
  await api("POST", "/planning/standard", data);
  toast("Planning saved", "success");
}

// ── EXCEPTIONS ─────────────────────────────────────────────
async function loadExceptions() {
  const data = await api("GET", "/planning/exceptions");
  state.exceptions = data.exceptions || [];
  renderExceptionList();
}

function renderExceptionList() {
  const el = document.getElementById("exceptionList");
  if (state.exceptions.length === 0) {
    el.innerHTML = `<div class="empty-state">No exceptions yet</div>`;
    return;
  }
  el.innerHTML = state.exceptions.map(e =>
    `<div class="item-row ${e.name === state.currentException ? 'active' : ''}"
          onclick="openException('${e.name}')">
      <div>
        <div>${e.name}</div>
        <div style="font-size:10px;color:var(--text3);margin-top:2px">${e.description}</div>
      </div>
    </div>`
  ).join("");
}

async function openException(name) {
  state.currentException = name;
  renderExceptionList();
  const data = await api("GET", `/planning/exceptions/${name}`);
  state.exceptionData = data;

  document.getElementById("exceptionName").value = name.replace(/^planning_/, "");
  document.getElementById("exceptionDesc").value = data._description || "";

  const fmt = dt => dt ? dt.replace(" ", "T") : "";
  document.getElementById("exceptionStart").value = fmt(data.period?.start);
  document.getElementById("exceptionEnd").value   = fmt(data.period?.end);

  const events = (data.events || []).filter(e => e.day);
  state.exceptionData._events = events;
  renderExceptionEvents();
  document.getElementById("exceptionEditorPanel").style.display = "block";
}

function newException() {
  state.currentException = null;
  state.exceptionData = { _events: [] };
  document.getElementById("exceptionName").value = "";
  document.getElementById("exceptionDesc").value = "";
  document.getElementById("exceptionStart").value = "";
  document.getElementById("exceptionEnd").value = "";
  renderExceptionEvents();
  document.getElementById("exceptionEditorPanel").style.display = "block";
  renderExceptionList();
}

function renderExceptionEvents() {
  const el = document.getElementById("exceptionEvents");
  const events = state.exceptionData?._events || [];
  const configOptions = state.configs.map(c => `<option value="${c}">${c}</option>`).join("");

  if (events.length === 0) {
    el.innerHTML = `<div class="empty-state">No events</div>`;
    return;
  }

  el.innerHTML = events.map((ev, i) => `
    <div class="event-row">
      <select onchange="updateExcEvent(${i}, 'day', this.value)">
        ${DAYS.map(d => `<option value="${d}" ${ev.day===d?'selected':''}>${d.slice(0,3)}</option>`).join("")}
      </select>
      <input type="time" value="${ev.time || '00:00'}"
             onchange="updateExcEvent(${i}, 'time', this.value)" />
      <select onchange="updateExcEvent(${i}, 'week', this.value)">
        ${WEEKS.map(w => `<option value="${w}" ${ev.week===w?'selected':''}>${w}</option>`).join("")}
      </select>
      <div class="event-level-toggle">
        <button class="level-btn ${ev.level===1?'active-l1':''}" onclick="setExcEventLevel(${i},1)">L1</button>
        <button class="level-btn ${ev.level===2?'active-l2':''}" onclick="setExcEventLevel(${i},2)">L2</button>
      </div>
      <select onchange="updateExcEvent(${i}, 'config', this.value)">
        ${configOptions}
        ${!state.configs.includes(ev.config) && ev.config ?
          `<option value="${ev.config}" selected>${ev.config}</option>` : ''}
      </select>
      <button class="event-delete" onclick="deleteExcEvent(${i})">✕</button>
    </div>`).join("");
}

function updateExcEvent(i, field, val) {
  state.exceptionData._events[i][field] = val;
}

function setExcEventLevel(i, level) {
  state.exceptionData._events[i].level = level;
  renderExceptionEvents();
}

function addExceptionEvent() {
  if (!state.exceptionData) state.exceptionData = { _events: [] };
  state.exceptionData._events.push({
    day: "monday", time: "00:00", week: "both",
    level: 1, config: state.configs[0] || ""
  });
  renderExceptionEvents();
}

function deleteExcEvent(i) {
  state.exceptionData._events.splice(i, 1);
  renderExceptionEvents();
}

async function saveException() {
  const rawName = document.getElementById("exceptionName").value.trim();
  if (!rawName) { toast("Please enter a name", "error"); return; }
  const name = rawName.startsWith("planning_") ? rawName : `planning_${rawName}`;

  const fmtDt = v => v ? v.replace("T", " ") : "";
  const data = {
    _description: document.getElementById("exceptionDesc").value.trim(),
    period: {
      start: fmtDt(document.getElementById("exceptionStart").value),
      end:   fmtDt(document.getElementById("exceptionEnd").value),
    },
    events: state.exceptionData._events || []
  };

  await api("POST", `/planning/exceptions/${name}`, data);
  state.currentException = name;
  await loadExceptions();
  toast(`Saved: ${name}`, "success");
}

async function deleteException() {
  if (!state.currentException) return;
  if (!confirm(`Delete exception "${state.currentException}"?`)) return;
  await api("DELETE", `/planning/exceptions/${state.currentException}`);
  state.currentException = null;
  state.exceptionData = null;
  document.getElementById("exceptionEditorPanel").style.display = "none";
  await loadExceptions();
  toast("Deleted", "success");
}
