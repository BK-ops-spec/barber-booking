const state = {
  config: null,
  step: 1,
  serviceId: null,
  staffId: null,
  date: null,
  time: null,
  dates: [],
  slots: [],
  loadingSlots: false,
  submitting: false,
  booking: null,
};

const el = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function money(cents) {
  const currency = (state.config && state.config.currency) || "USD";
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
  const response = await fetch(path, config);
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const error = new Error(payload.error || "Something went wrong");
    error.field = payload.field || "";
    error.status = response.status;
    throw error;
  }
  return payload;
}

function parseIsoDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function initials(name) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

function selectedService() {
  return (state.config?.services || []).find((s) => s.id === state.serviceId) || null;
}

function selectedStaff() {
  return (state.config?.staff || []).find((s) => s.id === state.staffId) || null;
}

function staffForService() {
  const service = selectedService();
  if (!service) return [];
  return (state.config?.staff || []).filter((member) => member.service_ids.includes(service.id));
}

async function loadConfig() {
  state.config = await api("/api/config");
  const shop = state.config;
  el("shop-name").textContent = shop.shop_name;
  el("footer-shop").textContent = shop.shop_name;
  el("hero-tagline").textContent = shop.shop_tagline;
  el("shop-address").textContent = shop.shop_address;
  el("shop-phone").textContent = shop.shop_phone;
  el("shop-hours").textContent = `${shop.open_time} – ${shop.close_time}`;
  document.title = `${shop.shop_name} — Book an Appointment`;
  renderServices();
  renderTeam();
  render();
}

function renderServices() {
  el("services-grid").innerHTML = state.config.services
    .map(
      (service) => `
      <article class="card service-card">
        <h4>${esc(service.name)}</h4>
        <p>${esc(service.description)}</p>
        <div class="option-meta">
          <span class="price">${money(service.price_cents)}</span>
          <span class="duration">${service.duration_min} min</span>
        </div>
      </article>`
    )
    .join("");
}

function renderTeam() {
  el("team-grid").innerHTML = state.config.staff
    .map(
      (member) => `
      <article class="card team-card">
        <div class="avatar">${esc(initials(member.name))}</div>
        <div class="role">${esc(member.title)}</div>
        <h4>${esc(member.name)}</h4>
        <p>${esc(member.bio)}</p>
      </article>`
    )
    .join("");
}

function setStep(step) {
  state.step = step;
  render();
}

function render() {
  const panels = {
    1: renderServiceStep,
    2: renderStaffStep,
    3: renderTimeStep,
    4: renderDetailsStep,
  };
  el("step-panel").innerHTML = panels[state.step]();
  el("step-panel").classList.remove("fade-in");
  void el("step-panel").offsetWidth;
  el("step-panel").classList.add("fade-in");

  document.querySelectorAll(".step-tab").forEach((tab) => {
    const index = Number(tab.dataset.step);
    tab.classList.toggle("active", index === state.step);
    tab.classList.toggle("done", index < state.step);
    tab.disabled = index > state.step;
  });

  const back = el("btn-back");
  back.disabled = state.step === 1;
  const next = el("btn-next");
  next.disabled = state.step === 4 ? false : !canContinue();
  next.textContent = state.step === 4 ? "Confirm booking" : "Continue";
  el("foot-summary").innerHTML = summaryHtml();
}

function summaryHtml() {
  const service = selectedService();
  if (!service) return "Select a service to begin.";
  const parts = [`<b>${esc(service.name)}</b> · ${money(service.price_cents)}`];
  const staff = selectedStaff();
  if (staff) parts.push(esc(staff.name));
  else if (state.step > 2) parts.push("Any available barber");
  if (state.date) parts.push(esc(formatDate(state.date)));
  if (state.time) parts.push(`<b>${esc(state.time)}</b>`);
  return parts.join(" &nbsp;·&nbsp; ");
}

