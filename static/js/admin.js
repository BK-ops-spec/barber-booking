const state = {
  user: null,
  csrfToken: "",
  view: "schedule",
  date: null,
  staff: [],
  services: [],
  appointments: [],
  settings: {},
  hours: { shop_hours: [], staff_hours: [] },
  stats: {},
  closures: [],
};

const el = (id) => document.getElementById(id);
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function money(cents) {
  const currency = state.settings.currency || "USD";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: cents % 100 === 0 ? 0 : 2,
    }).format(cents / 100);
  } catch {
    return `$${(cents / 100).toFixed(2)}`;
  }
}

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  el("toasts").appendChild(node);
  setTimeout(() => node.remove(), 4600);
}

async function api(path, options = {}) {
  const config = { headers: {}, ...options };
  if (config.body !== undefined && typeof config.body !== "string") {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(config.body);
  }
  const method = (config.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD" && state.csrfToken) {
    config.headers["X-CSRF-Token"] = state.csrfToken;
  }
  const response = await fetch(path, config);
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const error = new Error(payload.error || "Something went wrong");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function todayIso() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function shiftDate(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d + days);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function prettyDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

function initials(name) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

function openModal(html) {
  el("modal-card").innerHTML = html;
  el("modal").classList.remove("hidden");
}

function closeModal() {
  el("modal").classList.add("hidden");
  el("modal-card").innerHTML = "";
}

el("modal").addEventListener("click", (event) => {
  if (event.target.id === "modal") closeModal();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeModal();
});

async function boot() {
  try {
    const me = await api("/api/admin/me");
    state.user = me.user;
    state.csrfToken = me.csrf_token || "";
    showAdmin();
  } catch {
    el("login-screen").classList.remove("hidden");
  }
}

function showAdmin() {
  el("login-screen").classList.add("hidden");
  el("admin-shell").classList.remove("hidden");
  el("admin-user").textContent = state.user.username;
  state.date = todayIso();
  el("schedule-date").value = state.date;
  loadEverything();
}

async function loadEverything() {
  try {
    const [staff, services, settings] = await Promise.all([
      api("/api/admin/staff"),
      api("/api/admin/services"),
      api("/api/admin/settings"),
    ]);
    state.staff = staff;
    state.services = services;
    state.settings = settings;
    populateStaffFilter();
    renderServices();
    renderBarbers();
    renderSettings();
    await loadSchedule();
    await loadHours();
    await loadClosures();
  } catch (error) {
    toast(error.message, "error");
  }
}

function populateStaffFilter() {
  const select = el("filter-staff");
  const current = select.value;
  select.innerHTML =
    `<option value="">All barbers</option>` +
    state.staff.map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join("");
  select.value = current;
}

async function loadSchedule() {
  const params = new URLSearchParams();
  if (state.date) params.set("date", state.date);
  const staffId = el("filter-staff").value;
  const status = el("filter-status").value;
  const search = el("filter-search").value.trim();
  if (staffId) params.set("staff_id", staffId);
  if (status) params.set("status", status);
  if (search) params.set("q", search);

  try {
    const [appointments, stats] = await Promise.all([
      api(`/api/admin/appointments?${params}`),
      api("/api/admin/stats"),
    ]);
    state.appointments = appointments.appointments;
    state.stats = stats;
    renderStats();
    renderAppointments();
    el("schedule-subtitle").textContent = prettyDate(state.date);
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderStats() {
  const stats = state.stats;
  const cards = [
    { label: "Today", value: stats.today_count, accent: true },
    { label: "This week", value: stats.week_count },
    { label: "Week revenue", value: money(stats.week_revenue_cents || 0), accent: true },
    { label: "Cancelled", value: (stats.status_counts || {}).cancelled || 0 },
  ];
  el("stat-grid").innerHTML = cards
    .map(
      (card) => `<div class="stat"><div class="label">${card.label}</div><div class="value ${card.accent ? "accent" : ""}">${card.value}</div></div>`
    )
    .join("");
}

function renderAppointments() {
  const list = el("appointment-list");
  if (!state.appointments.length) {
    list.innerHTML = `<div class="empty"><b>Nothing booked for this day</b>Add a walk-in or pick another date.</div>`;
    return;
  }
  list.innerHTML = state.appointments
    .map(
      (booking) => `
      <div class="appt-row">
        <div class="appt-time">${esc(booking.local_time)}<small>to ${esc(booking.local_end_time)}</small></div>
        <div class="appt-name">
          <b>${esc(booking.customer_name)}</b>
          <span>${esc(booking.phone)}${booking.email ? " · " + esc(booking.email) : ""}</span>
          ${booking.notes ? `<span style="display:block;color:var(--text-dim);margin-top:4px">“${esc(booking.notes)}”</span>` : ""}
        </div>
        <div>
          <div>${esc(booking.service_name)}</div>
          <span class="faint" style="font-size:12px">${esc(booking.staff_name)} · ${money(booking.price_cents)}</span>
        </div>
        <div>
          <span class="tag tag-${booking.status}">${esc(booking.status.replace("_", " "))}</span>
          <div class="faint" style="font-size:11.5px;margin-top:5px">${esc(booking.code)}</div>
        </div>
        <div class="appt-actions">
          ${
            booking.status === "confirmed"
              ? `<button class="btn btn-ghost btn-sm" data-act="complete" data-id="${booking.id}">Complete</button>
                 <button class="btn btn-ghost btn-sm" data-act="reschedule" data-id="${booking.id}">Reschedule</button>
                 <button class="btn btn-danger btn-sm" data-act="cancel" data-id="${booking.id}">Cancel</button>`
              : `<button class="btn btn-ghost btn-sm" data-act="restore" data-id="${booking.id}">Restore</button>`
          }
        </div>
      </div>`
    )
    .join("");
}

function renderServices() {
  const list = el("service-list");
  if (!state.services.length) {
    list.innerHTML = `<div class="empty">No services yet.</div>`;
    return;
  }
  list.innerHTML = `<table class="table">
    <thead><tr><th>Service</th><th>Duration</th><th>Price</th><th>Status</th><th></th></tr></thead>
    <tbody>
      ${state.services
        .map(
          (service) => `
        <tr>
          <td>
            <b>${esc(service.name)}</b>
            <div class="faint" style="font-size:12px">${esc(service.description)}</div>
          </td>
          <td>${service.duration_min} min</td>
          <td>${money(service.price_cents)}</td>
          <td>${service.active ? '<span class="tag tag-confirmed">Active</span>' : '<span class="tag tag-inactive">Hidden</span>'}</td>
          <td style="text-align:right;white-space:nowrap">
            <button class="btn btn-ghost btn-sm" data-act="edit-service" data-id="${service.id}">Edit</button>
            <button class="btn btn-danger btn-sm" data-act="delete-service" data-id="${service.id}">Delete</button>
          </td>
        </tr>`
        )
        .join("")}
    </tbody>
  </table>`;
}

function renderBarbers() {
  const list = el("barber-list");
  if (!state.staff.length) {
    list.innerHTML = `<div class="empty">No barbers yet.</div>`;
    return;
  }
  list.innerHTML = state.staff
    .map((member) => {
      const names = state.services
        .filter((service) => member.service_ids.includes(service.id))
        .map((service) => service.name);
      return `
      <div class="card team-card">
        <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
          <div class="avatar" style="margin-bottom:10px">${esc(initials(member.name))}</div>
          ${member.active ? "" : '<span class="tag tag-inactive">Inactive</span>'}
        </div>
        <div class="role">${esc(member.title)}</div>
        <h4>${esc(member.name)}</h4>
        <p>${esc(member.bio)}</p>
        <div class="faint" style="font-size:12.5px;margin:12px 0 16px">${esc(names.join(" · ")) || "No services assigned"}</div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-ghost btn-sm" data-act="edit-staff" data-id="${member.id}">Edit</button>
          <button class="btn btn-danger btn-sm" data-act="delete-staff" data-id="${member.id}">Remove</button>
        </div>
      </div>`;
    })
    .join("");
}

function renderSettings() {
  const s = state.settings;
  el("s-name").value = s.shop_name || "";
  el("s-tagline").value = s.shop_tagline || "";
  el("s-phone").value = s.shop_phone || "";
  el("s-address").value = s.shop_address || "";
  el("s-timezone").value = s.shop_timezone || "UTC";
  el("s-currency").value = s.currency || "USD";
  el("s-lead").value = s.lead_time_min || 60;
  el("s-advance").value = s.max_advance_days || 60;
  el("s-slot").value = s.slot_interval_min || 15;
  el("s-anystaff").value = s.allow_any_staff === "1" ? "1" : "0";
}

function renderHours() {
  const shopMap = new Map(state.hours.shop_hours.map((h) => [h.day_of_week, h]));
  el("shop-hours").innerHTML = DAYS.map((label, day) => {
    const entry = shopMap.get(day);
    return `
      <div class="hours-row">
        <div class="day-label">${label}</div>
        <input class="input" type="time" data-shop-day="${day}" data-shop-field="start_time" value="${entry ? entry.start_time : ""}">
        <input class="input" type="time" data-shop-day="${day}" data-shop-field="end_time" value="${entry ? entry.end_time : ""}">
        <button class="btn btn-quiet btn-sm" data-act="clear-shop-day" data-day="${day}">Clear</button>
      </div>`;
  }).join("");

  el("staff-hours-panels").innerHTML = state.hours.staff_hours
    .map((member) => {
      const map = new Map(member.hours.map((h) => [h.day_of_week, h]));
      return `
      <div class="panel" style="margin-bottom:16px">
        <div class="panel-head"><h3>${esc(member.name)}</h3></div>
        <div class="panel-body"><div class="hours-editor">
          ${DAYS.map((label, day) => {
            const entry = map.get(day);
            return `
            <div class="hours-row">
              <div class="day-label">${label}</div>
              <input class="input" type="time" data-staff-day="${day}" data-staff-id="${member.staff_id}" data-staff-field="start_time" value="${entry ? entry.start_time : ""}">
              <input class="input" type="time" data-staff-day="${day}" data-staff-id="${member.staff_id}" data-staff-field="end_time" value="${entry ? entry.end_time : ""}">
              <button class="btn btn-quiet btn-sm" data-act="clear-staff-day" data-day="${day}" data-staff-id="${member.staff_id}">Clear</button>
            </div>`;
          }).join("")}
        </div></div>
      </div>`;
    })
    .join("");
}

async function loadHours() {
  try {
    state.hours = await api("/api/admin/hours");
    renderHours();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadClosures() {
  try {
    state.closures = await api("/api/admin/closures");
    el("closure-list").innerHTML = state.closures.length
      ? `<table class="table"><tbody>${state.closures
          .map(
            (closure) => `<tr>
              <td><b>${esc(prettyDate(closure.day))}</b></td>
              <td class="muted">${esc(closure.reason || "Closed")}</td>
              <td style="text-align:right"><button class="btn btn-danger btn-sm" data-act="delete-closure" data-day="${esc(closure.day)}">Remove</button></td>
            </tr>`
          )
          .join("")}</tbody></table>`
      : `<div class="empty">No closures scheduled.</div>`;
  } catch (error) {
    toast(error.message, "error");
  }
}

function collectHours() {
  const shop = [];
  for (let day = 0; day < 7; day += 1) {
    const start = document.querySelector(`[data-shop-day="${day}"][data-shop-field="start_time"]`);
    const end = document.querySelector(`[data-shop-day="${day}"][data-shop-field="end_time"]`);
    if (start.value && end.value) {
      shop.push({ day_of_week: day, start_time: start.value, end_time: end.value });
    }
  }
  const staffMap = new Map(state.hours.staff_hours.map((m) => [m.staff_id, []]));
  document.querySelectorAll("[data-staff-day]").forEach((input) => {
    const staffId = Number(input.dataset.staffId);
    const day = Number(input.dataset.staffDay);
    const field = input.dataset.staffField;
    const bucket = staffMap.get(staffId) || [];
    let entry = bucket.find((item) => item.day_of_week === day);
    if (!entry) {
      entry = { day_of_week: day, start_time: "", end_time: "" };
      bucket.push(entry);
    }
    entry[field] = input.value;
  });
  const staff = [];
  staffMap.forEach((hours, staffId) => {
    const cleaned = hours.filter((h) => h.start_time && h.end_time);
    staff.push({ staff_id: staffId, hours: cleaned });
  });
  return { shop_hours: shop, staff_hours: staff };
}

async function saveHours() {
  try {
    await api("/api/admin/hours", { method: "PUT", body: collectHours() });
    await loadHours();
    toast("Hours updated", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function saveSettings() {
  const body = {
    shop_name: el("s-name").value.trim(),
    shop_tagline: el("s-tagline").value.trim(),
    shop_phone: el("s-phone").value.trim(),
    shop_address: el("s-address").value.trim(),
    shop_timezone: el("s-timezone").value.trim() || "UTC",
    currency: el("s-currency").value.trim() || "USD",
    lead_time_min: Number(el("s-lead").value || 0),
    max_advance_days: Number(el("s-advance").value || 60),
    slot_interval_min: Number(el("s-slot").value || 15),
    allow_any_staff: el("s-anystaff").value,
  };
  try {
    state.settings = await api("/api/admin/settings", { method: "PUT", body });
    toast("Settings saved", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

function serviceForm(service) {
  const data = service || { name: "", description: "", price_cents: 0, duration_min: 30, active: true };
  openModal(`
    <div class="modal-head"><h3>${service ? "Edit service" : "New service"}</h3><button class="btn btn-quiet btn-sm" data-act="close-modal">Close</button></div>
    <div class="modal-body">
      <div class="field"><label>Name</label><input class="input" id="m-name" value="${esc(data.name)}"></div>
      <div class="field"><label>Description</label><textarea class="textarea" id="m-desc">${esc(data.description)}</textarea></div>
      <div class="form-grid">
        <div class="field"><label>Price</label><input class="input" id="m-price" type="number" min="0" step="0.01" value="${(data.price_cents / 100).toFixed(2)}"></div>
        <div class="field"><label>Duration (minutes)</label><input class="input" id="m-duration" type="number" min="5" max="480" step="5" value="${data.duration_min}"></div>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn btn-ghost" data-act="close-modal">Cancel</button>
      <button class="btn btn-primary" data-act="save-service" data-id="${service ? service.id : ""}">Save service</button>
    </div>`);
}

function staffForm(member) {
  const data = member || { name: "", title: "", bio: "", phone: "", active: true, service_ids: [] };
  const pills = state.services
    .map(
      (service) => `
      <label class="check-pill ${data.service_ids.includes(service.id) ? "checked" : ""}">
        <input type="checkbox" value="${service.id}" ${data.service_ids.includes(service.id) ? "checked" : ""}>
        ${esc(service.name)}
      </label>`
    )
    .join("");
  openModal(`
    <div class="modal-head"><h3>${member ? "Edit barber" : "New barber"}</h3><button class="btn btn-quiet btn-sm" data-act="close-modal">Close</button></div>
    <div class="modal-body">
      <div class="form-grid">
        <div class="field"><label>Name</label><input class="input" id="m-name" value="${esc(data.name)}"></div>
        <div class="field"><label>Title</label><input class="input" id="m-title" value="${esc(data.title)}"></div>
      </div>
      <div class="field"><label>Bio</label><textarea class="textarea" id="m-bio">${esc(data.bio)}</textarea></div>
      <div class="field"><label>Phone</label><input class="input" id="m-phone" value="${esc(data.phone)}"></div>
      <div class="field">
        <label>Services offered</label>
        <div class="checkbox-grid" id="m-services">${pills}</div>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn btn-ghost" data-act="close-modal">Cancel</button>
      <button class="btn btn-primary" data-act="save-staff" data-id="${member ? member.id : ""}">Save barber</button>
    </div>`);
}

async function appointmentForm() {
  const serviceOptions = state.services
    .map((service) => `<option value="${service.id}">${esc(service.name)} (${service.duration_min}m)</option>`)
    .join("");
  const staffOptions = `<option value="">Any available barber</option>` +
    state.staff.map((member) => `<option value="${member.id}">${esc(member.name)}</option>`).join("");
  openModal(`
    <div class="modal-head"><h3>New booking</h3><button class="btn btn-quiet btn-sm" data-act="close-modal">Close</button></div>
    <div class="modal-body">
      <div class="form-grid">
        <div class="field"><label>Service</label><select class="select" id="m-service">${serviceOptions}</select></div>
        <div class="field"><label>Barber</label><select class="select" id="m-staff">${staffOptions}</select></div>
        <div class="field"><label>Date</label><input class="input" id="m-date" type="date" value="${state.date}"></div>
        <div class="field"><label>Time</label><select class="select" id="m-time"></select></div>
        <div class="field"><label>Name</label><input class="input" id="m-customer"></div>
        <div class="field"><label>Phone</label><input class="input" id="m-phone"></div>
      </div>
      <div class="field"><label>Notes</label><textarea class="textarea" id="m-notes"></textarea></div>
    </div>
    <div class="modal-foot">
      <button class="btn btn-ghost" data-act="close-modal">Cancel</button>
      <button class="btn btn-primary" data-act="save-appointment">Create booking</button>
    </div>`);

  const refresh = async () => {
    const serviceId = el("m-service").value;
    const staffId = el("m-staff").value;
    const date = el("m-date").value;
    const params = new URLSearchParams({ service_id: serviceId, date });
    if (staffId) params.set("staff_id", staffId);
    const select = el("m-time");
    select.innerHTML = `<option value="">Loading…</option>`;
    try {
      const data = await api(`/api/availability?${params}`);
      select.innerHTML = data.slots.length
        ? data.slots.map((slot) => `<option value="${slot}">${slot}</option>`).join("")
        : `<option value="">No open times</option>`;
    } catch {
      select.innerHTML = `<option value="">No open times</option>`;
    }
  };
  el("m-service").addEventListener("change", refresh);
  el("m-staff").addEventListener("change", refresh);
  el("m-date").addEventListener("change", refresh);
  await refresh();
}

document.addEventListener("change", (event) => {
  const pill = event.target.closest(".check-pill");
  if (pill) pill.classList.toggle("checked", event.target.checked);
});

document.addEventListener("click", async (event) => {
  const action = event.target.closest("[data-act]");
  const nav = event.target.closest("[data-view]");

  if (nav) {
    document.querySelectorAll(".nav-item").forEach((node) => node.classList.remove("active"));
    nav.classList.add("active");
    state.view = nav.dataset.view;
    ["schedule", "services", "barbers", "hours", "settings"].forEach((view) => {
      el(`view-${view}`).classList.toggle("hidden", view !== state.view);
    });
    return;
  }

  if (!action) return;
  const id = Number(action.dataset.id);

  switch (action.dataset.act) {
    case "close-modal":
      closeModal();
      break;
    case "edit-service": {
      const service = state.services.find((item) => item.id === id);
      serviceForm(service);
      break;
    }
    case "save-service": {
      const body = {
        name: el("m-name").value.trim(),
        description: el("m-desc").value.trim(),
        price: Number(el("m-price").value || 0),
        duration_min: Number(el("m-duration").value || 30),
        active: true,
      };
      try {
        if (action.dataset.id) {
          await api(`/api/admin/services/${action.dataset.id}`, { method: "PUT", body });
        } else {
          await api("/api/admin/services", { method: "POST", body });
        }
        closeModal();
        state.services = await api("/api/admin/services");
        renderServices();
        populateStaffFilter();
        toast("Service saved", "success");
      } catch (error) {
        toast(error.message, "error");
      }
      break;
    }
    case "delete-service": {
      if (!confirm("Delete this service?")) return;
      try {
        await api(`/api/admin/services/${id}`, { method: "DELETE" });
        state.services = await api("/api/admin/services");
        renderServices();
        populateStaffFilter();
        toast("Service removed", "success");
      } catch (error) {
        toast(error.message, "error");
      }
      break;
    }
    case "edit-staff": {
      const member = state.staff.find((item) => item.id === id);
      staffForm(member);
      break;
    }
    case "save-staff": {
      const serviceIds = [...document.querySelectorAll("#m-services input:checked")].map((input) => Number(input.value));
      const body = {
        name: el("m-name").value.trim(),
        title: el("m-title").value.trim(),
        bio: el("m-bio").value.trim(),
        phone: el("m-phone").value.trim(),
        active: true,
        service_ids: serviceIds,
      };
      try {
        if (action.dataset.id) {
          await api(`/api/admin/staff/${action.dataset.id}`, { method: "PUT", body });
        } else {
          await api("/api/admin/staff", { method: "POST", body });
        }
        closeModal();
        state.staff = await api("/api/admin/staff");
        renderBarbers();
        populateStaffFilter();
        await loadHours();
        toast("Barber saved", "success");
      } catch (error) {
        toast(error.message, "error");
      }
      break;
    }
    case "delete-staff": {
      if (!confirm("Remove this barber? Existing bookings are kept.")) return;
      try {
        await api(`/api/admin/staff/${id}`, { method: "DELETE" });
        state.staff = await api("/api/admin/staff");
        renderBarbers();
        populateStaffFilter();
        await loadHours();
        toast("Barber removed", "success");
      } catch (error) {
        toast(error.message, "error");
      }
      break;
    }
    case "complete": {
      try {
        await api(`/api/admin/appointments/${id}`, { method: "PATCH", body: { status: "completed" } });
        await loadSchedule();
        toast("Marked complete", "success");
      } catch (error) {
        toast(error.message, "error");
      }
      break;
    }
    case "restore": {
      try {
        await api(`/api/admin/appointments/${id}`, { method: "PATCH", body: { status: "confirmed" } });
        await loadSchedule();
        toast("Booking restored", "success");
      } catch (error) {
        toast(error.message, "error");
      }
      break;
    }
    case "cancel": {
      if (!confirm("Cancel this appointment?")) return;
      try {
        await api(`/api/admin/appointments/${id}/cancel`, { method: "POST" });
        await loadSchedule();
        toast("Appointment cancelled", "success");
      } catch (error) {
        toast(error.message, "error");
      }
      break;
    }
    case "reschedule": {
      const booking = state.appointments.find((item) => item.id === id);
      if (!booking) return;
      openModal(`
        <div class="modal-head"><h3>Reschedule</h3><button class="btn btn-quiet btn-sm" data-act="close-modal">Close</button></div>
        <div class="modal-body">
          <div class="field"><label>New date</label><input class="input" id="m-date" type="date" value="${esc(booking.local_date)}"></div>
          <div class="field"><label>New time</label><select class="select" id="m-time"></select></div>
        </div>
        <div class="modal-foot">
          <button class="btn btn-ghost" data-act="close-modal">Cancel</button>
          <button class="btn btn-primary" data-act="save-reschedule" data-id="${id}">Save</button>
        </div>`);
      const params = new URLSearchParams({
        service_id: booking.service_id,
        staff_id: booking.staff_id,
        date: booking.local_date,
      });
      try {
        const data = await api(`/api/availability?${params}`);
        el("m-time").innerHTML = data.slots.length
          ? data.slots.map((slot) => `<option value="${slot}">${slot}</option>`).join("")
          : `<option value="">No open times</option>`;
      } catch {
        el("m-time").innerHTML = `<option value="">No open times</option>`;
      }
      break;
    }
    case "save-reschedule": {
      try {
        await api(`/api/admin/appointments/${id}`, {
          method: "PATCH",
          body: { date: el("m-date").value, time: el("m-time").value },
        });
        closeModal();
        await loadSchedule();
        toast("Rescheduled", "success");
      } catch (error) {
        toast(error.message, "error");
      }
      break;
    }
    case "save-appointment": {
      const body = {
        service_id: Number(el("m-service").value),
        staff_id: el("m-staff").value ? Number(el("m-staff").value) : null,
        date: el("m-date").value,
        time: el("m-time").value,
        customer_name: el("m-customer").value.trim(),
        phone: el("m-phone").value.trim(),
        notes: el("m-notes").value.trim(),
      };
      try {
        await api("/api/admin/appointments", { method: "POST", body });
        closeModal();
        state.date = body.date;
        el("schedule-date").value = body.date;
        await loadSchedule();
        toast("Booking created", "success");
      } catch (error) {
        toast(error.message, "error");
      }
      break;
    }
    case "clear-shop-day": {
      const day = action.dataset.day;
      document.querySelector(`[data-shop-day="${day}"][data-shop-field="start_time"]`).value = "";
      document.querySelector(`[data-shop-day="${day}"][data-shop-field="end_time"]`).value = "";
      break;
    }
    case "clear-staff-day": {
      const day = action.dataset.day;
      const staffId = action.dataset.staffId;
      document.querySelector(`[data-staff-day="${day}"][data-staff-id="${staffId}"][data-staff-field="start_time"]`).value = "";
      document.querySelector(`[data-staff-day="${day}"][data-staff-id="${staffId}"][data-staff-field="end_time"]`).value = "";
      break;
    }
    case "delete-closure": {
      try {
        await api(`/api/admin/closures/${action.dataset.day}`, { method: "DELETE" });
        await loadClosures();
        toast("Closure removed", "success");
      } catch (error) {
        toast(error.message, "error");
      }
      break;
    }
  }
});

el("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = el("login-btn");
  const errorBox = el("login-error");
  errorBox.classList.add("hidden");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> Signing in…';
  try {
    const result = await api("/api/admin/login", {
      method: "POST",
      body: { username: el("login-user").value, password: el("login-pass").value },
    });
    state.user = result.user;
    state.csrfToken = result.csrf_token || "";
    showAdmin();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  } finally {
    button.disabled = false;
    button.textContent = "Sign in";
  }
});

el("logout-btn").addEventListener("click", async () => {
  await api("/api/admin/logout", { method: "POST" });
  location.reload();
});

el("today-btn").addEventListener("click", () => {
  state.date = todayIso();
  el("schedule-date").value = state.date;
  loadSchedule();
});

el("prev-day").addEventListener("click", () => {
  state.date = shiftDate(state.date, -1);
  el("schedule-date").value = state.date;
  loadSchedule();
});

el("next-day").addEventListener("click", () => {
  state.date = shiftDate(state.date, 1);
  el("schedule-date").value = state.date;
  loadSchedule();
});

el("schedule-date").addEventListener("change", (event) => {
  state.date = event.target.value;
  loadSchedule();
});

["filter-staff", "filter-status"].forEach((id) =>
  el(id).addEventListener("change", loadSchedule)
);

let searchTimer;
el("filter-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadSchedule, 260);
});

el("new-appointment").addEventListener("click", appointmentForm);
el("new-service").addEventListener("click", () => serviceForm(null));
el("new-staff").addEventListener("click", () => staffForm(null));
el("save-hours").addEventListener("click", saveHours);
el("save-settings").addEventListener("click", saveSettings);

el("add-closure").addEventListener("click", async () => {
  const day = el("closure-date").value;
  if (!day) {
    toast("Pick a date first", "error");
    return;
  }
  try {
    await api("/api/admin/closures", {
      method: "POST",
      body: { day, reason: el("closure-reason").value.trim() },
    });
    el("closure-reason").value = "";
    await loadClosures();
    toast("Closure added", "success");
  } catch (error) {
    toast(error.message, "error");
  }
});

el("change-password").addEventListener("click", async () => {
  try {
    await api("/api/admin/password", {
      method: "POST",
      body: {
        current_password: el("p-current").value,
        new_password: el("p-new").value,
      },
    });
    el("p-current").value = "";
    el("p-new").value = "";
    toast("Password updated", "success");
  } catch (error) {
    toast(error.message, "error");
  }
});

boot();
