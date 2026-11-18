#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/flash.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/pio.h"
#include "hardware/sync.h"
#include "hardware/vreg.h"
#include "hardware/watchdog.h"
#include "lwip/pbuf.h"
#include "lwip/stats.h"
#include "lwip/tcp.h"
#include "pico/bootrom.h"
#include "pico/cyw43_arch.h"
#include "pico/multicore.h"
#include "pico/stdlib.h"
#include "tusb.h"

#include "hub75.pio.h"

#ifndef DEFAULT_WIFI_SSID
#define DEFAULT_WIFI_SSID ""
#endif

#ifndef DEFAULT_WIFI_PASSWORD
#define DEFAULT_WIFI_PASSWORD ""
#endif

// --- SeenGreat V3.9 HUB75E Pinout ---
#define RGB_BASE_PIN   2   // GP2..GP7: R1, G1, B1, R2, G2, B2
#define ADDR_BASE_PIN  8   // GP8..GP12: A, B, C, D, E
#define CLK_PIN        13  // GP13
#define LAT_PIN        14  // GP14 (side-set bit 0 of hub75_row)
#define OE_PIN         15  // GP15 (side-set bit 1 of hub75_row, active-low)

#define PANEL_WIDTH    64
#define PANEL_HEIGHT   64
#define SCAN_ROWS      32
#define BIT_DEPTH      11
#define GAMMA_MAX      ((1u << BIT_DEPTH) - 1u)        // 2047
#define TCP_PORT       9999

#define PIXEL_COUNT    (PANEL_WIDTH * PANEL_HEIGHT)      // 4096
#define RGB888_BYTES   (PIXEL_COUNT * 3)                 // 12288
#define RGB565_BYTES   (PIXEL_COUNT * 2)                 // 8192
#define DATA_WORDS     ((SCAN_ROWS * BIT_DEPTH * PANEL_WIDTH) / 4) // 5632 u32 words
#define CTRL_WORDS     (SCAN_ROWS * BIT_DEPTH)                     // 352 u32 words

// MSB pulse width in 10 ns ticks at hw_brightness = 255: 2944 * 10ns = 29.44us,
// the same MSB as the former 8-bit scan, so the extra depth adds three short
// LSB planes (2.9, 5.75, 11.5 ticks) below the old 230 ns LSB.
#define MSB_TICKS      2944u

// Top 4 KiB flash sector for persistent Wi-Fi credentials
#define FLASH_CFG_OFFSET (PICO_FLASH_SIZE_BYTES - FLASH_SECTOR_SIZE)
#define FLASH_CFG_MAGIC  0x4D545857u  // "MTXW"

typedef struct {
    uint32_t magic;
    char ssid[64];
    char password[96];
    uint32_t crc;
} wifi_flash_cfg_t;

// --- Lock-Free Triple Planar Framebuffers + Double OE Control Tables ---
static uint8_t __attribute__((aligned(4))) plane_buf[3][SCAN_ROWS][BIT_DEPTH][PANEL_WIDTH];
static uint32_t __attribute__((aligned(4))) ctrl_buf[2][SCAN_ROWS][BIT_DEPTH];

static volatile uint8_t front_idx = 0;
static volatile uint8_t ready_idx = 1;
static uint8_t work_idx = 2;
static volatile bool has_ready_frame = false;

static volatile uint8_t front_ctrl_idx = 0;
static volatile uint8_t ready_ctrl_idx = 0;
static volatile bool has_ready_ctrl = false;
static uint8_t built_hw_brightness = 255;

static volatile uint32_t vsync_hz_counter = 0;
static spin_lock_t *buf_lock = NULL;

// Per input byte: bitplanes 0..7 and 8..10 of its 11-bit gamma value, one plane per byte
static uint64_t gamma_planes_lo[256];
static uint32_t gamma_planes_hi[256];

// Persistent 24-bit RGB888 canvas on Core 0 for R889/S889 delta+RLE decoding
static uint8_t canvas_rgb[RGB888_BYTES];
static bool canvas_valid = false;

// --- Minimal 5x7 Font for Standby Screen ---
typedef struct {
    char ch;
    uint8_t col[5];
} glyph5x7_t;

static const glyph5x7_t font5x7[] = {
    {' ', {0x00, 0x00, 0x00, 0x00, 0x00}},
    {'.', {0x00, 0x60, 0x60, 0x00, 0x00}},
    {':', {0x00, 0x36, 0x36, 0x00, 0x00}},
    {'-', {0x08, 0x08, 0x08, 0x08, 0x08}},
    {'/', {0x20, 0x10, 0x08, 0x04, 0x02}},
    {'0', {0x3E, 0x51, 0x49, 0x45, 0x3E}},
    {'1', {0x00, 0x42, 0x7F, 0x40, 0x00}},
    {'2', {0x42, 0x61, 0x51, 0x49, 0x46}},
    {'3', {0x21, 0x41, 0x45, 0x4B, 0x31}},
    {'4', {0x18, 0x14, 0x12, 0x7F, 0x10}},
    {'5', {0x27, 0x45, 0x45, 0x45, 0x39}},
    {'6', {0x3C, 0x4A, 0x49, 0x49, 0x30}},
    {'7', {0x01, 0x71, 0x09, 0x05, 0x03}},
    {'8', {0x36, 0x49, 0x49, 0x49, 0x36}},
    {'9', {0x06, 0x49, 0x49, 0x29, 0x1E}},
    {'A', {0x7E, 0x11, 0x11, 0x11, 0x7E}},
    {'B', {0x7F, 0x49, 0x49, 0x49, 0x36}},
    {'C', {0x3E, 0x41, 0x41, 0x41, 0x22}},
    {'D', {0x7F, 0x41, 0x41, 0x22, 0x1C}},
    {'E', {0x7F, 0x49, 0x49, 0x49, 0x41}},
    {'F', {0x7F, 0x09, 0x09, 0x09, 0x01}},
    {'G', {0x3E, 0x41, 0x49, 0x49, 0x7A}},
    {'H', {0x7F, 0x08, 0x08, 0x08, 0x7F}},
    {'I', {0x00, 0x41, 0x7F, 0x41, 0x00}},
    {'K', {0x7F, 0x08, 0x14, 0x22, 0x41}},
    {'L', {0x7F, 0x40, 0x40, 0x40, 0x40}},
    {'M', {0x7F, 0x02, 0x0C, 0x02, 0x7F}},
    {'N', {0x7F, 0x04, 0x08, 0x10, 0x7F}},
    {'O', {0x3E, 0x41, 0x41, 0x41, 0x3E}},
    {'P', {0x7F, 0x09, 0x09, 0x09, 0x06}},
    {'R', {0x7F, 0x09, 0x19, 0x29, 0x46}},
    {'S', {0x46, 0x49, 0x49, 0x49, 0x31}},
    {'T', {0x01, 0x01, 0x7F, 0x01, 0x01}},
    {'U', {0x3F, 0x40, 0x40, 0x40, 0x3F}},
    {'V', {0x1F, 0x20, 0x40, 0x20, 0x1F}},
    {'W', {0x3F, 0x40, 0x38, 0x40, 0x3F}},
    {'X', {0x63, 0x14, 0x08, 0x14, 0x63}},
    {'Y', {0x07, 0x08, 0x70, 0x08, 0x07}},
};

