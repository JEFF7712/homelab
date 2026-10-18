"""Mobile Web Controller single-page application for LED Matrix Arcade."""

CONTROLLER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#12131a">
<title>LED MATRIX ARCADE</title>
<style>
  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    -webkit-touch-callout: none;
    -webkit-user-select: none;
    user-select: none;
  }
  html, body {
    height: 100%;
    height: 100dvh;
    overflow: hidden;
    background: #0d0e14;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Press Start 2P", monospace;
    touch-action: none;
  }
  body {
    display: flex;
    justify-content: center;
    align-items: center;
    padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);
  }

  /* Retro Handheld Chassis */
  .chassis {
    width: 100%;
    max-width: 440px;
    height: 100%;
    max-height: 840px;
    background: linear-gradient(180deg, #242735 0%, #1a1b24 100%);
    border: 3px solid #363a4e;
    border-radius: 32px;
    box-shadow: 0 16px 40px rgba(0,0,0,0.8), inset 0 2px 4px rgba(255,255,255,0.08);
    display: flex;
    flex-direction: column;
    padding: 16px 18px 24px 18px;
    position: relative;
    justify-content: space-between;
  }

  /* Top LCD Screen Bezel */
  .screen-bezel {
    background: #10121a;
    border: 3px solid #282c3c;
    border-radius: 18px;
    padding: 12px 14px;
    box-shadow: inset 0 4px 12px rgba(0,0,0,0.7);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  /* LCD Glass */
  .lcd-glass {
    background: #090c10;
    border: 2px solid #1a2230;
    border-radius: 10px;
    padding: 8px 10px;
    color: #00ffcc;
    font-family: monospace;
    text-shadow: 0 0 4px rgba(0,255,204,0.5);
  }

  .lcd-top-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
    border-bottom: 1px dashed #1a3038;
    padding-bottom: 4px;
  }

  .player-pill {
    padding: 3px 8px;
    border-radius: 6px;
    font-size: 10px;
    font-weight: 900;
    background: #16242c;
    color: #00e5ff;
    border: 1px solid #00e5ff;
  }
  body.p2-mode .player-pill {
    background: #2c2016;
    color: #ff9100;
    border-color: #ff9100;
  }

  .conn-status {
    font-size: 10px;
    color: #7a889b;
  }
  .conn-status.online { color: #00ff88; }

  /* Quick Game Picker Chips */
  .game-chips-row {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding-bottom: 4px;
    scrollbar-width: none;
    -ms-overflow-style: none;
  }
  .game-chips-row::-webkit-scrollbar { display: none; }

  .chip-game {
    flex: 1;
    min-width: 62px;
    background: #141722;
    border: 1px solid #2e3448;
    color: #8c96ac;
    padding: 6px 2px;
    border-radius: 8px;
    font-size: 10px;
    font-weight: 800;
    text-align: center;
    cursor: pointer;
    touch-action: manipulation;
    transition: all 0.15s ease;
  }
  .chip-game:active { transform: scale(0.96); }
  .chip-game.active {
    background: #004455;
    border-color: #00ffcc;
    color: #ffffff;
    box-shadow: 0 0 10px rgba(0,255,204,0.4);
  }
  body.p2-mode .chip-game.active {
    background: #553300;
    border-color: #ff9900;
    box-shadow: 0 0 10px rgba(255,153,0,0.4);
  }

  .lcd-msg-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    font-weight: 800;
    color: #ffffff;
    min-height: 20px;
  }
  .lcd-status {
    color: #00ffcc;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  body.p2-mode .lcd-status { color: #ffaa33; }

  .mode-badge {
    font-size: 9px;
    padding: 2px 6px;
    border-radius: 4px;
    background: #1b212f;
    color: #9cb2cc;
    cursor: pointer;
    border: 1px solid #323d54;
  }
  .mode-badge:active { background: #283348; color: #fff; }

  /* Brand Branding */
  .brand-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 6px;
  }
  .brand-text {
    font-size: 11px;
    font-weight: 900;
    letter-spacing: 2px;
    color: #4c526a;
    text-transform: uppercase;
  }
  .speaker-vents {
    display: flex;
    gap: 4px;
  }
  .vent {
    width: 22px;
    height: 4px;
    background: #12141c;
    border-radius: 2px;
    box-shadow: inset 0 1px 2px rgba(0,0,0,0.8);
  }

  /* Main Controls Container */
  .controls-area {
    flex: 1;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 0;
    position: relative;
  }

  /* Classic 4-Way Cross D-PAD */
  .dpad-container {
    width: 160px;
    height: 160px;
    position: relative;
    touch-action: none;
  }
  .dpad-cross {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: 148px;
    height: 148px;
  }
  .dpad-wing {
    position: absolute;
    background: linear-gradient(145deg, #2a2c3a, #1a1c26);
    border: 2px solid #141520;
    box-shadow: 0 6px 12px rgba(0,0,0,0.6), inset 0 1px 1px rgba(255,255,255,0.15);
    display: flex;
    justify-content: center;
    align-items: center;
    color: #555b77;
    font-size: 16px;
    font-weight: 900;
    cursor: pointer;
    touch-action: none;
    transition: background 0.08s, transform 0.08s, box-shadow 0.08s;
  }
  .dpad-wing.touch-active, .dpad-wing:active {
    background: linear-gradient(145deg, #161820, #0f1016);
    color: #00ffcc;
    box-shadow: 0 2px 4px rgba(0,0,0,0.8), inset 0 2px 4px rgba(0,0,0,0.9);
    transform: scale(0.96);
  }
  body.p2-mode .dpad-wing.touch-active, body.p2-mode .dpad-wing:active {
    color: #ff9900;
  }

  .wing-up {
    top: 0;
    left: 49px;
    width: 50px;
    height: 52px;
    border-radius: 10px 10px 0 0;
    border-bottom: none;
  }
  .wing-down {
    bottom: 0;
    left: 49px;
    width: 50px;
    height: 52px;
    border-radius: 0 0 10px 10px;
    border-top: none;
  }
  .wing-left {
    top: 49px;
    left: 0;
    width: 52px;
    height: 50px;
    border-radius: 10px 0 0 10px;
    border-right: none;
  }
  .wing-right {
    top: 49px;
    right: 0;
    width: 52px;
    height: 50px;
    border-radius: 0 10px 10px 0;
    border-left: none;
  }
  .dpad-center {
    position: absolute;
    top: 49px;
    left: 49px;
    width: 50px;
    height: 50px;
    background: #1c1d27;
    border-radius: 50%;
    box-shadow: inset 0 2px 4px rgba(0,0,0,0.7);
    pointer-events: none;
  }

  /* Classic Action Buttons (B and A) */
  .action-container {
    width: 150px;
    height: 150px;
    position: relative;
    transform: rotate(-24deg);
    touch-action: none;
  }
  .btn-round {
    width: 66px;
    height: 66px;
    border-radius: 50%;
    position: absolute;
    background: linear-gradient(145deg, #c41e3a, #851024);
    border: 3px solid #540916;
    box-shadow: 0 8px 16px rgba(0,0,0,0.7), inset 0 3px 5px rgba(255,255,255,0.3);
    color: #ffffff;
    font-size: 22px;
    font-weight: 900;
    display: flex;
    justify-content: center;
    align-items: center;
    cursor: pointer;
    touch-action: none;
    transition: transform 0.08s, box-shadow 0.08s;
  }
  .btn-round.btn-b {
    bottom: 8px;
    left: 6px;
  }
  .btn-round.btn-a {
    top: 8px;
    right: 6px;
  }
  .btn-round.touch-active, .btn-round:active {
    background: linear-gradient(145deg, #851024, #540916);
    box-shadow: 0 2px 6px rgba(0,0,0,0.8), inset 0 3px 8px rgba(0,0,0,0.8);
    transform: scale(0.94);
  }

  /* Center Rubber Pill Buttons (SELECT & START) */
  .center-pills {
    display: flex;
    justify-content: center;
    gap: 32px;
    margin-top: 8px;
    padding-bottom: 6px;
  }
  .pill-wrapper {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
  }
  .btn-pill {
    width: 58px;
    height: 20px;
    background: #181922;
    border: 2px solid #111219;
    border-radius: 12px;
    transform: rotate(-25deg);
    box-shadow: 0 4px 8px rgba(0,0,0,0.6), inset 0 1px 2px rgba(255,255,255,0.15);
    cursor: pointer;
    touch-action: manipulation;
    transition: all 0.08s ease;
  }
  .btn-pill:active, .btn-pill.touch-active {
    background: #0d0e14;
    box-shadow: 0 1px 3px rgba(0,0,0,0.8), inset 0 2px 4px rgba(0,0,0,0.9);
    transform: rotate(-25deg) scale(0.92);
  }
  .pill-label {
    font-size: 10px;
    font-weight: 900;
    color: #555b77;
    letter-spacing: 1px;
    text-transform: uppercase;
  }
</style>
</head>
<body>

<div class="chassis">
  <!-- Top LCD Display Box -->
  <div class="screen-bezel">
    <div class="lcd-glass">
      <div class="lcd-top-row">
        <span class="player-pill" id="player-pill">PLAYER 1 (CYAN)</span>
        <span class="conn-status" id="conn-status">CONNECTING...</span>
      </div>

      <!-- Quick Game Select Chips -->
      <div class="game-chips-row" id="game-chips">
        <div class="chip-game active" data-game="pong">PONG</div>
        <div class="chip-game" data-game="tron">TRON</div>
        <div class="chip-game" data-game="snake">SNAKE</div>
        <div class="chip-game" data-game="breakout">BREAKOUT</div>
        <div class="chip-game" data-game="tetris">TETRIS</div>
      </div>

      <!-- Live Info / Score Row -->
      <div class="lcd-msg-row">
        <span class="lcd-status" id="lcd-status">SELECT GAME OR PRESS START</span>
        <span class="mode-badge" id="mode-badge">1P / 2P</span>
      </div>
    </div>
  </div>

  <!-- Branding -->
  <div class="brand-row">
    <div class="brand-text">RETRO CONTROLLER</div>
    <div class="speaker-vents">
      <div class="vent"></div>
      <div class="vent"></div>
      <div class="vent"></div>
    </div>
  </div>

  <!-- Main Gamepad Area -->
  <div class="controls-area">
    <!-- D-PAD -->
    <div class="dpad-container" id="dpad-container">
      <div class="dpad-cross">
        <div class="dpad-wing wing-up" data-action="up">▲</div>
        <div class="dpad-wing wing-down" data-action="down">▼</div>
        <div class="dpad-wing wing-left" data-action="left">◀</div>
        <div class="dpad-wing wing-right" data-action="right">▶</div>
        <div class="dpad-center"></div>
      </div>
    </div>

    <!-- ACTION BUTTONS -->
    <div class="action-container">
      <div class="btn-round btn-b" data-action="action_b">B</div>
      <div class="btn-round btn-a" data-action="action_a">A</div>
    </div>
  </div>

  <!-- SELECT & START Pills -->
  <div class="center-pills">
    <div class="pill-wrapper">
      <button class="btn-pill" id="btn-select" title="Cycle Game / Change Mode"></button>
      <div class="pill-label">SELECT</div>
    </div>
    <div class="pill-wrapper">
      <button class="btn-pill" id="btn-start" title="Start / Rematch"></button>
      <div class="pill-label">START</div>
    </div>
  </div>
</div>

<script>
(function() {
  let ws = null;
  let playerRole = 1;
  let currentLobby = null;
  let selectedSolo = false;

  const playerPill = document.getElementById('player-pill');
  const connStatus = document.getElementById('conn-status');
  const lcdStatus = document.getElementById('lcd-status');
  const modeBadge = document.getElementById('mode-badge');
  const gameChips = document.querySelectorAll('.chip-game');
  const btnSelect = document.getElementById('btn-select');
  const btnStart = document.getElementById('btn-start');

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
    connStatus.textContent = 'CONNECTING...';
    connStatus.className = 'conn-status';
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      connStatus.textContent = 'ONLINE';
      connStatus.className = 'conn-status online';
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
      connStatus.textContent = 'OFFLINE';
      connStatus.className = 'conn-status';
      setTimeout(connect, 1500);
    };
  }

  function handleMessage(msg) {
    if (msg.type === 'assign') {
      playerRole = msg.player;
      if (playerRole === 1) {
        playerPill.textContent = 'P1 (CYAN)';
        document.body.classList.remove('p2-mode');
      } else if (playerRole === 2) {
        playerPill.textContent = 'P2 (ORANGE)';
        document.body.classList.add('p2-mode');
      } else {
        playerPill.textContent = 'SPECTATOR';
      }
    } else if (msg.type === 'lobby_state') {
      currentLobby = msg;
      updateLobbyUI(msg);
    } else if (msg.type === 'game_start') {
      highlightActiveGame(msg.game);
      lcdStatus.textContent = `${(msg.game || '').toUpperCase()} ACTIVE!`;
      vibrate(40);
    } else if (msg.type === 'game_update') {
      if (msg.score_text) {
        lcdStatus.textContent = msg.score_text;
      }
    } else if (msg.type === 'game_over') {
      lcdStatus.textContent = `${msg.winner_text || 'GAME OVER'} (PRESS START)`;
      if (msg.winner === playerRole) {
        vibrate([40, 60, 40, 60, 80]);
      } else {
        vibrate(100);
      }
    }
  }

  function highlightActiveGame(gameId) {
    gameChips.forEach(chip => {
      if (chip.dataset.game === gameId) {
        chip.classList.add('active');
        chip.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
      } else {
        chip.classList.remove('active');
      }
    });
  }

  function updateLobbyUI(state) {
    highlightActiveGame(state.selected_game);
    const activeGameConfig = (state.games || []).find(g => g.id === state.selected_game);
    const supports2p = activeGameConfig ? activeGameConfig.supports_2p : false;

    if (!supports2p) {
      selectedSolo = true;
      modeBadge.textContent = '1P SOLO';
    } else {
      modeBadge.textContent = selectedSolo ? '1P SOLO' : '2P BATTLE';
    }

    if (state.phase === 'lobby') {
      if (state.selected_game) {
        const title = (state.selected_game || '').toUpperCase();
        if (supports2p && !selectedSolo && !state.p2_connected) {
          lcdStatus.textContent = `${title}: WAITING FOR P2...`;
        } else {
          lcdStatus.textContent = `${title}: PRESS START`;
        }
      }
    } else if (state.phase === 'countdown') {
      lcdStatus.textContent = 'GET READY...';
    }
  }

  // Tapping Game Chips directly switches active game without crash
  gameChips.forEach(chip => {
    chip.addEventListener('click', (e) => {
      e.preventDefault();
      const game = chip.dataset.game;
      highlightActiveGame(game);
      send({ type: 'select_game', game: game });
      vibrate(15);
    });
  });

  // Mode badge toggles 1P Solo vs 2P Battle
  modeBadge.addEventListener('click', (e) => {
    e.preventDefault();
    selectedSolo = !selectedSolo;
    modeBadge.textContent = selectedSolo ? '1P SOLO' : '2P BATTLE';
    vibrate(15);
  });

  // SELECT button: cycles game or menu navigation
  btnSelect.addEventListener('click', (e) => {
    e.preventDefault();
    send({ type: 'menu_nav', dir: 'next' });
    vibrate(20);
  });

  // START button: starts game or triggers rematch
  btnStart.addEventListener('click', (e) => {
    e.preventDefault();
    const game = currentLobby ? currentLobby.selected_game : 'pong';
    if (currentLobby && currentLobby.phase === 'gameover') {
      send({ type: 'rematch' });
    } else {
      send({ type: 'start_game', game: game, solo: selectedSolo });
    }
    vibrate(30);
  });

  // Bind Physical Buttons with immediate zero-lag touch and haptics
  function bindTouchButton(btn, action) {
    const onDown = (e) => {
      e.preventDefault();
      btn.classList.add('touch-active');
      send({ type: 'input', action: action, state: 'down' });

      // In lobby/gameover, A button can also start or rematch!
      if (action === 'action_a' && currentLobby && (currentLobby.phase === 'lobby' || currentLobby.phase === 'gameover')) {
        btnStart.click();
      }
      vibrate(15);
    };

    const onUp = (e) => {
      e.preventDefault();
      btn.classList.remove('touch-active');
      send({ type: 'input', action: action, state: 'up' });
    };

    btn.addEventListener('touchstart', onDown, { passive: false });
    btn.addEventListener('touchend', onUp, { passive: false });
    btn.addEventListener('touchcancel', onUp, { passive: false });
    btn.addEventListener('mousedown', onDown);
    btn.addEventListener('mouseup', onUp);
    btn.addEventListener('mouseleave', onUp);
  }

  document.querySelectorAll('[data-action]').forEach(el => {
    bindTouchButton(el, el.dataset.action);
  });

  // Desktop keyboard controls for testing / pair programming
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
    'Enter': 'start',
    'Tab': 'select',
  };

  const heldKeys = new Set();
  window.addEventListener('keydown', (e) => {
    if (e.repeat) return;
    if (e.code === 'Enter') {
      btnStart.click();
      return;
    }
    if (e.code === 'Tab') {
      e.preventDefault();
      btnSelect.click();
      return;
    }
    const act = KEY_MAP[e.code];
    if (act && !heldKeys.has(act)) {
      heldKeys.add(act);
      send({ type: 'input', action: act, state: 'down' });
    }
  });

  window.addEventListener('keyup', (e) => {
    const act = KEY_MAP[e.code];
    if (act) {
      heldKeys.delete(act);
      send({ type: 'input', action: act, state: 'up' });
    }
  });

  connect();
})();
</script>
</body>
</html>
"""
