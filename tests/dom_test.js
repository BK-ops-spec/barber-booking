const { JSDOM, VirtualConsole } = require("jsdom");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE || "http://127.0.0.1:8000";
if (BASE.startsWith("https://")) {
  process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
}
const ROOT = path.join(__dirname, "..", "static");

let failures = 0;
let checks = 0;

function check(name, condition, detail = "") {
  checks += 1;
  if (condition) {
    console.log(`  ok   ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL ${name} ${detail}`);
  }
}

async function loadPage(url, { width = 1400 } = {}) {
  const virtualConsole = new VirtualConsole();
  const errors = [];
  virtualConsole.on("jsdomError", (err) => errors.push(err.message));
  virtualConsole.on("error", (msg) => errors.push(String(msg)));

  const html = fs.readFileSync(path.join(ROOT, url.endsWith("admin") ? "admin.html" : "index.html"), "utf8");
  const dom = new JSDOM(html, {
    url: BASE + url,
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole,
  });

  const { window } = dom;
  const jar = new Map();
  window.fetch = async (input, init) => {
    const url = new URL(input, BASE).toString();
    const headers = Object.assign({}, (init && init.headers) || {});
    if (jar.size) headers.Cookie = [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
    const response = await fetch(url, Object.assign({}, init, { headers }));
    const cookies = response.headers.getSetCookie ? response.headers.getSetCookie() : [];
    for (const cookie of cookies) {
      const [pair] = cookie.split(";");
      const index = pair.indexOf("=");
      const key = pair.slice(0, index).trim();
      const value = pair.slice(index + 1).trim();
      if (value) jar.set(key, value);
      else jar.delete(key);
    }
    return response;
  };
  window.confirm = () => true;
  Object.defineProperty(window, "innerWidth", { value: width, writable: true });

  await new Promise((resolve) => {
    if (window.document.readyState === "complete") return resolve();
    window.addEventListener("load", resolve, { once: true });
    setTimeout(resolve, 8000);
  });
  await new Promise((resolve) => setTimeout(resolve, 700));
  return { dom, window, errors };
}

async function main() {
  console.log("\ncustomer booking flow");
  const { window, errors } = await loadPage("/");
  const doc = window.document;

  check("no jsdom errors", errors.length === 0, errors.join(" | "));
  check("shop name rendered", doc.getElementById("shop-name").textContent.length > 0);
  check("menu rendered", doc.querySelectorAll("#services-grid .service-card").length >= 6);
  check("crew rendered", doc.querySelectorAll("#team-grid .team-card").length >= 3);

  const serviceButtons = doc.querySelectorAll("[data-service]");
  check("service options rendered", serviceButtons.length >= 6);
  serviceButtons[0].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 250));

  check("step 2 shows barber options", doc.querySelectorAll("[data-staff]").length >= 1);
  check("back button enabled on step 2", !doc.getElementById("btn-back").disabled);

  const anyStaff = doc.querySelector('[data-staff="any"]');
  check("any-barber option present", Boolean(anyStaff));
  anyStaff.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 700));

  const slots = doc.querySelectorAll("[data-time]");
  check("time slots rendered", slots.length > 0, `count=${slots.length}`);
  const datePills = doc.querySelectorAll("[data-date]");
  check("date strip rendered", datePills.length === 21, `count=${datePills.length}`);
  const enabledDates = [...datePills].filter((pill) => !pill.disabled);
  check("some dates are bookable", enabledDates.length > 0);

  const firstSlot = [...slots].find((slot) => !slot.disabled);
  const slotTime = firstSlot.dataset.time;
  firstSlot.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 200));
  const selectedSlot = doc.querySelector(`[data-time="${slotTime}"]`);
  check("slot marked selected", selectedSlot && selectedSlot.classList.contains("selected"));

  doc.getElementById("btn-next").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 250));
  check("details form rendered", Boolean(doc.getElementById("details-form")));

  doc.getElementById("f-name").value = "Jordan Ellis";
  doc.getElementById("f-phone").value = "555-0101-0101";
  doc.getElementById("f-email").value = "jordan@example.com";
  doc.getElementById("f-notes").value = "Fade on the sides";
  doc.getElementById("details-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 900));

  const codeBox = doc.querySelector(".code-box b");
  check("confirmation rendered", Boolean(codeBox));
  check("confirmation code shown", codeBox && /^[A-Z0-9]{6}$/.test(codeBox.textContent.trim()), codeBox && codeBox.textContent);

  const confirmText = doc.querySelector(".confirm p");
  check("confirmation mentions service", confirmText && confirmText.textContent.length > 10);

  console.log("\nbooking lookup");
  const code = codeBox ? codeBox.textContent.trim() : "";
  doc.getElementById("lookup-code").value = code;
  doc.getElementById("lookup-phone").value = "55501010101";
  doc.getElementById("lookup-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 700));
  check("lookup returns booking", Boolean(doc.querySelector(".lookup-result")));
  check("lookup shows code", doc.querySelector(".lookup-result")?.textContent.includes(code));
  check("cancel button present", Boolean(doc.getElementById("cancel-booking")));

  console.log("\nbooking lookup with wrong phone");
  doc.getElementById("lookup-code").value = code;
  doc.getElementById("lookup-phone").value = "5550000000";
  doc.getElementById("lookup-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 700));
  const errBox = doc.querySelector("#lookup-result .alert-error");
  check("wrong phone shows error", Boolean(errBox));

  console.log("\nadmin portal");
  const admin = await loadPage("/admin");
  const adoc = admin.window.document;
  check("login screen visible", !adoc.getElementById("login-screen").classList.contains("hidden"));
  check("admin shell hidden pre-login", adoc.getElementById("admin-shell").classList.contains("hidden"));

  adoc.getElementById("login-user").value = "admin";
  adoc.getElementById("login-pass").value = "admin123";
  adoc.getElementById("login-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 1200));

  check("admin shell visible after login", !adoc.getElementById("admin-shell").classList.contains("hidden"));
  check("stats rendered", adoc.querySelectorAll("#stat-grid .stat").length === 4);
  check("services table rendered", adoc.querySelectorAll("#service-list tbody tr").length >= 6);
  check("barber cards rendered", adoc.querySelectorAll("#barber-list .team-card").length >= 3);
  check("shop hours rows rendered", adoc.querySelectorAll("[data-shop-day]").length === 14);

  const navItems = adoc.querySelectorAll("[data-view]");
  navItems[2].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 200));
  check("nav switches view", !adoc.getElementById("view-barbers").classList.contains("hidden"));

  navItems[1].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 200));
  check("services view visible", !adoc.getElementById("view-services").classList.contains("hidden"));
  check("other views hidden", adoc.getElementById("view-schedule").classList.contains("hidden"));

  console.log(`\n${checks - failures}/${checks} checks passed`);
  if (failures) {
    console.log(`${failures} FAILURES`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error("harness error:", err);
  process.exit(1);
});
