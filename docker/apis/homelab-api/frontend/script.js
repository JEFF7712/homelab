// ===== Small helper =====
function logMessage(message) {
  const log = document.getElementById("activity-log");
  if (!log) return;
  const timestamp = new Date().toISOString().replace("T", " ").slice(0, 19);
  log.textContent = `[${timestamp}] ${message}\n` + log.textContent;
}

// ===== API key handling =====
function loadSettings() {
  const savedKey = localStorage.getItem("homelab_api_key");
  const input = document.getElementById("api-key");
  if (savedKey && input) {
    input.value = savedKey;
    logMessage("Loaded API key from localStorage.");
  } else {
    logMessage("No API key found in localStorage.");
  }
}

function saveSettings() {
  const input = document.getElementById("api-key");
  const status = document.getElementById("settings-status");
  if (!input) return;

  const key = input.value.trim();
  if (!key) {
    if (status) status.textContent = "API key is empty.";
    logMessage("Attempted to save empty API key.");
    return;
  }

  localStorage.setItem("homelab_api_key", key);
  if (status) status.textContent = "API key saved.";
  logMessage("API key saved to localStorage.");

  input.value = "";
}

function clearKey() {
  const input = document.getElementById("api-key");
  const status = document.getElementById("settings-status");
  localStorage.removeItem("homelab_api_key");
  if (input) input.value = "";
  if (status) status.textContent = "API key cleared.";
  logMessage("API key cleared from localStorage.");
}

// ===== HTTP helpers =====
function getHeaders() {
  const input = document.getElementById("api-key");
  const key = input ? input.value.trim() : "";
  return {
    "Content-Type": "application/json",
    "x-api-key": key,
  };
}

async function fetchJson(path, options = {}) {
  const resp = await fetch(path, {
    ...options,
    headers: { ...(options.headers || {}), ...getHeaders() },
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
  const tbody = document.getElementById("services-body");
  const meta = document.getElementById("services-meta");

  if (!tbody) {
    logMessage("services-body element not found.");
    return;
  }

  tbody.innerHTML = `
    <tr>
      <td colspan="5" class="px-3 py-2 text-xs text-slate-400">
        Loading services…
      </td>
    </tr>
  `;
  if (meta) meta.textContent = "";

  try {
    const services = await fetchJson("/services");
    logMessage(`Loaded ${services.length} services from /services.`);

    if (!services.length) {
      tbody.innerHTML = `
        <tr>
          <td colspan="5" class="px-3 py-2 text-xs text-slate-400">
            No running containers.
          </td>
        </tr>
      `;
      if (meta) meta.textContent = "0 services";
      return;
    }

    if (meta) meta.textContent = `${services.length} services`;

    let rows = "";
    for (const svc of services) {
      rows += `
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

    tbody.innerHTML = rows;
  } catch (err) {
    tbody.innerHTML = `
      <tr>
        <td colspan="5" class="px-3 py-2 text-xs text-rose-300">
          Error loading services: ${err.message}
        </td>
      </tr>
    `;
    logMessage("Error loading services: " + err.message);
  }
}

// ===== Restart service =====
async function restartService(name) {
  const svc = name.toLowerCase();
  logMessage(`Restart requested for service: ${svc}`);

  try {
    const data = await fetchJson(`/restart/${svc}`, { method: "POST" });
    logMessage(`Restart successful for ${svc}: ${JSON.stringify(data)}`);
    alert(`Restarted ${svc}.`);
    loadServices();
  } catch (err) {
    logMessage(`Restart failed for ${svc}: ${err.message}`);
    alert(`Error restarting ${svc}: ${err.message}`);
  }
}
window.restartService = restartService;

// ===== Deploy =====
async function triggerDeploy() {
  const output = document.getElementById("deploy-output");
  if (output) output.textContent = "Triggering deploy…";
  logMessage("Deploy triggered via /deploy.");

  try {
    const data = await fetchJson("/deploy", { method: "POST" });
    if (output) output.textContent = JSON.stringify(data, null, 2);
    logMessage("Deploy API response: " + JSON.stringify(data));
  } catch (err) {
    if (output) output.textContent = "Error: " + err.message;
    logMessage("Deploy failed: " + err.message);
  }
}

// ===== Wire up events after page load =====
window.addEventListener("load", () => {
  console.log("Page loaded, wiring up controls…");

  const saveBtn = document.getElementById("save-settings");
  const clearBtn = document.getElementById("clear-key");
  const loadBtn = document.getElementById("load-services");
  const deployBtn = document.getElementById("deploy-btn");

  console.log("save-settings exists:", !!saveBtn);
  console.log("clear-key exists:", !!clearBtn);
  console.log("load-services exists:", !!loadBtn);
  console.log("deploy-btn exists:", !!deployBtn);

  if (saveBtn) saveBtn.addEventListener("click", saveSettings);
  if (clearBtn) clearBtn.addEventListener("click", clearKey);
  if (loadBtn) loadBtn.addEventListener("click", loadServices);
  if (deployBtn) deployBtn.addEventListener("click", triggerDeploy);

  loadSettings();
});