static uint32_t simple_crc32(const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; ++i) {
        crc ^= p[i];
        for (int b = 0; b < 8; ++b) {
            crc = (crc >> 1) ^ (0xEDB88320u & (-(int32_t)(crc & 1u)));
        }
    }
    return ~crc;
}

static uint64_t spread_bits(uint32_t v, uint32_t bits) {
    uint64_t s = 0;
    for (uint32_t b = 0; b < bits; ++b) {
        if ((v >> b) & 1u) {
            s |= (1ULL << (b * 8u));
        }
    }
    return s;
}

static void init_luts(void) {
    // Inputs 0..2 stay off to swallow host quantization noise; every other input
    // lights at least the LSB. Hardware brightness scales OE pulse width, so all
    // 2048 levels survive dimming until the shortest planes fall below one tick.
    for (int i = 0; i < 256; ++i) {
        uint32_t v = 0;
        if (i >= 3) {
            double norm = (double)(i - 2) / (255.0 - 2.0);
            long g = lround(pow(norm, 2.2) * (double)GAMMA_MAX);
            v = g < 1 ? 1u : (g > (long)GAMMA_MAX ? GAMMA_MAX : (uint32_t)g);
        }
        gamma_planes_lo[i] = spread_bits(v & 0xFFu, 8);
        gamma_planes_hi[i] = (uint32_t)spread_bits(v >> 8, BIT_DEPTH - 8);
    }
}

static void build_ctrl_table(uint8_t idx, uint8_t hw_bright) {
    for (uint32_t bit = 0; bit < BIT_DEPTH; ++bit) {
        // Rounded MSB_TICKS * 2^(bit - 10) * hw_bright / 255
        uint32_t shift = BIT_DEPTH - 1u - bit;
        uint32_t scaled = MSB_TICKS * (uint32_t)hw_bright;
        uint32_t denom = 255u << shift;
        uint32_t ticks = (scaled + denom / 2u) / denom;
        // Planes at or above the former 8-bit scan's third bit stay visible when
        // dimmed; the three sub-230 ns planes may legitimately round to zero.
        if (hw_bright > 0 && ticks == 0u && bit >= 5u) {
            ticks = 1u;
        }
        for (uint32_t row = 0; row < SCAN_ROWS; ++row) {
            ctrl_buf[idx][row][bit] = (ticks << 5) | (row & 0x1Fu);
        }
    }
}

static void update_hardware_brightness(uint8_t hw_bright) {
    if (hw_bright == built_hw_brightness) {
        return;
    }
    uint8_t next_idx = (uint8_t)(front_ctrl_idx ^ 1u);
    build_ctrl_table(next_idx, hw_bright);
    built_hw_brightness = hw_bright;

    uint32_t save = spin_lock_blocking(buf_lock);
    ready_ctrl_idx = next_idx;
    has_ready_ctrl = true;
    spin_unlock(buf_lock, save);
}

// --- Pre-initialize ICN2038S / FM6124 Driver Registers (SeenGreat V3.9) ---
static void init_panel_registers(void) {
    const uint pins[] = {2, 3, 4, 5, 6, 7, CLK_PIN, LAT_PIN, OE_PIN};
    for (size_t i = 0; i < sizeof(pins) / sizeof(pins[0]); ++i) {
        gpio_init(pins[i]);
        gpio_set_dir(pins[i], GPIO_OUT);
    }
    gpio_put(OE_PIN, 1);
    gpio_put(LAT_PIN, 0);
    gpio_put(CLK_PIN, 0);

    static const uint8_t c12[16] = {0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1};
    static const uint8_t c13[16] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0};

    const uint8_t *regs[2] = {c12, c13};
    const int thresholds[2] = {12, 13};

    for (int r = 0; r < 2; ++r) {
        for (int col = 0; col < 64; ++col) {
            bool val = regs[r][col % 16] != 0;
            uint32_t rgb_mask = val ? (0x3Fu << RGB_BASE_PIN) : 0u;
            gpio_put_masked(0x3Fu << RGB_BASE_PIN, rgb_mask);
            gpio_put(LAT_PIN, col > (64 - thresholds[r]));
            busy_wait_at_least_cycles(24);
            gpio_put(CLK_PIN, 1);
            busy_wait_at_least_cycles(24);
            gpio_put(CLK_PIN, 0);
            busy_wait_at_least_cycles(24);
        }
        gpio_put(LAT_PIN, 0);
        gpio_put(CLK_PIN, 0);
    }
}

