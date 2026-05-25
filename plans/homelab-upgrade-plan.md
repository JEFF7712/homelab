# Homelab Upgrade Plan

Last reviewed: 2026-05-24

Pricing note: estimates below are realistic U.S. street-price ranges for May 2026. HDD pricing is unusually elevated in 2026 because of high storage demand, so drive costs are less friendly than older homelab advice may suggest.

## Current Pressure Points

- Media storage is the main bottleneck. `media-pvc-hdd` is 1700Gi and about 97% full, with roughly 53Gi free.
- Longhorn media backend has only about 145Gi of immediate headroom while the old media expansion snapshot continues pruning.
- Storage is single-replica, so a disk/node failure can still cause data loss.
- There is no true off-cluster backup target yet. Longhorn snapshots are useful rollback points, but they are not disaster recovery.
- CPU and memory are acceptable for the current workload. Nodes were around 50-57% memory, with worker CPU load varying by media activity.
- GPU acceleration would help Jellyfin/Immich/AI workloads, but it is not the biggest reliability issue.

## Recommended Purchase Order

### 1. NAS

Priority: Highest

Why:
- Solves the biggest current problem: media capacity.
- Provides a real backup target for VolSync/restic and Longhorn backups.
- Lets large media move off Longhorn or at least gives Longhorn somewhere external to back up to.
- Reduces pressure on the WYSE 2TB media disk.

Recommended target:
- 4-bay NAS minimum.
- 5-6 bay if the budget allows and you want more growth room.
- ZFS-capable system preferred.
- 2.5GbE minimum; 10GbE nice if you expect large media transfers.

Estimated cost:
- Budget used/custom 4-bay NAS without drives: $300-600
- New 4-bay NAS appliance: $500-900
- Higher-end 4-bay NAS with 10GbE or strong CPU: $700-1100
- Better 5-6 bay NAS or custom build: $900-1600 before drives
- Drives:
  - 8TB NAS HDD: about $170-220 each
  - 12TB NAS HDD: about $220-330 each
  - 16TB NAS HDD: about $280-420 each
  - 20TB NAS/enterprise HDD: about $330-500 each
  - 24TB NAS/enterprise HDD: about $420-600 each

See: 
https://pricepergig.com/en/amazon-us 

Used/refurb enterprise drives:
- Often the best $/TB if bought from a reputable seller with warranty.
- Typical 12-20TB refurb enterprise drives: $120-300 each, depending capacity, age, and warranty.
- Higher risk than new NAS drives, so buy extras or keep a cold spare.

Practical starting point:
- 4-bay NAS with 2x or 3x 12TB or 16TB drives.
- Add more drives later.
- Use it for backups first, then decide whether media should move there.

Realistic starting budget:
- Low end with used/custom NAS and refurb drives: $600-1000
- New 4-bay NAS with 2x 12TB new drives: $950-1500
- New 4-bay NAS with 3x 16TB new drives: $1500-2200
- 5-6 bay NAS with several large new drives: $2200-3500+

### 2. UPS

Priority: Very high

Why:
- Protects Longhorn, databases, downloads, and filesystems from sudden power loss.
- Especially important once a NAS is added.
- Not exciting, but high value for data integrity.

Estimated cost:
- Small 850-1000VA APC/CyberPower UPS: $110-220
- 1500VA consumer UPS: $180-350
- 1500VA pure sine wave or business-class UPS: $280-500
- Rackmount/business UPS with fresh batteries: $350-800+

Target:
- Enough runtime for graceful shutdown or ride-through of short outages.
- USB monitoring support is useful for NAS/server shutdown automation.

### 3. GPU Or GPU-Capable Compute Node

Priority: Medium

Why:
- Useful if you care about Jellyfin transcoding, Immich ML, subtitle/audio processing, or local AI.
- Not the current bottleneck for reliability or capacity.

Best options by workload:
- Jellyfin transcoding: Intel Quick Sync or Intel Arc is usually best value.
- Immich ML/light acceleration: Intel iGPU/Arc can be useful, NVIDIA is also fine.
- Local LLM/AI experiments: NVIDIA is the safer ecosystem choice.

Estimated cost:
- Used Intel Quick Sync mini PC: $175-450
- Newer Intel Quick Sync mini PC with 32GB RAM: $350-700
- Intel Arc A310/A380 class GPU: $100-180, if you have a compatible host
- Intel Arc A580/A750 class GPU: $170-280
- Used NVIDIA P4/T4/P100-class card: $150-450, depends on power/cooling and seller
- Modern NVIDIA GPU suitable for local AI: $350-1000+

Note:
- Current thin-client/mini-PC hardware may make GPU installation awkward. A dedicated GPU-capable box may be cleaner than trying to retrofit the current nodes.

### 4. Network Upgrade

Priority: Medium after NAS

Why:
- NAS will make 1GbE feel limiting for large media transfers and backups.
- 2.5GbE is cheap and usually enough.
- 10GbE is best for NAS-to-compute, but costs more and may add heat/power.

Estimated cost:
- 2.5GbE unmanaged switch: $70-180
- 2.5GbE managed switch: $120-300
- 2.5GbE USB/PCIe NICs: $20-70 each
- Used 10GbE NICs: $30-100 each
- Used 10GbE switch: $150-500+
- Quiet/new 10GbE switch: $300-900+

Recommended:
- Start with 2.5GbE unless you know you need 10GbE.

### 5. Stronger Compute Node

Priority: Lower than NAS/UPS

Why:
- Current CPU/memory is usable.
- A stronger node would improve scheduling headroom and let you run heavier services.
- It could also host GPU, faster NVMe, or NAS-adjacent workloads.

Estimated cost:
- Used mini PC with 32GB RAM: $300-650
- Used workstation/server with GPU room: $500-1200+
- Newer efficient mini PC with 64GB RAM support: $500-1000

## Short Recommendation

Buy in this order:

1. NAS with enough drive bays to grow.
2. UPS.
3. GPU-capable compute only if you have a clear Jellyfin/Immich/AI use case.
4. 2.5GbE or 10GbE networking after the NAS.

The NAS is the most impactful next upgrade because it addresses capacity, backups, and future growth at the same time.

## Pricing References

- 2026 4-bay NAS buyer/pricing guides: https://www.serverman.co.uk/server/nas/best-4-bay-nas/ and https://storagediskprices.com/best-synology-nas/
- 2026 HDD market/pricing context: https://www.tomshardware.com/pc-components/hdds/hard-drive-prices-have-surged-by-an-average-of-46-percent-since-september-iconic-24tb-seagate-barracuda-now-usd500-as-ai-claims-another-victim
- Current $/TB checks for drives: https://pricepergig.com/en/amazon-us
