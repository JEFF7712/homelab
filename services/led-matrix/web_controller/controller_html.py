"""Ultra-minimal retro gamepad mobile interface with W3C Pointer Events and 8-bit Web Audio synth."""

CONTROLLER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#1a1d24">
<title>LED MATRIX ARCADE</title>
<style>
  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    -webkit-touch-callout: none !important;
    -webkit-user-select: none !important;
    -khtml-user-select: none !important;
    -moz-user-select: none !important;
    -ms-user-select: none !important;
    user-select: none !important;
    touch-action: none !important;
  }
  html, body {
    width: 100%;
    height: 100%;
    height: 100dvh;
    overflow: hidden;
    background: #1a1d24;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  }
  body {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);
    position: relative;
    background: radial-gradient(circle at 50% 50%, #22262f 0%, #16181f 100%);
  }

  /* Connection Dot (Subtle indicator at top center) */
  .conn-dot {
    position: absolute;
    top: calc(env(safe-area-inset-top) + 12px);
    left: 50%;
    transform: translateX(-50%);
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: rgba(0, 255, 180, 0.25);
    transition: all 0.3s ease;
    pointer-events: none;
    z-index: 10;
  }
  .conn-dot.offline {
    background: #ff3344;
    box-shadow: 0 0 8px #ff3344;
    opacity: 0.9;
  }

  /* Sound Mute Toggle (Discreet icon at top right) */
  .sound-toggle {
    position: absolute;
    top: calc(env(safe-area-inset-top) + 8px);
    right: max(16px, env(safe-area-inset-right));
    background: transparent;
    border: none;
    color: #64748b;
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
    opacity: 0.45;
    padding: 8px;
    border-radius: 50%;
    z-index: 20;
    transition: opacity 0.2s, color 0.2s;
  }
  .sound-toggle:active, .sound-toggle:hover {
    opacity: 1.0;
    color: #cbd5e1;
  }
  .sound-toggle.muted {
    color: #ef4444;
    opacity: 0.65;
  }

  /* Tilt Toggle (Discreet icon next to sound toggle) */
  .tilt-toggle {
    position: absolute;
    top: calc(env(safe-area-inset-top) + 8px);
    right: max(56px, calc(env(safe-area-inset-right) + 40px));
    background: transparent;
    border: none;
    color: #64748b;
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
    opacity: 0.45;
    padding: 8px;
    border-radius: 50%;
    z-index: 20;
    transition: opacity 0.2s, color 0.2s, transform 0.2s;
  }
  .tilt-toggle:active, .tilt-toggle:hover {
    opacity: 1.0;
    color: #cbd5e1;
  }
  .tilt-toggle.active {
    color: #00e5ff;
    opacity: 0.95;
    filter: drop-shadow(0 0 6px rgba(0, 229, 255, 0.6));
    transform: rotate(15deg);
  }

  /* Gamepad Stage */
  .gamepad-stage {
    width: 100%;
    height: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 max(16px, env(safe-area-inset-left)) 0 max(16px, env(safe-area-inset-right));
  }

  /* Portrait mode: ergonomic lower-half positioning */
  @media (orientation: portrait) {
    .gamepad-stage {
      align-items: flex-end;
      padding-bottom: max(45px, env(safe-area-inset-bottom));
    }
  }

  /* ---------------- CLASSIC CROSS D-PAD (ENLARGED) ---------------- */
  .dpad-container {
    width: 220px;
    height: 220px;
    position: relative;
    touch-action: none !important;
  }
  .dpad-cross {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: 210px;
    height: 210px;
    pointer-events: none;
  }
  .dpad-wing {
    position: absolute;
    background: #252830;
    border: 3px solid #17191f;
    box-shadow: 0 10px 24px rgba(0,0,0,0.68), inset 0 1px 1px rgba(255,255,255,0.09);
    display: flex;
    justify-content: center;
    align-items: center;
    color: #4b5568;
    font-size: 20px;
    font-weight: 900;
    pointer-events: none;
    transition: background 0.05s, transform 0.05s, box-shadow 0.05s, color 0.05s;
  }
  .dpad-wing.touch-active {
    background: #14161c;
    box-shadow: 0 2px 5px rgba(0,0,0,0.85), inset 0 2px 7px rgba(0,0,0,0.95);
    color: #9cb3d6;
    transform: scale(0.96);
  }

  .wing-up {
    top: 0;
    left: 70px;
    width: 70px;
    height: 70px;
    border-radius: 12px 12px 0 0;
    border-bottom: none;
  }
  .wing-down {
    bottom: 0;
    left: 70px;
    width: 70px;
    height: 70px;
    border-radius: 0 0 12px 12px;
    border-top: none;
  }
  .wing-left {
    top: 70px;
    left: 0;
    width: 70px;
    height: 70px;
    border-radius: 12px 0 0 12px;
    border-right: none;
  }
  .wing-right {
    top: 70px;
    right: 0;
    width: 70px;
    height: 70px;
    border-radius: 0 12px 12px 0;
    border-left: none;
  }
  .dpad-center {
    position: absolute;
    top: 70px;
    left: 70px;
    width: 70px;
    height: 70px;
    background: #17191f;
    border-radius: 50%;
    box-shadow: inset 0 4px 8px rgba(0,0,0,0.9), 0 1px 1px rgba(255,255,255,0.04);
    pointer-events: none;
  }

  /* ---------------- ACTION BUTTONS A & B (ENLARGED) ---------------- */
  .action-container {
    width: 220px;
    height: 220px;
    position: relative;
    touch-action: none !important;
  }
  .btn-round {
    width: 96px;
    height: 96px;
    border-radius: 50%;
    position: absolute;
    background: radial-gradient(circle at 36% 30%, #a82433 0%, #7e1723 58%, #520b13 100%);
    border: 4px solid #36060c;
    box-shadow: 0 10px 22px rgba(0,0,0,0.75), inset 0 2px 3px rgba(255,255,255,0.25), inset 0 -4px 5px rgba(0,0,0,0.55);
    color: #ffffff;
    font-size: 34px;
    font-weight: 800;
    letter-spacing: -0.5px;
    display: flex;
    justify-content: center;
    align-items: center;
    cursor: pointer;
    touch-action: none !important;
    text-shadow: 0 1px 3px rgba(0,0,0,0.5);
    transform: rotate(-12deg);
    transition: transform 0.05s, box-shadow 0.05s, background 0.05s;
  }
  .btn-round.btn-b {
    bottom: 8px;
    left: 6px;
  }
  .btn-round.btn-a {
    top: 8px;
    right: 6px;
  }
  .btn-round.touch-active {
    background: radial-gradient(circle at 36% 30%, #7d1723 0%, #540b13 60%, #36060c 100%);
    box-shadow: 0 2px 6px rgba(0,0,0,0.85), inset 0 4px 8px rgba(0,0,0,0.92);
    transform: rotate(-12deg) scale(0.93);
  }
</style>
</head>
<body oncontextmenu="return false;">

<div class="conn-dot" id="conn-dot" title="Connection Status"></div>
<button class="sound-toggle" id="sound-toggle" aria-label="Toggle Sound" title="Sound Mute/Unmute">
  <span id="sound-icon">🔊</span>
</button>
<button class="tilt-toggle" id="tilt-toggle" aria-label="Toggle Tilt Steering" title="Tilt Steering (Motion)">
  <span id="tilt-icon">⚖️</span>
</button>

<div class="gamepad-stage" oncontextmenu="return false;">
  <!-- D-PAD CONTAINER -->
  <div class="dpad-container" id="dpad-container" draggable="false" oncontextmenu="return false;">
    <div class="dpad-cross" draggable="false">
      <div class="dpad-wing wing-up" data-action="up">▲</div>
      <div class="dpad-wing wing-down" data-action="down">▼</div>
      <div class="dpad-wing wing-left" data-action="left">◀</div>
      <div class="dpad-wing wing-right" data-action="right">▶</div>
      <div class="dpad-center"></div>
    </div>
  </div>

  <!-- ACTION BUTTONS -->
  <div class="action-container" id="action-container" draggable="false" oncontextmenu="return false;">
    <div class="btn-round btn-b" id="btn-b" data-action="action_b" draggable="false" oncontextmenu="return false;">B</div>
    <div class="btn-round btn-a" id="btn-a" data-action="action_a" draggable="false" oncontextmenu="return false;">A</div>
  </div>
</div>

<script>
(function() {
  // ---------------- RETRO 8-BIT SOUND CHIP (WEB AUDIO API) ----------------
  const SoundChip = (function() {
    let ctx = null;
    let muted = localStorage.getItem('arcade_sfx_muted') === '1';

    function getContext() {
      if (!ctx) {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (AudioCtx) {
          ctx = new AudioCtx();
        }
      }
      if (ctx && ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
      return ctx;
    }

    function isMuted() {
      return muted;
    }

    function setMuted(m) {
      muted = !!m;
      localStorage.setItem('arcade_sfx_muted', muted ? '1' : '0');
    }

    function toggleMuted() {
      setMuted(!muted);
      return muted;
    }

    function tone({ type = 'square', freq = 440, endFreq = null, dur = 0.1, vol = 0.15 } = {}) {
      if (muted) return;
      const c = getContext();
      if (!c) return;

      const now = c.currentTime;
      const osc = c.createOscillator();
      const gain = c.createGain();

      osc.type = type;
      osc.frequency.setValueAtTime(freq, now);
      if (endFreq && endFreq !== freq) {
        osc.frequency.exponentialRampToValueAtTime(Math.max(1, endFreq), now + dur);
      }

      gain.gain.setValueAtTime(vol, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + dur);

      osc.connect(gain);
      gain.connect(c.destination);

      osc.start(now);
      osc.stop(now + dur + 0.02);
    }

    function noise({ dur = 0.2, vol = 0.2, filterFreq = 1200 } = {}) {
      if (muted) return;
      const c = getContext();
      if (!c) return;

      const now = c.currentTime;
      const bufferSize = Math.floor(c.sampleRate * dur);
      const buffer = c.createBuffer(1, bufferSize, c.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < bufferSize; i++) {
        data[i] = Math.random() * 2 - 1;
      }

      const noiseSource = c.createBufferSource();
      noiseSource.buffer = buffer;

      const filter = c.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.setValueAtTime(filterFreq, now);
      filter.frequency.exponentialRampToValueAtTime(80, now + dur);

      const gain = c.createGain();
      gain.gain.setValueAtTime(vol, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + dur);

      noiseSource.connect(filter);
      filter.connect(gain);
      gain.connect(c.destination);

      noiseSource.start(now);
      noiseSource.stop(now + dur + 0.02);
    }

    const SFX = {
      button: () => tone({ type: 'square', freq: 520, endFreq: 400, dur: 0.035, vol: 0.08 }),
      move: () => tone({ type: 'triangle', freq: 300, endFreq: 220, dur: 0.04, vol: 0.09 }),
      turn: () => tone({ type: 'square', freq: 380, endFreq: 480, dur: 0.04, vol: 0.09 }),
      rotate: () => tone({ type: 'square', freq: 360, endFreq: 580, dur: 0.06, vol: 0.12 }),
      drop: () => tone({ type: 'triangle', freq: 160, endFreq: 70, dur: 0.07, vol: 0.15 }),
      lock: () => tone({ type: 'triangle', freq: 220, endFreq: 110, dur: 0.08, vol: 0.14 }),
      line_clear: () => {
        [523.25, 659.25, 783.99, 1046.50].forEach((f, i) => {
          setTimeout(() => tone({ type: 'square', freq: f, dur: 0.08, vol: 0.13 }), i * 65);
        });
      },
      tetris: () => {
        [523.25, 659.25, 783.99, 987.77, 1046.50, 1318.51].forEach((f, i) => {
          setTimeout(() => tone({ type: 'square', freq: f, dur: 0.11, vol: 0.16 }), i * 75);
        });
      },
      eat: () => {
        tone({ type: 'square', freq: 587, endFreq: 880, dur: 0.07, vol: 0.14 });
        setTimeout(() => tone({ type: 'square', freq: 880, endFreq: 1174, dur: 0.09, vol: 0.15 }), 60);
      },
      paddle: () => tone({ type: 'square', freq: 440, endFreq: 330, dur: 0.06, vol: 0.15 }),
      wall: () => tone({ type: 'square', freq: 220, endFreq: 180, dur: 0.05, vol: 0.11 }),
      brick: () => tone({ type: 'square', freq: 520 + Math.random() * 300, endFreq: 400, dur: 0.05, vol: 0.13 }),
      score: () => {
        tone({ type: 'square', freq: 659, dur: 0.08, vol: 0.14 });
        setTimeout(() => tone({ type: 'square', freq: 880, dur: 0.14, vol: 0.15 }), 80);
      },
      pip: () => tone({ type: 'square', freq: 440, dur: 0.06, vol: 0.12 }),
      start: () => {
        tone({ type: 'square', freq: 440, dur: 0.07, vol: 0.14 });
        setTimeout(() => tone({ type: 'square', freq: 554, dur: 0.07, vol: 0.14 }), 70);
        setTimeout(() => tone({ type: 'square', freq: 659, dur: 0.07, vol: 0.14 }), 140);
        setTimeout(() => tone({ type: 'square', freq: 880, dur: 0.18, vol: 0.17 }), 210);
      },
      crash: () => noise({ dur: 0.28, vol: 0.22, filterFreq: 900 }),
      game_over: () => {
        [392, 370, 349, 311].forEach((f, i) => {
          setTimeout(() => tone({ type: 'triangle', freq: f, dur: 0.15, vol: 0.16 }), i * 140);
        });
      },
      high_score: () => {
        [523.25, 659.25, 783.99, 1046.50, 1174.66, 1318.51, 1567.98].forEach((f, i) => {
          setTimeout(() => tone({ type: 'square', freq: f, dur: 0.12, vol: 0.16 }), i * 70);
        });
      },
    };

    function play(name) {
      if (SFX[name]) {
        try { SFX[name](); } catch (e) {}
      }
    }

    return {
      init: getContext,
      play: play,
      isMuted: isMuted,
      toggleMuted: toggleMuted,
    };
  })();

  // ---------------- GAMEPAD CLIENT LOGIC ----------------
  let ws = null;
  let playerRole = 1;
  let currentPhase = 'lobby';
  let selectedGame = 'pong';
  let soloMode = false;
  let bHoldTimer = null;

  const connDot = document.getElementById('conn-dot');
  const soundToggle = document.getElementById('sound-toggle');
  const soundIcon = document.getElementById('sound-icon');
  const dpadContainer = document.getElementById('dpad-container');
  const btnA = document.getElementById('btn-a');
  const btnB = document.getElementById('btn-b');

  function updateSoundUI() {
    if (SoundChip.isMuted()) {
      soundToggle.classList.add('muted');
      soundIcon.textContent = '🔇';
    } else {
      soundToggle.classList.remove('muted');
      soundIcon.textContent = '🔊';
    }
  }
  updateSoundUI();

  soundToggle.addEventListener('pointerdown', (e) => {
    e.stopPropagation();
    SoundChip.init();
    const isNowMuted = SoundChip.toggleMuted();
    updateSoundUI();
    vibrate(isNowMuted ? 40 : 15);
    if (!isNowMuted) {
      SoundChip.play('button');
    }
  });

  // Resume AudioContext on any user interaction
  window.addEventListener('pointerdown', () => SoundChip.init(), { once: true });

  // Suppress Firefox/Chrome/Safari mobile long-press selection & context menus
  ['contextmenu', 'selectstart', 'selectionchange'].forEach((evt) => {
    window.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      const sel = window.getSelection();
      if (sel && sel.removeAllRanges) {
        sel.removeAllRanges();
      }
      return false;
    }, { passive: false, capture: true });
  });

  function vibrate(ms) {
    if (navigator.vibrate) {
      try { navigator.vibrate(ms); } catch (e) {}
    }
  }

  function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  function connect() {
    connDot.classList.add('offline');
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      connDot.classList.remove('offline');
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
      } catch (err) {
        console.error('WS Parse Error', err);
      }
    };

    ws.onclose = () => {
      connDot.classList.add('offline');
      setTimeout(connect, 1500);
    };
  }

  const HAPTICS = {
    crash: [80, 50, 100],
    wall: 25,
    paddle: 35,
    brick: 20,
    eat: 25,
    line_clear: [30, 40, 50],
    tetris: [40, 30, 60, 30, 80],
    drop: 30,
    lock: 15,
    score: [40, 40, 60],
    pip: 20,
    start: [20, 30, 20, 30, 60],
  };

  function handleMessage(msg) {
    if (msg.type === 'assign') {
      playerRole = msg.player;
    } else if (msg.type === 'lobby_state') {
      currentPhase = msg.phase || 'lobby';
      if (msg.selected_game) selectedGame = msg.selected_game;
      if (msg.solo_vs_ai !== undefined) soloMode = msg.solo_vs_ai;
    } else if (msg.type === 'game_start') {
      currentPhase = 'playing';
      if (msg.game) selectedGame = msg.game;
      vibrate(35);
    } else if (msg.type === 'game_over') {
      currentPhase = 'gameover';
      clearTilt();
      if (msg.new_high) {
        vibrate([60, 40, 80, 40, 120]);
        SoundChip.play('high_score');
      } else {
        vibrate(msg.winner === playerRole ? [40, 50, 40, 50, 80] : [120, 60, 100]);
      }
    } else if (msg.type === 'sfx') {
      if (msg.name) {
        SoundChip.play(msg.name);
        if (HAPTICS[msg.name]) {
          vibrate(HAPTICS[msg.name]);
        }
      }
    }
  }

  // ---------------- TILT CONTROLS (DEVICE ORIENTATION API) ----------------
  const tiltToggle = document.getElementById('tilt-toggle');
  let tiltEnabled = localStorage.getItem('arcade_tilt_enabled') === '1';
  let activeTiltAction = null;
  let orientationListenerActive = false;

  function updateTiltUI() {
    if (tiltToggle) {
      if (tiltEnabled) {
        tiltToggle.classList.add('active');
      } else {
        tiltToggle.classList.remove('active');
      }
    }
  }
  updateTiltUI();

  function clearTilt() {
    if (activeTiltAction) {
      const prevWing = document.querySelector(`.dpad-wing[data-action="${activeTiltAction}"]`);
      if (prevWing) prevWing.classList.remove('touch-active');
      send({ type: 'input', action: activeTiltAction, state: 'up' });
      activeTiltAction = null;
    }
  }

  function handleOrientation(e) {
    if (!tiltEnabled || currentPhase !== 'playing') {
      if (activeTiltAction) clearTilt();
      return;
    }
    // If player is actively touching on-screen D-pad, touch overrides tilt
    if (dpadPointerId !== null) {
      if (activeTiltAction) clearTilt();
      return;
    }

    let roll = e.gamma || 0;
    let pitch = e.beta || 0;

    const orientation = (screen.orientation && screen.orientation.type) || '';
    if (orientation.includes('landscape-primary')) {
      const temp = roll;
      roll = pitch;
      pitch = -temp;
    } else if (orientation.includes('landscape-secondary')) {
      const temp = roll;
      roll = -pitch;
      pitch = temp;
    }

    let tiltAction = null;

    if (selectedGame === 'breakout') {
      if (roll < -7) tiltAction = 'left';
      else if (roll > 7) tiltAction = 'right';
    } else if (selectedGame === 'snake' || selectedGame === 'tron') {
      const dPitch = pitch - 42;
      if (Math.abs(roll) > Math.abs(dPitch)) {
        if (roll < -13) tiltAction = 'left';
        else if (roll > 13) tiltAction = 'right';
      } else {
        if (dPitch < -13) tiltAction = 'up';
        else if (dPitch > 13) tiltAction = 'down';
      }
    } else if (selectedGame === 'pong') {
      const dPitch = pitch - 42;
      if (dPitch < -10) tiltAction = 'up';
      else if (dPitch > 10) tiltAction = 'down';
    }

    if (tiltAction !== activeTiltAction) {
      clearTilt();
      activeTiltAction = tiltAction;
      if (activeTiltAction) {
        const wing = document.querySelector(`.dpad-wing[data-action="${activeTiltAction}"]`);
        if (wing) wing.classList.add('touch-active');
        send({ type: 'input', action: activeTiltAction, state: 'down' });
      }
    }
  }

  function startOrientation() {
    if (!orientationListenerActive && window.DeviceOrientationEvent) {
      window.addEventListener('deviceorientation', handleOrientation);
      orientationListenerActive = true;
    }
  }

  function toggleTilt() {
    tiltEnabled = !tiltEnabled;
    localStorage.setItem('arcade_tilt_enabled', tiltEnabled ? '1' : '0');
    updateTiltUI();
    vibrate(tiltEnabled ? [20, 30, 20] : 30);
    if (tiltEnabled) {
      startOrientation();
      SoundChip.play('button');
    } else {
      clearTilt();
    }
  }

  if (tiltToggle) {
    tiltToggle.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      SoundChip.init();
      if (
        typeof DeviceOrientationEvent !== 'undefined' &&
        typeof DeviceOrientationEvent.requestPermission === 'function'
      ) {
        DeviceOrientationEvent.requestPermission()
          .then((resp) => {
            if (resp === 'granted') toggleTilt();
          })
          .catch(() => toggleTilt());
      } else {
        toggleTilt();
      }
    });
  }

  if (tiltEnabled) {
    startOrientation();
  }

  // ---------------- MULTI-TOUCH W3C POINTER EVENTS ----------------
  let dpadPointerId = null;
  let activeDpadAction = null;

  function updateDpadFromCoords(clientX, clientY) {
    const rect = dpadContainer.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const dx = clientX - cx;
    const dy = clientY - cy;
    const dist = Math.sqrt(dx * dx + dy * dy);

    // Deadzone in the center dish (~26px)
    if (dist < 26) {
      setDpadAction(null);
      return;
    }

    // 4 cardinal sectors based on angle
    const angle = Math.atan2(dy, dx);
    let action = null;
    if (angle >= -Math.PI / 4 && angle < Math.PI / 4) {
      action = 'right';
    } else if (angle >= Math.PI / 4 && angle < (3 * Math.PI) / 4) {
      action = 'down';
    } else if (angle >= (-3 * Math.PI) / 4 && angle < -Math.PI / 4) {
      action = 'up';
    } else {
      action = 'left';
    }
    setDpadAction(action);
  }

  function setDpadAction(action) {
    if (action === activeDpadAction) return;

    if (activeDpadAction) {
      const prevWing = document.querySelector(`.dpad-wing[data-action="${activeDpadAction}"]`);
      if (prevWing) prevWing.classList.remove('touch-active');
      send({ type: 'input', action: activeDpadAction, state: 'up' });
    }

    activeDpadAction = action;

    if (activeDpadAction) {
      const newWing = document.querySelector(`.dpad-wing[data-action="${activeDpadAction}"]`);
      if (newWing) newWing.classList.add('touch-active');
      vibrate(10);
      SoundChip.play('button');

      // Carousel menu navigation in lobby
      if (currentPhase === 'lobby') {
        if (activeDpadAction === 'left') {
          send({ type: 'menu_nav', dir: 'prev' });
          return;
        } else if (activeDpadAction === 'right') {
          send({ type: 'menu_nav', dir: 'next' });
          return;
        }
      }

      send({ type: 'input', action: activeDpadAction, state: 'down' });
    }
  }

  dpadContainer.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    SoundChip.init();
    try { dpadContainer.setPointerCapture(e.pointerId); } catch (err) {}
    dpadPointerId = e.pointerId;
    updateDpadFromCoords(e.clientX, e.clientY);
  });

  dpadContainer.addEventListener('pointermove', (e) => {
    if (e.pointerId !== dpadPointerId) return;
    e.preventDefault();
    updateDpadFromCoords(e.clientX, e.clientY);
  });

  const releaseDpad = (e) => {
    if (e.pointerId !== dpadPointerId) return;
    e.preventDefault();
    try { dpadContainer.releasePointerCapture(e.pointerId); } catch (err) {}
    dpadPointerId = null;
    setDpadAction(null);
  };

  dpadContainer.addEventListener('pointerup', releaseDpad);
  dpadContainer.addEventListener('pointercancel', releaseDpad);
  ['contextmenu', 'selectstart'].forEach((evt) => {
    dpadContainer.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      return false;
    });
  });

  // 2. ACTION BUTTONS (A & B) with independent pointer tracking
  const activeActionPointers = new Map();

  function bindActionButton(btn, action) {
    ['contextmenu', 'selectstart'].forEach((evt) => {
      btn.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        return false;
      });
    });

    btn.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      SoundChip.init();
      try { btn.setPointerCapture(e.pointerId); } catch (err) {}
      activeActionPointers.set(e.pointerId, action);
      btn.classList.add('touch-active');
      vibrate(12);
      SoundChip.play('button');

      // In lobby: A starts game
      if (currentPhase === 'lobby' && action === 'action_a') {
        send({ type: 'start_game', game: selectedGame, solo: soloMode });
        return;
      }

      // In gameover: A rematches, B returns to menu
      if (currentPhase === 'gameover') {
        if (action === 'action_a') {
          send({ type: 'rematch' });
          return;
        } else if (action === 'action_b') {
          send({ type: 'menu_exit' });
          return;
        }
      }

      // Hold B for 1.2s to exit active game back to matrix carousel
      if (action === 'action_b' && currentPhase === 'playing') {
        clearTimeout(bHoldTimer);
        bHoldTimer = setTimeout(() => {
          vibrate([30, 40, 60]);
          send({ type: 'menu_exit' });
        }, 1200);
      }

      send({ type: 'input', action: action, state: 'down' });
    });

    const onPointerRelease = (e) => {
      if (!activeActionPointers.has(e.pointerId)) return;
      e.preventDefault();
      try { btn.releasePointerCapture(e.pointerId); } catch (err) {}
      activeActionPointers.delete(e.pointerId);
      btn.classList.remove('touch-active');

      if (action === 'action_b') {
        clearTimeout(bHoldTimer);
      }
      send({ type: 'input', action: action, state: 'up' });
    };

    btn.addEventListener('pointerup', onPointerRelease);
    btn.addEventListener('pointercancel', onPointerRelease);
  }

  bindActionButton(btnA, 'action_a');
  bindActionButton(btnB, 'action_b');

  // ---------------- HTML5 GAMEPAD API INTEGRATION ----------------
  let lastGamepadAxes = { x: 0, y: 0 };
  let lastGamepadButtons = new Map();

  function pollGamepads() {
    const gamepads = navigator.getGamepads ? navigator.getGamepads() : [];
    for (const gp of gamepads) {
      if (!gp) continue;

      // Analog stick or D-pad axes
      const ax = gp.axes[0] || 0;
      const ay = gp.axes[1] || 0;
      let gpAction = null;
      if (Math.abs(ax) > 0.45 || Math.abs(ay) > 0.45) {
        if (Math.abs(ax) > Math.abs(ay)) {
          gpAction = ax > 0 ? 'right' : 'left';
        } else {
          gpAction = ay > 0 ? 'down' : 'up';
        }
      }

      // Standard D-pad buttons (B12=Up, B13=Down, B14=Left, B15=Right)
      if (gp.buttons[12] && gp.buttons[12].pressed) gpAction = 'up';
      if (gp.buttons[13] && gp.buttons[13].pressed) gpAction = 'down';
      if (gp.buttons[14] && gp.buttons[14].pressed) gpAction = 'left';
      if (gp.buttons[15] && gp.buttons[15].pressed) gpAction = 'right';

      if (dpadPointerId === null) {
        setDpadAction(gpAction);
      }

      // Standard A & B buttons (B0=A, B1=B)
      const b0 = !!(gp.buttons[0] && gp.buttons[0].pressed);
      const b1 = !!(gp.buttons[1] && gp.buttons[1].pressed);

      if (b0 !== lastGamepadButtons.get('action_a')) {
        lastGamepadButtons.set('action_a', b0);
        if (b0) {
          btnA.classList.add('touch-active');
          vibrate(12);
          SoundChip.play('button');
          if (currentPhase === 'lobby') {
            send({ type: 'start_game', game: selectedGame, solo: soloMode });
          } else if (currentPhase === 'gameover') {
            send({ type: 'rematch' });
          } else {
            send({ type: 'input', action: 'action_a', state: 'down' });
          }
        } else {
          btnA.classList.remove('touch-active');
          send({ type: 'input', action: 'action_a', state: 'up' });
        }
      }

      if (b1 !== lastGamepadButtons.get('action_b')) {
        lastGamepadButtons.set('action_b', b1);
        if (b1) {
          btnB.classList.add('touch-active');
          vibrate(12);
          SoundChip.play('button');
          if (currentPhase === 'gameover') {
            send({ type: 'menu_exit' });
          } else {
            send({ type: 'input', action: 'action_b', state: 'down' });
          }
        } else {
          btnB.classList.remove('touch-active');
          send({ type: 'input', action: 'action_b', state: 'up' });
        }
      }
    }
    requestAnimationFrame(pollGamepads);
  }
  requestAnimationFrame(pollGamepads);

  // Keyboard controls for desktop testing
  const KEY_MAP = {
    'ArrowUp': 'up',
    'KeyW': 'up',
    'ArrowDown': 'down',
    'KeyS': 'down',
    'ArrowLeft': 'left',
    'KeyA': 'left',
    'ArrowRight': 'right',
    'KeyD': 'right',
    'KeyX': 'action_b',
    'KeyZ': 'action_a',
    'Space': 'action_a',
  };

  const heldKeys = new Set();
  window.addEventListener('keydown', (e) => {
    if (e.repeat) return;
    SoundChip.init();
    const act = KEY_MAP[e.code];
    if (act && !heldKeys.has(act)) {
      heldKeys.add(act);
      if (['up', 'down', 'left', 'right'].includes(act)) {
        setDpadAction(act);
      } else if (act === 'action_a') {
        btnA.classList.add('touch-active');
        vibrate(10);
        SoundChip.play('button');
        if (currentPhase === 'lobby') {
          send({ type: 'start_game', game: selectedGame, solo: soloMode });
        } else if (currentPhase === 'gameover') {
          send({ type: 'rematch' });
        } else {
          send({ type: 'input', action: act, state: 'down' });
        }
      } else if (act === 'action_b') {
        btnB.classList.add('touch-active');
        vibrate(10);
        SoundChip.play('button');
        if (currentPhase === 'gameover') {
          send({ type: 'menu_exit' });
        } else {
          send({ type: 'input', action: act, state: 'down' });
        }
      }
    }
  });

  window.addEventListener('keyup', (e) => {
    const act = KEY_MAP[e.code];
    if (act && heldKeys.has(act)) {
      heldKeys.delete(act);
      if (['up', 'down', 'left', 'right'].includes(act)) {
        if (activeDpadAction === act) {
          setDpadAction(null);
        }
      } else if (act === 'action_a') {
        btnA.classList.remove('touch-active');
        send({ type: 'input', action: act, state: 'up' });
      } else if (act === 'action_b') {
        btnB.classList.remove('touch-active');
        send({ type: 'input', action: act, state: 'up' });
      }
    }
  });

  connect();
})();
</script>
</body>
</html>
"""
