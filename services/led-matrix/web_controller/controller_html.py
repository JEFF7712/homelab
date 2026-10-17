"""Mobile Web Controller single-page application for LED Matrix Arcade."""

CONTROLLER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0a0a14">
<title>LED Matrix Arcade</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; -webkit-touch-callout: none; -webkit-user-select: none; user-select: none; }
  body {
    background-color: #070711;
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
    background: #0f1020;
    border-bottom: 2px solid #1f2038;
    padding: 10px 14px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    z-index: 50;
  }
  .header-left {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .btn-menu-exit {
    background: #1c1c32;
    border: 1px solid #353555;
    color: #a0a0cc;
    font-size: 11px;
    font-weight: 700;
    padding: 6px 10px;
    border-radius: 8px;
    cursor: pointer;
    display: none;
  }
  .btn-menu-exit:active { background: #282845; color: #fff; }
  .title { font-weight: 800; font-size: 13px; letter-spacing: 1px; color: #7878a0; }
  .badge {
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .badge-p1 { background: rgba(0, 229, 255, 0.2); color: #00e5ff; border: 1px solid #00e5ff; }
  .badge-p2 { background: rgba(255, 145, 0, 0.2); color: #ff9100; border: 1px solid #ff9100; }
  .badge-spec { background: #222234; color: #7878a0; border: 1px solid #383850; }

  #main-view {
    flex: 1;
    display: flex;
    flex-direction: column;
    padding: 12px;
    justify-content: center;
    align-items: center;
    position: relative;
    overflow: hidden;
  }

  /* Carousel Wheel Menu View */
  #menu-view {
    display: flex;
    flex-direction: column;
    width: 100%;
    max-width: 380px;
    height: 100%;
    justify-content: space-between;
    padding: 8px 0;
  }
  .menu-card {
    background: #101222;
    border: 2px solid #282c48;
    border-radius: 18px;
    padding: 16px;
    text-align: center;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    margin-bottom: 8px;
  }
  .menu-banner-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .matrix-hint {
    font-size: 11px;
    color: #00e5ff;
    letter-spacing: 1px;
    font-weight: 700;
  }
  .game-badge {
    padding: 3px 8px;
    border-radius: 8px;
    font-size: 11px;
    font-weight: 800;
    background: rgba(0, 229, 255, 0.15);
    color: #00e5ff;
    border: 1px solid #00e5ff;
  }
  .game-title-lg {
    font-size: 26px;
    font-weight: 900;
    color: #ffffff;
    letter-spacing: 1px;
    margin: 4px 0;
  }
  .game-desc {
    font-size: 13px;
    color: #8c8caa;
    min-height: 34px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .nav-row {
    display: flex;
    gap: 10px;
    width: 100%;
    margin-top: 6px;
  }
  .btn-nav {
    flex: 1;
    background: #17182c;
    border: 2px solid #303356;
    color: #00e5ff;
    font-size: 16px;
    font-weight: 800;
    padding: 14px 10px;
    border-radius: 14px;
    cursor: pointer;
    touch-action: manipulation;
    transition: all 0.1s ease;
  }
  .btn-nav:active {
    background: #252848;
    border-color: #00e5ff;
    transform: scale(0.97);
  }

  .mode-toggle-box {
    background: #0d0e1a;
    border: 1px solid #23253c;
    border-radius: 12px;
    padding: 6px;
    display: flex;
    gap: 6px;
    margin: 10px 0;
  }
  .btn-mode {
    flex: 1;
    background: none;
    border: none;
    color: #777799;
    padding: 8px 4px;
    font-size: 12px;
    font-weight: 700;
    border-radius: 8px;
    cursor: pointer;
  }
  .btn-mode.active {
    background: #1f223d;
    color: #fff;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4);
  }

  .btn-play-action {
    background: linear-gradient(135deg, #00e5ff, #0077cc);
    color: #000;
    font-size: 20px;
    font-weight: 900;
    padding: 16px;
    border-radius: 14px;
    border: none;
    width: 100%;
    cursor: pointer;
    box-shadow: 0 4px 20px rgba(0, 229, 255, 0.4);
    touch-action: manipulation;
  }
  .btn-play-action:active { transform: scale(0.98); }
  .btn-play-action:disabled {
    background: #202234;
    color: #555577;
    box-shadow: none;
    cursor: not-allowed;
  }

  .lobby-footer {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin-top: 8px;
  }
  .btn-sub-action {
    flex: 1;
    background: #141424;
    border: 1px solid #2a2a44;
    color: #8888aa;
    padding: 8px 6px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
  }
  .btn-sub-action:active { background: #222238; color: #fff; }

  /* Generic Game HUD Header */
  .game-hud {
    text-align: center;
    font-size: 18px;
    font-weight: 800;
    padding: 6px 0 12px 0;
    letter-spacing: 1.5px;
    color: #ffffff;
    width: 100%;
  }

  /* Pong View */
  #pong-view {
    display: none;
    width: 100%;
    height: 100%;
    flex-direction: column;
    gap: 12px;
  }
  .btn-pong {
    flex: 1;
    border-radius: 18px;
    border: 3px solid #303358;
    background: #131428;
    color: #fff;
    font-size: 32px;
    font-weight: 900;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    touch-action: none;
  }
  .btn-pong:active, .btn-pong.touch-active {
    background: #23254c;
    border-color: #00e5ff;
    color: #00e5ff;
  }
  body.p2-mode .btn-pong:active, body.p2-mode .btn-pong.touch-active {
    border-color: #ff9100;
    color: #ff9100;
  }

  /* D-pad Views (Tron & Snake) */
  #tron-view, #snake-view {
    display: none;
    width: 100%;
    height: 100%;
    flex-direction: column;
    justify-content: space-between;
    align-items: center;
  }
  .dpad-container {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
  }
  .dpad {
    width: min(320px, 85vw);
    height: min(320px, 85vw);
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    grid-template-rows: 1fr 1fr 1fr;
    gap: 10px;
  }
  .dpad-btn {
    background: #14152a;
    border: 3px solid #2d3154;
    border-radius: 18px;
    color: #fff;
    font-size: 32px;
    font-weight: 900;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    touch-action: none;
  }
  .dpad-btn:active, .dpad-btn.touch-active {
    background: #252852;
    border-color: #00e5ff;
    color: #00e5ff;
  }
  body.p2-mode .dpad-btn:active, body.p2-mode .dpad-btn.touch-active {
    border-color: #ff9100;
    color: #ff9100;
  }
  .dpad-up    { grid-column: 2; grid-row: 1; }
  .dpad-left  { grid-column: 1; grid-row: 2; }
  .dpad-right { grid-column: 3; grid-row: 2; }
  .dpad-down  { grid-column: 2; grid-row: 3; }
  .dpad-center {
    grid-column: 2;
    grid-row: 2;
    background: #0d0e1b;
    border: 2px solid #202238;
    border-radius: 50%;
  }

  /* Breakout View */
  #breakout-view {
    display: none;
    width: 100%;
    height: 100%;
    flex-direction: column;
    justify-content: space-between;
    gap: 14px;
  }
  .breakout-controls {
    flex: 1;
    display: flex;
    gap: 14px;
    width: 100%;
  }
  .btn-paddle {
    flex: 1;
    border-radius: 20px;
    border: 3px solid #303358;
    background: #14152a;
    color: #fff;
    font-size: 26px;
    font-weight: 900;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    touch-action: none;
    gap: 8px;
  }
  .btn-paddle span { font-size: 13px; font-weight: 700; color: #8888bb; }
  .btn-paddle:active, .btn-paddle.touch-active {
    background: #252852;
    border-color: #00e5ff;
    color: #00e5ff;
  }

  /* Tetris View */
  #tetris-view {
    display: none;
    width: 100%;
    height: 100%;
    flex-direction: column;
    justify-content: space-between;
  }
  .tetris-controls {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 12px;
    justify-content: center;
    width: 100%;
    max-width: 380px;
  }
  .tetris-row {
    display: flex;
    gap: 12px;
    height: 90px;
  }
  .btn-tetris {
    flex: 1;
    background: #14152a;
    border: 3px solid #303358;
    border-radius: 16px;
    color: #fff;
    font-size: 24px;
    font-weight: 900;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    touch-action: none;
    gap: 4px;
  }
  .btn-tetris span { font-size: 11px; font-weight: 700; color: #8888bb; }
  .btn-tetris.action-rot {
    background: #191b36;
    border-color: #7b42f6;
    color: #d1b3ff;
  }
  .btn-tetris.action-drop {
    background: #1c152a;
    border-color: #f6427b;
    color: #ffb3d1;
  }
  .btn-tetris:active, .btn-tetris.touch-active {
    background: #252852;
    border-color: #00e5ff;
    color: #00e5ff;
  }

  /* Game Over Banner Overlay */
  #banner {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: rgba(10, 11, 22, 0.96);
    border: 3px solid #00e5ff;
    border-radius: 20px;
    padding: 24px 28px;
    text-align: center;
    display: none;
    z-index: 100;
    width: 90%;
    max-width: 340px;
    box-shadow: 0 10px 40px rgba(0,0,0,0.8);
  }
  #banner.p2-border { border-color: #ff9100; }
  #banner-title { font-size: 28px; font-weight: 900; margin-bottom: 8px; color: #fff; }
  #banner-score { font-size: 15px; color: #00e5ff; margin-bottom: 20px; font-weight: 700; }
  .banner-buttons {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .btn-rematch {
    background: #00e5ff;
    color: #000;
    font-size: 17px;
    font-weight: 900;
    padding: 14px;
    border-radius: 12px;
    border: none;
    cursor: pointer;
  }
  .btn-back-menu {
    background: #1e2034;
    border: 1px solid #353856;
    color: #fff;
    font-size: 14px;
    font-weight: 700;
    padding: 12px;
    border-radius: 12px;
    cursor: pointer;
  }
</style>
</head>
<body>
<header>
  <div class="header-left">
    <button class="btn-menu-exit" id="btn-header-menu">⌂ MENU</button>
    <div class="title">LED MATRIX ARCADE</div>
  </div>
  <div id="player-badge" class="badge badge-spec">Connecting...</div>
</header>

<div id="main-view">
  <!-- Carousel Menu View -->
  <div id="menu-view">
    <div class="menu-card">
      <div class="menu-banner-top">
        <span class="matrix-hint">◀ MATRIX CAROUSEL ▶</span>
        <span class="game-badge" id="menu-game-badge">1P / 2P</span>
      </div>
      <div class="game-title-lg" id="menu-game-title">PONG 2P</div>
      <div class="game-desc" id="menu-game-desc">Classic arcade table tennis battle</div>

      <!-- Left / Right Carousel Controls -->
      <div class="nav-row">
        <button class="btn-nav" id="btn-nav-prev">◀ PREV</button>
        <button class="btn-nav" id="btn-nav-next">NEXT ▶</button>
      </div>
    </div>

    <!-- Mode Selector (For games supporting 2P) -->
    <div class="mode-toggle-box" id="mode-box">
      <button class="btn-mode active" id="btn-mode-2p">2-PLAYER BATTLE</button>
      <button class="btn-mode" id="btn-mode-1p">PLAY SOLO VS AI</button>
    </div>

    <!-- Start / Play Action -->
    <button id="btn-start" class="btn-play-action">▶ START GAME</button>

    <!-- Sub Actions: Toggle Matrix QR & Info -->
    <div class="lobby-footer">
      <button class="btn-sub-action" id="btn-toggle-qr">Toggle QR on Matrix</button>
      <button class="btn-sub-action" id="btn-p2-status">P2: Waiting...</button>
    </div>
  </div>

  <!-- Pong View -->
  <div id="pong-view">
    <div class="game-hud" id="pong-hud">P1: 0  |  P2: 0</div>
    <button class="btn-pong" data-action="up">▲ PADDLE UP</button>
    <button class="btn-pong" data-action="down">▼ PADDLE DOWN</button>
  </div>

  <!-- Tron View -->
  <div id="tron-view">
    <div class="game-hud" id="tron-hud">ROUND 1</div>
    <div class="dpad-container">
      <div class="dpad">
        <button class="dpad-btn dpad-up" data-action="up">▲</button>
        <button class="dpad-btn dpad-left" data-action="left">◀</button>
        <div class="dpad-center"></div>
        <button class="dpad-btn dpad-right" data-action="right">▶</button>
        <button class="dpad-btn dpad-down" data-action="down">▼</button>
      </div>
    </div>
  </div>

  <!-- Snake View -->
  <div id="snake-view">
    <div class="game-hud" id="snake-hud">SCORE: 0</div>
    <div class="dpad-container">
      <div class="dpad">
        <button class="dpad-btn dpad-up" data-action="up">▲</button>
        <button class="dpad-btn dpad-left" data-action="left">◀</button>
        <div class="dpad-center"></div>
        <button class="dpad-btn dpad-right" data-action="right">▶</button>
        <button class="dpad-btn dpad-down" data-action="down">▼</button>
      </div>
    </div>
  </div>

  <!-- Breakout View -->
  <div id="breakout-view">
    <div class="game-hud" id="breakout-hud">SCORE: 0 | LIVES: ♥♥♥</div>
    <div class="breakout-controls">
      <button class="btn-paddle" data-action="left">
        ◀
        <span>LEFT</span>
      </button>
      <button class="btn-paddle" data-action="right">
        ▶
        <span>RIGHT</span>
      </button>
    </div>
  </div>

  <!-- Tetris View -->
  <div id="tetris-view">
    <div class="game-hud" id="tetris-hud">SCORE: 0 | LINES: 0</div>
    <div class="tetris-controls">
      <div class="tetris-row">
        <button class="btn-tetris action-rot" data-action="rotate">
          ⟳
          <span>ROTATE</span>
        </button>
        <button class="btn-tetris action-drop" data-action="drop">
          ⏬
          <span>HARD DROP</span>
        </button>
      </div>
      <div class="tetris-row">
        <button class="btn-tetris" data-action="left">
          ◀
          <span>LEFT</span>
        </button>
        <button class="btn-tetris" data-action="down">
          ▼
          <span>SOFT DROP</span>
        </button>
        <button class="btn-tetris" data-action="right">
          ▶
          <span>RIGHT</span>
        </button>
      </div>
    </div>
  </div>

  <!-- Game Over Banner -->
  <div id="banner">
    <div id="banner-title">GAME OVER</div>
    <div id="banner-score"></div>
    <div class="banner-buttons">
      <button class="btn-rematch" id="btn-banner-rematch">REMATCH</button>
      <button class="btn-back-menu" id="btn-banner-menu">BACK TO MENU</button>
    </div>
  </div>
</div>

<script>
(function() {
  let ws = null;
  let playerRole = null; // 1, 2, or 0
  let currentLobbyState = null;
  let selectedModeSolo = false;

  const badge = document.getElementById('player-badge');
  const btnHeaderMenu = document.getElementById('btn-header-menu');

  const menuView = document.getElementById('menu-view');
  const pongView = document.getElementById('pong-view');
  const tronView = document.getElementById('tron-view');
  const snakeView = document.getElementById('snake-view');
  const breakoutView = document.getElementById('breakout-view');
  const tetrisView = document.getElementById('tetris-view');
  const allViews = [menuView, pongView, tronView, snakeView, breakoutView, tetrisView];

  const banner = document.getElementById('banner');
  const bannerTitle = document.getElementById('banner-title');
  const bannerScore = document.getElementById('banner-score');

  const menuGameBadge = document.getElementById('menu-game-badge');
  const menuGameTitle = document.getElementById('menu-game-title');
  const menuGameDesc = document.getElementById('menu-game-desc');
  const modeBox = document.getElementById('mode-box');
  const btnMode2p = document.getElementById('btn-mode-2p');
  const btnMode1p = document.getElementById('btn-mode-1p');
  const btnStart = document.getElementById('btn-start');
  const btnP2Status = document.getElementById('btn-p2-status');
  const btnToggleQr = document.getElementById('btn-toggle-qr');

  function vibrate(ms) {
    if (navigator.vibrate) {
      try { navigator.vibrate(ms); } catch (e) {}
    }
  }

  function send(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
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
      currentLobbyState = msg;
      if (msg.phase === 'lobby') {
        showView('menu');
      }
      updateMenuUI(msg);
    } else if (msg.type === 'game_start') {
      launchGame(msg.game);
    } else if (msg.type === 'game_update') {
      updateGameHUD(msg);
    } else if (msg.type === 'game_over') {
      showGameOver(msg);
    }
  }

  function showView(viewName) {
    allViews.forEach(v => v.style.display = 'none');
    banner.style.display = 'none';

    if (viewName === 'menu') {
      menuView.style.display = 'flex';
      btnHeaderMenu.style.display = 'none';
    } else {
      btnHeaderMenu.style.display = 'block';
      if (viewName === 'pong') pongView.style.display = 'flex';
      else if (viewName === 'tron') tronView.style.display = 'flex';
      else if (viewName === 'snake') snakeView.style.display = 'flex';
      else if (viewName === 'breakout') breakoutView.style.display = 'flex';
      else if (viewName === 'tetris') tetrisView.style.display = 'flex';
    }
  }

  function updateMenuUI(state) {
    const games = state.games || [];
    const currGame = games[state.selected_game_index] || {
      id: state.selected_game,
      name: state.selected_game.toUpperCase(),
      badge: '1P / 2P',
      desc: '',
      supports_2p: true,
    };

    menuGameTitle.textContent = currGame.name;
    menuGameBadge.textContent = currGame.badge;
    menuGameDesc.textContent = currGame.desc;

    // Toggle 2P vs Solo mode buttons
    if (currGame.supports_2p) {
      modeBox.style.display = 'flex';
      if (selectedModeSolo) {
        btnMode1p.classList.add('active');
        btnMode2p.classList.remove('active');
      } else {
        btnMode2p.classList.add('active');
        btnMode1p.classList.remove('active');
      }
    } else {
      modeBox.style.display = 'none';
      selectedModeSolo = true;
    }

    // P2 connection badge
    if (state.p2_connected) {
      btnP2Status.textContent = 'P2: READY';
      btnP2Status.style.color = '#ff9100';
    } else {
      btnP2Status.textContent = 'P2: WAITING...';
      btnP2Status.style.color = '#8888aa';
    }

    // Start button state
    if (playerRole === 1) {
      btnStart.disabled = false;
      if (currGame.supports_2p && !selectedModeSolo && !state.p2_connected) {
        btnStart.textContent = 'WAITING FOR P2 (OR PLAY SOLO)';
      } else {
        btnStart.textContent = '▶ START GAME';
      }
    } else if (playerRole === 2) {
      btnStart.disabled = true;
      btnStart.textContent = 'WAITING FOR P1 TO START';
    } else {
      btnStart.disabled = true;
      btnStart.textContent = 'SPECTATING';
    }
  }

  function launchGame(game) {
    showView(game);
    vibrate(40);
  }

  function updateGameHUD(msg) {
    if (msg.score_text) {
      const el = document.getElementById(`${currentLobbyState?.selected_game || 'pong'}-hud`);
      if (el) el.textContent = msg.score_text;
    }
  }

  function showGameOver(msg) {
    banner.style.display = 'block';
    bannerTitle.textContent = msg.winner_text || 'GAME OVER';
    bannerScore.textContent = msg.score_text || '';
    if (msg.winner === playerRole) {
      bannerTitle.textContent += ' 🏆';
      vibrate([40, 60, 40, 60, 80]);
    } else {
      vibrate(120);
    }
  }

  // Bind Carousel Navigation
  document.getElementById('btn-nav-prev').addEventListener('click', () => {
    send({ type: 'menu_nav', dir: 'prev' });
    vibrate(15);
  });
  document.getElementById('btn-nav-next').addEventListener('click', () => {
    send({ type: 'menu_nav', dir: 'next' });
    vibrate(15);
  });

  // Mode Selection
  btnMode2p.addEventListener('click', () => {
    selectedModeSolo = false;
    btnMode2p.classList.add('active');
    btnMode1p.classList.remove('active');
    updateMenuUI(currentLobbyState);
    vibrate(15);
  });
  btnMode1p.addEventListener('click', () => {
    selectedModeSolo = true;
    btnMode1p.classList.add('active');
    btnMode2p.classList.remove('active');
    updateMenuUI(currentLobbyState);
    vibrate(15);
  });

  // Start & Rematch
  btnStart.addEventListener('click', () => {
    const game = currentLobbyState?.selected_game || 'pong';
    send({ type: 'start_game', game: game, solo: selectedModeSolo });
    vibrate(30);
  });

  document.getElementById('btn-banner-rematch').addEventListener('click', () => {
    banner.style.display = 'none';
    send({ type: 'rematch' });
    vibrate(25);
  });

  // Menu Exit
  btnHeaderMenu.addEventListener('click', () => {
    send({ type: 'menu_exit' });
    showView('menu');
    vibrate(20);
  });
  document.getElementById('btn-banner-menu').addEventListener('click', () => {
    send({ type: 'menu_exit' });
    showView('menu');
    vibrate(20);
  });

  btnToggleQr.addEventListener('click', () => {
    send({ type: 'toggle_qr' });
    vibrate(15);
  });

  // Unified Touch & Input Event Handlers
  function bindControllerButton(btn) {
    const action = btn.dataset.action;
    if (!action) return;

    const onDown = (e) => {
      e.preventDefault();
      btn.classList.add('touch-active');
      send({ type: 'input', action: action, state: 'down' });
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

  document.querySelectorAll('[data-action]').forEach(bindControllerButton);

  // Keyboard controls for desktop browser pair programming / testing
  const KEY_MAP = {
    'ArrowUp': 'up',
    'KeyW': 'up',
    'ArrowDown': 'down',
    'KeyS': 'down',
    'ArrowLeft': 'left',
    'KeyA': 'left',
    'ArrowRight': 'right',
    'KeyD': 'right',
    'Space': 'drop',
    'KeyR': 'rotate',
  };

  const activeHeldKeys = new Set();
  window.addEventListener('keydown', (e) => {
    if (e.repeat) return;
    const action = KEY_MAP[e.code];

    // Menu navigation on keyboard
    if (menuView.style.display !== 'none') {
      if (e.code === 'ArrowLeft' || e.code === 'KeyA') {
        send({ type: 'menu_nav', dir: 'prev' });
        return;
      } else if (e.code === 'ArrowRight' || e.code === 'KeyD') {
        send({ type: 'menu_nav', dir: 'next' });
        return;
      } else if (e.code === 'Enter' || e.code === 'Space') {
        btnStart.click();
        return;
      }
    }

    if (e.code === 'Escape') {
      send({ type: 'menu_exit' });
      showView('menu');
      return;
    }

    if (action && !activeHeldKeys.has(action)) {
      activeHeldKeys.add(action);
      send({ type: 'input', action: action, state: 'down' });
    }
  });

  window.addEventListener('keyup', (e) => {
    const action = KEY_MAP[e.code];
    if (action) {
      activeHeldKeys.delete(action);
      send({ type: 'input', action: action, state: 'up' });
    }
  });

  connect();
})();
</script>
</body>
</html>
"""
