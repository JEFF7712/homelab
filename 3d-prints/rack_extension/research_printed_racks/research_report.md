# Printed rack references for the T1 extension

Research date: 2026-09-24. Recommendation based on creator documentation and source inspection. No external CAD imported and no production geometry changed during this research.

## Decision

Adapt modular frame and printable rail ideas to the measured T1 interface. Use Rackstack as the first source reference for rail construction, Lab Rax for frame decomposition, and Natalie T's reworked rack for bracing independent of equipment. Keep a metal-rail alternative available if reducing printed structural material becomes the priority.

Preserve accepted mating dimensions wherever practical. Accepted coupons demonstrate fit only; they do not establish the final mount strength or make the current uprights ready to print.

## Best references

| Project | Evidence and useful feature | Application and limitation |
| --- | --- | --- |
| [Rackstack](https://github.com/jazwa/rackstack) | Editable OpenSCAD, MIT license, separate bolted rails and frame bars. [Rail export](https://raw.githubusercontent.com/jazwa/rackstack/master/rack/print/mainRail_P.scad) explicitly rotates the rail for printing without supports. | First reference for separating the equipment rail from the structural column and keeping nuts accessible. Its default dimensions differ from ours. Some other parts require supports. |
| [Lab Rax](https://the-diy-life.com/introducing-lab-rax-a-3d-printable-modular-10-rack-system/) | Creator's A1 build separates posts, horizontal members, edge pieces, and panels. Post and panel joints can be staggered. Edge pieces use limited supports. | Useful frame architecture. Published 236.525 mm hole spacing is close to our approximate 236 mm, but its 222 mm opening is narrower than our 223 mm design opening. Do not copy dimensions blindly. Model license was not verified. |
| [Natalie T's reworked rack](https://www.printables.com/model/1090551-modular-10-inch-server-rack-reworked) | Adds dedicated crossbraces and a stabilizing topper; creator explains that the predecessor depended on installed shelves for stability. SolidWorks source advertised; CC BY-NC-SA 4.0 shown on the model page. | Make the extension stable with equipment removed. Define brace connections before releasing columns. Source CAD was not imported or inspected. |
| [ButterflyRack](https://github.com/axiopaladin/ButterflyRack) | Printed corner nodes and interchangeable dovetail spacers join four metal rack rails. FreeCAD and STEP available; [CERN-OHL-W-2.0](https://github.com/axiopaladin/ButterflyRack/blob/main/LICENSE.txt). | Strong alternative for small replaceable printed connectors. Requires purchased rails and different adapters. Its tight-fit dovetail retention does not establish strength for our extension. |

Additional references: [HomeRacker](https://github.com/kellerlabs/homeracker/) demonstrates separate support-free core connectors, but its 15 mm grid and printed pins would require substantial adaptation. Software is MIT; models are CC BY-SA 4.0. [OpenNode](https://github.com/NodleCode/OpenNode) demonstrates accessible rod-tied frames, but explicitly uses tree supports. Its repository is BSD-3-Clause; linked external CAD licensing was not independently verified. Detailed source links are in the two findings files.

## Changes to carry into the design

These are proposed engineering adaptations, not tested improvements:

1. Separate the equipment fastening rail from the column where this enables flat printing and accessible inserts or nuts. Evaluate a continuous supported insert rail against a separate rail before choosing the final section.
2. Resolve abrupt internal starts at bosses, ribs, sockets, spigots, and the top block. Use supported slopes, open channels, or separate caps as appropriate. The current screenshot and successful coupons do not settle full-column printability.
3. Finish top and bottom crossmember connections plus rear and side restraint. Equipment shelves must not be required to keep the empty frame square.
4. Make crossmembers and braces replaceable without discarding an entire upright. Keep fasteners and support-removal paths accessible.
5. Retain the accepted T1 mating geometry as the interface datum. Retain M5 rods if compatible with the selected architecture; vertical clamping alone does not prevent sideways sway. Review remaining mount wall thickness and actual clamp stack before declaring printed mounts final.
6. Keep the approximately 236 mm hole spacing as provisional measured input. Check it against an actual equipment interface before changing it to another project's nominal dimension.

## Filament and release criteria

Do not print the current full uprights yet. Complete the assembled frame first, including rear mount differences, brace attachments, top hardware access, and printable joints. A 281 mm member exceeds one A1 bed axis; any diagonal layout must include the complete part and brim envelope.

Before releasing revised production STLs, inspect the actual sliced layers for unsupported starts, bridge spans, and removable supports. Use a small coupon only for newly changed interfaces that existing accepted coupons did not cover. Record which printed pieces remain compatible with the released assembly.

The reviewed projects show practical builds, but none supplied a quantified load test applicable to this 355.6 mm extension, payload, material, and temperature. Capacity remains unverified.

## Research scope

Reviewed primary project documentation, source files, and license files where available. Did not run CAD builds, slice new parts, perform physical tests, or infer a load rating from photographs. A T1-specific 1–3U bottom expansion surfaced through an aggregator, but the primary model page could not be verified; it was excluded from the recommendation.
