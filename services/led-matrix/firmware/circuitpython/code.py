import gc
import os
import time

import board
import displayio
import framebufferio
import rgbmatrix
import socketpool
import supervisor
import terminalio
import usb_cdc
import wifi
from adafruit_display_text import label
from digitalio import DigitalInOut, Direction

supervisor.runtime.autoreload = False

print("=== Starting Pico W Matrix Receiver (V3.9 Staged Dual Stream) ===")


# --- Step 1: Pre-initialize driver ICs (ICN2038S/FM6124) with V3.9 pins ---
def init_panel_registers():
    try:
        r1 = DigitalInOut(board.GP2)
        r1.direction = Direction.OUTPUT
        g1 = DigitalInOut(board.GP3)
        g1.direction = Direction.OUTPUT
        b1 = DigitalInOut(board.GP4)
        b1.direction = Direction.OUTPUT
        r2 = DigitalInOut(board.GP5)
        r2.direction = Direction.OUTPUT
        g2 = DigitalInOut(board.GP6)
        g2.direction = Direction.OUTPUT
        b2 = DigitalInOut(board.GP7)
        b2.direction = Direction.OUTPUT
        clk = DigitalInOut(board.GP13)
        clk.direction = Direction.OUTPUT
        stb = DigitalInOut(board.GP14)
        stb.direction = Direction.OUTPUT
        oe = DigitalInOut(board.GP15)
        oe.direction = Direction.OUTPUT

        oe.value = True
        stb.value = False
        clk.value = False

        c12 = [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
        c13 = [0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0]

        for reg, threshold in [(c12, 12), (c13, 13)]:
            for column in range(64):
                y = column % 16
                val = bool(reg[y])
                r1.value = val
                g1.value = val
                b1.value = val
                r2.value = val
                g2.value = val
                b2.value = val
                stb.value = column > (64 - threshold)
                clk.value = True
                clk.value = False
            stb.value = False
            clk.value = False

        for p in (r1, g1, b1, r2, g2, b2, clk, stb, oe):
            p.deinit()
    except (OSError, RuntimeError, ValueError) as e:
        print("Panel pre-init error:", e)


displayio.release_displays()
init_panel_registers()

# --- Step 2: Initialize RGB Matrix Hardware (SeenGreat V3.9 Pinout) ---
matrix = rgbmatrix.RGBMatrix(
    width=64,
    height=64,
    bit_depth=4,
    rgb_pins=[board.GP2, board.GP3, board.GP4, board.GP5, board.GP6, board.GP7],
    addr_pins=[board.GP8, board.GP9, board.GP10, board.GP11, board.GP12],
    clock_pin=board.GP13,
    latch_pin=board.GP14,
    output_enable_pin=board.GP15,
    tile=1,
    serpentine=True,
    doublebuffer=True,
)

display = framebufferio.FramebufferDisplay(matrix, auto_refresh=False)

# --- Step 3: Setup UI Groups ---
status_group = displayio.Group()

lbl_title = label.Label(terminalio.FONT, text="LED MATRIX", color=0x00E5FF, x=2, y=8)
lbl_status = label.Label(terminalio.FONT, text="STANDBY", color=0x00FFB4, x=11, y=24)
lbl_ip = label.Label(terminalio.FONT, text="CONNECTING...", color=0x94A3B8, x=2, y=40)
lbl_port = label.Label(terminalio.FONT, text="TCP :9999", color=0x64748B, x=5, y=54)

status_group.append(lbl_title)
status_group.append(lbl_status)
status_group.append(lbl_ip)
status_group.append(lbl_port)

display.root_group = status_group
display.refresh()

# --- Step 4: Setup Wi-Fi & Auto-Reconnect ---
ssid = os.getenv("CIRCUITPY_WIFI_SSID")
password = os.getenv("CIRCUITPY_WIFI_PASSWORD")
pool = None
listener = None
client = None
last_wifi_attempt = 0.0


def ensure_wifi_listener(now):
    global pool, listener, client, last_wifi_attempt
    if not ssid:
        return
    if wifi.radio.connected and listener is not None:
        return
    if now - last_wifi_attempt < 5.0:
        return
    last_wifi_attempt = now

    if client is not None:
        try:
            client.close()
        except OSError:
            pass
        client = None
    if listener is not None:
        try:
            listener.close()
        except OSError:
            pass
        listener = None

    try:
        wifi.radio.power_management = wifi.PowerManagement.NONE
        if not wifi.radio.connected:
            wifi.radio.connect(ssid, password)
        ip_str = str(wifi.radio.ipv4_address)
        lbl_ip.text = ip_str
        lbl_ip.x = max(1, (64 - len(ip_str) * 6) // 2)
        if pool is None:
            pool = socketpool.SocketPool(wifi.radio)
        listener = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
        listener.setsockopt(pool.SOL_SOCKET, pool.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", 9999))
        listener.listen(1)
        listener.setblocking(False)
        if not in_stream_mode:
            display.refresh()
        print("Wi-Fi connected. IP:", ip_str)
    except (OSError, RuntimeError, ValueError) as e:
        print("Wi-Fi init note:", e)
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
            listener = None


# --- Step 5: Staged Frame Buffer & Streaming Endpoints ---
raw_bytes = memoryview(matrix).cast("B")
staging = bytearray(8192)
staging_view = memoryview(staging)

cdc_console = usb_cdc.console
cdc_data = usb_cdc.data
if cdc_data:
    cdc_data.timeout = 0.1
    cdc_data.write_timeout = 0.5
usb_header = bytearray(4)
usb_header_view = memoryview(usb_header)


def read_usb_exact(buffer):
    offset = 0
    deadline = time.monotonic() + 0.5
    while offset < len(buffer) and time.monotonic() < deadline:
        count = cdc_data.readinto(buffer[offset:])
        if count:
            offset += count
        if not cdc_data.connected:
            return False
    return offset == len(buffer)


in_stream_mode = False
last_frame_time = 0.0
frames = 0
incomplete = 0
refresh_ns = 0
stats_time = time.monotonic()
ensure_wifi_listener(stats_time)
print("Ready: USB CDC data (S565 + 8192 bytes) or TCP :9999 (8192 bytes), RGB565 LE")

while True:
    got_frame = False
    tcp_frame = False
    usb_frame = False

    usb_active = bool(cdc_data and cdc_data.connected)
    if usb_active and client:
        try:
            client.close()
        except OSError:
            pass
        client = None

    if not usb_active:
        ensure_wifi_listener(time.monotonic())

    if listener and client is None and not usb_active:
        try:
            client, _ = listener.accept()
            client.settimeout(5.0)
            client.setsockopt(pool.IPPROTO_TCP, pool.TCP_NODELAY, 1)
            client.sendall(b"R565")
            if cdc_console is None or cdc_console.connected:
                print("TCP connected")
        except OSError:
            pass

    if client and not usb_active:
        count = 0
        try:
            while count < 8192:
                n = client.recv_into(staging_view[count:])
                if not n:
                    raise OSError("TCP closed")
                count += n
            got_frame = tcp_frame = True
        except OSError as e:
            if count == 0 and getattr(e, "errno", None) == 110:
                # Idle keepalive gap: socket remains healthy
                pass
            else:
                if cdc_console is None or cdc_console.connected:
                    print("TCP disconnected:", e)
                if count:
                    incomplete += 1
                try:
                    client.close()
                except OSError:
                    pass
                client = None

    if usb_active and cdc_data.in_waiting:
        try:
            if read_usb_exact(usb_header_view) and usb_header == b"S565":
                got_frame = usb_frame = read_usb_exact(staging_view)
            if not usb_frame:
                incomplete += 1
                cdc_data.reset_input_buffer()
        except OSError:
            incomplete += 1
            got_frame = False

    if got_frame:
        raw_bytes[:] = staging_view
        refresh_start = time.monotonic_ns()
        last_frame_time = time.monotonic()
        in_stream_mode = True
        matrix.refresh()
        refresh_ns += time.monotonic_ns() - refresh_start
        frames += 1
        try:
            if usb_frame:
                cdc_data.write(b"A")
            elif tcp_frame:
                client.sendall(b"A")
        except OSError:
            if client:
                try:
                    client.close()
                except OSError:
                    pass
                client = None

    now = time.monotonic()
    if in_stream_mode and (now - last_frame_time > 30):
        display.root_group = None
        display.root_group = status_group
        display.refresh()
        in_stream_mode = False
        gc.collect()

    if now - stats_time >= 5:
        if cdc_console is None or cdc_console.connected:
            fps_val = frames / (now - stats_time)
            ref_ms = refresh_ns / max(frames, 1) / 1000000
            print(
                f"STATS fps={fps_val:.2f} frames={frames} "
                f"incomplete={incomplete} refresh_ms={ref_ms:.2f}"
            )
        frames = incomplete = refresh_ns = 0
        stats_time = now
