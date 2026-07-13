# GPU findings, July 12, 2026 (US)

## Bottom line

For this workload, NVIDIA is the low-friction choice because CUDA support dominates ML training tooling and NVENC/NVDEC directly cover Jellyfin and video pipelines. Buy VRAM first, then performance. The current 2026 market has distorted the old value hierarchy: used RTX 3090s are commonly being discussed around $1,300 to $1,500, while used RTX 4090 asking/transaction anecdotes cluster roughly around $2,100 to $2,500. Those are volatile marketplace observations, not guaranteed sale prices.

The sensible purchasing rules are:

1. **Best initial target if bought at a genuinely good local price: used RTX 3090 24 GB, ideally no more than about $900 to $1,050.** At the reported $1,300 to $1,500 eBay market, it is no longer the automatic value winner. Test it in person and inspect memory temperatures, fans, ports, and benchmark stability.
2. **Best performant long-lived consumer card if found near $1,600 to $1,900: RTX 4090 24 GB.** It is far faster and more efficient than the 3090, has Ada AV1 encoding, and is excellent for training and upscaling. Above roughly $2,100, the same 24 GB VRAM ceiling makes it hard to justify for an LLM-oriented build.
3. **RTX 5090 32 GB is the best consumer single GPU, but poor value in the July 2026 shortage.** NVIDIA specifies 32 GB and 575 W. Market reporting says it remains far above its $1,999 launch MSRP, with premium retail examples around $4,099. That is too much money for only 8 GB more VRAM than a 3090/4090 unless training speed directly earns money.
4. **RTX 5060 Ti 16 GB is the budget floor, not the preferred foundation.** Current retail reporting is around $550 to $590, with occasional clearance prices substantially lower. It is capable for coursework, modest training, video services, and smaller quantized LLMs, but 16 GB will become the limiting factor quickly. It only makes sense if the total build must stay near $2,000 or it will later become a dedicated Jellyfin/Immich card.
5. **Professional cards solve density and memory, but not value for a first homelab GPU.** RTX PRO 4000 Blackwell has 24 GB ECC at 145 W, RTX PRO 5000 Blackwell has 48 GB ECC (reported list price about $4,569), and RTX PRO 6000 Blackwell has 96 GB ECC at up to 600 W (NVIDIA marketplace pricing reported around $13,250 in June 2026). These are relevant only if one-card VRAM capacity, ECC, dual-slot density, and warranty justify the premium.

## Comparison

| GPU | VRAM | Board power | July 2026 price signal | Fit |
|---|---:|---:|---:|---|
| RTX 5060 Ti 16 GB | 16 GB | 180 W class | ~$550 to $590 normal retail; rare ~$420 or lower clearance | Cheapest credible CUDA entry, but weak future LLM foundation |
| RTX 3090 used | 24 GB | 350 W | ~$1,300 to $1,500 reported eBay asking/sales environment | Still useful, but only compelling below the inflated market |
| RTX 4090 used | 24 GB | 450 W | roughly ~$2,100 to $2,500 current anecdotes | Excellent training/upscaling speed; same VRAM ceiling as 3090 |
| RTX 5090 | 32 GB | 575 W | $1,999 MSRP, commonly far higher; premium model example ~$4,099 | Fastest consumer option, modest capacity uplift for extreme price/power |
| RTX PRO 4000 Blackwell | 24 GB ECC | 145 W | roughly $1,500 launch/list signal | Efficient single-slot-ish service/compute card, weaker raw value |
| RTX PRO 5000 Blackwell | 48 GB ECC | professional dual-slot | ~$4,569 reported list | First materially better single-card LLM capacity tier |
| RTX PRO 6000 Blackwell | 96 GB ECC | 600 W | ~$13,250 reported current NVIDIA listing | Serious workstation/server purchase, not homelab value |

Prices above are snapshots/signals. Check sold listings and local offers immediately before purchasing.

## Multi-GPU implications

- Two 24 GB GPUs provide 48 GB of aggregate VRAM only when the training or inference software explicitly shards the model/workload. They do not become one transparent 48 GB GPU.
- The RTX 3090 is the last GeForce generation here with NVLink support. This can improve peer-to-peer communication for software that uses it, but NVLink does not automatically pool memory, and modern PyTorch/distributed inference can shard over PCIe.
- RTX 4090 and RTX 5090 have no NVLink. Therefore motherboard lane layout, PCIe peer-to-peer behavior, chassis spacing, and cooling matter more than chasing nominal PCIe generation.
- For a future two-GPU box plus SAS HBA and networking, avoid a normal consumer motherboard whose slots are physically x16 but electrically x16/x4. Prefer a workstation platform with two CPU-connected x16-length slots capable of x8/x8 at minimum, plus a separate x8 slot for the HBA/NIC. Threadripper Pro is ideal but expensive; carefully selected AM5 can work for two GPUs plus a modest HBA only with significant lane/IOMMU compromises.
- Physical density is as important as lanes. Three-slot or four-slot open-air GeForce cards are difficult to run side by side. A large tower, riser arrangement, or blower/water-cooled cards may be needed. Professional dual-slot cards avoid this problem at a major cost premium.
- Power design should be based on simultaneous worst case. A 3090 plus future second 3090 is 700 W of GPU board power before CPU, drives, and transients. Dual 4090 is 900 W. Dual 5090 is 1,150 W. A first build should not blindly install a huge PSU for an unspecified future pair, but the chassis, electrical circuit, board, and cooling should be chosen with the intended pair in mind.

