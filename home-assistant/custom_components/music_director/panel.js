// Music Colours: review the colour and effect each playlist song gets in Music
// Mode, and pin or unpin them per album. Served by music_director (panel.py).

const SOURCES = {
  cover: "Cover",
  vibe: "Genre & mood",
  pinned: "Pinned",
  pending: "Not prepared",
};
const FILTERS = ["all", "cover", "vibe", "pinned", "pending"];
const STORAGE_KEY = "music-colours-playlist";

function effectLabel(effect) {
  return effect
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function remembered() {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch (_error) {
    return null;
  }
}

function remember(name) {
  try {
    window.localStorage.setItem(STORAGE_KEY, name);
  } catch (_error) {
    // Private windows and blocked storage: the choice is not kept.
  }
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style") node.setAttribute("style", value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

const STYLE = `
  :host {
    display: block;
    min-height: 100vh;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 56px;
    padding: 0 12px;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, #fff);
    font-size: 20px;
    position: sticky;
    top: 0;
    z-index: 2;
  }
  main {
    max-width: 960px;
    margin: 0 auto;
    padding: 16px;
    box-sizing: border-box;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 8px;
  }
  .controls select, input[type="search"] {
    flex: 1 1 220px;
    min-width: 0;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font-size: 15px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 8px 0 4px;
  }
  .chip {
    padding: 6px 12px;
    border-radius: 16px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font-size: 13px;
    cursor: pointer;
  }
  .chip.on {
    background: var(--primary-color);
    border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .note {
    color: var(--secondary-text-color);
    font-size: 13px;
    margin: 8px 0 12px;
  }
  .row {
    display: grid;
    grid-template-columns: 52px minmax(0, 1fr) auto;
    gap: 12px;
    align-items: center;
    padding: 10px;
    margin-bottom: 8px;
    border-radius: 12px;
    background: var(--card-background-color);
    box-shadow: var(--ha-card-box-shadow, none);
  }
  .art {
    width: 52px;
    height: 52px;
    border-radius: 6px;
    object-fit: cover;
    background: var(--divider-color);
  }
  .text { min-width: 0; }
  .title, .sub {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .title { font-weight: 500; }
  .sub { color: var(--secondary-text-color); font-size: 13px; }
  .strip {
    height: 10px;
    border-radius: 5px;
    margin-top: 6px;
    background: var(--divider-color);
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin-top: 6px;
    font-size: 12px;
    color: var(--secondary-text-color);
  }
  .dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    border: 1px solid rgba(127, 127, 127, 0.4);
  }
  .badge {
    padding: 1px 8px;
    border-radius: 10px;
    border: 1px solid var(--divider-color);
  }
  .badge.pinned { border-color: var(--primary-color); color: var(--primary-color); }
  .actions {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 6px;
  }
  .pick {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    cursor: pointer;
  }
  input[type="color"] {
    width: 36px;
    height: 32px;
    padding: 0;
    border: none;
    background: none;
    cursor: pointer;
  }
  select.effect {
    max-width: 150px;
    padding: 5px 6px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font-size: 13px;
  }
  select.effect.pinned { border-color: var(--primary-color); }
  button.unpin {
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: transparent;
    color: var(--primary-text-color);
    cursor: pointer;
  }
  .empty { padding: 32px 0; text-align: center; color: var(--secondary-text-color); }
  @media (max-width: 600px) {
    main { padding: 12px 16px; }
    .row { grid-template-columns: 44px minmax(0, 1fr); }
    .art { width: 44px; height: 44px; }
    .actions {
      grid-column: 1 / -1;
      flex-direction: row;
      justify-content: flex-end;
    }
  }
`;

class MusicColoursPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._playlists = [];
    this._effects = [];
    this._playlist = null;
    this._songs = [];
    this._filter = "all";
    this._query = "";
    this._status = "Loading playlists…";
    this._started = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (!this._started) {
      this._started = true;
      this._build();
      this._loadPlaylists();
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menu) this._menu.narrow = narrow;
  }

  _build() {
    const root = this.shadowRoot;
    root.append(el("style", { text: STYLE }));
    this._menu = document.createElement("ha-menu-button");
    this._menu.hass = this._hass;
    this._menu.narrow = this._narrow;
    root.append(el("div", { class: "toolbar" }, [this._menu, el("span", { text: "Music Colours" })]));
    this._select = el("select", {
      "aria-label": "Playlist",
      onchange: (event) => this._loadPlaylist(event.target.value),
    });
    this._search = el("input", {
      type: "search",
      placeholder: "Search songs, artists, albums",
      "aria-label": "Search",
      oninput: (event) => {
        this._query = event.target.value.trim().toLowerCase();
        this._renderSongs();
      },
    });
    this._chips = el("div", { class: "chips" });
    this._summary = el("div", { class: "note" });
    this._list = el("div");
    root.append(
      el("main", {}, [
        el("div", { class: "controls" }, [this._select, this._search]),
        this._chips,
        this._summary,
        this._list,
      ]),
    );
    this._renderSongs();
  }

  async _loadPlaylists() {
    try {
      const result = await this._hass.callWS({ type: "music_director/playlists" });
      this._playlists = result.playlists;
      this._effects = result.effects;
    } catch (error) {
      this._status = `Could not load playlists: ${error.message || error}`;
      this._renderSongs();
      return;
    }
    this._select.replaceChildren(
      ...this._playlists.map((p) => el("option", { value: p.name, text: `${p.name} (${p.songs})` })),
    );
    if (!this._playlists.length) {
      this._status = "No playlists prepared yet. Run music_director.prepare_playlists.";
      this._renderSongs();
      return;
    }
    const saved = remembered();
    const first = this._playlists.some((p) => p.name === saved) ? saved : this._playlists[0].name;
    this._select.value = first;
    this._loadPlaylist(first);
  }

  async _loadPlaylist(name) {
    this._playlist = name;
    remember(name);
    this._status = "Loading songs…";
    this._songs = [];
    this._renderSongs();
    try {
      this._songs = await this._hass.callWS({ type: "music_director/playlist", name });
      this._status = "";
    } catch (error) {
      this._status = `Could not load ${name}: ${error.message || error}`;
    }
    this._renderSongs();
  }

  // changes: { colour } and/or { effect }; null unpins that one.
  async _pin(song, changes) {
    try {
      await this._hass.callWS({
        type: "music_director/pin",
        title: song.title,
        artist: song.artist,
        album: song.album,
        ...changes,
      });
    } catch (error) {
      window.alert(`Could not change the pin: ${error.message || error}`);
      return;
    }
    // A pin covers the whole album, so other rows can change too.
    const scroll = window.scrollY;
    await this._loadPlaylist(this._playlist);
    window.scrollTo(0, scroll);
  }

  _counts() {
    const counts = { all: this._songs.length, cover: 0, vibe: 0, pinned: 0, pending: 0 };
    for (const song of this._songs) counts[song.source] = (counts[song.source] || 0) + 1;
    return counts;
  }

  _renderSongs() {
    if (!this._list) return;
    const counts = this._counts();
    this._chips.replaceChildren(
      ...FILTERS.filter((f) => f === "all" || counts[f]).map((f) =>
        el("button", {
          class: `chip${this._filter === f ? " on" : ""}`,
          text: `${f === "all" ? "All" : SOURCES[f]} ${counts[f] || 0}`,
          onclick: () => {
            this._filter = f;
            this._renderSongs();
          },
        }),
      ),
    );
    this._summary.textContent = this._songs.length
      ? "The bar is the LED strip; the dots are the room lights. Colour and effect pins " +
        "apply to the whole album; unpin or choose Auto to go back to automatic."
      : "";
    if (this._status) {
      this._list.replaceChildren(el("div", { class: "empty", text: this._status }));
      return;
    }
    const query = this._query;
    const shown = this._songs.filter(
      (song) =>
        (this._filter === "all" || song.source === this._filter) &&
        (!query || `${song.title} ${song.artist} ${song.album}`.toLowerCase().includes(query)),
    );
    if (!shown.length) {
      this._list.replaceChildren(el("div", { class: "empty", text: "No matching songs." }));
      return;
    }
    this._list.replaceChildren(...shown.map((song) => this._row(song)));
  }

  _row(song) {
    const art = song.art
      ? el("img", {
          class: "art",
          src: song.art,
          alt: "",
          loading: "lazy",
          referrerpolicy: "no-referrer",
        })
      : el("div", { class: "art" });
    const meta = el("div", { class: "meta" }, [
      el("span", { class: `badge ${song.source}`, text: SOURCES[song.source] || song.source }),
      song.effect_pin ? el("span", { class: "badge pinned", text: "Effect pinned" }) : null,
      ...(song.areas || []).map((hex) => el("span", { class: "dot", style: `background:${hex}`, title: hex })),
      song.genres && song.genres.length ? el("span", { text: song.genres.join(", ") }) : null,
      song.intensity !== undefined
        ? el("span", { text: `intensity ${Math.round(song.intensity * 100)}%` })
        : null,
    ]);
    const text = el("div", { class: "text" }, [
      el("div", { class: "title", text: song.title }),
      el("div", { class: "sub", text: `${song.artist} · ${song.album}` }),
      el("div", { class: "strip", style: song.gradient ? `background:${song.gradient}` : "" }),
      meta,
    ]);
    const picker = el("input", {
      type: "color",
      value: song.colour || "#ff0000",
      "aria-label": `Pin a colour for ${song.album || song.title}`,
      onchange: (event) => this._pin(song, { colour: event.target.value }),
    });
    const auto = song.auto_effect ? `Auto (${effectLabel(song.auto_effect)})` : "Auto";
    const effect = el(
      "select",
      {
        class: `effect${song.effect_pin ? " pinned" : ""}`,
        "aria-label": `Effect for ${song.album || song.title}`,
        onchange: (event) => this._pin(song, { effect: event.target.value || null }),
      },
      [
        el("option", { value: "", text: auto }),
        ...this._effects.map((name) => el("option", { value: name, text: effectLabel(name) })),
      ],
    );
    effect.value = song.effect_pin || "";
    const actions = el("div", { class: "actions" }, [
      el("label", { class: "pick" }, [el("span", { text: song.pin ? "Pinned" : "Pin" }), picker]),
      song.pin
        ? el("button", {
            class: "unpin",
            text: "Unpin colour",
            onclick: () => this._pin(song, { colour: null }),
          })
        : null,
      effect,
    ]);
    return el("div", { class: "row" }, [art, text, actions]);
  }
}

if (!customElements.get("music-colours-panel")) {
  customElements.define("music-colours-panel", MusicColoursPanel);
}