// --- SRAM-Pinned 64-Bit LUT Bitplane Transpose ---
static void __not_in_flash_func(publish_rgb888_frame)(const uint8_t *rgb, uint8_t hw_bright) {
    uint8_t (*dst)[BIT_DEPTH][PANEL_WIDTH] = plane_buf[work_idx];

    for (int row = 0; row < SCAN_ROWS; ++row) {
        const uint8_t *top = rgb + (row * PANEL_WIDTH * 3);
        const uint8_t *bot = rgb + ((row + SCAN_ROWS) * PANEL_WIDTH * 3);

        for (int col = 0; col < PANEL_WIDTH; ++col) {
            uint64_t lo =
                (gamma_planes_lo[top[0]] << 0) |
                (gamma_planes_lo[top[1]] << 1) |
                (gamma_planes_lo[top[2]] << 2) |
                (gamma_planes_lo[bot[0]] << 3) |
                (gamma_planes_lo[bot[1]] << 4) |
                (gamma_planes_lo[bot[2]] << 5);
            uint32_t hi =
                (gamma_planes_hi[top[0]] << 0) |
                (gamma_planes_hi[top[1]] << 1) |
                (gamma_planes_hi[top[2]] << 2) |
                (gamma_planes_hi[bot[0]] << 3) |
                (gamma_planes_hi[bot[1]] << 4) |
                (gamma_planes_hi[bot[2]] << 5);
            top += 3;
            bot += 3;

            dst[row][0][col] = (uint8_t)(lo >> 0);
            dst[row][1][col] = (uint8_t)(lo >> 8);
            dst[row][2][col] = (uint8_t)(lo >> 16);
            dst[row][3][col] = (uint8_t)(lo >> 24);
            dst[row][4][col] = (uint8_t)(lo >> 32);
            dst[row][5][col] = (uint8_t)(lo >> 40);
            dst[row][6][col] = (uint8_t)(lo >> 48);
            dst[row][7][col] = (uint8_t)(lo >> 56);
            dst[row][8][col] = (uint8_t)(hi >> 0);
            dst[row][9][col] = (uint8_t)(hi >> 8);
            dst[row][10][col] = (uint8_t)(hi >> 16);
        }
    }

    if (hw_bright != built_hw_brightness) {
        uint8_t next_ctrl = (uint8_t)(front_ctrl_idx ^ 1u);
        build_ctrl_table(next_ctrl, hw_bright);
        built_hw_brightness = hw_bright;

        uint32_t save = spin_lock_blocking(buf_lock);
        uint8_t prev_ready = ready_idx;
        ready_idx = work_idx;
        work_idx = prev_ready;
        has_ready_frame = true;
        ready_ctrl_idx = next_ctrl;
        has_ready_ctrl = true;
        spin_unlock(buf_lock, save);
    } else {
        uint32_t save = spin_lock_blocking(buf_lock);
        uint8_t prev_ready = ready_idx;
        ready_idx = work_idx;
        work_idx = prev_ready;
        has_ready_frame = true;
        spin_unlock(buf_lock, save);
    }
}

static bool __not_in_flash_func(process_r889_frame)(
    uint8_t frame_type,
    uint8_t hw_bright,
    const uint8_t *payload,
    uint16_t payload_len
) {
    if (frame_type == 'F') {
        if (payload_len != RGB888_BYTES) {
            return false;
        }
        memcpy(canvas_rgb, payload, RGB888_BYTES);
        canvas_valid = true;
        publish_rgb888_frame(canvas_rgb, hw_bright);
        return true;
    }

    if (frame_type == 'R') {
        if (!canvas_valid) {
            return false;
        }
        if (payload_len == 0) {
            // 4-byte keepalive or pure hardware brightness update!
            update_hardware_brightness(hw_bright);
            return true;
        }

        uint32_t px = 0;
        uint32_t pos = 0;
        while (pos < payload_len && px < PIXEL_COUNT) {
            uint8_t op = payload[pos++];
            if ((op & 0x80u) == 0) {
                uint32_t count = (uint32_t)op + 1u;
                if (px + count > PIXEL_COUNT) {
                    return false;
                }
                px += count;
            } else if ((op & 0x40u) != 0) {
                uint32_t count = (uint32_t)(op & 0x3Fu) + 1u;
                if (pos + 3u > payload_len || px + count > PIXEL_COUNT) {
                    return false;
                }
                uint8_t r = payload[pos];
                uint8_t g = payload[pos + 1];
                uint8_t b = payload[pos + 2];
                pos += 3u;
                uint8_t *dst = canvas_rgb + px * 3u;
                for (uint32_t i = 0; i < count; ++i) {
                    dst[0] = r;
                    dst[1] = g;
                    dst[2] = b;
                    dst += 3u;
                }
                px += count;
            } else {
                uint32_t count = (uint32_t)(op & 0x3Fu) + 1u;
                uint32_t bytes = count * 3u;
                if (pos + bytes > payload_len || px + count > PIXEL_COUNT) {
                    return false;
                }
                memcpy(canvas_rgb + px * 3u, payload + pos, bytes);
                pos += bytes;
                px += count;
            }
        }
        if (pos != payload_len || px != PIXEL_COUNT) {
            return false;
        }
        publish_rgb888_frame(canvas_rgb, hw_bright);
        return true;
    }

    return false;
}

static void publish_rgb565_frame(const uint8_t *rgb565) {
    uint8_t *out = canvas_rgb;
    for (int i = 0; i < PIXEL_COUNT; ++i) {
        uint16_t px = (uint16_t)rgb565[2 * i] | ((uint16_t)rgb565[2 * i + 1] << 8);
        uint8_t r5 = (uint8_t)((px >> 11) & 0x1Fu);
        uint8_t g6 = (uint8_t)((px >> 5) & 0x3Fu);
        uint8_t b5 = (uint8_t)(px & 0x1Fu);
        *out++ = (uint8_t)((r5 << 3) | (r5 >> 2));
        *out++ = (uint8_t)((g6 << 2) | (g6 >> 4));
        *out++ = (uint8_t)((b5 << 3) | (b5 >> 2));
    }
    canvas_valid = true;
    publish_rgb888_frame(canvas_rgb, 255);
}

// --- Standby Screen Renderer (reuses canvas_rgb when idle to save 12 KiB SRAM) ---
static void draw_char(int x, int y, char c, uint8_t r, uint8_t g, uint8_t b) {
    if (c >= 'a' && c <= 'z') {
        c = (char)(c - 'a' + 'A');
    }
    const uint8_t *cols = font5x7[0].col;
    for (size_t i = 0; i < sizeof(font5x7) / sizeof(font5x7[0]); ++i) {
        if (font5x7[i].ch == c) {
            cols = font5x7[i].col;
            break;
        }
    }
    for (int cx = 0; cx < 5; ++cx) {
        uint8_t bits = cols[cx];
        for (int cy = 0; cy < 7; ++cy) {
            if (bits & (1u << cy)) {
                int px = x + cx;
                int py = y + cy;
                if (px >= 0 && px < PANEL_WIDTH && py >= 0 && py < PANEL_HEIGHT) {
                    size_t idx = (size_t)((py * PANEL_WIDTH + px) * 3);
                    canvas_rgb[idx + 0] = r;
                    canvas_rgb[idx + 1] = g;
                    canvas_rgb[idx + 2] = b;
                }
            }
        }
    }
}

