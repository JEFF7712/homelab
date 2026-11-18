# Pico W HUB75 Receiver

SeenGreat V3.9 HUB75 adapter (`GP2–GP7` RGB, `GP8–GP12` A–E, `GP13` CLK, `GP14` LAT, `GP15` OE) driving a 64x64 1/32-scan P3 RGB LED matrix.

Receiver firmware for the host service in `services/led-matrix/`. Two options are maintained here:

## 1. Dual-Core RP2040 C / PIO / DMA V2 Firmware (this directory, active)

- **200 MHz System Clock & 5s Hardware Watchdog:** Runs at Pico SDK's certified 200 MHz (`1.15 V` VREG) with automatic hardware watchdog recovery.
- **Core 1 (Isolated on `PIO1` + `DMA_IRQ_1`):**
  - Dual overlapped PIO state machines (`hub75_data` shifting 64 columns at 22.2 MHz while `hub75_row` illuminates the previously latched bitplane with exact 10 ns `OE` resolution).
  - Whole-frame dual-channel DMA (`5,632` pixel words + `352` control words per frame) while Core 1 sleeps in low-power `__wfe()`, achieving about 513 Hz refresh at the production brightness (178) with zero CPU spin and zero XIP cache jitter (`__not_in_flash_func` SRAM pinning + per-byte 64/32-bit gamma-bitplane LUTs).
  - **11-bit BCM:** an 11-bit gamma table (inputs 0..2 stay off, every other input lights at least the LSB) gives 49 distinct levels for inputs 0..63 where the former 8-bit scan had 12, so dark gradients and fades no longer band. The MSB is unchanged at 29.44 us; the extra depth is three short LSB planes.
  - **Hardware `OE` Brightness Scaling:** Brightness modulates `OE` active ticks directly in hardware rather than pre-scaling RGB values on the host, preserving the full gamma resolution while dimming. Below about 25% brightness the three shortest planes round to zero 10 ns ticks, so night mode keeps roughly the former 8-bit depth.
- **Core 0 (`PIO0` CYW43 Wi-Fi + lwIP + Dual TinyUSB CDC):**
  - Greets TCP `:9999` senders with `R890` (`TCP_NODELAY`, `CYW43_NONE_PM`). `R890` is `R889` framing plus a `Q` header (`payload_len` 0) answered on the same stream with `S` + 34 bytes little-endian: `uptime_ms`, `frames`, `incomplete`, `rx_bytes`, `tcp_rexmit`, `tcp_drop`, `tcp_memerr` (u32 each), `refresh_hz` (u16), `hw_bright` (u8), `rssi_dbm` (i8), `wifi_reconnects` (u16).
  - CYW43 gSPI runs at 33 MHz (`CYW43_PIO_CLOCK_DIV_INT=3`) instead of the SDK default divider's 50 MHz at 200 MHz sys_clk, and lwIP runs a 100 ms TCP timer (200 ms retransmission granularity).
  - **`R889` / `S889` Delta + RLE Wire Compression:** 4-byte frame header (`type: 'F'|'R'`, `hw_brightness: uint8`, `payload_len: uint16_le`) supporting 4-byte keepalive/brightness updates and lossless `SKIP`/`REPEAT`/`LITERAL` delta encoding (with automatic `'F'` fallback on incompressible noise). USB `CDC1` also accepts legacy `S888` + 12288 bytes and `S565` + 8192 bytes.

### Building & Hands-Free Flashing

Build a reproducible UF2 from the repository root (only the C sources enter the build):

```fish
nix build ./services/led-matrix/firmware#firmware -o services/led-matrix/firmware/result-uf2
```

`build-local.sh` builds incrementally into the ignored `build/` directory and can bake default Wi-Fi credentials from `WIFI_SSID`/`WIFI_PASSWORD`.

The Pico is USB-attached to the laptop. Flash without touching it:

```fish
printf 'BOOTSEL\r\n' > /dev/ttyACM0
udisksctl mount -b /dev/disk/by-label/RPI-RP2
cp services/led-matrix/firmware/result-uf2/pico_w_hub75_receiver.uf2 /run/media/$USER/RPI-RP2/
```

`picotool` needs udev permissions this laptop does not grant; the mass-storage copy does not. Opening `/dev/ttyACM0` at 1200 baud also enters BOOTSEL.

`/dev/ttyACM0` is the console: `STATUS`, `WIFI <ssid>\t<password>`, `CLEAR_WIFI`, `BOOTSEL`, and a `STATS` line every 5 s (`fps`, `incomplete`, `refresh_hz`, `kbps`, `hw_bright`, `rssi`, `tcp_rexmit`, `tcp_drop`, `wifi_reconnects`).

**Deploy order:** when the TCP handshake changes, ship the host first. The host accepts `R565`, `R888`, `R889`, and `R890`; an older host rejects a newer greeting and the panel falls back to its standby screen.

## 2. CircuitPython 10.3.1 Fallback (`circuitpython/`)

- Copy `circuitpython/code.py` and `circuitpython/boot.py` to `CIRCUITPY/`, alongside (dual USB CDC) and `settings.toml`.
- Uses 4-bit matrix color depth (`R565`, 8192-byte little-endian RGB565 frames); the host streamer automatically applies perceptual gamma and 4x4 Bayer ordered dithering when connected to an `R565` receiver.

## Validation records

`validation/` holds the 2026-10-16 CircuitPython-era benchmark receipts from `benchmark_stream.py`.
