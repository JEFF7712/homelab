# NAS side of a combined NixOS GPU/NAS box, July 2026 US

## Bottom line

A combined box is practical, but its physical design should be selected around three simultaneous expansion requirements: a first 3 to 4-slot GPU, a future second GPU, and a PCIe SAS HBA. A normal mainstream ATX board often has enough nominal slots but not enough usable spacing or CPU-connected lanes once two large GPUs are installed. A workstation platform and a large storage-oriented full tower are the safer long-term choices.

The existing Seagate Exos X16 `ST10000NM002G` is a 12 Gb/s SAS enterprise disk. It cannot attach to a motherboard SATA port. It needs a SAS HBA or SAS backplane. One disk is useful as an initial bulk-data target, but it provides neither redundancy nor a backup.

## HBA and cabling

- Best-value baseline: a genuine Broadcom/LSI SAS 9300-8i, or OEM equivalent based on the SAS3008 controller, flashed to IT/HBA mode. It exposes two internal SFF-8643 connectors, supports eight directly attached SAS/SATA disks, uses PCIe 3.0 x8, and is more than fast enough for HDDs. The official 9300-8i guide identifies its two internal connectors as SFF-8643 Mini-SAS HD. Source: [LSI SAS 9300-8i user guide](https://images10.newegg.com/UploadFilesForNewegg/itemintelligence/LSI/LSI_SAS_9300_8i_UG_v1.01411783006615.pdf).
- Expected used/refurbished cost: about **$40 to $80** for a tested 9300-8i in IT mode. A 9305-16i becomes worthwhile only when planning beyond eight drives, roughly **$120 to $180 used with cables**. As a current market check, a July 2026 9305-16i package with four cables was listed for $140: [HardwareSwap listing](https://www.reddit.com/r/hardwareswap/comments/1uhfpqx/usany_h_lsi_930516i_hba_itmode_16drive_cables_2x/).
- For the bare Exos SAS disk, buy a **forward breakout** cable from SFF-8643 to four SFF-8482 SAS drive connectors, with power leads. Do not buy an ordinary SFF-8643-to-four-SATA-data cable for this SAS disk. Budget **$20 to $40 per four-drive cable**. Ensure the power design does not feed 3.3 V into Power Disable if later disks exhibit that behavior.
- The 9300-8i runs hot. Give its heatsink direct airflow, typically a quiet 80 to 120 mm fan or strong front-to-back case airflow. Budget **$10 to $25** for a dedicated fan/bracket if the case airflow does not cross the card.
- Avoid unverified counterfeit cards and RAID-firmware listings. Confirm IT firmware, a visible SAS address, SMART passthrough, and full-disk visibility. After installation, inspect the disk's current logical sector size and health before creating a pool.

## Cases

### Strongest combined-box choice: Fractal Define 7 XL or Meshify 2 XL

- The Define 7 XL supports multi-GPU configurations, large E-ATX/enterprise boards, **6 + 2 included 3.5/2.5-inch mounts**, and up to **18 drive positions** after buying additional trays. Official product sheet: [Fractal Define 7 XL](https://www.fractal-design.com/app/uploads/2020/10/Define-7-XL_Product-Sheet_EN.pdf). Fractal's current support page explains the accessories required to reach maximum capacity: [drive accessory guide](https://fractaldesign.freshdesk.com/support/solutions/articles/4000160756-define-7-xl-meshify-2-xl-how-many-accessories-do-i-need-to-get-to-install-the-maximum-amount-of-s).
- Current price band: about **$180 to $250** for the case, plus roughly **$20 to $45 per pair of additional trays**. A January 2026 sale placed the Define 7 XL at $179.99: [BuildAPCSales reference](https://www.reddit.com/r/buildapcsales/comments/1q28s70/case_fractal_design_define_7_xl_full_tower/).
- Prefer the **Meshify 2 XL** if the machine will sustain GPU training loads, because the mesh front makes high airflow easier. Prefer the **Define 7 XL** if disk noise matters more and the box will be in an occupied room. The Define can use its ventilated panel, so this is not an absolute distinction.
- Verify the exact GPU length and thickness in the intended storage layout before purchase. The storage wall and installed trays can constrain the front edge of very long GPUs. Two modern 3 to 4-slot cards may also cover the HBA slot even in a large case, so motherboard slot spacing remains decisive.

### Lower-cost alternatives

- A used Define R6/7, Meshify 2, or older Define XL R2 can often be found for **$80 to $160** and provides 6 to 8 useful HDD mounts. The older Define XL R2 officially supports eight 3.5-inch trays, nine expansion slots, and GPUs up to 330 mm with the upper cage installed: [Fractal product sheet](https://www.fractal-design.com/app/uploads/2019/06/Define-XL-R2-Product-Sheet-0.36-MB.pdf).
- These are good for one GPU plus HBA, but are weaker choices for two thick GPUs. A rackmount 4U chassis with a SAS backplane can solve drive density and hot-swap needs, but consumer GPUs, acoustic noise, rail depth, and proprietary fan/backplane details make it a worse first combined build unless a rack is already part of the plan.

## PSU and cooling

- One RTX 3090-class GPU plus CPU, HBA, fans, and 4 to 8 HDDs: use a high-quality **1000 to 1200 W** PSU. Budget **$170 to $280**.
- A future pair of 3090-class cards can exceed what a normal 120 V / 15 A branch and a 1200 W PSU should continuously support. Design for **1600 W**, GPU power limits, and ideally a dedicated 20 A circuit if dual high-power GPUs are a real target. Budget **$300 to $500** for a reputable 1600 W unit. ATX 3.0/3.1 transient handling is desirable even if the initial GPU uses 8-pin PCIe power.
- Do not size around nameplate TDP alone. Allow roughly 20 to 30 W spin-up per enterprise HDD for PSU and cable planning, even though steady-state draw is much lower.
- Populate at least three high-quality 140 mm intakes and two exhausts in a large tower. Keep one airflow path through the HDD stack and another across GPUs/HBA. Budget **$60 to $140** for durable PWM fans. Track HDD temperatures and target roughly the 30s to low 40s Celsius during sustained GPU load.

## Networking

- **2.5 GbE** is the cheap minimum that makes a 10 TB NAS feel meaningfully faster than 1 GbE. One spinning disk can often saturate 1 GbE. If the motherboard lacks 2.5 GbE, a supported Intel or Realtek PCIe adapter is roughly **$20 to $45**, but it consumes another slot.
- **10 GbE SFP+** is the value choice when the switch is nearby. Used Mellanox ConnectX-3/ConnectX-4 or Intel X520-class cards are commonly **$25 to $80**, plus **$10 to $25** for a DAC. Linux/NixOS support is mature, power and heat are usually lower than 10GBase-T, and no transceivers are needed for an in-rack DAC run.
- **10GBase-T** is convenient over existing Cat6/Cat6a but costs and runs hotter. A used Intel X550-T1/T2-class NIC is typically **$80 to $180**. The X550-T2 is a dual-port 10GbE adapter: [Intel X550-T2 quick-spec reference](https://www.bhphotovideo.com/lit_files/453677.pdf).
- Platform recommendation: strongly prefer a motherboard with onboard 10GbE or enough independent PCIe slots for two GPUs, HBA, and NIC. Otherwise the NIC is the fourth add-in-card requirement. End-to-end cost also includes the 10GbE switch, which can range from roughly **$100 to $400+** depending on port count, SFP+ versus RJ45, and managed/VLAN requirements.

## UPS

- A single-GPU combined NAS should have a USB-manageable pure-sine UPS so NixOS can perform an orderly shutdown. Entry-level 1500 VA / 1000 W pure-sine tower models are around **$300** MSRP. CyberPower currently lists the GX150C2 at $299.99: [CyberPower 1500 VA models](https://www.cyberpowersystems.com/capacity-va/1500-va/).
- For more runtime and a higher output ceiling, a 1500 VA / 1500 W rack/tower line-interactive pure-sine unit is roughly **$800 to $1,500**. CyberPower's PR1500RT2UC is rated 1500 W, 18.2 minutes at half load, and 6.5 minutes at full load, with a $1,155 MSRP: [PR1500RT2UC](https://www.cyberpowersystems.com/product/ups/smart-app-sinewave/pr1500rt2uc/).
- Online double-conversion units are approximately **$1,500 to $2,500+**. CyberPower's 1500 VA / 1350 W OL1500RTXL2UN is $2,169 MSRP: [OL1500RTXL2UN](https://www.cyberpowersystems.com/product/ups/smart-app-online/ol1500rtxl2un/). This is hard to justify for a first home build unless power quality is poor or availability requirements are unusually high.
- A 1500 VA UPS is not a credible full-load solution for two unrestricted high-power GPUs plus storage. Either power-limit GPUs and shut down quickly, protect only the NAS/network side separately, or move to a larger UPS and suitable electrical circuit.

## NAS-side incremental budget bands

These exclude CPU, motherboard, RAM, GPU, OS SSDs, and additional HDDs:

| NAS-related item | Lean first build | Expansion-ready build |
|---|---:|---:|
| HBA and one SAS breakout | $60 to $120 | $140 to $220 for 16-port path |
| Storage-oriented case and trays | $100 to $200 used | $220 to $400 new with extra trays |
| Added case/HBA cooling | $50 to $100 | $100 to $165 |
| PSU allocation | $170 to $280 | $300 to $500 dual-GPU class |
| NIC and link | $30 to $80 for 2.5G or used SFP+ | $100 to $220 for 10GBase-T |
| UPS | $300 to $500 | $800 to $1,500 |
| **Total** | **about $710 to $1,280** | **about $1,660 to $3,005** |

The practical high-value configuration is initially one GPU, the existing SAS disk, a 9300-8i, 2.5 or 10 GbE SFP+, a large airflow-oriented case, and a 1000 to 1200 W PSU. Buy a second data disk and establish backups before treating the NAS as durable storage. Defer the second GPU and 1600 W/large-UPS expense until local LLM needs are concrete.
