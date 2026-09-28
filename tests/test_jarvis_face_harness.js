/* Behavioral harness for the Jarvis face entity subscription.
 *
 * Usage: node test_jarvis_face_harness.js <app.js> <snapshot.json>
 *
 * Drives the real app.js against a live-captured subscribe_entities
 * snapshot plus change events in the exact HA 2026.9.1 wire shape
 * (websocket_api/messages._state_diff_event). Exits 0 when every
 * assertion holds, 1 with a failure on stderr otherwise.
 */
'use strict';

const fs = require('fs');
const assert = require('assert');

const [appPath, snapshotPath] = process.argv.slice(2);

function makeEl() {
  return {
    classList: { add() {}, remove() {} },
    style: { setProperty() {}, removeProperty() {} },
    textContent: '',
    dataset: {},
    addEventListener() {},
    contains() { return false; },
  };
}

const appEl = makeEl();
const statusEl = makeEl();
const badgeEl = makeEl();
Object.defineProperty(badgeEl.classList, 'visible', { value: 'visible' });

const sentMessages = [];
let wsInstance = null;
class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    wsInstance = this;
  }
  send(data) {
    sentMessages.push(JSON.parse(data));
  }
  close() {
    this.readyState = 3;
  }
}
FakeWebSocket.OPEN = 1;
FakeWebSocket.CLOSED = 3;

global.window = global;
global.addEventListener = () => {};
global.document = {
  getElementById: (id) => {
    if (id === 'app') return appEl;
    if (id === 'status-text') return statusEl;
    if (id === 'connection-badge') return badgeEl;
    return null;
  },
  addEventListener() {},
  fullscreenElement: null,
};
global.location = { host: 'face-test', protocol: 'http:', search: '?v=1' };
global.localStorage = {
  getItem: (key) => (key === 'jarvis_token' ? 'test-token' : null),
  setItem() {},
  removeItem() {},
};
global.fetch = () => Promise.reject(new Error('offline harness'));
global.WebSocket = FakeWebSocket;

eval(fs.readFileSync(appPath, 'utf8'));

const T = global.Jarvis._faceTest;
const cfg = global.Jarvis.config;
const SAT = cfg.satelliteEntity;
const MUTE = cfg.muteEntity;
const MEDIA = cfg.mediaPlayerEntity;

function check(name, fn) {
  try {
    fn();
    console.log('ok - ' + name);
  } catch (err) {
    console.error('FAIL - ' + name + ': ' + err.message);
    process.exitCode = 1;
  }
}

// Subscription is restricted to the three displayed entities.
check('subscribes to exactly the displayed entities', () => {
  assert.ok(wsInstance, 'websocket was opened');
  wsInstance.onopen();
  wsInstance.onmessage({ data: JSON.stringify({ type: 'auth_required' }) });
  const auth = sentMessages.find((m) => m.type === 'auth');
  assert.ok(auth, 'auth message was sent');
  wsInstance.onmessage({ data: JSON.stringify({ type: 'auth_ok' }) });
  const sub = sentMessages.find((m) => m.type === 'subscribe_entities');
  assert.ok(sub, 'subscribe_entities was sent');
  assert.deepStrictEqual(
    [...sub.entity_ids].sort(),
    [SAT, MUTE, MEDIA].sort(),
  );
  assert.ok(
    !sentMessages.some((m) => m.type === 'get_states' || m.type === 'subscribe_events'),
    'no global state fetch or event firehose',
  );
});

// Live-captured snapshot leaves the face idle and retains only tracked state.
const snapshot = JSON.parse(fs.readFileSync(snapshotPath, 'utf8'));
check('real snapshot renders idle with bounded retention', () => {
  T.handleEntityEvent(snapshot);
  assert.strictEqual(global.Jarvis.getState(), 'idle');
  assert.deepStrictEqual(
    Object.keys(global.Jarvis.getLastStates()).sort(),
    [SAT, MUTE, MEDIA].sort(),
  );
});

// The pre-fix shape (bare diff.s) must not drive the face.
check('bare diff without +/- is ignored', () => {
  T.handleEntityEvent({ c: { [SAT]: { s: 'listening' } } });
  assert.strictEqual(global.Jarvis.getState(), 'idle');
});

// Real change shape drives the satellite states.
for (const [satState, faceState] of [
  ['listening', 'listening'],
  ['processing', 'processing'],
  ['responding', 'responding'],
]) {
  check(`real diff moves face to ${faceState}`, () => {
    T.handleEntityEvent({ c: { [SAT]: { '+': { s: satState } } } });
    assert.strictEqual(global.Jarvis.getState(), faceState);
  });
}

check('satellite idle plus playing media shows music with track label', () => {
  T.handleEntityEvent({ c: { [SAT]: { '+': { s: 'idle' } } } });
  T.handleEntityEvent({
    c: {
      [MEDIA]: {
        '+': {
          s: 'playing',
          a: {
            media_title: 'Out of Time',
            media_artist: 'The Weeknd',
            entity_picture: '/api/media_player_proxy/x?token=abc&cache=1',
          },
        },
      },
    },
  });
  assert.strictEqual(global.Jarvis.getState(), 'music');
  assert.ok(
    statusEl.textContent.includes('OUT OF TIME - THE WEEKND'),
    'track label shown, got: ' + statusEl.textContent,
  );
  assert.ok(
    T.musicArt().includes('/api/media_player_proxy/x?token=abc&cache=1'),
    'album art picked up, got: ' + T.musicArt(),
  );
});

check('mute switch takes priority and release returns to idle', () => {
  T.handleEntityEvent({ c: { [MUTE]: { '+': { s: 'on' } } } });
  assert.strictEqual(global.Jarvis.getState(), 'muted');
  T.handleEntityEvent({
    c: {
      [MUTE]: { '+': { s: 'off' } },
      [MEDIA]: { '+': { s: 'idle' } },
    },
  });
  assert.strictEqual(global.Jarvis.getState(), 'idle');
});

check('partial attribute adds merge and removals delete', () => {
  T.handleEntityEvent({ c: { [MEDIA]: { '+': { a: { volume_level: 0.5 } } } } });
  assert.strictEqual(
    global.Jarvis.getLastStates()[MEDIA].attributes.volume_level,
    0.5,
  );
  T.handleEntityEvent({ c: { [MEDIA]: { '-': { a: ['volume_level'] } } } });
  assert.ok(
    !('volume_level' in global.Jarvis.getLastStates()[MEDIA].attributes),
    'removed attribute is gone',
  );
});

check('untracked entities are ignored and never retained', () => {
  T.handleEntityEvent({
    c: { 'light.kitchen_lights': { '+': { s: 'on' } } },
    a: { 'light.kitchen_lights': { s: 'on', a: {} } },
  });
  assert.ok(
    !('light.kitchen_lights' in global.Jarvis.getLastStates()),
    'unrelated entity retained',
  );
  assert.strictEqual(global.Jarvis.getState(), 'idle');
});

if (process.exitCode) {
  console.error('face harness failed');
}
process.exit(process.exitCode || 0);
