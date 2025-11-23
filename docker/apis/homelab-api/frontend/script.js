// ===== Helper: log to activity pane =====
function logMessage(message) {
  const log = document.getElementById("activity-log");
  const timestamp = new Date().toISOString().replace("T", " ").slice(0, 19);
  log.textContent = `[${timestamp}] ${message}\n` + log.textContent;
}

// ===== API key storage =====
function loadSettings() {
  const savedKey = localStorage.getItem("homelab_api_key");
  if (savedKey) {
    document.getElementById("api-key").value = savedKey;
    logMessage("Loaded API key from localStorage.");
  } else {
    logMessage("No API key found in localStorage.");
  }
}

function saveSettings() {
  const key = document.getElementById("api-key").value.trim();
  if (!key) {
    document.getElementById("settings-status").textContent = "API key is empty.";
    logMessage("Attempted to save empty API key.");
    return;
  }
  localStorage.setItem("homelab_api_key", key);
  document.getElementById("settings-status").textContent = "API key saved.";
  logMessage("API key saved to localStorage.");
}

function clearKey() {
  localStorage.removeItem("homelab_api_key");
  document.getElementById("api-key").value = "";
  document.getElementById("settings-status").textContent = "API key cleared.";
  logMessage("API key cleared from localStorage.");
}

document.getElementById("save-settings").addEventListener("click", saveSettings);
document.getElementById("clear-key").addEventListener("click", clearKey);

// ===== HTTP helper =====
function getHeaders() {
  const key = document.getElementById("api-key").value.trim();
  return {
    "Content-Type": "application/json",
    "x-api-key": key
  };
}

async function fetchJson(path, options = {}) {
  const resp = await fetch(path, {
    ...options,
    headers: { ...(options.headers || {}), ...getHeaders() }
  });

  const text = await resp.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }

  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${JSON.stringify(data)}`);
  }

  return data;
}

// ===== Services =====
async function loadServices() {
  const container = document.getElementById("services");
  const meta = document.getElementById("services-meta");

  container.innerHTML =
    '<div class="px-3 py-2 text-xs text-slate-400">Loading services…</div>';
  meta.textContent = "";

  try {
    const services = await fetchJson("/services");
    logMessage(`Loaded ${services.length} services from /services.`);

    if (!services.length) {
      container.innerHTML =
        '<div class="px-3 py-2 text-xs text-slate-400">No running containers.</div>';
      meta.textContent = "0 services";
      return;
    }

    meta.textContent = `${services.length} services`;

    // Build a simple table
    let html = `
      <table class="w-full border-collapse text-xs">
        <thead class="bg-slate-900 text-slate-300 border-b border-slate-800">
          <tr>
            <th class="px-3 py-2 text-left font-semibold">Name</th>
            <th class="px-3 py-2 text-left font-semibold">Image</th>
            <th class="px-3 py-2 text-left font-semibold">Status</th>
            <th class="px-3 py-2 text-left font-semibold">Uptime</th>
            <th class="px-3 py-2 text-left font-semibold">Action</th>
          </tr>
        </thead>
        <tbody class="bg-black text-slate-200">
    `;

    for (const svc of services) {
      html += `
        <tr class="border-b border-slate-900">
          <td class="px-3 py-2 align-top">
            <div class="font-mono text-[11px] text-sky-300">${svc.name}</div>
            <div class="text-[10px] text-slate-500">${svc.id}</div>
          </td>
          <td class="px-3 py-2 align-top text-[11px] text-slate-300">
            ${svc.image}
          </td>
          <td class="px-3 py-2 align-top text-[11px] text-slate-300">
            ${svc.status}
          </td>
          <td class="px-3 py-2 align-top text-[11px] text-slate-300">
            ${svc.uptime || "unknown"}
          </td>
          <td class="px-3 py-2 align-top">
            <button
              class="border border-amber-500 bg-black px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-amber-200 hover:bg-amber-900/40"
              onclick="restartService('${svc.name}')"
            >
              Restart
            </button>
          </td>
        </tr>
      `;
    }

    html += "</tbody></table>";
    container.innerHTML = html;
  } catch (err) {
    container.innerHTML =
      '<div class="px-3 py-2 text-xs text-rose-300">Error loading services: ' +
      err.message +
      "</div>";
    logMessage("Error loading services: " + err.message);
  }
}

document.getElementById("load-services").addEventListener("click", loadServices);

// ===== Restart service =====
async function restartService(name) {
  const svc = name.toLowerCase();
  logMessage(`Restart requested for service: ${svc}`);

  try {
    const data = await fetchJson(`/restart/${svc}`, { method: "POST" });
    logMessage(`Restart successful for ${svc}: ${JSON.stringify(data)}`);
    alert(`Restarted ${svc}.`);
    // Optionally refresh services after restart
    loadServices();
  } catch (err) {
    logMessage(`Restart failed for ${svc}: ${err.message}`);
    alert(`Error restarting ${svc}: ${err.message}`);
  }
}

// Expose to global scope for inline onclick
window.restartService = restartService;

// ===== Deploy =====
async function triggerDeploy() {
  const output = document.getElementById("deploy-output");
  output.textContent = "Triggering deploy…";
  logMessage("Deploy triggered via /deploy.");

  try {
    const data = await fetchJson("/deploy", { method: "POST" });
    output.textContent = JSON.stringify(data, null, 2);
    logMessage("Deploy API response: " + JSON.stringify(data));
  } catch (err) {
    output.textContent = "Error: " + err.message;
    logMessage("Deploy failed: " + err.message);
  }
}

document.getElementById("deploy-btn").addEventListener("click", triggerDeploy);

// ===== Initial load =====
loadSettings();
