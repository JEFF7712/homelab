// Load saved API key
function loadSettings() {
  const savedKey = localStorage.getItem("homelab_api_key");
  if (savedKey) document.getElementById("api-key").value = savedKey;
}

function saveSettings() {
  const key = document.getElementById("api-key").value.trim();
  localStorage.setItem("homelab_api_key", key);
  document.getElementById("settings-status").textContent = "Saved.";
}

document.getElementById("save-settings").addEventListener("click", saveSettings);

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

// Load Services
document.getElementById("load-services").addEventListener("click", async () => {
  const div = document.getElementById("services");
  div.textContent = "Loading...";
  try {
    const services = await fetchJson("/services");
    div.innerHTML = "";

    services.forEach(svc => {
      const el = document.createElement("div");
      el.className = "service-card";
      el.innerHTML = `
        <strong>${svc.name}</strong> (${svc.id})<br/>
        Image: ${svc.image}<br/>
        Status: ${svc.status}<br/>
        Uptime: ${svc.uptime || "unknown"}<br/>
        <button onclick="restartService('${svc.name}')">Restart</button>
        <hr/>
      `;
      div.appendChild(el);
    });

  } catch (err) {
    div.textContent = "Error: " + err.message;
  }
});

// Restart service
async function restartService(name) {
  const svc = name.toLowerCase();
  try {
    const data = await fetchJson(`/restart/${svc}`, { method: "POST" });
    alert("Restarted: " + JSON.stringify(data));
  } catch (err) {
    alert("Error: " + err.message);
  }
}

// Status
document.getElementById("load-status").addEventListener("click", async () => {
  const out = document.getElementById("status-output");
  out.textContent = "Loading...";
  try {
    const data = await fetchJson("/status");
    out.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// Deploy
document.getElementById("deploy-btn").addEventListener("click", async () => {
  const out = document.getElementById("deploy-output");
  out.textContent = "Triggering...";
  try {
    const data = await fetchJson("/deploy", { method: "POST" });
    out.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// Initial load
loadSettings();
