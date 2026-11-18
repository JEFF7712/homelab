cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD pico_w CACHE STRING "Board type")
include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(pico_w_hub75_receiver C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

set(WIFI_SSID "" CACHE STRING "Optional default Wi-Fi SSID")
set(WIFI_PASSWORD "" CACHE STRING "Optional default Wi-Fi password")

add_executable(pico_w_hub75_receiver
    main.c
    usb_descriptors.c
)

pico_generate_pio_header(pico_w_hub75_receiver ${CMAKE_CURRENT_LIST_DIR}/hub75.pio)

target_include_directories(pico_w_hub75_receiver PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}
)

target_compile_definitions(pico_w_hub75_receiver PRIVATE
    DEFAULT_WIFI_SSID="${WIFI_SSID}"
    DEFAULT_WIFI_PASSWORD="${WIFI_PASSWORD}"
    # 200 MHz / 3 keeps the CYW43 gSPI bus at 33 MHz instead of its 50 MHz limit
    CYW43_PIO_CLOCK_DIV_INT=3
)

target_compile_options(pico_w_hub75_receiver PRIVATE
    -Wall
    -Wextra
    -O3
)

target_link_libraries(pico_w_hub75_receiver
    pico_stdlib
    pico_multicore
    pico_unique_id
    pico_cyw43_arch_lwip_poll
    hardware_pio
    hardware_dma
    hardware_flash
    hardware_sync
    hardware_vreg
    hardware_watchdog
    tinyusb_device
    tinyusb_board
    m
)

pico_enable_stdio_usb(pico_w_hub75_receiver 0)
pico_enable_stdio_uart(pico_w_hub75_receiver 0)

pico_add_extra_outputs(pico_w_hub75_receiver)