static void draw_text_centered(int y, const char *text, uint8_t r, uint8_t g, uint8_t b) {
    int len = (int)strlen(text);
    int width = len > 0 ? (len * 6 - 1) : 0;
    int x = (PANEL_WIDTH - width) / 2;
    if (x < 1) {
        x = 1;
    }
    for (int i = 0; i < len; ++i) {
        draw_char(x + i * 6, y, text[i], r, g, b);
    }
}

static void render_standby_screen(const char *ip_text) {
    canvas_valid = false;
    memset(canvas_rgb, 0, sizeof(canvas_rgb));
    draw_text_centered(6, "LED MATRIX", 0, 229, 255);
    draw_text_centered(19, "STANDBY", 0, 255, 180);
    draw_text_centered(34, ip_text, 148, 163, 184);
    draw_text_centered(49, "R890 :9999", 100, 116, 139);
    publish_rgb888_frame(canvas_rgb, 179);
}

// --- Core 1 Zero-Spin Dual-SM PIO + Whole-Frame DMA Engine (Isolated on PIO1) ---
static int g_dma_data = -1;
static volatile bool g_dma_frame_done = false;

static void __not_in_flash_func(dma_irq1_handler)(void) {
    if (g_dma_data >= 0 && dma_channel_get_irq1_status(g_dma_data)) {
        dma_channel_acknowledge_irq1(g_dma_data);
        g_dma_frame_done = true;
    }
}

static void __not_in_flash_func(core1_scanout_entry)(void) {
    multicore_lockout_victim_init();

    // Dedicated PIO1 block so CYW43 Wi-Fi owns PIO0 with zero resource sharing
    PIO pio = pio1;
    uint sm_data = 0;
    uint sm_row = 1;
    pio_sm_claim(pio, sm_data);
    pio_sm_claim(pio, sm_row);

    pio->irq = (1u << 4) | (1u << 5);

    uint off_data = pio_add_program(pio, &hub75_data_program);
    uint off_row = pio_add_program(pio, &hub75_row_program);

    hub75_row_program_init(pio, sm_row, off_row, ADDR_BASE_PIN, LAT_PIN);
    hub75_data_program_init(pio, sm_data, off_data, RGB_BASE_PIN, CLK_PIN);

    int dma_data = dma_claim_unused_channel(true);
    int dma_ctrl = dma_claim_unused_channel(true);
    g_dma_data = dma_data;

    dma_channel_config cfg_data = dma_channel_get_default_config(dma_data);
    channel_config_set_transfer_data_size(&cfg_data, DMA_SIZE_32);
    channel_config_set_read_increment(&cfg_data, true);
    channel_config_set_write_increment(&cfg_data, false);
    channel_config_set_dreq(&cfg_data, pio_get_dreq(pio, sm_data, true));
    dma_channel_configure(dma_data, &cfg_data, &pio->txf[sm_data], NULL, DATA_WORDS, false);

    dma_channel_config cfg_ctrl = dma_channel_get_default_config(dma_ctrl);
    channel_config_set_transfer_data_size(&cfg_ctrl, DMA_SIZE_32);
    channel_config_set_read_increment(&cfg_ctrl, true);
    channel_config_set_write_increment(&cfg_ctrl, false);
    channel_config_set_dreq(&cfg_ctrl, pio_get_dreq(pio, sm_row, true));
    dma_channel_configure(dma_ctrl, &cfg_ctrl, &pio->txf[sm_row], NULL, CTRL_WORDS, false);

    dma_channel_set_irq1_enabled(dma_data, true);
    irq_set_exclusive_handler(DMA_IRQ_1, dma_irq1_handler);
    irq_set_enabled(DMA_IRQ_1, true);

    pio_enable_sm_mask_in_sync(pio, (1u << sm_data) | (1u << sm_row));

    while (true) {
        uint8_t active_fb = front_idx;
        uint8_t active_ct = front_ctrl_idx;

        g_dma_frame_done = false;
        dma_channel_set_read_addr(dma_ctrl, ctrl_buf[active_ct], true);
        dma_channel_set_read_addr(dma_data, plane_buf[active_fb], true);

        // Sleep Core 1 in low-power WFE while DMA + dual PIO SMs scan all 256 planes
        while (!g_dma_frame_done) {
            __wfe();
        }
        dma_channel_wait_for_finish_blocking(dma_ctrl);

        uint32_t save = spin_lock_blocking(buf_lock);
        if (has_ready_frame) {
            uint8_t prev_front = front_idx;
            front_idx = ready_idx;
            ready_idx = prev_front;
            has_ready_frame = false;
        }
        if (has_ready_ctrl) {
            front_ctrl_idx = ready_ctrl_idx;
            has_ready_ctrl = false;
        }
        spin_unlock(buf_lock, save);

        vsync_hz_counter++;
    }
}

// --- Core 0 TCP (:9999 R890) + Dual USB CDC Receiver & Provisioning ---
// R890 = R889 framing plus a 'Q' header (payload_len 0) that requests an
// 'S' + R890_STATS_BYTES little-endian telemetry reply on the same stream.
static struct tcp_pcb *server_pcb = NULL;
static struct tcp_pcb *active_client = NULL;

typedef enum {
    TCP_RX_HDR = 0,
    TCP_RX_PAYLOAD,
} tcp_rx_state_t;

static tcp_rx_state_t tcp_state = TCP_RX_HDR;
static uint8_t tcp_hdr[4];
static uint32_t tcp_hdr_off = 0;
static uint16_t tcp_payload_target = 0;
static uint32_t tcp_payload_off = 0;
static uint8_t tcp_rx_buf[RGB888_BYTES];
static uint64_t tcp_rx_started_us = 0;

static bool in_stream_mode = false;
static uint64_t last_frame_us = 0;
static uint64_t last_usb_frame_us = 0;
static uint32_t stat_frames = 0;
static uint32_t stat_incomplete = 0;
static uint32_t stat_rx_bytes = 0;

