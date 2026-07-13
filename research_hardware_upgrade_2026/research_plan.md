# Homelab Hardware Upgrade Research Plan

## Main question

What are realistic July 2026 budgets and component classes for a combined NixOS GPU compute server and expandable SAS/ZFS NAS, with an initial ML-training GPU and a future second GPU for local LLM inference?

## Subtopics

1. GPU options and current pricing: compare practical NVIDIA cards for ML training, video upscaling, Jellyfin, Immich, and later multi-GPU inference, emphasizing VRAM, power, and used-market value.
2. Platform options and current pricing: compare mainstream, workstation, and used enterprise CPU/motherboard/RAM platforms that can support two GPUs plus a SAS HBA without crippling slot or lane constraints.
3. NAS chassis, HBA, power, and networking costs: establish realistic costs for drive bays, SAS connectivity, cooling, PSU, UPS, and 2.5/10 GbE around the existing Exos X16 drive.

## Synthesis

Combine the findings into complete budget tiers, identify what each tier compromises, and recommend a starting range that preserves a credible two-GPU and multi-drive upgrade path.