## Workload fit

- **ML training:** 24 GB is the recommended starting point. It enables larger batch sizes/models and avoids much more host-memory offload than 16 GB. A 4090 is dramatically more useful for iteration speed, but a correctly priced 3090 still buys the same capacity.
- **Local LLM inference:** capacity dominates. One 24 GB card is useful for roughly 7B to 14B-class models at high precision or larger models quantized, depending on context/KV cache. Two cards expand what can be sharded, but latency and software configuration become more complex. A future 48 GB professional card may be cleaner than two 24 GB cards if its price falls.
- **Video upscaling:** 4090/5090 performance is valuable because this is compute-heavy and often long-running. The 3090 remains capable but consumes more energy per job.
- **Jellyfin:** all candidates have dedicated NVENC/NVDEC, so transcoding does not require sacrificing the primary ML GPU's full compute capacity. Ada and newer GPUs add AV1 encode. The 3090 supports AV1 decode but not AV1 encode. NVIDIA documents FFmpeg/NVENC/NVDEC as suitable for high-performance 1:N transcoding.
- **Immich:** GPU acceleration is a small workload relative to training. It does not justify a separate expensive GPU initially. Keep always-on media/Immich containers available while batch ML jobs run by controlling GPU assignment and resource scheduling; later, a retained 16 GB or low-power professional card could become the service GPU.

## Recommended budget framing for the parent answer

- **About $1,800 to $2,300 total build:** RTX 5060 Ti 16 GB or unusually well-priced used 3090. Compromises the future two-GPU platform and should be treated as an entry build.
- **About $2,700 to $3,500 total build:** used RTX 3090 at a disciplined price plus a storage-capable, lane-aware workstation/NAS chassis. This is the value target if a sub-$1,050 3090 can actually be found.
- **About $3,800 to $5,000 total build:** used 4090 at a disciplined price and a genuinely expandable platform. Best balance for fast training, upscaling, and a future second GPU, but avoid paying inflated 2026 prices merely for 24 GB.
- **$6,000 and up:** 5090 or professional-memory territory. Current 5090 markup makes waiting or buying compute in the cloud for occasional large runs more rational unless the GPU will be used continuously.

## Sources

- NVIDIA RTX 5090, 32 GB and 575 W: https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/
- NVIDIA RTX Blackwell architecture comparison (3090, 4090, 5090): https://images.nvidia.com/aem-dam/Solutions/geforce/blackwell/nvidia-rtx-blackwell-gpu-architecture.pdf
- NVIDIA RTX 3090 page, 24 GB, 350 W, and NVLink: https://www.nvidia.com/en-my/geforce/graphics-cards/30-series/rtx-3090/
- NVIDIA Video Codec SDK and FFmpeg support: https://developer.nvidia.com/video-codec-sdk and https://developer.nvidia.com/ffmpeg
- NVIDIA Ada AV1 encode: https://developer.nvidia.com/blog/improving-video-quality-and-performance-with-av1-and-nvidia-ada-lovelace-architecture/
- NVIDIA RTX PRO 4000 Blackwell datasheet, 24 GB ECC and 145 W: https://www.nvidia.com/content/dam/en-zz/Solutions/products/workstations/professional-desktop-gpus/rtx-pro-4000/workstation-datasheet-rtx-pro-4000-nvidia-us-web.pdf
- NVIDIA RTX PRO 5000 Blackwell datasheet, 48 GB ECC: https://www.nvidia.com/content/dam/en-zz/Solutions/products/workstations/professional-desktop-gpus/rtx-pro-5000/workstation-datasheet-blackwell-rtx-pro-5000-gtc25-spring-nvidia-3658700.pdf
- NVIDIA RTX PRO 6000 Blackwell, 96 GB ECC and 600 W: https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/
- July 2026 current retail tracker: https://www.pcgamer.com/hardware/graphics-cards/graphics-card-price-watch-deals/
- July 2026 RTX 5090 premium retail example: https://www.techradar.com/computing/gpu/asus-proart-geforce-rtx-5090-review-a-slimmer-sff-ready-rtx-5090-for-creators-who-need-flagship-performance-and-32gb-of-vram
- June 2026 community RTX 3090 market report: https://www.reddit.com/r/LocalLLaMA/comments/1tysbyj/rtx_3090_ebay_pricing_is_crazy/
- May 2026 RTX PRO list price reporting: https://www.tomshardware.com/pc-components/gpus/nvidia-rtx-pro-6000-blackwell-gpu-is-listed-for-usd8-565-at-us-retailer-26-percent-more-expensive-than-the-last-gen-rtx-6000-ada