// Lifetime totals (wrapping u32) and the latest 5 s samples for R890 stats replies
#define R890_STATS_BYTES 34u
static uint32_t total_frames = 0;
static uint32_t total_incomplete = 0;
static uint32_t total_rx_bytes = 0;
static uint16_t last_refresh_hz = 0;
static int8_t last_rssi_dbm = 0;
static uint16_t wifi_reconnects = 0;

static void put_u32le(uint8_t *dst, uint32_t v) {
    dst[0] = (uint8_t)v;
    dst[1] = (uint8_t)(v >> 8);
    dst[2] = (uint8_t)(v >> 16);
    dst[3] = (uint8_t)(v >> 24);
}

static void put_u16le(uint8_t *dst, uint16_t v) {
    dst[0] = (uint8_t)v;
    dst[1] = (uint8_t)(v >> 8);
}

static void send_r890_stats(struct tcp_pcb *tpcb) {
    uint8_t reply[1 + R890_STATS_BYTES];
    uint8_t *p = reply + 1;
    reply[0] = 'S';
    put_u32le(p + 0, (uint32_t)(time_us_64() / 1000ULL));
    put_u32le(p + 4, total_frames + stat_frames);
    put_u32le(p + 8, total_incomplete + stat_incomplete);
    put_u32le(p + 12, total_rx_bytes + stat_rx_bytes);
    put_u32le(p + 16, (uint32_t)lwip_stats.mib2.tcpretranssegs);
    put_u32le(p + 20, (uint32_t)lwip_stats.tcp.drop);
    put_u32le(p + 24, (uint32_t)lwip_stats.tcp.memerr);
    put_u16le(p + 28, last_refresh_hz);
    p[30] = built_hw_brightness;
    p[31] = (uint8_t)last_rssi_dbm;
    put_u16le(p + 32, wifi_reconnects);
    tcp_write(tpcb, reply, sizeof(reply), TCP_WRITE_FLAG_COPY);
}

static inline bool usb_stream_active(void) {
    uint64_t now = time_us_64();
    return last_usb_frame_us != 0 && now > last_usb_frame_us &&
           (now - last_usb_frame_us) < 3000000ULL;
}

void tud_cdc_line_coding_cb(uint8_t itf, cdc_line_coding_t const *p_line_coding) {
    if (itf == 0 && p_line_coding->bit_rate == 1200) {
        reset_usb_boot(0, 0);
    }
}

static void console_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void console_printf(const char *fmt, ...) {
    if (!tud_cdc_n_connected(0)) {
        return;
    }
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) {
        uint32_t len = (uint32_t)n < sizeof(buf) ? (uint32_t)n : (sizeof(buf) - 1);
        tud_cdc_n_write(0, buf, len);
        tud_cdc_n_write_flush(0);
    }
}

static void reset_tcp_rx_parser(void) {
    tcp_state = TCP_RX_HDR;
    tcp_hdr_off = 0;
    tcp_payload_target = 0;
    tcp_payload_off = 0;
}

static void close_tcp_client(void) {
    if (active_client != NULL) {
        tcp_arg(active_client, NULL);
        tcp_recv(active_client, NULL);
        tcp_err(active_client, NULL);
        tcp_poll(active_client, NULL, 0);
        if (tcp_close(active_client) != ERR_OK) {
            tcp_abort(active_client);
        }
        active_client = NULL;
    }
    if (tcp_hdr_off > 0 || tcp_payload_off > 0) {
        stat_incomplete++;
    }
    reset_tcp_rx_parser();
}

static void tcp_client_err_cb(void *arg, err_t err) {
    (void)arg;
    (void)err;
    if (tcp_hdr_off > 0 || tcp_payload_off > 0) {
        stat_incomplete++;
    }
    reset_tcp_rx_parser();
    active_client = NULL;
}

static err_t tcp_client_poll_cb(void *arg, struct tcp_pcb *tpcb) {
    (void)arg;
    uint64_t poll_now = time_us_64();
    if ((tcp_hdr_off > 0 || tcp_state == TCP_RX_PAYLOAD) &&
        poll_now > tcp_rx_started_us &&
        (poll_now - tcp_rx_started_us) > 4000000ULL) {
        close_tcp_client();
        return ERR_ABRT;
    }
    (void)tpcb;
    return ERR_OK;
}

