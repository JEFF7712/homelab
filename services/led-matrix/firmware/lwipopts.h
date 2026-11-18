#ifndef _LWIPOPTS_H
#define _LWIPOPTS_H

#define NO_SYS                      1
#define LWIP_SOCKET                 0
#define LWIP_NETCONN                0
#define MEM_LIBC_MALLOC             0

#define MEM_ALIGNMENT               4
#define MEM_SIZE                    32768
#define MEMP_NUM_TCP_SEG            64
#define MEMP_NUM_ARP_QUEUE          10
#define PBUF_POOL_SIZE              32

#define LWIP_ARP                    1
#define LWIP_ETHERNET               1
#define LWIP_ICMP                   1
#define LWIP_RAW                    1
#define LWIP_DHCP                   1
#define LWIP_IPV4                   1
#define LWIP_TCP                    1
#define LWIP_UDP                    1
#define LWIP_DNS                    0
#define LWIP_TCP_KEEPALIVE          1
#define LWIP_NETIF_TX_SINGLE_PBUF   1
#define DHCP_DOES_ARP_CHECK         0
#define LWIP_DHCP_DOES_ACD_CHECK    0

// High-throughput TCP window for 60 FPS 12 KiB RGB888 frames
#define TCP_MSS                     1460
#define TCP_WND                     (16 * TCP_MSS)
#define TCP_SND_BUF                 (8 * TCP_MSS)
#define TCP_SND_QUEUELEN            ((4 * (TCP_SND_BUF) + (TCP_MSS - 1)) / (TCP_MSS))

#define LWIP_CHKSUM_ALGORITHM       3

// 100 ms TCP timer -> 200 ms slow timer: halves RTO granularity on a lossy Wi-Fi link
#define TCP_TMR_INTERVAL            100

// Counters exported through the R890 stats reply
#define LWIP_STATS                  1
#define LWIP_STATS_DISPLAY          0
#define LINK_STATS                  1
#define TCP_STATS                   1
#define MIB2_STATS                  1

#endif
