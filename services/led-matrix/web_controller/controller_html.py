"""Mobile Web Controller single-page application for LED Matrix Arcade."""

CONTROLLER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0a0a12">
<title>LED Matrix Arcade</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; -webkit-touch-callout: none; -webkit-user-select: none; user-select: none; }
  body {
    background-color: #080810;
    color: #e0e0ff;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
    height: 100vh;
    height: 100dvh;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    touch-action: none;
  }
  header {
    background: #101020;
    border-bottom: 2px solid #202038;
    padding: 12px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .title { font-weight: 800; font-size: 14px; letter-spacing: 1px; color: #8888aa; }
  .badge {
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .badge-p1 { background: rgba(0, 229, 255, 0.2); color: #00e5ff; border: 1px solid #00e5ff; }
  .badge-p2 { background: rgba(255, 145, 0, 0.2); color: #ff9100; border: 1px solid #ff9100; }
  .badge-spec { background: #252535; color: #8888aa; border: 1px solid #404055; }

  #main-view {
    flex: 1;
    display: flex;
    flex-direction: column;
    padding: 16px;
    justify-content: center;
    align-items: center;
    position: relative;
  }

  /* Lobby view */
  .lobby-card {
    background: #121222;
    border: 2px solid #2a2a44;
    border-radius: 16px;
    padding: 24px;
    width: 100%;
    max-width: 360px;
    text-align: center;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  }
  .lobby-title { font-size: 22px; font-weight: 800; margin-bottom: 8px; color: #fff; }
  .lobby-subtitle { font-size: 14px; color: #8888aa; margin-bottom: 20px; }
  .player-slots {
    display: flex;
    gap: 12px;
    margin-bottom: 24px;
  }
  .slot {
    flex: 1;
    padding: 12px 8px;
    border-radius: 10px;
    background: #18182c;
    border: 1px dashed #353555;
    font-size: 13px;
    font-weight: 700;
  }
  .slot.connected.p1 { border: 1px solid #00e5ff; background: rgba(0, 229, 255, 0.1); color: #00e5ff; }
  .slot.connected.p2 { border: 1px solid #ff9100; background: rgba(255, 145, 0, 0.1); color: #ff9100; }

  .game-select {
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: 100%;
    margin-bottom: 20px;
  }
  .btn-game {
    background: #1c1c34;
    border: 2px solid #35355a;
    color: #fff;
    padding: 14px;
    border-radius: 12px;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .btn-game.active {
    border-color: #00e5ff;
    background: rgba(0, 229, 255, 0.15);
    color: #00e5ff;
  }
  .btn-start {
    background: linear-gradient(135deg, #00e5ff, #0088cc);
    color: #000;
    font-size: 18px;
    font-weight: 800;
    padding: 16px;
    border-radius: 12px;
    border: none;
    width: 100%;
    cursor: pointer;
    box-shadow: 0 4px 16px rgba(0, 229, 255, 0.4);
  }
  .btn-start:disabled {
    background: #252535;
    color: #555577;
    box-shadow: none;
    cursor: not-allowed;
  }

  /* Pong View */
  #pong-view {
    display: none;
    width: 100%;
    height: 100%;
    flex-direction: column;
    gap: 12px;
  }
  .pong-header {
    text-align: center;
    font-size: 20px;
    font-weight: 800;
    padding: 8px 0;
    letter-spacing: 2px;
  }
  .btn-pong-up, .btn-pong-down {
    flex: 1;
    border-radius: 16px;
    border: 3px solid #303055;
    background: #141428;
    color: #fff;
    font-size: 32px;
    font-weight: 800;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    touch-action: none;
  }
  .btn-pong-up:active, .btn-pong-up.touch-active { background: #25254d; border-color: #00e5ff; color: #00e5ff; }
  .btn-pong-down:active, .btn-pong-down.touch-active { background: #25254d; border-color: #00e5ff; color: #00e5ff; }
  body.p2-mode .btn-pong-up:active, body.p2-mode .btn-pong-up.touch-active { border-color: #ff9100; color: #ff9100; }
  body.p2-mode .btn-pong-down:active, body.p2-mode .btn-pong-down.touch-active { border-color: #ff9100; color: #ff9100; }

  /* Tron View */
  #tron-view {
    display: none;
    width: 100%;
    height: 100%;
    flex-direction: column;
    justify-content: center;
    align-items: center;
  }
  .dpad {
    width: 280px;
    height: 280px;
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    grid-template-rows: 1fr 1fr 1fr;
    gap: 8px;
  }
  .dpad-btn {
    background: #141428;
    border: 3px solid #303055;
    border-radius: 16px;
    color: #fff;
    font-size: 28px;
    font-weight: 800;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    touch-action: none;
  }
  .dpad-btn:active, .dpad-btn.touch-active { background: #25254d; border-color: #00e5ff; color: #00e5ff; }
  body.p2-mode .dpad-btn:active, body.p2-mode .dpad-btn.touch-active { border-color: #ff9100; color: #ff9100; }
  .dpad-up    { grid-column: 2; grid-row: 1; }
  .dpad-left  { grid-column: 1; grid-row: 2; }
  .dpad-right { grid-column: 3; grid-row: 2; }
  .dpad-down  { grid-column: 2; grid-row: 3; }
  .dpad-center { grid-column: 2; grid-row: 2; background: #0c0c16; border-color: #202035; border-radius: 50%; }

  /* Banner overlay */
  #banner {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: rgba(10, 10, 20, 0.95);
    border: 2px solid #00e5ff;
    border-radius: 16px;
    padding: 24px 32px;
    text-align: center;
    display: none;
    z-index: 100;
  }
  #banner.p2-border { border-color: #ff9100; }
  #banner-text { font-size: 26px; font-weight: 900; margin-bottom: 16px; }
  .btn-rematch {
    background: #00e5ff;
    color: #000;
    font-size: 16px;
    font-weight: 800;
    padding: 12px 24px;
    border-radius: 10px;
    border: none;
    cursor: pointer;
  }
</style>
</head>
<body>
<header>
  <div class="title">LED MATRIX ARCADE</div>
  <div id="player-badge" class="badge badge-spec">Connecting...</div>
</header>

<div id="main-view">
  <!-- Lobby View -->
  <div id="lobby-view" class="lobby-card">
    <div class="lobby-title">BATTLE LOBBY</div>
    <div class="lobby-subtitle" id="lobby-sub">Connecting to matrix...</div>

    <div class="player-slots">
      <div id="slot-p1" class="slot">P1: WAITING</div>
      <div id="slot-p2" class="slot">P2: WAITING</div>
    </div>

    <div class="game-select">
      <button class="btn-game active" data-game="pong" id="opt-pong">🏓 2P PONG</button>
      <button class="btn-game" data-game="tron" id="opt-tron">🏍️ TRON CYCLES</button>
    </div>

    <button id="btn-start" class="btn-start" disabled>WAITING FOR PLAYERS...</button>
    <div style="margin-top: 14px;">
      <button id="btn-solo" style="background: none; border: 1px solid #444; color: #888; padding: 8px 16px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer;">
        PLAY SOLO VS AI
      </button>
    </div>
  </div>

  <!-- Pong View -->
  <div id="pong-view">
    <div class="pong-header" id="pong-score">P1: 0  |  P2: 0</div>
    <button class="btn-pong-up" id="btn-pong-up">▲ UP</button>
    <button class="btn-pong-down" id="btn-pong-down">▼ DOWN</button>
  </div>

  <!-- Tron View -->
  <div id="tron-view">
    <div class="pong-header" id="tron-score">ROUND 1</div>
    <div class="dpad">
      <button class="dpad-btn dpad-up" data-dir="up">▲</button>
      <button class="dpad-btn dpad-left" data-dir="left">◀</button>
      <div class="dpad-center"></div>
      <button class="dpad-btn dpad-right" data-dir="right">▶</button>
      <button class="dpad-btn dpad-down" data-dir="down">▼</button>
    </div>
  </div>

  <!-- Game Over Banner -->
  <div id="banner">
    <div id="banner-text">P1 WINS!</div>
    <button class="btn-rematch" id="btn-rematch">REMATCH</button>
  </div>
</div>

<script>
(function() {
  let ws = null;
  let playerRole = null; // 1, 2, or null
  let selectedGame = 'pong';
  let activeState = 'lobby'; // lobby, playing, gameover

  const badge = document.getElementById('player-badge');
  const lobbyView = document.getElementById('lobby-view');
  const pongView = document.getElementById('pong-view');
  const tronView = document.getElementById('tron-view');
  const banner = document.getElementById('banner');
  const bannerText = document.getElementById('banner-text');
  const slotP1 = document.getElementById('slot-p1');
  const slotP2 = document.getElementById('slot-p2');
  const btnStart = document.getElementById('btn-start');
  const btnSolo = document.getElementById('btn-solo');
  const lobbySub = document.getElementById('lobby-sub');

  function vibrate(ms) {
    if (navigator.vibrate) {
      try { navigator.vibrate(ms); } catch (e) {}
    }
  }

  function connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      badge.textContent = 'CONNECTED';
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
      } catch (e) {
        console.error('WS Parse Error', e);
      }
    };

    ws.onclose = () => {
      badge.className = 'badge badge-spec';
      badge.textContent = 'DISCONNECTED';
      setTimeout(connect, 1500);
    };
  }

  function send(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }

  function handleMessage(msg) {
    if (msg.type === 'assign') {
      playerRole = msg.player;
      if (playerRole === 1) {
        badge.className = 'badge badge-p1';
        badge.textContent = 'PLAYER 1 (CYAN)';
        document.body.classList.remove('p2-mode');
      } else if (playerRole === 2) {
        badge.className = 'badge badge-p2';
        badge.textContent = 'PLAYER 2 (ORANGE)';
        document.body.classList.add('p2-mode');
      } else {
        badge.className = 'badge badge-spec';
        badge.textContent = 'SPECTATOR';
      }
    } else if (msg.type === 'lobby_state') {
      updateLobby(msg);
    } else if (msg.type === 'game_start') {
      launchGame(msg.game);
    } else if (msg.type === 'game_update') {
      updateGameUI(msg);
    } else if (msg.type === 'game_over') {
      showGameOver(msg);
    }
  }

  function updateLobby(data) {
    if (data.p1_connected) {
      slotP1.className = 'slot connected p1';
      slotP1.textContent = 'P1: READY';
    } else {
      slotP1.className = 'slot';
      slotP1.textContent = 'P1: WAITING';
    }

    if (data.p2_connected) {
      slotP2.className = 'slot connected p2';
      slotP2.textContent = 'P2: READY';
    } else {
      slotP2.className = 'slot';
      slotP2.textContent = 'P2: WAITING';
    }

    if (playerRole === 1) {
      if (data.p2_connected) {
        lobbySub.textContent = 'Player 2 joined! Ready to start.';
        btnStart.disabled = false;
        btnStart.textContent = 'START BATTLE';
      } else {
        lobbySub.textContent = 'Scan QR code on second phone...';
        btnStart.disabled = true;
        btnStart.textContent = 'WAITING FOR PLAYER 2...';
      }
    } else if (playerRole === 2) {
      lobbySub.textContent = 'Waiting for Player 1 to start...';
      btnStart.disabled = true;
      btnStart.textContent = 'READY (WAITING FOR P1)';
    } else {
      lobbySub.textContent = 'Lobby is full. Spectating.';
      btnStart.disabled = true;
    }
  }

  function launchGame(game) {
    activeState = 'playing';
    lobbyView.style.display = 'none';
    banner.style.display = 'none';

    if (game === 'pong') {
      pongView.style.display = 'flex';
      tronView.style.display = 'none';
    } else if (game === 'tron') {
      tronView.style.display = 'flex';
      pongView.style.display = 'none';
    }
    vibrate(50);
  }

  function updateGameUI(msg) {
    if (msg.score_text) {
      const pongScore = document.getElementById('pong-score');
      const tronScore = document.getElementById('tron-score');
      if (pongScore) pongScore.textContent = msg.score_text;
      if (tronScore) tronScore.textContent = msg.score_text;
    }
  }

  function showGameOver(msg) {
    activeState = 'gameover';
    banner.style.display = 'block';
    bannerText.textContent = msg.winner_text || 'GAME OVER';
    if (msg.winner === playerRole) {
      bannerText.textContent += ' 🏆';
      vibrate([40, 60, 40, 60, 80]);
    } else {
      vibrate(100);
    }
  }

  // Bind Lobby Controls
  document.querySelectorAll('.btn-game').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.btn-game').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      selectedGame = btn.dataset.game;
      vibrate(15);
    });
  });

  btnStart.addEventListener('click', () => {
    send({ type: 'start_game', game: selectedGame, solo: false });
    vibrate(25);
  });

  btnSolo.addEventListener('click', () => {
    send({ type: 'start_game', game: selectedGame, solo: true });
    vibrate(25);
  });

  document.getElementById('btn-rematch').addEventListener('click', () => {
    banner.style.display = 'none';
    send({ type: 'rematch' });
    vibrate(20);
  });

  // Touch Handling for Pong
  function attachTouch(btn, action) {
    const startHandler = (e) => {
      e.preventDefault();
      btn.classList.add('touch-active');
      send({ type: 'input', action: action, state: 'down' });
      vibrate(15);
    };
    const endHandler = (e) => {
      e.preventDefault();
      btn.classList.remove('touch-active');
      send({ type: 'input', action: action, state: 'up' });
    };

    btn.addEventListener('touchstart', startHandler, { passive: false });
    btn.addEventListener('touchend', endHandler, { passive: false });
    btn.addEventListener('touchcancel', endHandler, { passive: false });
    btn.addEventListener('mousedown', startHandler);
    btn.addEventListener('mouseup', endHandler);
    btn.addEventListener('mouseleave', endHandler);
  }

  attachTouch(document.getElementById('btn-pong-up'), 'up');
  attachTouch(document.getElementById('btn-pong-down'), 'down');

  // Touch Handling for Tron
  document.querySelectorAll('.dpad-btn').forEach(btn => {
    const dir = btn.dataset.dir;
    const startHandler = (e) => {
      e.preventDefault();
      btn.classList.add('touch-active');
      send({ type: 'input', action: dir, state: 'down' });
      vibrate(15);
    };
    const endHandler = (e) => {
      e.preventDefault();
      btn.classList.remove('touch-active');
    };
    btn.addEventListener('touchstart', startHandler, { passive: false });
    btn.addEventListener('touchend', endHandler, { passive: false });
    btn.addEventListener('touchcancel', endHandler, { passive: false });
    btn.addEventListener('mousedown', startHandler);
    btn.addEventListener('mouseup', endHandler);
  });

  connect();
})();
</script>
</body>
</html>
"""
