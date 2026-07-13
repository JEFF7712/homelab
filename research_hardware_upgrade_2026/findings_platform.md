# Platform research: dual NVIDIA GPU + SAS HBA NixOS server

Research snapshot: 2026-07-12, US market. Prices are current examples or realistic bands, not quotes. GPU, disks, chassis, PSU, and storage cabling are excluded unless stated.

## Bottom line

For two GPUs plus one SAS HBA, AM5 is the highest-value platform if both GPUs can operate at CPU-connected PCIe 5.0 x8 and the HBA can use a chipset-connected PCIe 4.0 x4 slot. PCIe 5.0 x8 is not a meaningful bottleneck for present NVIDIA cards. The hard problem is physical fit: two 3-to-4-slot air-cooled GPUs can cover the third slot used by the HBA. A large chassis, risers, blower/water-cooled cards, or careful board/card selection is therefore mandatory.

Threadripper/TRX50 is the clean new-build answer when conventional full-width slots, more memory bandwidth, and fewer lane-sharing compromises are worth roughly $1,500-$2,000 more platform cost. Used single-socket EPYC 7002/7003 is the value answer for many lanes, ECC RAM, IPMI, and server duties, but has markedly worse idle efficiency and single-thread performance. Current Intel workstation platforms offer no compelling value here.

## Option 1: mainstream AMD AM5, recommended value tier

Representative platform:

- Ryzen 9 9950X-class CPU: approximately $450-$600.
- ASUS ProArt X870E-Creator WiFi: approximately $450-$500; less expensive x8/x8 boards may exist, but every exact slot/lane table must be checked.
- 128 GB DDR5 UDIMM: approximately $300-$450; 192/256 GB is possible on selected boards/QVLs but becomes expensive and may require lower memory clocks.
- CPU cooling: $80-$150.
- Approximate CPU + board + 128 GB RAM + cooling: $1,300-$1,700.