static err_t tcp_client_recv_cb(void *arg, struct tcp_pcb *tpcb, struct pbuf *p, err_t err) {
    (void)arg;
    if (p == NULL || err != ERR_OK) {
        if (p != NULL) {
            pbuf_free(p);
        }
        close_tcp_client();
        return ERR_OK;
    }

    uint16_t pbuf_off = 0;
    uint16_t remaining = p->tot_len;
    stat_rx_bytes += remaining;
    uint8_t acks = 0;
    bool stats_requested = false;
    bool protocol_err = false;

    while (remaining > 0) {
        if (tcp_state == TCP_RX_HDR) {
            if (tcp_hdr_off == 0) {
                tcp_rx_started_us = time_us_64();
            }
            uint16_t need = (uint16_t)(4u - tcp_hdr_off);
            uint16_t chunk = remaining < need ? remaining : need;
            pbuf_copy_partial(p, tcp_hdr + tcp_hdr_off, chunk, pbuf_off);
            tcp_hdr_off += chunk;
            pbuf_off = (uint16_t)(pbuf_off + chunk);
            remaining = (uint16_t)(remaining - chunk);

            if (tcp_hdr_off == 4u) {
                uint8_t ftype = tcp_hdr[0];
                uint16_t plen = (uint16_t)tcp_hdr[2] | ((uint16_t)tcp_hdr[3] << 8);
                if (ftype == 'Q' && plen == 0u) {
                    tcp_hdr_off = 0;
                    stats_requested = true;
                    continue;
                }
                if ((ftype != 'F' && ftype != 'R') || plen > RGB888_BYTES ||
                    (ftype == 'F' && plen != RGB888_BYTES)) {
                    protocol_err = true;
                    break;
                }
                tcp_hdr_off = 0;
                tcp_payload_target = plen;
                tcp_payload_off = 0;
                if (plen == 0u) {
                    if (!process_r889_frame(ftype, tcp_hdr[1], tcp_rx_buf, 0)) {
                        protocol_err = true;
                        break;
                    }
                    last_frame_us = time_us_64();
                    in_stream_mode = true;
                    stat_frames++;
                    acks++;
                } else {
                    tcp_state = TCP_RX_PAYLOAD;
                }
            }
        } else {
            uint32_t need = (uint32_t)tcp_payload_target - tcp_payload_off;
            uint16_t chunk = remaining < need ? remaining : (uint16_t)need;
            pbuf_copy_partial(p, tcp_rx_buf + tcp_payload_off, chunk, pbuf_off);
            tcp_payload_off += chunk;
            pbuf_off = (uint16_t)(pbuf_off + chunk);
            remaining = (uint16_t)(remaining - chunk);

            if (tcp_payload_off == tcp_payload_target) {
                tcp_state = TCP_RX_HDR;
                tcp_payload_off = 0;
                if (!process_r889_frame(tcp_hdr[0], tcp_hdr[1], tcp_rx_buf, tcp_payload_target)) {
                    protocol_err = true;
                    break;
                }
                last_frame_us = time_us_64();
                in_stream_mode = true;
                stat_frames++;
                acks++;
            }
        }
    }

    tcp_recved(tpcb, p->tot_len);
    pbuf_free(p);

    if (protocol_err) {
        stat_incomplete++;
        close_tcp_client();
        return ERR_OK;
    }

    if (acks > 0) {
        static const char ack_bytes[] = "AAAAAAAAAAAAAAAA";
        uint8_t max_acks = (uint8_t)(sizeof(ack_bytes) - 1);
        uint8_t to_send = acks <= max_acks ? acks : max_acks;
        tcp_write(tpcb, ack_bytes, to_send, TCP_WRITE_FLAG_COPY);
    }
    if (stats_requested) {
        send_r890_stats(tpcb);
    }
    if (acks > 0 || stats_requested) {
        tcp_output(tpcb);
    }
    return ERR_OK;
}

static err_t tcp_server_accept_cb(void *arg, struct tcp_pcb *newpcb, err_t err) {
    (void)arg;
    if (err != ERR_OK || newpcb == NULL) {
        return ERR_VAL;
    }
    if (usb_stream_active()) {
        tcp_abort(newpcb);
        return ERR_ABRT;
    }
    if (active_client != NULL) {
        close_tcp_client();
    }

    active_client = newpcb;
    reset_tcp_rx_parser();
    canvas_valid = false;
    tcp_nagle_disable(newpcb);
    newpcb->so_options |= SOF_KEEPALIVE;
    tcp_recv(newpcb, tcp_client_recv_cb);
    tcp_err(newpcb, tcp_client_err_cb);
    tcp_poll(newpcb, tcp_client_poll_cb, 4);

    if (tcp_write(newpcb, "R890", 4, TCP_WRITE_FLAG_COPY) == ERR_OK) {
        tcp_output(newpcb);
    }
    console_printf("TCP R890 client connected\r\n");
    return ERR_OK;
}

static void start_tcp_listener(void) {
    if (server_pcb != NULL) {
        return;
    }
    struct tcp_pcb *pcb = tcp_new_ip_type(IPADDR_TYPE_V4);
    if (pcb == NULL) {
        return;
    }
    ip_set_option(pcb, SOF_REUSEADDR);
    if (tcp_bind(pcb, IP_ANY_TYPE, TCP_PORT) != ERR_OK) {
        tcp_close(pcb);
        return;
    }
    server_pcb = tcp_listen_with_backlog(pcb, 1);
    if (server_pcb != NULL) {
        tcp_accept(server_pcb, tcp_server_accept_cb);
    }
}

// --- Wi-Fi Credential Storage & Auto-Reconnect ---
static char active_ssid[64];
static char active_password[96];

static void load_wifi_credentials(void) {
    const wifi_flash_cfg_t *stored =
        (const wifi_flash_cfg_t *)(XIP_BASE + FLASH_CFG_OFFSET);
    if (stored->magic == FLASH_CFG_MAGIC) {
        uint32_t expected = simple_crc32(stored, offsetof(wifi_flash_cfg_t, crc));
        if (stored->crc == expected && stored->ssid[0] != '\0') {
            memcpy(active_ssid, stored->ssid, sizeof(active_ssid));
            active_ssid[sizeof(active_ssid) - 1] = '\0';
            memcpy(active_password, stored->password, sizeof(active_password));
            active_password[sizeof(active_password) - 1] = '\0';
            return;
        }
    }
    snprintf(active_ssid, sizeof(active_ssid), "%s", DEFAULT_WIFI_SSID);
    snprintf(active_password, sizeof(active_password), "%s", DEFAULT_WIFI_PASSWORD);
}

static void save_wifi_credentials(const char *ssid, const char *password) {
    uint8_t page[FLASH_PAGE_SIZE];
    memset(page, 0xFF, sizeof(page));
    wifi_flash_cfg_t *cfg = (wifi_flash_cfg_t *)page;
    cfg->magic = FLASH_CFG_MAGIC;
    snprintf(cfg->ssid, sizeof(cfg->ssid), "%s", ssid);
    snprintf(cfg->password, sizeof(cfg->password), "%s", password);
    cfg->crc = simple_crc32(cfg, offsetof(wifi_flash_cfg_t, crc));

    watchdog_update();
    multicore_lockout_start_blocking();
    uint32_t ints = save_and_disable_interrupts();
    flash_range_erase(FLASH_CFG_OFFSET, FLASH_SECTOR_SIZE);
    if (ssid[0] != '\0') {
        flash_range_program(FLASH_CFG_OFFSET, page, FLASH_PAGE_SIZE);
    }
    restore_interrupts(ints);
    multicore_lockout_end_blocking();
    watchdog_update();

    snprintf(active_ssid, sizeof(active_ssid), "%s", ssid);
    snprintf(active_password, sizeof(active_password), "%s", password);
}

// --- USB CDC1 Raw Stream State Machine (S889, S888, or S565) ---
typedef enum {
    USB_RX_MAGIC = 0,
    USB_RX_R889_HDR,
    USB_RX_R889_PAYLOAD,
    USB_RX_PAYLOAD_888,
    USB_RX_PAYLOAD_565,
} usb_rx_state_t;