function formatDate(iso) {
  return parseIsoDate(iso).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function canContinue() {
  if (state.step === 1) return Boolean(state.serviceId);
  if (state.step === 2) return state.staffId !== undefined;
  if (state.step === 3) return Boolean(state.date && state.time);
  return false;
}

function renderServiceStep() {
  return `
    <p class="muted" style="margin-top:0">Choose the service you'd like booked.</p>
    <div class="option-grid">
      ${state.config.services
        .map(
          (service) => `
        <button class="option ${service.id === state.serviceId ? "selected" : ""}" data-service="${service.id}">
          <h4>${esc(service.name)}</h4>
          <p>${esc(service.description)}</p>
          <div class="option-meta">
            <span class="price">${money(service.price_cents)}</span>
            <span class="duration">${service.duration_min} min</span>
          </div>
        </button>`
        )
        .join("")}
    </div>`;
}

function renderStaffStep() {
  const members = staffForService();
  return `
    <p class="muted" style="margin-top:0">Who would you like to see?</p>
    <div class="option-grid">
      ${
        state.config.allow_any_staff
          ? `<button class="option ${state.staffId === null ? "selected" : ""}" data-staff="any">
               <div class="avatar">✦</div>
               <h4>Any available barber</h4>
               <p>We'll assign whoever has the chair free at that time.</p>
             </button>`
          : ""
      }
      ${members
        .map(
          (member) => `
        <button class="option ${member.id === state.staffId ? "selected" : ""}" data-staff="${member.id}">
          <div class="avatar">${esc(initials(member.name))}</div>
          <h4>${esc(member.name)}</h4>
          <p>${esc(member.title)}</p>
        </button>`
        )
        .join("")}
    </div>`;
}

function renderTimeStep() {
  const days = buildDateStrip();
  return `
    <p class="muted" style="margin-top:0">Pick a day, then a time slot.</p>
    <div class="date-strip" id="date-strip">
      ${days
        .map(
          (day) => `
        <button class="date-pill ${day.iso === state.date ? "selected" : ""}" data-date="${day.iso}" ${day.open ? "" : "disabled"}>
          <span class="dow">${day.dow}</span>
          <span class="num">${day.num}</span>
          <span class="mon">${day.mon}</span>
        </button>`
        )
        .join("")}
    </div>
    <div id="slot-area">${renderSlots()}</div>`;
}

function buildDateStrip() {
  const openDates = new Set(state.dates);
  const days = [];
  const today = new Date();
  for (let offset = 0; offset < 21; offset += 1) {
    const date = new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset);
    const iso = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    days.push({
      iso,
      dow: date.toLocaleDateString(undefined, { weekday: "short" }),
      num: date.getDate(),
      mon: date.toLocaleDateString(undefined, { month: "short" }),
      open: openDates.has(iso),
    });
  }
  return days;
}

function renderSlots() {
  if (!state.date) {
    return `<div class="empty"><b>Pick a date to see open times</b>Times update live as bookings come in.</div>`;
  }
  if (state.loadingSlots) {
    return `<div class="empty"><b>Loading availability…</b>Checking the book for ${esc(formatDate(state.date))}.</div>`;
  }
  if (!state.slots.length) {
    return `<div class="empty"><b>No openings on this day</b>Try another date or choose a different barber.</div>`;
  }
  const now = new Date();
  return `<div class="slot-grid">${state.slots
    .map((slot) => {
      const [h, m] = slot.split(":").map(Number);
      const slotDate = new Date(state.date);
      slotDate.setHours(h, m, 0, 0);
      const past = slotDate <= now;
      return `<button class="slot ${slot === state.time ? "selected" : ""}" data-time="${slot}" ${past ? "disabled" : ""}>${slot}</button>`;
    })
    .join("")}</div>`;
}

function renderDetailsStep() {
  const service = selectedService();
  const staff = selectedStaff();
  return `
    <div class="summary-card">
      <div class="summary-row"><span>Service</span><span>${esc(service.name)}</span></div>
      <div class="summary-row"><span>Barber</span><span>${esc(staff ? staff.name : "Any available barber")}</span></div>
      <div class="summary-row"><span>Date</span><span>${esc(formatDate(state.date))}</span></div>
      <div class="summary-row"><span>Time</span><span>${esc(state.time)}</span></div>
      <div class="summary-row"><span>Duration</span><span>${service.duration_min} minutes</span></div>
      <div class="summary-row total-row"><span>Total</span><span>${money(service.price_cents)}</span></div>
    </div>
    <form id="details-form" novalidate>
      <div class="form-grid">
        <div class="field">
          <label for="f-name">Full name</label>
          <input class="input" id="f-name" name="customer_name" placeholder="Jordan Ellis" autocomplete="name" required>
          <div class="field-error" data-error="customer_name"></div>
        </div>
        <div class="field">
          <label for="f-phone">Phone</label>
          <input class="input" id="f-phone" name="phone" placeholder="(555) 123-4567" autocomplete="tel" required>
          <div class="field-error" data-error="phone"></div>
        </div>
        <div class="field">
          <label for="f-email">Email <span class="faint">(optional)</span></label>
          <input class="input" id="f-email" name="email" placeholder="you@example.com" autocomplete="email">
          <div class="field-error" data-error="email"></div>
        </div>
        <div class="field full">
          <label for="f-notes">Notes <span class="faint">(optional)</span></label>
          <textarea class="textarea" id="f-notes" name="notes" placeholder="Fade tight on the sides, keep the length on top."></textarea>
        </div>
      </div>
    </form>`;
}

