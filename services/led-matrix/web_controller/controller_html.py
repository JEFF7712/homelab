"""Ultra-minimal retro gamepad mobile interface for LED Matrix Arcade."""

CONTROLLER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#1a1d24">
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
    width: 100%;
    height: 100%;
    height: 100dvh;
    overflow: hidden;
    background: #1a1d24;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    touch-action: none;
  }
  body {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);
    position: relative;
    background: radial-gradient(circle at 50% 50%, #20242c 0%, #16181e 100%);
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
    background: rgba(0, 255, 180, 0.2);
    transition: all 0.3s ease;
    pointer-events: none;
    z-index: 10;
  }
  .conn-dot.offline {
    background: #ff3344;
    box-shadow: 0 0 8px #ff3344;
    opacity: 0.9;
  }

  /* Gamepad Container */
  .gamepad-stage {
    width: 100%;
    height: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 max(28px, env(safe-area-inset-left)) 0 max(28px, env(safe-area-inset-right));
  }

  /* Portrait adjustment: split nicely across bottom half */
  @media (orientation: portrait) {
    .gamepad-stage {
      align-items: flex-end;
      padding-bottom: max(60px, env(safe-area-inset-bottom));
    }
  }

  /* ---------------- CLASSIC D-PAD ---------------- */
  .dpad-container {
    width: 176px;
    height: 176px;
    position: relative;
    touch-action: none;
  }
  .dpad-cross {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: 168px;
    height: 168px;
  }
  .dpad-wing {
    position: absolute;
    background: #252830;
    border: 2px solid #1a1c22;
    box-shadow: 0 8px 20px rgba(0,0,0,0.65), inset 0 1px 1px rgba(255,255,255,0.08);
    display: flex;
    justify-content: center;
    align-items: center;
    color: #4b5568;
    font-size: 15px;
    font-weight: 900;
    cursor: pointer;
    touch-action: none;
    transition: background 0.06s, transform 0.06s, box-shadow 0.06s, color 0.06s;
  }
  .dpad-wing.touch-active, .dpad-wing:active {
    background: #17191f;
    box-shadow: 0 2px 4px rgba(0,0,0,0.8), inset 0 2px 5px rgba(0,0,0,0.9);
    color: #8da2c0;
    transform: scale(0.96);
  }

  .wing-up {
    top: 0;
    left: 55px;
    width: 58px;
    height: 58px;
    border-radius: 9px 9px 0 0;
    border-bottom: none;
  }
  .wing-down {
    bottom: 0;
    left: 55px;
    width: 58px;
    height: 58px;
    border-radius: 0 0 9px 9px;
    border-top: none;
  }
  .wing-left {
    top: 55px;
    left: 0;
    width: 58px;
    height: 58px;
    border-radius: 9px 0 0 9px;
    border-right: none;
  }
  .wing-right {
    top: 55px;
    right: 0;
    width: 58px;
    height: 58px;
    border-radius: 0 9px 9px 0;
    border-left: none;
  }
  .dpad-center {
    position: absolute;
    top: 55px;
    left: 55px;
    width: 58px;
    height: 58px;
    background: #1a1c22;
    border-radius: 50%;
    box-shadow: inset 0 3px 6px rgba(0,0,0,0.85), 0 1px 1px rgba(255,255,255,0.04);
    pointer-events: none;
  }

  /* ---------------- ACTION BUTTONS A & B ---------------- */
  .action-container {
    width: 176px;
    height: 176px;
    position: relative;
    touch-action: none;
  }
  .btn-round {
    width: 78px;
    height: 78px;
    border-radius: 50%;
    position: absolute;
    background: radial-gradient(circle at 36% 30%, #a82433 0%, #7e1723 58%, #520b13 100%);
    border: 3.5px solid #36060c;
    box-shadow: 0 8px 18px rgba(0,0,0,0.7), inset 0 2px 3px rgba(255,255,255,0.25), inset 0 -3px 4px rgba(0,0,0,0.5);
    color: #ffffff;
    font-size: 28px;
    font-weight: 800;
    letter-spacing: -0.5px;
    display: flex;
    justify-content: center;
    align-items: center;
    cursor: pointer;
    touch-action: none;
    text-shadow: 0 1px 2px rgba(0,0,0,0.4);
    transform: rotate(-12deg);
    transition: transform 0.06s, box-shadow 0.06s, background 0.06s;
  }
  .btn-round.btn-b {
    bottom: 8px;
    left: 8px;
  }
  .btn-round.btn-a {
    top: 8px;
    right: 8px;
  }
  .btn-round.touch-active, .btn-round:active {
    background: radial-gradient(circle at 36% 30%, #7d1723 0%, #540b13 60%, #36060c 100%);
    box-shadow: 0 2px 5px rgba(0,0,0,0.8), inset 0 3px 6px rgba(0,0,0,0.9);
    transform: rotate(-12deg) scale(0.93);
  }
</style>
</head>
<body>

<div class="conn-dot" id="conn-dot" title="Connection Status"></div>

<div class="gamepad-stage">
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

<script>
(function() {
  let ws = null;
  let playerRole = 1;
  let currentPhase = 'lobby';
  let selectedGame = 'pong';
  let soloMode = false;
  let bHoldTimer = null;

  const connDot = document.getElementById('conn-dot');

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
      vibrate(msg.winner === playerRole ? [40, 50, 40, 50, 60] : 80);
    }
  }

  // Bind Buttons with immediate zero-latency touch and tactile haptics
  function bindTouchButton(btn, action) {
    const onDown = (e) => {
      e.preventDefault();
      btn.classList.add('touch-active');
      vibrate(12);

      // Handle Carousel Menu Navigation on D-Pad Left / Right
      if (currentPhase === 'lobby') {
        if (action === 'left') {
          send({ type: 'menu_nav', dir: 'prev' });
          return;
        } else if (action === 'right') {
          send({ type: 'menu_nav', dir: 'next' });
          return;
        } else if (action === 'action_a') {
          send({ type: 'start_game', game: selectedGame, solo: soloMode });
          return;
        }
      }

      // Handle Gameover Actions: A = Rematch, B = Menu Exit
      if (currentPhase === 'gameover') {
        if (action === 'action_a') {
          send({ type: 'rematch' });
          return;
        } else if (action === 'action_b') {
          send({ type: 'menu_exit' });
          return;
        }
      }

      // Hold B for 1.2s to exit active game back to matrix carousel menu
      if (action === 'action_b' && currentPhase === 'playing') {
        clearTimeout(bHoldTimer);
        bHoldTimer = setTimeout(() => {
          vibrate([30, 40, 60]);
          send({ type: 'menu_exit' });
        }, 1200);
      }

      // Send standard gameplay action
      send({ type: 'input', action: action, state: 'down' });
    };

    const onUp = (e) => {
      e.preventDefault();
      btn.classList.remove('touch-active');
      if (action === 'action_b') {
        clearTimeout(bHoldTimer);
      }
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

  // Desktop keyboard controls for testing / debugging
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
    const act = KEY_MAP[e.code];
    if (act && !heldKeys.has(act)) {
      heldKeys.add(act);
      const targetBtn = document.querySelector(`[data-action="${act}"]`);
      if (targetBtn) {
        const evt = new MouseEvent('mousedown', { bubbles: true, cancelable: true });
        targetBtn.dispatchEvent(evt);
      }
    }
  });

  window.addEventListener('keyup', (e) => {
    const act = KEY_MAP[e.code];
    if (act) {
      heldKeys.delete(act);
      const targetBtn = document.querySelector(`[data-action="${act}"]`);
      if (targetBtn) {
        const evt = new MouseEvent('mouseup', { bubbles: true, cancelable: true });
        targetBtn.dispatchEvent(evt);
      }
    }
  });

  connect();
})();
</script>
</body>
</html>
"""