static usb_rx_state_t usb_state = USB_RX_MAGIC;
static uint8_t usb_hdr[4];
static uint32_t usb_hdr_len = 0;
static uint8_t usb_r889_meta[4];
static uint16_t usb_r889_target = 0;
static uint8_t usb_payload[RGB888_BYTES];
static uint32_t usb_payload_len = 0;
static uint64_t usb_rx_deadline_us = 0;

static void complete_usb_frame(void) {
    if (active_client != NULL) {
        cyw43_arch_lwip_begin();
        close_tcp_client();
        cyw43_arch_lwip_end();
    }
    uint64_t done_us = time_us_64();
    last_frame_us = done_us;
    last_usb_frame_us = done_us;
    in_stream_mode = true;
    stat_frames++;
    tud_cdc_n_write_char(1, 'A');
    tud_cdc_n_write_flush(1);
}

static void poll_usb_data_stream(void) {
    if (!tud_cdc_n_connected(1)) {
        usb_state = USB_RX_MAGIC;
        usb_hdr_len = 0;
        usb_payload_len = 0;
        return;
    }

    uint64_t now = time_us_64();
    if (usb_state != USB_RX_MAGIC && now > usb_rx_deadline_us) {
        stat_incomplete++;
        tud_cdc_n_read_flush(1);
        usb_state = USB_RX_MAGIC;
        usb_hdr_len = 0;
        usb_payload_len = 0;
    }

    while (tud_cdc_n_available(1) > 0) {
        if (usb_state == USB_RX_MAGIC) {
            uint32_t n = tud_cdc_n_read(1, usb_hdr + usb_hdr_len, 4 - usb_hdr_len);
            stat_rx_bytes += n;
            usb_hdr_len += n;
            if (usb_hdr_len == 4) {
                usb_hdr_len = 0;
                usb_payload_len = 0;
                usb_rx_deadline_us = time_us_64() + 500000ULL;
                if (memcmp(usb_hdr, "S889", 4) == 0) {
                    usb_state = USB_RX_R889_HDR;
                } else if (memcmp(usb_hdr, "S888", 4) == 0) {
                    usb_state = USB_RX_PAYLOAD_888;
                } else if (memcmp(usb_hdr, "S565", 4) == 0) {
                    usb_state = USB_RX_PAYLOAD_565;
                } else {
                    stat_incomplete++;
                    tud_cdc_n_read_flush(1);
                }
            }
        } else if (usb_state == USB_RX_R889_HDR) {
            uint32_t n = tud_cdc_n_read(1, usb_r889_meta + usb_hdr_len, 4 - usb_hdr_len);
            stat_rx_bytes += n;
            usb_hdr_len += n;
            if (usb_hdr_len == 4) {
                usb_hdr_len = 0;
                uint8_t ftype = usb_r889_meta[0];
                uint16_t plen = (uint16_t)usb_r889_meta[2] | ((uint16_t)usb_r889_meta[3] << 8);
                if ((ftype != 'F' && ftype != 'R') || plen > RGB888_BYTES ||
                    (ftype == 'F' && plen != RGB888_BYTES)) {
                    stat_incomplete++;
                    tud_cdc_n_read_flush(1);
                    usb_state = USB_RX_MAGIC;
                } else if (plen == 0) {
                    usb_state = USB_RX_MAGIC;
                    if (process_r889_frame(ftype, usb_r889_meta[1], usb_payload, 0)) {
                        complete_usb_frame();
                    } else {
                        stat_incomplete++;
                    }
                } else {
                    usb_r889_target = plen;
                    usb_payload_len = 0;
                    usb_state = USB_RX_R889_PAYLOAD;
                }
            }
        } else if (usb_state == USB_RX_R889_PAYLOAD) {
            uint32_t n = tud_cdc_n_read(
                1, usb_payload + usb_payload_len, usb_r889_target - usb_payload_len
            );
            stat_rx_bytes += n;
            usb_payload_len += n;
            if (usb_payload_len == usb_r889_target) {
                usb_state = USB_RX_MAGIC;
                usb_payload_len = 0;
                if (process_r889_frame(
                        usb_r889_meta[0], usb_r889_meta[1], usb_payload, usb_r889_target
                    )) {
                    complete_usb_frame();
                } else {
                    stat_incomplete++;
                }
            }
        } else {
            uint32_t target = (usb_state == USB_RX_PAYLOAD_888) ? RGB888_BYTES : RGB565_BYTES;
            uint32_t n = tud_cdc_n_read(1, usb_payload + usb_payload_len, target - usb_payload_len);
            stat_rx_bytes += n;
            usb_payload_len += n;
            if (usb_payload_len == target) {
                if (usb_state == USB_RX_PAYLOAD_888) {
                    memcpy(canvas_rgb, usb_payload, RGB888_BYTES);
                    canvas_valid = true;
                    publish_rgb888_frame(canvas_rgb, 255);
                } else {
                    publish_rgb565_frame(usb_payload);
                }
                usb_state = USB_RX_MAGIC;
                usb_payload_len = 0;
                complete_usb_frame();
            }
        }
    }
}

// --- USB CDC0 Console Command Parser ---
static char cmd_line[192];
static size_t cmd_len = 0;
static bool force_wifi_reconnect = false;