function renderConfirmation(booking) {
  const service = selectedService();
  const staff = booking.staff_name;
  return `
    <div class="confirm">
      <div class="confirm-icon">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
      </div>
      <h3>You're on the book</h3>
      <p>${esc(booking.service_name)} with ${esc(staff)} on ${esc(formatDate(booking.local_date))} at <b>${esc(booking.local_time)}</b>. A confirmation has been sent to your phone.</p>
      <div class="code-box">
        <span>Confirmation code</span>
        <b>${esc(booking.code)}</b>
      </div>
      <p class="faint" style="font-size:13px">Keep this code safe — you'll need it along with your phone number to make changes.</p>
      <div style="display:flex;gap:10px;justify-content:center;margin-top:22px;flex-wrap:wrap">
        <button class="btn btn-ghost" id="btn-manage">Manage this booking</button>
        <button class="btn btn-primary" id="btn-new">Book another</button>
      </div>
    </div>`;
}

async function loadAvailability() {
  if (!state.serviceId) return;
  state.loadingSlots = true;
  render();
  const params = new URLSearchParams({ service_id: String(state.serviceId) });
  if (state.staffId !== null) params.set("staff_id", String(state.staffId));
  if (state.date) params.set("date", state.date);
  try {
    const data = await api(`/api/availability?${params}`);
    state.slots = data.slots || [];
    if (state.date && !state.slots.includes(state.time)) {
      state.time = null;
    }
  } catch (error) {
    state.slots = [];
    toast(error.message, "error");
  } finally {
    state.loadingSlots = false;
    render();
  }
}

async function loadDates() {
  const params = new URLSearchParams({ service_id: String(state.serviceId) });
  if (state.staffId !== null) params.set("staff_id", String(state.staffId));
  try {
    const data = await api(`/api/dates?${params}`);
    state.dates = data.dates || [];
    if (!state.date || !state.dates.includes(state.date)) {
      state.date = state.dates[0] || null;
    }
  } catch {
    state.dates = [];
  }
}

async function submitBooking(form) {
  if (state.submitting) return;
  const data = new FormData(form);
  const payload = {
    service_id: state.serviceId,
    staff_id: state.staffId,
    date: state.date,
    time: state.time,
    customer_name: String(data.get("customer_name") || "").trim(),
    phone: String(data.get("phone") || "").trim(),
    email: String(data.get("email") || "").trim(),
    notes: String(data.get("notes") || "").trim(),
  };
  state.submitting = true;
  const button = el("btn-next");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> Booking…';
  try {
    const booking = await api("/api/bookings", { method: "POST", body: payload });
    state.booking = booking;
    showConfirmation(booking);
    toast("Booking confirmed — code " + booking.code, "success");
  } catch (error) {
    showFieldError(error.field, error.message);
    toast(error.message, "error");
    if (error.field === "time") {
      setStep(3);
      await loadAvailability();
    }
  } finally {
    state.submitting = false;
    button.disabled = false;
    button.textContent = "Confirm booking";
  }
}

function showFieldError(field, message) {
  document.querySelectorAll(".field-error").forEach((node) => {
    node.textContent = "";
    node.previousElementSibling?.classList.remove("invalid");
  });
  if (!field) return;
  const target = document.querySelector(`.field-error[data-error="${field}"]`);
  if (target) {
    target.textContent = message;
    target.previousElementSibling?.classList.add("invalid");
  }
}

function showConfirmation(booking) {
  el("step-panel").innerHTML = renderConfirmation(booking);
  el("step-panel").classList.add("fade-in");
  document.querySelectorAll(".step-tab").forEach((tab, index) => {
    tab.classList.toggle("done", index < 3);
    tab.classList.remove("active");
  });
  el("btn-back").hidden = true;
  el("btn-next").hidden = true;
  el("foot-summary").innerHTML = `<b>${esc(booking.service_name)}</b> &nbsp;·&nbsp; ${esc(formatDate(booking.local_date))} at <b>${esc(booking.local_time)}</b>`;

  el("btn-new")?.addEventListener("click", () => {
    state.serviceId = null;
    state.staffId = null;
    state.date = null;
    state.time = null;
    state.booking = null;
    state.dates = [];
    state.slots = [];
    el("btn-back").hidden = false;
    el("btn-next").hidden = false;
    setStep(1);
  });

  el("btn-manage")?.addEventListener("click", () => {
    el("lookup-code").value = booking.code;
    el("lookup-phone").value = booking.phone;
    document.getElementById("manage").scrollIntoView({ behavior: "smooth" });
    lookupBooking();
  });
}

function resetToServices() {
  state.serviceId = null;
  state.staffId = null;
  state.date = null;
  state.time = null;
  state.slots = [];
  state.dates = [];
  el("btn-back").hidden = false;
  el("btn-next").hidden = false;
  setStep(1);
}

