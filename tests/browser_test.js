const { chromium } = require("playwright-core");

const BASE = process.env.BASE || "http://127.0.0.1:8000";
const SECURE = BASE.startsWith("https://");
const CONTEXT_OPTIONS = { ignoreHTTPSErrors: SECURE };
(async () => {
  const browser = await chromium.launch({ args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, ...CONTEXT_OPTIONS });
  const errs = [];
  page.on("pageerror", (e) => errs.push("pageerror: " + e.message));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    if (text.includes("Failed to load resource")) return;
    errs.push("console: " + text);
  });
  page.on("dialog", (d) => d.accept());
  let fails = 0;
  const check = (n, c, d = "") => { console.log(`  ${c ? "ok  " : "FAIL"} ${n}${d ? " " + d : ""}`); if (!c) fails++; };

  console.log("\ncustomer booking in a real browser");
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.waitForTimeout(600);

  await page.click("[data-service='2']");
  await page.waitForTimeout(400);
  check("step 2 visible", await page.locator("[data-staff]").first().isVisible());

  await page.click("[data-staff='1']");
  await page.waitForTimeout(900);
  const slotCount = await page.locator("[data-time]").count();
  check("slots rendered for barber 1", slotCount > 0, `count=${slotCount}`);

  const chosen = page.locator("[data-time]:not([disabled])").first();
  const time = await chosen.getAttribute("data-time");
  const date = await page.locator("[data-date].selected").getAttribute("data-date");
  await chosen.click();
  await page.waitForTimeout(300);
  check("slot selected", await page.locator(`[data-time="${time}"].selected`).count() === 1);

  await page.click("#btn-next");
  await page.waitForTimeout(400);
  check("details form shown", await page.locator("#details-form").isVisible());

  await page.fill("#f-name", "Casey Morgan");
  await page.fill("#f-phone", "555-777-8888");
  await page.fill("#f-email", "casey@example.com");
  await page.fill("#f-notes", "Keep it short on top");
  await page.click("#btn-next");
  await page.waitForTimeout(1200);

  const code = await page.locator(".code-box b").textContent();
  check("confirmation code issued", /^[A-Z0-9]{6}$/.test(code.trim()), code);
  const confirmText = await page.locator(".confirm p").first().textContent();
  check("confirmation names barber", confirmText.includes("Marcus"), confirmText.slice(0, 80));

  console.log("\nbooked slot disappears from availability");
  const avail = await page.evaluate(async ({ t, date }) => {
    const r = await fetch(`/api/availability?service_id=2&staff_id=1&date=${date}`);
    const d = await r.json();
    return d.slots.includes(t);
  }, { t: time, date });
  check("booked slot removed from public availability", avail === false);

  console.log("\nsecond customer cannot take the same slot");
  const conflict = await page.evaluate(async ({ t, date }) => {
    const r = await fetch("/api/bookings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ service_id: 2, staff_id: 1, date, time: t, customer_name: "Rival Agent", phone: "5550001111" }),
    });
    return { status: r.status, body: await r.json() };
  }, { t: time, date });
  check("conflicting booking rejected", conflict.status === 400, JSON.stringify(conflict.body));

  console.log("\nmanage booking");
  await page.fill("#lookup-code", code.trim());
  await page.fill("#lookup-phone", "5557778888");
  await page.click("#lookup-form button[type=submit]");
  await page.waitForTimeout(900);
  check("booking found by code + phone", await page.locator(".lookup-result").isVisible());
  check("cancel button shown", await page.locator("#cancel-booking").isVisible());
  await page.click("#cancel-booking");
  await page.waitForTimeout(1000);
  check("booking cancelled", (await page.locator(".lookup-result .tag").textContent()).toLowerCase().includes("cancelled"));

  console.log("\ncancelled slot returns to availability");
  const restored = await page.evaluate(async ({ t, date }) => {
    const r = await fetch(`/api/availability?service_id=2&staff_id=1&date=${date}`);
    const d = await r.json();
    return d.slots.includes(t);
  }, { t: time, date });
  check("slot available again after cancel", restored === true);

  console.log("\nadmin portal");
  const admin = await browser.newPage({ viewport: { width: 1440, height: 1000 }, ...CONTEXT_OPTIONS });
  const aerrs = [];
  admin.on("pageerror", (e) => aerrs.push(e.message));
  await admin.goto(BASE + "/admin", { waitUntil: "networkidle" });
  await admin.fill("#login-user", "admin");
  await admin.fill("#login-pass", "admin123");
  await admin.click("#login-btn");
  await admin.waitForTimeout(1500);
  check("admin logged in", await admin.locator("#admin-shell").isVisible());

  const rowCount = await admin.locator(".appt-row").count();
  check("appointments listed", rowCount > 0, `count=${rowCount}`);

  await admin.click("#new-appointment");
  await admin.waitForTimeout(1200);
  check("new booking modal open", await admin.locator("#modal-card").isVisible());
  const adminSlots = await admin.locator("#m-time option").count();
  check("admin modal loads availability", adminSlots > 1, `options=${adminSlots}`);
  await admin.fill("#m-customer", "Walk In Guest");
  await admin.fill("#m-phone", "5552223333");
  await admin.click("[data-act='save-appointment']");
  await admin.waitForTimeout(1500);
  check("admin created booking", (await admin.locator(".appt-row").count()) >= rowCount);

  await admin.click("[data-view='hours']");
  await admin.waitForTimeout(400);
  check("hours editor rendered", await admin.locator("[data-shop-day]").count() === 14);
  await admin.click("#save-hours");
  await admin.waitForTimeout(900);
  check("hours saved without error", (await admin.locator(".toast.error").count()) === 0);

  await admin.click("[data-view='settings']");
  await admin.waitForTimeout(400);
  await admin.fill("#s-tagline", "Sharp fades, honest work");
  await admin.click("#save-settings");
  await admin.waitForTimeout(900);
  check("settings saved without error", (await admin.locator(".toast.error").count()) === 0);

  const home = await browser.newPage(CONTEXT_OPTIONS);
  await home.goto(BASE + "/", { waitUntil: "networkidle" });
  await home.waitForTimeout(700);
  const tagline = await home.locator("#hero-tagline").textContent();
  check("customer site reflects new tagline", tagline.includes("honest work"), tagline);

  check("no customer page errors", errs.length === 0, errs.join(" | "));
  check("no admin page errors", aerrs.length === 0, aerrs.join(" | "));

  await browser.close();
  console.log(fails ? `\n${fails} FAILURES` : "\nall e2e checks passed");
  process.exit(fails ? 1 : 0);
})();
