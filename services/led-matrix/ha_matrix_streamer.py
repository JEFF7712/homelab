"""Render Spotify playback with low-latency Spotify API polling and HA fallback."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
from pathlib import Path

from ha_source import ArtworkCache, HomeAssistantSource, HybridPlaybackSource
from modes import (
    BlankRenderer,
    ClockWeatherRenderer,
    HomelabRenderer,
    LedFxReceiver,
    LedFxRenderer,
    LifeRenderer,
    MultiplayerArcadeRenderer,
    NightClockRenderer,
    PhysicsRenderer,
    PrometheusSource,
    RetroArcadeRenderer,
    SpotifyVinylRenderer,
    VisualizerRenderer,
)
from modes.base import RenderContext
from spotify_matrix_streamer import (
    SpotifyClient,
    TCPMatrixStreamer,
    write_private_file,
)
from web_controller import MultiplayerHub, start_background_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ha-url", default=os.getenv("HASS_URL", "http://10.0.40.13:8123")
    )
    parser.add_argument(
        "--ha-token-file",
        default=os.getenv("HASS_TOKEN_FILE"),
        type=Path,
        required=not bool(os.getenv("HASS_TOKEN_FILE")),
    )
    parser.add_argument(
        "--entity",
        default=os.getenv(
            "SPOTIFY_ENTITY", "media_player.spotify_rupan,media_player.bedroom_speaker"
        ),
    )
    parser.add_argument(
        "--power-entity",
        default=os.getenv("POWER_ENTITY", "input_boolean.bedroom_matrix_power"),
    )
    parser.add_argument(
        "--mode-entity",
        default=os.getenv("MODE_ENTITY", "input_select.bedroom_matrix_mode"),
    )
    parser.add_argument(
        "--brightness-entity",
        default=os.getenv(
            "BRIGHTNESS_ENTITY", "input_number.bedroom_matrix_brightness"
        ),
    )
    parser.add_argument(
        "--weather-entity",
        default=os.getenv("WEATHER_ENTITY", "weather.forecast_home"),
    )
    parser.add_argument(
        "--music-mode-entity",
        default=os.getenv("MUSIC_MODE_ENTITY", "input_boolean.bedroom_music_mode"),
    )
    parser.add_argument(
        "--ledfx-port",
        type=int,
        default=int(os.getenv("LEDFX_PORT", "4048")),
    )
    parser.add_argument("--spotify-client-id", default=os.getenv("SPOTIFY_CLIENT_ID"))
    parser.add_argument(
        "--spotify-client-secret", default=os.getenv("SPOTIFY_CLIENT_SECRET")
    )
    parser.add_argument(
        "--spotify-refresh-token", default=os.getenv("SPOTIFY_REFRESH_TOKEN")
    )
    parser.add_argument(
        "--spotify-poll-interval",
        type=float,
        default=float(os.getenv("SPOTIFY_POLL_INTERVAL", "3.0")),
    )
    parser.add_argument(
        "--spotify-poll-interval-paused",
        type=float,
        default=float(os.getenv("SPOTIFY_POLL_INTERVAL_PAUSED", "15.0")) or None,
    )
    parser.add_argument(
        "--spotify-poll-interval-idle",
        type=float,
        default=float(os.getenv("SPOTIFY_POLL_INTERVAL_IDLE", "30.0")),
    )
    parser.add_argument(
        "--spotify-token-cache",
        type=Path,
        default=Path(
            os.getenv(
                "SPOTIFY_TOKEN_CACHE",
                str(Path.home() / ".cache" / "spotify_matrix_token.json"),
            )
        ),
    )
    parser.add_argument("--pico-ip", default="10.0.20.136")
    parser.add_argument(
        "--prometheus-url",
        default=os.getenv(
            "PROMETHEUS_URL",
            "http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090",
        ),
    )
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument(
        "--web-port",
        type=int,
        default=int(os.getenv("WEB_PORT", "8000")),
        help="Port for the mobile web controller HTTP & WebSocket server",
    )
    parser.add_argument("--fps", type=float, default=20)

    parser.add_argument("--rotation-speed", type=float, default=45)
    parser.add_argument("--brightness", type=float, default=0.7)
    parser.add_argument("--stale-after", type=float, default=120)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--stats-file", type=Path)
    parser.add_argument("--health-file", type=Path)
    args = parser.parse_args()
    if (
        not 1 <= args.fps <= 60
        or not 0 <= args.brightness <= 1
        or args.stale_after <= 0
        or (args.duration is not None and args.duration <= 0)
        or args.spotify_poll_interval <= 0
        or (
            args.spotify_poll_interval_paused is not None
            and args.spotify_poll_interval_paused <= 0
        )
        or args.spotify_poll_interval_idle <= 0
    ):
        parser.error(
            "Invalid FPS, brightness, stale timeout, poll interval, or duration"
        )
    try:
        token = args.ha_token_file.read_text().strip()
        ha_source = HomeAssistantSource(
            base_url=args.ha_url,
            token=token,
            entity=args.entity,
            power_entity=args.power_entity,
            mode_entity=args.mode_entity,
            brightness_entity=args.brightness_entity,
            weather_entity=args.weather_entity,
            music_mode_entity=args.music_mode_entity,
            stale_after=args.stale_after,
        )
    except (OSError, ValueError) as error:
        parser.error(f"Cannot configure HA source ({type(error).__name__})")

    if args.spotify_client_id and args.spotify_client_secret:
        spotify_client = SpotifyClient(
            client_id=args.spotify_client_id,
            client_secret=args.spotify_client_secret,
            redirect_uri="http://127.0.0.1:8888/callback",
            token_cache=args.spotify_token_cache,
            refresh_token=args.spotify_refresh_token,
        )
        source: HomeAssistantSource | HybridPlaybackSource = HybridPlaybackSource(
            ha_source=ha_source,
            spotify_client=spotify_client,
            poll_interval_playing=args.spotify_poll_interval,
            poll_interval_paused=args.spotify_poll_interval_paused,
            poll_interval_idle=args.spotify_poll_interval_idle,
        )
        paused_info = (
            f"; paused: {args.spotify_poll_interval_paused:g}s"
            if args.spotify_poll_interval_paused
            else ""
        )
        print(
            f"Playback source: Hybrid (Spotify API primary at {args.spotify_poll_interval:g}s{paused_info} + HA fallback)",
            flush=True,
        )
    else:
        source = ha_source
        print(
            f"Playback source: Home Assistant events only ({args.entity})",
            flush=True,
        )

    multiplayer_hub = MultiplayerHub()
    ledfx_receiver = LedFxReceiver(port=args.ledfx_port)
    ledfx_receiver.start()
    visualizer = VisualizerRenderer(audio_source=ledfx_receiver.get_latest_audio)
    ledfx_mode = LedFxRenderer(receiver=ledfx_receiver, fallback_renderer=visualizer)
    prometheus = PrometheusSource(args.prometheus_url)

    renderers = {
        "Off": BlankRenderer(),
        "Spotify": SpotifyVinylRenderer(),
        "Clock & Weather": ClockWeatherRenderer(),
        "Night Clock": NightClockRenderer(),
        "Retro Arcade": RetroArcadeRenderer(),
        "Game of Life": LifeRenderer(),
        "2-Player Arcade": MultiplayerArcadeRenderer(multiplayer_hub),
        "Visualizer": visualizer,
        "LedFx": ledfx_mode,
        "Physics": PhysicsRenderer(),
        "Homelab Dashboards": HomelabRenderer(prometheus),
    }

    cache = ArtworkCache(source)
    streamer = TCPMatrixStreamer(args.pico_ip, args.port)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    start_background_server(
        multiplayer_hub, host="0.0.0.0", port=args.web_port, stop_event=stop
    )

    started = last_tick = time.monotonic()
    last_ack = last_health = 0.0
    max_frame_ms = 0.0
    last_title = ""
    last_playing: bool | None = None
    last_mode: str | None = None
    last_power: bool | None = None
    print(
        f"Target Pico: {args.pico_ip}:{args.port}; {args.fps:g} FPS",
        flush=True,
    )
    source.start()
    prometheus.start()
    try:
        while not stop.is_set():
            tick = time.monotonic()
            dt = tick - last_tick
            if args.duration and tick - started >= args.duration:
                break

            spotify_state = source.snapshot()
            ha_state = source.hub_state()

            art = cache.get(
                spotify_state.artwork_url if spotify_state.available else ""
            )

            # Determine active mode
            if not ha_state.power or ha_state.mode == "Off":
                active_mode = "Off"
            elif ha_state.mode == "Auto":
                if ha_state.music_mode:
                    active_mode = "LedFx"
                elif spotify_state.available and spotify_state.playing:
                    active_mode = "Spotify"
                else:
                    active_mode = "Clock & Weather"
            else:
                active_mode = ha_state.mode

            if ha_state.power != last_power:
                power_str = "ON" if ha_state.power else "OFF"
                print(f"[Hub] Power: {power_str}", flush=True)
                last_power = ha_state.power

            if active_mode != last_mode:
                print(f"[Hub] Mode: {active_mode} (HA: {ha_state.mode})", flush=True)
                last_mode = active_mode
                target_renderer = renderers.get(active_mode)
                if target_renderer and hasattr(target_renderer, "reset"):
                    try:
                        target_renderer.reset()
                    except Exception:
                        pass

            if active_mode == "Spotify":
                if spotify_state.title != last_title:
                    provider = getattr(source, "active_provider", "ha").upper()
                    print(
                        f"[{provider}] Track: {spotify_state.title} | {spotify_state.artist}",
                        flush=True,
                    )
                    last_title = spotify_state.title
                if spotify_state.playing != last_playing and spotify_state.available:
                    status_str = "Playing" if spotify_state.playing else "Paused"
                    provider = getattr(source, "active_provider", "ha").upper()
                    print(f"[{provider}] Playback: {status_str}", flush=True)
                    last_playing = spotify_state.playing

            renderer = renderers.get(active_mode, renderers["Clock & Weather"])
            brightness = (
                ha_state.brightness
                if ha_state.brightness is not None
                else args.brightness
            )
            ctx = RenderContext(
                tick=tick,
                dt=dt,
                ha_state=ha_state,
                spotify_state=spotify_state,
                spotify_art=art,
                brightness=brightness,
                rotation_speed=args.rotation_speed,
            )
            frame = renderer.render(ctx, size=64)

            effective_brightness = 0.0 if active_mode == "Off" else brightness
            if streamer.send_frame(frame, brightness=effective_brightness):
                last_ack = time.monotonic()
            max_frame_ms = max(max_frame_ms, (time.monotonic() - tick) * 1000)

            if args.health_file and tick - last_health >= 2:
                write_private_file(
                    args.health_file,
                    json.dumps(
                        {
                            "updated_at": time.time(),
                            "ready": source.connected and last_ack > tick - 10,
                            "mode": active_mode,
                            "power": ha_state.power,
                            "brightness": brightness,
                            "metadata_available": spotify_state.available,
                            "artwork_ready": art is not None,
                            "frames": streamer.frames,
                            "failures": streamer.failures,
                            "provider": getattr(
                                source, "active_provider", "home_assistant"
                            ),
                        }
                    ),
                )
                last_health = tick
            last_tick = tick
            stop.wait(max(0, 1 / args.fps - (time.monotonic() - tick)))
    finally:
        elapsed = time.monotonic() - started
        spotify_state = source.snapshot()
        ha_state = source.hub_state()
        source_connected = source.connected
        streamer.close()
        ledfx_receiver.stop()
        prometheus.close()
        source.close()
        cache.close()
        stats = {
            "source": getattr(source, "active_provider", "home_assistant"),
            "seconds": elapsed,
            "acknowledged_frames": streamer.frames,
            "failures": streamer.failures,
            "displayed_fps": streamer.frames / elapsed if elapsed > 0 else 0,
            "max_frame_ms": max_frame_ms,
            "entity_available": spotify_state.available,
            "connected": source_connected,
            "mode": last_mode,
            "power": ha_state.power,
            "brightness": ha_state.brightness,
        }
        if args.stats_file:
            write_private_file(args.stats_file, json.dumps(stats, indent=2) + "\n")
        print(json.dumps(stats), flush=True)


if __name__ == "__main__":
    main()