async function lookupBooking(event) {
  if (event) event.preventDefault();
  const code = el("lookup-code").value.trim();
  const phone = el("lookup-phone").value.trim();
  const target = el("lookup-result");
  if (!code || !phone) {
    target.innerHTML = `<div class="alert alert-error" style="margin-top:16px">Enter both your confirmation code and phone number.</div>`;
    return;
  }
  target.innerHTML = `<div class="empty">Looking up your booking…</div>`;
  try {
    const booking = await api(`/api/bookings/lookup?code=${encodeURIComponent(code)}&phone=${encodeURIComponent(phone)}`);
    target.innerHTML = renderLookupResult(booking);
    target.querySelector("#cancel-booking")?.addEventListener("click", () => cancelBooking(booking));
  } catch (error) {
    target.innerHTML = `<div class="alert alert-error" style="margin-top:16px">${esc(error.message)}</div>`;
  }
}

function renderLookupResult(booking) {
  const statusTag = `<span class="tag tag-${booking.status}">${esc(booking.status.replace("_", " "))}</span>`;
  const cancelButton =
    booking.status === "confirmed"
      ? `<button class="btn btn-danger btn-sm" id="cancel-booking">Cancel booking</button>`
      : "";
  return `
    <div class="lookup-result">
      <div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap">
        <div>
          <div class="option-meta" style="margin:0 0 6px">
            <span class="price" style="font-size:13px">${esc(booking.code)}</span>
            ${statusTag}
          </div>
          <h4 style="font-family:var(--font-body);font-size:17px;margin:0 0 4px">${esc(booking.service_name)}</h4>
          <p class="muted" style="margin:0;font-size:14px">${esc(booking.staff_name)} · ${esc(formatDate(booking.local_date))} at <b>${esc(booking.local_time)}</b> – ${esc(booking.local_end_time)}</p>
          <p class="muted" style="margin:6px 0 0;font-size:13.5px">${esc(booking.customer_name)} · ${esc(booking.phone)}</p>
        </div>
        <div style="display:flex;flex-direction:column;gap:8px;align-items:flex-end">
          <span class="price" style="font-size:16px">${money(booking.price_cents)}</span>
          ${cancelButton}
        </div>
      </div>
    </div>`;
}

async function cancelBooking(booking) {
  if (!confirm(`Cancel booking ${booking.code}?`)) return;
  try {
    const updated = await api(`/api/bookings/${encodeURIComponent(booking.code)}/cancel`, {
      method: "POST",
      body: { phone: booking.phone },
    });
    el("lookup-result").innerHTML = renderLookupResult(updated);
    toast("Booking cancelled", "success");
    if (state.booking && state.booking.code === updated.code) {
      showConfirmation(updated);
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

document.addEventListener("click", async (event) => {
  const serviceButton = event.target.closest("[data-service]");
  if (serviceButton) {
    state.serviceId = Number(serviceButton.dataset.service);
    state.staffId = null;
    state.date = null;
    state.time = null;
    state.slots = [];
    setStep(2);
    return;
  }

  const staffButton = event.target.closest("[data-staff]");
  if (staffButton) {
    state.staffId = staffButton.dataset.staff === "any" ? null : Number(staffButton.dataset.staff);
    state.date = null;
    state.time = null;
    state.slots = [];
    setStep(3);
    await loadDates();
    await loadAvailability();
    return;
  }

  const dateButton = event.target.closest("[data-date]");
  if (dateButton && !dateButton.disabled) {
    state.date = dateButton.dataset.date;
    state.time = null;
    state.slots = [];
    render();
    await loadAvailability();
    return;
  }

  const timeButton = event.target.closest("[data-time]");
  if (timeButton && !timeButton.disabled) {
    state.time = timeButton.dataset.time;
    render();
  }
});

el("btn-next").addEventListener("click", async () => {
  if (state.step === 1) {
    setStep(2);
  } else if (state.step === 2) {
    setStep(3);
    await loadDates();
    await loadAvailability();
  } else if (state.step === 3) {
    setStep(4);
  }
});

el("btn-back").addEventListener("click", () => {
  if (state.step === 2) setStep(1);
  else if (state.step === 3) setStep(2);
  else if (state.step === 4) setStep(3);
});

document.querySelectorAll(".step-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = Number(tab.dataset.step);
    if (target < state.step) setStep(target);
  });
});

el("step-panel").addEventListener("submit", async (event) => {
  if (event.target.id !== "details-form") return;
  event.preventDefault();
  await submitBooking(event.target);
});

el("lookup-form").addEventListener("submit", lookupBooking);

el("lookup-code").addEventListener("input", (event) => {
  event.target.value = event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
});

el("year").textContent = new Date().getFullYear();

loadConfig().catch((error) => {
  toast(error.message || "Could not load the shop", "error");
});
