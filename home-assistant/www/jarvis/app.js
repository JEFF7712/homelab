(function () {
  'use strict';

  // --- Configuration & Defaults ---
  const config = {
    satelliteEntity: 'assist_satellite.homelab_05_satellite_assist_satellite',
    muteEntity: 'switch.homelab_05_satellite_mute',
    mediaPlayerEntity: 'media_player.homelab_05_satellite_media_player_2',
    host: location.host,
    token: null
  };

  // State
  let currentState = 'idle';
  let ws = null;
  let msgId = 1;
  let reconnectTimer = null;
  let reconnectAttempts = 0;
  let lastEntityStates = {};

  // Bounded connection supervision: heartbeat ping every 30 s, reconnect
  // when nothing (events or pongs) arrives for 90 s, backoff capped at
  // 30 s with jitter. Retries continue so the kiosk recovers unattended;
  // only the timers are bounded, never the recovery itself.
  const HEARTBEAT_INTERVAL_MS = 30000;
  const STALE_AFTER_MS = 90000;
  const MAX_BACKOFF_MS = 30000;
  let lastMessageAt = 0;
  let heartbeatTimer = null;
  let watchdogTimer = null;

  // DOM Elements
  const appEl = document.getElementById('app');
  const statusLabel = document.getElementById('status-text');
  const connectionBadge = document.getElementById('connection-badge');
  const promptModal = document.getElementById('token-prompt');
  const tokenInput = document.getElementById('token-input');
  const tokenSubmit = document.getElementById('token-submit');

  // --- Parse URL Parameters ---
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('entity')) config.satelliteEntity = urlParams.get('entity');
  if (urlParams.get('mute')) config.muteEntity = urlParams.get('mute');
  if (urlParams.get('media')) config.mediaPlayerEntity = urlParams.get('media');
  if (urlParams.get('token')) config.token = urlParams.get('token');

  // Per-state face colors, overridden by config.json when present
  const faceColors = {};

  // --- Face version self-update ---
  // The page URL carries ?v=<face_version> and the index.html loader
  // threads it into every asset URL, so navigating to a new v bypasses
  // the browser cache for the whole face. Poll config.json (never cached)
  // and self-navigate when face_version moves: pushing new www files plus
  // a version bump is enough, the kiosk adopts it without a manual reload.
  let faceVersion = urlParams.get('v');
  window.__jarvisFaceVersion = faceVersion;
  function faceVersionTarget(configV) {
    if (configV === undefined || configV === null) return null;
    const want = String(configV);
    if (faceVersion === want) return null;
    const url = new URL(window.location.href);
    url.searchParams.set('v', want);
    return url.toString();
  }
  window.__jarvisFaceVersionTarget = faceVersionTarget;
  setInterval(() => {
    fetch('config.json', { cache: 'no-store' })
      .then(res => res.json())
      .then(data => {
        const target = faceVersionTarget(data.face_version);
        if (target) window.location.href = target;
      })
      .catch(() => {});
  }, 60000);

  // Load external config.json if available (never cached: color edits
  // must reach the kiosk without a hard refresh)
  fetch('config.json', { cache: 'no-store' })
    .then(res => res.json())
    .then(data => {
      if (data.satellite_entity && !urlParams.get('entity')) config.satelliteEntity = data.satellite_entity;
      if (data.mute_entity && !urlParams.get('mute')) config.muteEntity = data.mute_entity;
      if (data.media_player_entity && !urlParams.get('media')) config.mediaPlayerEntity = data.media_player_entity;
      for (const state of ['idle', 'listening', 'processing', 'responding', 'music', 'muted']) {
        if (data['face_color_' + state]) faceColors[state] = data['face_color_' + state];
      }
      // Re-apply the current state so configured colors take effect
      const s = currentState;
      currentState = '';
      setState(s, statusLabel ? statusLabel.textContent : undefined);
    })
    .catch(() => {});

  // --- Token Resolution ---
  function resolveToken() {
    if (config.token) return config.token;

    // 1. Check dedicated Jarvis storage
    const savedToken = localStorage.getItem('jarvis_token');
    if (savedToken) return savedToken;

    // 2. Check Home Assistant default localStorage token (if not expired)
    try {
      const hassTokens = JSON.parse(localStorage.getItem('hassTokens'));
      if (hassTokens && hassTokens.access_token) {
        if (!hassTokens.expires || hassTokens.expires > Date.now()) {
          return hassTokens.access_token;
        }
      }
    } catch (e) {}

    return null;
  }

  // --- Procedural Eye Engine (Cozmo-style) ---
  // Eyes are drawn on canvas every frame from an interpolated parameter set,
  // mirroring Anki Cozmo's procedural face: expression presets (eye scale plus
  // upper/lower lids with y/angle/bend) layered with a look assistant
  // (saccades) and a blink assistant (vertical squash, like the real engine).
  // Geometry units are "vmin-like": 1 unit = 1% of min(canvasW, canvasH).
  const EYE_BASE_W = 40;
  const EYE_BASE_H = 58;
  const EYE_GAP = 12;
  const GAZE_X = 10; // max horizontal gaze offset (Cozmo uses 10px on 128 wide)
  const GAZE_Y = 5;  // vertical gaze damped, like Cozmo's Y_FACTOR

  // Lid angles are magnitudes in degrees; the left eye mirrors them
  // (Cozmo anger: left upper -30, right upper +30).
  // `asym` holds optional right-eye overrides for asymmetric expressions
  // (Cozmo confusion squishes one eye): multipliers for scale, additive for lids.
  const EYE_PRESETS = {
    idle:       { sx: 1.0,  sy: 1.0,  upperY: 0,    upperAngle: 0,  upperBend: 0,
                  lowerY: 0,   lowerAngle: 0, lowerBend: 0,    radius: 0.1,
                  look: 'wander', bounce: false, pulse: false, glow: 1.0,
                  asym: null },
    listening:  { sx: 1.1,  sy: 1.16, upperY: 0,    upperAngle: 0,  upperBend: 0,
                  lowerY: 0,   lowerAngle: 0, lowerBend: 0,    radius: 0.1,
                  look: 'center', bounce: false, pulse: true,  glow: 1.3,
                  asym: null },
    processing: { sx: 0.95, sy: 0.85, upperY: 0.15, upperAngle: 0,  upperBend: 0,
                  lowerY: 0,   lowerAngle: 0, lowerBend: 0,    radius: 0.1,
                  look: 'think',  bounce: false, pulse: false, glow: 1.0,
                  asym: { sxM: 1.0, syM: 0.8, upperA: 0, lowerA: 0.22 } },
    responding: { sx: 1.05, sy: 0.95, upperY: 0,    upperAngle: 0,  upperBend: 0,
                  lowerY: 0.3, lowerAngle: 0, lowerBend: 0.35, radius: 0.1,
                  look: 'center', bounce: true,  pulse: false, glow: 1.15,
                  asym: null },
    music:      { sx: 1.02, sy: 0.92, upperY: 0,    upperAngle: 0,  upperBend: 0,
                  lowerY: 0.1, lowerAngle: 0, lowerBend: 0.15, radius: 0.1,
                  look: 'wander', bounce: true,  pulse: true,  glow: 1.0,
                  asym: null },
    muted:      { sx: 0.9,  sy: 1.0,  upperY: 0.52, upperAngle: 0,  upperBend: 0,
                  lowerY: 0.5, lowerAngle: 0, lowerBend: 0,    radius: 0.1,
                  look: 'none',   bounce: false, pulse: false, glow: 0.5,
                  asym: null }
  };

  const PARAM_RATES = {
    cx: 10, cy: 10, sx: 7, sy: 7,
    upperY: 9, upperAngle: 9, upperBend: 9,
    lowerY: 9, lowerAngle: 9, lowerBend: 9,
    radius: 7, glow: 5,
    rSxM: 7, rSyM: 7, rUpperA: 7, rLowerA: 7
  };

  const Eyes = (function () {
    const canvas = document.getElementById('face-canvas');
    const ctx = canvas ? canvas.getContext('2d') : null;
    let W = 0, H = 0;
    let fillColor = '#00f0ff';
    let glowColor = 'rgba(0, 240, 255, 0.45)';
    let vigGrad = null;

    const cur = { cx: 0, cy: 0, sx: 1, sy: 1,
      upperY: 0, upperAngle: 0, upperBend: 0,
      lowerY: 0, lowerAngle: 0, lowerBend: 0,
      radius: 0.1, glow: 1.0,
      rSxM: 1, rSyM: 1, rUpperA: 0, rLowerA: 0 };
    const tgt = Object.assign({}, cur);
    let lookMode = 'wander';
    let bounce = false;
    let pulse = false;
    let exprName = 'idle';
    let nextLookAt = 0;
    let nextBlinkAt = 0;
    let blinkStart = -1;
    const blinkDur = 0.11;
    let extraBlinkAt = -1;
    let blinkCount = 0;
    let rafStarted = false;

    function readColors() {
      if (!window.getComputedStyle || !appEl) return;
      const cs = getComputedStyle(appEl);
      const fill = (cs.getPropertyValue('--eye-bg') || '').trim();
      const glow = (cs.getPropertyValue('--primary-glow') || '').trim();
      if (fill) fillColor = fill;
      if (glow) glowColor = glow;
    }

    function resize() {
      if (!canvas || !ctx) return;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      W = canvas.clientWidth;
      H = canvas.clientHeight;
      canvas.width = Math.max(1, Math.round(W * dpr));
      canvas.height = Math.max(1, Math.round(H * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      // Cache the vignette for this size (old-display feel, rebuilt on resize)
      vigGrad = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.35,
        W / 2, H / 2, Math.max(W, H) * 0.75);
      vigGrad.addColorStop(0, 'rgba(0,0,0,0)');
      vigGrad.addColorStop(1, 'rgba(0,0,0,0.32)');
    }

    function setExpression(state) {
      const p = EYE_PRESETS[state] || EYE_PRESETS.idle;
      exprName = EYE_PRESETS[state] ? state : 'idle';
      tgt.sx = p.sx; tgt.sy = p.sy;
      tgt.upperY = p.upperY; tgt.upperAngle = p.upperAngle; tgt.upperBend = p.upperBend;
      tgt.lowerY = p.lowerY; tgt.lowerAngle = p.lowerAngle; tgt.lowerBend = p.lowerBend;
      tgt.radius = p.radius; tgt.glow = p.glow;
      tgt.rSxM = p.asym ? p.asym.sxM : 1;
      tgt.rSyM = p.asym ? p.asym.syM : 1;
      tgt.rUpperA = p.asym ? p.asym.upperA : 0;
      tgt.rLowerA = p.asym ? p.asym.lowerA : 0;
      lookMode = p.look;
      bounce = p.bounce;
      pulse = p.pulse;
      if (lookMode === 'none') {
        tgt.cx = 0; tgt.cy = 0;
        blinkStart = -1;
        extraBlinkAt = -1;
      }
      nextLookAt = 0;
      nextBlinkAt = 0;
    }

    function pickLookTarget(now) {
      if (lookMode === 'none') {
        tgt.cx = 0; tgt.cy = 0;
        nextLookAt = now + 1;
      } else if (lookMode === 'center') {
        tgt.cx = (Math.random() * 2 - 1) * 0.15;
        tgt.cy = (Math.random() * 2 - 1) * 0.1;
        nextLookAt = now + 0.9 + Math.random() * 0.7;
      } else if (lookMode === 'up') {
        tgt.cx = (Math.random() < 0.5 ? -1 : 1) * (0.1 + Math.random() * 0.4);
        tgt.cy = -(0.5 + Math.random() * 0.5);
        nextLookAt = now + 1.5 + Math.random() * 1.5;
      } else if (lookMode === 'think') {
        tgt.cx = (Math.random() < 0.6 ? -1 : 1) * (0.4 + Math.random() * 0.4);
        tgt.cy = -(0.2 + Math.random() * 0.4);
        nextLookAt = now + 2.5 + Math.random() * 1.5;
      } else {
        if (Math.random() < 0.2) {
          tgt.cx = 0; tgt.cy = 0;
        } else {
          tgt.cx = Math.random() * 2 - 1;
          tgt.cy = (Math.random() * 2 - 1) * 0.6;
        }
        nextLookAt = now + 2.2 + Math.random() * 2.8;
      }
    }

    function blinkClose(now) {
      if (blinkStart < 0) return 0;
      const p = (now - blinkStart) / blinkDur;
      if (p >= 1) {
        blinkStart = -1;
        return 0;
      }
      return Math.sin(Math.PI * p);
    }

    function frame(now, dt) {
      if (nextLookAt === 0) nextLookAt = now + 0.4;
      if (nextBlinkAt === 0) nextBlinkAt = now + 1.5 + Math.random() * 2;
      if (now >= nextLookAt) pickLookTarget(now);
      if (lookMode !== 'none') {
        if (extraBlinkAt > 0 && now >= extraBlinkAt) {
          extraBlinkAt = -1;
          blinkStart = now;
          blinkCount++;
        } else if (blinkStart < 0 && now >= nextBlinkAt) {
          blinkStart = now;
          blinkCount++;
          if (Math.random() < 0.15) extraBlinkAt = now + blinkDur + 0.13;
          nextBlinkAt = now + 3.2 + Math.random() * 3.3;
        }
      }
      const k = function (rate) { return 1 - Math.exp(-rate * dt); };
      for (const key of Object.keys(PARAM_RATES)) {
        cur[key] += (tgt[key] - cur[key]) * k(PARAM_RATES[key]);
      }
      render(now);
    }

    function eyePath(x, y, w, h, r) {
      r = Math.max(0, Math.min(r, h / 2, w / 2));
      ctx.beginPath();
      ctx.moveTo(x - w / 2 + r, y - h / 2);
      ctx.arcTo(x + w / 2, y - h / 2, x + w / 2, y + h / 2, r);
      ctx.arcTo(x + w / 2, y + h / 2, x - w / 2, y + h / 2, r);
      ctx.arcTo(x - w / 2, y + h / 2, x - w / 2, y - h / 2, r);
      ctx.arcTo(x - w / 2, y - h / 2, x + w / 2, y - h / 2, r);
      ctx.closePath();
    }

    // Lids are clipped to the eye bounds inflated by 3px: wide enough to
    // cover the fill's 1px antialiased fringe (which otherwise survives as
    // a faint full-eye outline wherever lids cover the eye), far too small
    // to reach the neighboring eye. Clipping to the exact eye path or not
    // clipping at all both produce artifacts, one a fringe outline, the
    // other black bites from the 2x-wide lid masks overlapping neighbors.
    function drawLids(x, y, w, h, sign, lids) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(x - w / 2 - 3, y - h / 2 - 3, w + 6, h + 6);
      ctx.clip();
      ctx.fillStyle = '#000000';
      if (lids.upperY > 0.001) {
        const edgeY = -h / 2 + lids.upperY * h;
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(sign * lids.upperAngle * Math.PI / 180);
        ctx.beginPath();
        ctx.moveTo(-w, -h);
        ctx.lineTo(w, -h);
        ctx.lineTo(w, edgeY);
        ctx.quadraticCurveTo(0, edgeY + lids.upperBend * h, -w, edgeY);
        ctx.closePath();
        ctx.fill();
        ctx.restore();
      }
      if (lids.lowerY > 0.001) {
        const edgeY = h / 2 - lids.lowerY * h;
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(sign * lids.lowerAngle * Math.PI / 180);
        ctx.beginPath();
        ctx.moveTo(-w, h);
        ctx.lineTo(w, h);
        ctx.lineTo(w, edgeY);
        ctx.quadraticCurveTo(0, edgeY - lids.lowerBend * h, -w, edgeY);
        ctx.closePath();
        ctx.fill();
        ctx.restore();
      }
      ctx.restore();
    }

    function drawEye(x, y, w, h, sign, u, t, lids) {
      eyePath(x, y, w, h, w * cur.radius);
      ctx.save();
      ctx.shadowBlur = 0;
      ctx.shadowColor = 'transparent';
      ctx.globalAlpha = Math.min(1, cur.glow) * (pulse ? 0.93 + 0.07 * Math.sin(2 * Math.PI * 0.8 * t) : 1);
      ctx.fillStyle = fillColor;
      ctx.fill();
      ctx.restore();
      drawLids(x, y, w, h, sign, lids);
    }

    // Full-screen music visualizer, drawn instead of the eyes while media
    // plays. Procedural layered sines (the kiosk has no audio tap to
    // analyze), flat fills only, same cost class as the eyes.
    const reduceMotion = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    function drawMusicVisualizer(t, u) {
      const vt = reduceMotion ? 0 : t;
      ctx.fillStyle = '#000000';
      ctx.fillRect(0, 0, W, H);
      const nBars = 56;
      const margin = u * 6;
      const span = Math.max(1, W - margin * 2);
      const step = span / nBars;
      const barW = step * 0.62;
      const baseY = H * 0.88;
      const maxBarH = H * 0.55;
      ctx.fillStyle = fillColor;
      for (let i = 0; i < nBars; i++) {
        const d = i / (nBars - 1);
        const env = Math.pow(Math.sin(Math.PI * d), 0.7);
        const w1 = 0.5 + 0.5 * Math.sin(2 * Math.PI * 1.1 * vt + d * 9.0);
        const w2 = 0.5 + 0.5 * Math.sin(2 * Math.PI * 2.3 * vt - d * 14.0 + 1.7);
        const bh = Math.max(u * 1.5, maxBarH * env * (0.12 + 0.88 * (0.6 * w1 + 0.4 * w2)));
        ctx.fillRect(margin + i * step + (step - barW) / 2, baseY - bh, barW, bh);
      }
      const midY = H * 0.3;
      const amp = u * 6;
      ctx.strokeStyle = fillColor;
      ctx.lineWidth = Math.max(2, u * 0.7);
      ctx.beginPath();
      const steps = 120;
      for (let s = 0; s <= steps; s++) {
        const d = s / steps;
        const x = margin + d * span;
        const y = midY + amp * Math.sin(2 * Math.PI * 2.0 * vt + d * 10.0) *
          (0.5 + 0.5 * Math.sin(2 * Math.PI * 0.7 * vt - d * 5.0));
        if (s === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }

    function render(t) {
      if (!ctx || W <= 0 || H <= 0) return;
      const u = Math.min(W, H) / 100;
      if (exprName === 'music') {
        drawMusicVisualizer(t, u);
      } else {
        const squash = 1 - 0.96 * blinkClose(t);
        const wob = bounce ? 1 + 0.015 * Math.sin(2 * Math.PI * 2.6 * t) : 1;
        const eyeW = EYE_BASE_W * u * cur.sx;
        const eyeH = EYE_BASE_H * u * cur.sy * squash * wob;
        const gap = EYE_GAP * u;
        const cy0 = H * 0.47;
        const gx = cur.cx * GAZE_X * u;
        const gy = cur.cy * GAZE_Y * u;
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, W, H);
        drawEye(W / 2 - (eyeW / 2 + gap / 2) + gx, cy0 + gy, eyeW, eyeH, -1, u, t, {
          upperY: cur.upperY, upperAngle: cur.upperAngle, upperBend: cur.upperBend,
          lowerY: cur.lowerY, lowerAngle: cur.lowerAngle, lowerBend: cur.lowerBend
        });
        const rW = eyeW * cur.rSxM;
        const rH = eyeH * cur.rSyM;
        drawEye(W / 2 + (rW / 2 + gap / 2) + gx, cy0 + gy, rW, rH, 1, u, t, {
          upperY: cur.upperY + cur.rUpperA, upperAngle: cur.upperAngle, upperBend: cur.upperBend,
          lowerY: cur.lowerY + cur.rLowerA, lowerAngle: cur.lowerAngle, lowerBend: cur.lowerBend
        });
      } // end non-music eye branch; scanlines apply to both modes
      // Old-hardware display feel: scanlines (invisible over black, darken
      // the lit shapes) plus a cached vignette.
      ctx.fillStyle = 'rgba(0,0,0,0.28)';
      const pitch = Math.max(3, u * 0.45);
      const lineH = Math.max(1, pitch * 0.35);
      for (let y = 0; y < H; y += pitch) ctx.fillRect(0, y, W, lineH);
      if (vigGrad) {
        ctx.fillStyle = vigGrad;
        ctx.fillRect(0, 0, W, H);
      }
    }

    let lastT = 0;
    function loop(tms) {
      if (!rafStarted) return;
      requestAnimationFrame(loop);
      const now = tms / 1000;
      const dt = Math.min(0.05, lastT ? now - lastT : 0.016);
      lastT = now;
      frame(now, dt);
    }

    function start() {
      resize();
      readColors();
      if (typeof requestAnimationFrame === 'function' && !rafStarted) {
        rafStarted = true;
        requestAnimationFrame(loop);
      }
    }

    setExpression('idle');

    return {
      setExpression: setExpression,
      readColors: readColors,
      resize: resize,
      frame: frame,
      render: render,
      start: start,
      getCur: function () { return cur; },
      getTgt: function () { return tgt; },
      getBlinkCount: function () { return blinkCount; }
    };
  })();

  if (window.addEventListener) {
    window.addEventListener('resize', function () { Eyes.resize(); });
  }

  // --- State & Expressions ---
  function setState(newState, customLabel) {
    if (currentState === newState && !customLabel) return;
    currentState = newState;

    // Remove existing state classes
    appEl.classList.remove('state-idle', 'state-listening', 'state-processing', 'state-responding', 'state-music', 'state-muted');
    appEl.classList.add('state-' + newState);
    appEl.style.removeProperty('--primary-color');
    appEl.style.removeProperty('--eye-bg');

    // Apply configured face color for this state when present
    if (faceColors[newState]) {
      appEl.style.setProperty('--primary-color', faceColors[newState]);
      appEl.style.setProperty('--eye-bg', faceColors[newState]);
    }

    // Drive the procedural eyes (reads --eye-bg / --primary-glow for fill)
    Eyes.setExpression(newState);
    Eyes.readColors();

    // Update status text
    const label = customLabel || (
      newState === 'idle' ? 'JARVIS // IDLE' :
      newState === 'listening' ? 'JARVIS // LISTENING' :
      newState === 'processing' ? 'JARVIS // THINKING' :
      newState === 'responding' ? 'JARVIS // RESPONDING' :
      newState === 'music' ? 'JARVIS // PLAYING' :
      newState === 'muted' ? 'JARVIS // MUTED' : 'JARVIS'
    );
    if (statusLabel) statusLabel.textContent = label;
  }

  // --- WebSocket Connection ---
  function connectWebSocket() {
    const token = resolveToken();
    if (!token) {
      showTokenPrompt();
      return;
    }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${config.host}/api/websocket`;

    try {
      ws = new WebSocket(wsUrl);
    } catch (e) {
      handleDisconnect();
      return;
    }

    ws.onopen = function () {
      reconnectAttempts = 0;
      lastMessageAt = Date.now();
      startSupervision();
    };

    ws.onmessage = function (event) {
      lastMessageAt = Date.now();
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (e) {
        return;
      }

      if (data.type === 'auth_required') {
        ws.send(JSON.stringify({
          type: 'auth',
          access_token: token
        }));
      } else if (data.type === 'auth_ok') {
        if (connectionBadge) connectionBadge.classList.remove('visible');
        if (promptModal) promptModal.style.display = 'none';

        // Subscribe to exactly the entities the face displays. The server
        // filters state traffic to this set; nothing else is retained.
        ws.send(JSON.stringify({
          id: ++msgId,
          type: 'subscribe_entities',
          entity_ids: [config.satelliteEntity, config.muteEntity, config.mediaPlayerEntity]
        }));
      } else if (data.type === 'auth_invalid') {
        localStorage.removeItem('jarvis_token');
        showTokenPrompt('Authentication failed. Please verify your token.');
      } else if (data.type === 'result') {
        if (data.success === false) {
          handleDisconnect();
        }
        // subscribe_entities answers result:null; initial states arrive
        // as an event below. Nothing else to do here.
      } else if (data.type === 'pong') {
        // Heartbeat reply; liveness already recorded above.
      } else if (data.type === 'event' && data.event) {
        handleEntityEvent(data.event);
      }
    };

    ws.onerror = function () {
      handleDisconnect();
    };

    ws.onclose = function () {
      handleDisconnect();
    };
  }

  function handleDisconnect() {
    stopSupervision();
    try {
      if (ws && ws.readyState !== WebSocket.CLOSED) ws.close();
    } catch (e) {}
    ws = null;
    if (connectionBadge) connectionBadge.classList.add('visible');
    clearTimeout(reconnectTimer);
    reconnectAttempts++;
    const backoff = Math.min(1000 * Math.pow(1.5, reconnectAttempts), MAX_BACKOFF_MS);
    const jitter = backoff * (0.5 + Math.random() * 0.5);
    reconnectTimer = setTimeout(connectWebSocket, jitter);
  }

  function startSupervision() {
    stopSupervision();
    heartbeatTimer = setInterval(() => {
      try {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ id: ++msgId, type: 'ping' }));
        }
      } catch (e) {}
    }, HEARTBEAT_INTERVAL_MS);
    watchdogTimer = setInterval(() => {
      if (lastMessageAt && Date.now() - lastMessageAt > STALE_AFTER_MS) {
        handleDisconnect();
      }
    }, HEARTBEAT_INTERVAL_MS);
  }

  function stopSupervision() {
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    if (watchdogTimer) clearInterval(watchdogTimer);
    heartbeatTimer = null;
    watchdogTimer = null;
  }

  // Entity-subscription event handling. The server only sends our three
  // entities; anything else is ignored and never retained. Shapes follow
  // HA 2026.9.1 websocket_api/messages._state_diff_event exactly: initial
  // states arrive as {"a": {entity: {s, a, c, lc, lu}}}, changes as
  // {"c": {entity: {"+": {s?, a? (partial), c?, lc?, lu?},
  //                    "-": {a?: [removed keys]}}}}, removals as {"r": [...]}.
  // Unknown shapes are ignored, never merged.
  function isTracked(entityId) {
    return entityId === config.satelliteEntity ||
      entityId === config.muteEntity ||
      entityId === config.mediaPlayerEntity;
  }

  function storeFullState(entityId, compressed) {
    if (!isTracked(entityId) || !compressed) return false;
    lastEntityStates[entityId] = {
      entity_id: entityId,
      state: compressed.s,
      attributes: compressed.a || {},
      context: compressed.c,
      last_changed: compressed.lc,
      last_updated: compressed.lu
    };
    return true;
  }

  function applyStateDiff(entityId, diff) {
    if (!isTracked(entityId) || !diff || typeof diff !== 'object') return false;
    const added = diff['+'];
    const removed = diff['-'];
    if (!added && !removed) return false;
    const cur = lastEntityStates[entityId] || {
      entity_id: entityId, state: 'unknown', attributes: {}
    };
    if (added) {
      if (typeof added.s === 'string') cur.state = added.s;
      if (added.a && typeof added.a === 'object') {
        cur.attributes = Object.assign({}, cur.attributes, added.a);
      }
      if (added.c !== undefined) cur.context = added.c;
      if (added.lc !== undefined) cur.last_changed = added.lc;
      if (added.lu !== undefined) cur.last_updated = added.lu;
    }
    if (removed && Array.isArray(removed.a)) {
      for (const key of removed.a) delete cur.attributes[key];
    }
    lastEntityStates[entityId] = cur;
    return true;
  }

  function handleEntityEvent(ev) {
    let changed = false;
    if (ev.a) {
      for (const entityId of Object.keys(ev.a)) {
        changed = storeFullState(entityId, ev.a[entityId]) || changed;
      }
    }
    if (ev.c) {
      for (const entityId of Object.keys(ev.c)) {
        changed = applyStateDiff(entityId, ev.c[entityId]) || changed;
      }
    }
    if (ev.r) {
      for (const entityId of ev.r) {
        if (isTracked(entityId) && lastEntityStates[entityId]) {
          delete lastEntityStates[entityId];
          changed = true;
        }
      }
    }
    // Drop anything that is not a tracked entity, bounding retained state.
    for (const entityId of Object.keys(lastEntityStates)) {
      if (!isTracked(entityId)) delete lastEntityStates[entityId];
    }
    if (changed) evaluateEntities();
  }

  // --- Evaluate Entities to State ---
  function evaluateEntities() {
    const muteState = lastEntityStates[config.muteEntity];
    const satelliteState = lastEntityStates[config.satelliteEntity];
    const mediaState = lastEntityStates[config.mediaPlayerEntity];

    // Priority 1: Mute switch
    if (muteState && muteState.state === 'on') {
      setState('muted', 'JARVIS // MUTED');
      return;
    }

    // Priority 2: Satellite state (listening, processing, responding, idle)
    if (satelliteState) {
      const st = (satelliteState.state || '').toLowerCase();
      if (st === 'listening') {
        setState('listening', 'JARVIS // LISTENING');
        return;
      }
      if (st === 'processing') {
        setState('processing', 'JARVIS // THINKING');
        return;
      }
      if (st === 'responding') {
        setState('responding', 'JARVIS // SPEAKING');
        return;
      }
    }

    // Priority 3: Media player active while the satellite is otherwise idle
    if (mediaState && mediaState.state === 'playing') {
      const attrs = mediaState.attributes || {};
      const track = [attrs.media_title, attrs.media_artist].filter(Boolean).join(' - ').slice(0, 80);
      setState('music', track ? 'JARVIS // PLAYING // ' + track.toUpperCase() : 'JARVIS // PLAYING');
      return;
    }

    // Default: Idle
    setState('idle', 'JARVIS // IDLE');
  }

  // --- Token Prompt UI ---
  function showTokenPrompt(errMsg) {
    if (!promptModal) return;
    promptModal.style.display = 'flex';
    const errEl = document.getElementById('token-error');
    if (errEl) {
      errEl.textContent = errMsg || '';
      errEl.style.display = errMsg ? 'block' : 'none';
    }
  }

  if (tokenSubmit && tokenInput) {
    tokenSubmit.addEventListener('click', () => {
      const val = tokenInput.value.trim();
      if (val) {
        localStorage.setItem('jarvis_token', val);
        config.token = val;
        promptModal.style.display = 'none';
        connectWebSocket();
      }
    });
    tokenInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') tokenSubmit.click();
    });
  }

  // --- Interactive Debug / Testing Keys ---
  const states = ['idle', 'listening', 'processing', 'responding', 'music', 'muted'];
  let stateIndex = 0;

  // Click on screen to cycle state for instant visual testing
  appEl.addEventListener('click', (e) => {
    if (promptModal && promptModal.contains(e.target)) return;
    stateIndex = (stateIndex + 1) % states.length;
    setState(states[stateIndex]);
  });

  // Keyboard shortcuts
  window.addEventListener('keydown', (e) => {
    if (promptModal && promptModal.style.display === 'flex') return;
    switch (e.key) {
      case '1': setState('idle'); break;
      case '2': setState('listening'); break;
      case '3': setState('processing'); break;
      case '4': setState('responding'); break;
      case '5': setState('music'); break;
      case '6': setState('muted'); break;
      case 'f':
      case 'F':
        if (!document.fullscreenElement) {
          document.documentElement.requestFullscreen().catch(() => {});
        } else {
          document.exitFullscreen().catch(() => {});
        }
        break;
    }
  });

  // Expose global controller for scripting/testing
  window.Jarvis = {
    build: '2026-09-19a',
    setState: setState,
    getState: () => currentState,
    getWs: () => ws,
    getLastStates: () => lastEntityStates,
    eyes: Eyes,
    config: config,
    _faceTest: {
      handleEntityEvent: handleEntityEvent,
      evaluateEntities: evaluateEntities,
      isTracked: isTracked
    }
  };

  // --- Initialize ---
  setState('idle');
  Eyes.start();
  connectWebSocket();

})();