The ProArt manual specifies two CPU-connected PCIe 5.0 x16 physical slots operating x16 or x8/x8, plus a chipset-connected physical x16 slot operating at PCIe 4.0 x4. It also includes onboard 10 GbE and 2.5 GbE, avoiding another expansion card. This maps almost perfectly to GPU + GPU + HBA electrically. Source: [ASUS ProArt X870E-Creator manual](https://dlcdnets.asus.com/pub/ASUS/mb/SocketAM5/ProArt_X870E-CREATOR_WIFI/E23930_ProArt_X870E-CREATOR_WIFI_EM_WEB.pdf?model=ProArt+X870E-CREATOR+WIFI), [ASUS product page](https://www.asus.com/us/motherboards-components/motherboards/proart/proart-x870e-creator-wifi/).

Constraints:

- Only 16 CPU graphics lanes are split across the two GPUs. That is acceptable for ML training/inference, where model compute and VRAM dominate, but less ideal for unusually communication-heavy multi-GPU training.
- The HBA shares the chipset uplink with chipset NVMe, networking, and USB. PCIe 4.0 x4 is vastly more bandwidth than a small HDD array needs, so this is not a NAS problem.
- Consumer boards generally lack IPMI/BMC.
- Four-DIMM high-capacity DDR5 configurations can be harder to train and run slower than two DIMMs.
- Physical clearance is the decisive risk. Do not assume two RTX 3090/4090/5090-style air coolers and a bottom HBA fit merely because the electrical slots exist. Build around exact card thicknesses and slot coordinates.

Power/idle profile:

- Best of these choices for a 24/7 combined NAS and compute box. A tuned AM5 system without active GPU workloads can plausibly idle far below workstation/server platforms, though GPUs, HBAs, 10 GbE, disks, BIOS ASPM support, and PSU efficiency determine the actual wall figure.
- A Ryzen Eco Mode or explicit PPT limit is attractive: near-workstation CPU throughput without forcing high package power during sustained jobs.

## Option 2: Threadripper 9000/7000 on TRX50, best clean expansion tier

Representative platform:

- Threadripper 9960X, 24 cores: official launch price $1,499; a June 2026 price example was $1,369.
- TRX50 motherboard: roughly $575-$900. A current example listed the Gigabyte TRX50 AERO D at $577.99.
- 128 GB quad-channel ECC RDIMM: roughly $700-$1,000; a 64 GB kit example was $549.99.
- Cooler: approximately $140-$200.
- Approximate CPU + board + 128 GB RAM + cooling: $2,800-$3,600.

AMD describes TRX50 as supporting up to 80 total PCIe lanes and four-channel DDR5; Threadripper 9000 has a 350 W TDP. This comfortably supports two GPUs at x16, an HBA, fast NVMe, and later high-speed networking without relying on a narrow chipset uplink. Sources: [AMD Threadripper platform](https://www.amd.com/en/products/processors/workstations/ryzen-threadripper.html), [AMD Threadripper platform brief](https://www.amd.com/content/dam/amd/en/documents/partner-hub/threadripper/ryzen-threadripper-7000-series-how-to-sell-competitive.pdf), [current component price example](https://pangoly.com/en/value-builds/threadripper).

Advantages:

- Much easier lane allocation and generally better workstation-board slot layouts.
- Quad-channel ECC RDIMM and much higher practical RAM ceiling.
- Strong CPU throughput for data preprocessing, compilation, VMs, and parallel workloads.
- A sensible base if a second high-end GPU is a firm near-term commitment.

Costs/tradeoffs:

- Roughly $1,500-$2,000 more than a strong AM5 platform before GPUs.
- The 350 W CPU and workstation board make 24/7 idle/low-load efficiency less attractive.
- CEB/E-ATX board dimensions and multiple large cards sharply narrow chassis choices.
- Even here, modern 3.5-to-4-slot GPUs can defeat nominal slot count. Physical layout still requires validation.

WRX90/Threadripper Pro offers 128 PCIe lanes and eight-channel memory, but it is unjustified for two GPUs plus an HBA. It only makes sense if planning four or more accelerators, enormous RAM, or specialized PCIe devices.

## Option 3: used single-socket EPYC 7002/7003, lane/RAM value tier

Representative platform:

- EPYC 7302P/7402P/7542-class used CPU + server motherboard + 128-256 GB ECC DDR4: roughly $800-$1,400 depending on board and memory. A June 2026 homelab sale completed at $1,100 for an EPYC 7542, ASRock Rack board, and 256 GB DDR4.
- Cooler: $50-$120.
- ASRock Rack ROMED8-2T is an ideal reference board: single SP3 socket, EPYC 7002/7003, seven PCIe 4.0 x16 slots, ten SATA ports, dual onboard 10 GbE, and IPMI/BMC. Sources: [ASRock Rack ROMED8-2T](https://www.asrockrack.com/general/productdetail.cn.asp?model=Romed8-2T), [ASRock Rack EPYC catalog](https://www.asrockrack.com/general/2022_AMDEPYC7000DM.pdf), [June 2026 used-market example](https://www.reddit.com/r/homelabsales/comments/1u1al9s/fs_usany_epyc_7542_32core_epycd82t_romed82t/).

Advantages:

- Exceptional PCIe lane count, cheap high-capacity ECC DDR4, IPMI, and server-grade I/O.
- Two GPUs plus HBA is electrically trivial, with room for NICs/NVMe.
- Strong fit for many VMs, storage services, and large-memory workloads.

Costs/tradeoffs:

- Older EPYC has weaker single-thread performance and usually significantly higher platform idle power than AM5. Eight populated memory channels, BMC, server NICs, and older board design all contribute.
- Server boards can have limited fan-control polish, long boot times, proprietary power/connectors, and workstation-case fit issues.
- Seven adjacent x16 slots do not mean seven large air-cooled GPUs. Risers or a purpose-built 4U/6U GPU chassis may be needed.
- Buy a `P` single-socket CPU where applicable, or verify the exact non-P CPU and board support. Avoid engineering samples and vendor-locked CPUs.

This is attractive if cheap ECC capacity and expansion matter more than electricity/heat and interactive CPU speed. For a combined NAS that stays on continuously while GPUs are often idle, AM5 is usually the better overall ownership experience.

## Option 4: Intel Xeon workstation, generally skip

Xeon W-2400 provides 64 PCIe 5.0 lanes and four memory channels; W-3400 provides up to 112 lanes and eight channels on W790. Electrically these platforms work well. Sources: [Intel W-2400/W-3400 platform brief](https://cdrdv2-public.intel.com/762785/xeon-w3400-w2400-platformbrief_1.1.pdf), [ASRock W790 WS specification](https://download.asrock.com/Download/e-catalog/W790%20WS.pdf).

However, used June 2026 examples put an ASUS W790 SAGE SE board around $800 and a w7-3455 around $1,650, with a complete board/CPU/64 GB/cooler bundle at $3,250. That is Threadripper money without a persuasive advantage for this workload. The new W890/Xeon 600 platform also targets expensive workstations. Unless an unusually cheap, validated used bundle appears, AMD is the stronger choice. Used-market source: [June 2026 W790 listing](https://www.reddit.com/r/hardwareswap/comments/1u8m6mc/usanvh_4x_ddr5_rdimm_xeon_w73455_asus_pro_ws_w790/).

## Recommendation for this homelab

Start with AM5 unless the second GPU is expected within roughly a year and both intended GPUs are thick air-cooled cards that cannot coexist with the HBA on the selected board. An ASUS ProArt X870E-Creator-class layout, Ryzen 9 9950X-class CPU, and 128 GB RAM is enough CPU/platform for a 24 GB initial GPU, NAS services, Jellyfin, Immich, and later two-GPU inference. It also preserves 10 GbE onboard.

Move to TRX50 if any of these are firm requirements:

- two GPUs at full x16 plus an HBA without risers or lane-sharing compromises;
- more than 192/256 GB RAM with predictable ECC RDIMM operation;
- sustained CPU-heavy preprocessing or many VMs;
- additional PCIe NIC/NVMe cards beyond the two GPUs and HBA.

Choose used EPYC only if low acquisition cost, IPMI, ECC RAM capacity, and expansion outweigh 24/7 electricity, heat, noise, and weaker interactive performance.

In all cases, size the chassis and PSU from the eventual two-GPU configuration. Two RTX 3090-class cards plus a high-end CPU and spinning disks point to a quality 1600 W PSU on a dedicated 120 V circuit with awareness of the circuit's total load, or power-limited GPUs with a carefully validated 1200-1500 W design. A combined NAS/GPU host also needs graceful shutdown via UPS and thermal/fan behavior that protects disks during GPU jobs.