static void poll_usb_console(const char *current_ip) {
    while (tud_cdc_n_available(0) > 0) {
        int ch = tud_cdc_n_read_char(0);
        if (ch < 0) {
            break;
        }
        if (ch == '\r' || ch == '\n') {
            if (cmd_len == 0) {
                continue;
            }
            cmd_line[cmd_len] = '\0';
            cmd_len = 0;

            if (strcmp(cmd_line, "STATUS") == 0) {
                console_printf(
                    "STATUS ssid=%s ip=%s stream=%d tcp_client=%d hw_bright=%u sys_mhz=%lu\r\n",
                    active_ssid[0] ? active_ssid : "<unset>",
                    current_ip,
                    in_stream_mode ? 1 : 0,
                    active_client != NULL ? 1 : 0,
                    (unsigned)built_hw_brightness,
                    (unsigned long)(clock_get_hz(clk_sys) / 1000000u)
                );
            } else if (strcmp(cmd_line, "BOOTSEL") == 0) {
                console_printf("Rebooting into UF2 BOOTSEL...\r\n");
                sleep_ms(20);
                reset_usb_boot(0, 0);
            } else if (strcmp(cmd_line, "CLEAR_WIFI") == 0) {
                save_wifi_credentials("", "");
                console_printf("OK Wi-Fi credentials cleared\r\n");
            } else if (strncmp(cmd_line, "WIFI ", 5) == 0) {
                char *rest = cmd_line + 5;
                char *sep = strchr(rest, '\t');
                if (sep == NULL) {
                    sep = strchr(rest, ' ');
                }
                if (sep != NULL) {
                    *sep = '\0';
                    const char *pass = sep + 1;
                    save_wifi_credentials(rest, pass);
                    force_wifi_reconnect = true;
                    console_printf("OK saved Wi-Fi SSID '%s'; reconnecting\r\n", rest);
                } else {
                    console_printf("ERR usage: WIFI <ssid>\\t<password>\r\n");
                }
            } else {
                console_printf("COMMANDS: STATUS | BOOTSEL | WIFI <ssid> <pass> | CLEAR_WIFI\r\n");
            }
        } else if (cmd_len + 1 < sizeof(cmd_line)) {
            cmd_line[cmd_len++] = (char)ch;
        }
    }
}

int main(void) {
    // Certified 200 MHz RP2040 system clock at 1.15 V core voltage
    vreg_set_voltage(VREG_VOLTAGE_1_15);
    sleep_ms(2);
    set_sys_clock_khz(200000, true);

    init_luts();
    buf_lock = spin_lock_init(spin_lock_claim_unused(true));
    build_ctrl_table(0, 179);
    built_hw_brightness = 179;

    init_panel_registers();
    load_wifi_credentials();
    render_standby_screen(active_ssid[0] ? "CONNECTING..." : "USB READY");

    multicore_launch_core1(core1_scanout_entry);

    tusb_init();

    bool wifi_hw_ok = (cyw43_arch_init() == 0);
    if (wifi_hw_ok) {
        cyw43_arch_enable_sta_mode();
        cyw43_wifi_pm(&cyw43_state, CYW43_NONE_PM);
        cyw43_arch_lwip_begin();
        start_tcp_listener();
        cyw43_arch_lwip_end();
    }

    // 5-second hardware watchdog for unattended wall-mounted self-recovery
    watchdog_enable(5000, true);

    char ip_str[24] = "NO WIFI";
    bool wifi_up = false;
    uint64_t last_wifi_try_us = 0;
    uint64_t stats_start_us = time_us_64();

    while (true) {
        watchdog_update();
        tud_task();
        poll_usb_data_stream();
        poll_usb_console(ip_str);

        uint64_t now = time_us_64();

        if (wifi_hw_ok) {
            cyw43_arch_poll();

            int link = cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA);
            if (link == CYW43_LINK_UP && !force_wifi_reconnect) {
                const ip4_addr_t *ip = netif_ip4_addr(netif_default);
                if (ip != NULL && !ip4_addr_isany_val(*ip)) {
                    char next_ip[24];
                    snprintf(next_ip, sizeof(next_ip), "%s", ip4addr_ntoa(ip));
                    if (!wifi_up || strcmp(ip_str, next_ip) != 0) {
                        snprintf(ip_str, sizeof(ip_str), "%s", next_ip);
                        wifi_up = true;
                        if (!in_stream_mode) {
                            render_standby_screen(ip_str);
                        }
                        console_printf("Wi-Fi connected. IP: %s\r\n", ip_str);
                    }
                }
            } else {
                if (wifi_up) {
                    wifi_up = false;
                    wifi_reconnects++;
                    snprintf(ip_str, sizeof(ip_str), "RECONNECTING");
                    if (!in_stream_mode) {
                        render_standby_screen(ip_str);
                    }
                }
                if (active_ssid[0] != '\0' &&
                    (force_wifi_reconnect || last_wifi_try_us == 0 ||
                     (now > last_wifi_try_us && (now - last_wifi_try_us) >= 5000000ULL))) {
                    force_wifi_reconnect = false;
                    last_wifi_try_us = now;
                    cyw43_wifi_pm(&cyw43_state, CYW43_NONE_PM);
                    cyw43_arch_wifi_connect_async(
                        active_ssid,
                        active_password,
                        active_password[0] ? CYW43_AUTH_WPA2_AES_PSK : CYW43_AUTH_OPEN
                    );
                }
            }
        }

        now = time_us_64();
        if (in_stream_mode && now > last_frame_us && (now - last_frame_us) > 30000000ULL) {
            in_stream_mode = false;
            render_standby_screen(ip_str);
        }

        if (now > stats_start_us && (now - stats_start_us) >= 5000000ULL) {
            double dt = (double)(now - stats_start_us) / 1000000.0;
            uint32_t scans = vsync_hz_counter;
            vsync_hz_counter = 0;
            double kbps = ((double)stat_rx_bytes * 8.0 / 1000.0) / dt;
            int32_t rssi = 0;
            if (wifi_up && cyw43_wifi_get_rssi(&cyw43_state, &rssi) == 0) {
                last_rssi_dbm = (int8_t)(rssi < -128 ? -128 : (rssi > 0 ? 0 : rssi));
            }
            last_refresh_hz = (uint16_t)((double)scans / dt);
            console_printf(
                "STATS fps=%.2f frames=%lu incomplete=%lu refresh_hz=%.1f kbps=%.1f hw_bright=%u "
                "rssi=%d tcp_rexmit=%u tcp_drop=%u wifi_reconnects=%u\r\n",
                (double)stat_frames / dt,
                (unsigned long)stat_frames,
                (unsigned long)stat_incomplete,
                (double)scans / dt,
                kbps,
                (unsigned)built_hw_brightness,
                (int)last_rssi_dbm,
                (unsigned)lwip_stats.mib2.tcpretranssegs,
                (unsigned)lwip_stats.tcp.drop,
                (unsigned)wifi_reconnects
            );
            total_frames += stat_frames;
            total_incomplete += stat_incomplete;
            total_rx_bytes += stat_rx_bytes;
            stat_frames = 0;
            stat_incomplete = 0;
            stat_rx_bytes = 0;
            stats_start_us = now;
        }
    }
}
