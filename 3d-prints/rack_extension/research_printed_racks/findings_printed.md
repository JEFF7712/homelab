# Existing printed rack research

Research date: 2026-09-24. Primary creator documentation reviewed online. No outside CAD has been copied or changed. The transfer recommendations below are engineering inferences, not demonstrated capacity claims.

## 1. Rackstack: strongest source for printable rails and explicit assembly

[Creator repository](https://github.com/jazwa/rackstack) provides parametric OpenSCAD and generated STLs. Default profiles are approximately 100, 180, and 200 mm, so these are not direct T1 replacements. Front rails use sliding M4 hex nuts. Frame hardware includes M3 screws/nuts, 3 x 10 mm dowels, and magnets for side walls. Stacked modules use four joiners and eight M3 fasteners. [README](https://raw.githubusercontent.com/jazwa/rackstack/master/README.md)

The [assembly guide](https://raw.githubusercontent.com/jazwa/rackstack/master/assembly-guide/README.md) joins X/Y bars into two trays with screws, bolts separate main rails to the trays, and installs separate panels. The side panels use dowels and magnets; do not assume they are structural shear panels.

Printability is specified individually:

- [Main rail](https://raw.githubusercontent.com/jazwa/rackstack/master/rack/print/mainRail_P.scad): rotated 90 degrees about Y for printing; creator explicitly states no supports.
- [X bar](https://raw.githubusercontent.com/jazwa/rackstack/master/rack/print/xBar_P.scad): generally no supports, depending on rounding.
- [Y bar](https://raw.githubusercontent.com/jazwa/rackstack/master/rack/print/yBarLeft_P.scad): supports at XY wall connections, also affected by rounding.

[License](https://raw.githubusercontent.com/jazwa/rackstack/master/LICENSE): MIT, copyright Zhao Wang, 2023. Its stated condition is retaining the copyright and permission notice in copies/substantial portions.

Real build evidence: [Brian Moses's own build](https://blog.briancmoses.com/2025/03/rackstack-an-open-source-modular-and-3d-printed-miniature-rack.html), March 2025, reports PETG-CF and transparent PETG construction for a MikroTik switch and PiKVM. He encountered equipment fit and X1C plate-size limits. This is useful manufacture/assembly evidence, not a measured load test.

**Transfer:** separate the rack fastening rail from the column so the rail can print flat and its fasteners remain accessible. Borrow bolted frame-node concepts, not the default envelope. A 281 mm member needs segmentation or a demonstrated diagonal plate layout on the A1.

## 2. HomeRacker: strongest example of designed-in support-free connectors

[Creator documentation](https://github.com/kellerlabs/homeracker/) describes 15 mm square supports, lengths in 15 mm increments, separate one-to-six-way connectors, and 4 mm square locking pins. The core requires no purchased assembly hardware or print supports. The source is parametric OpenSCAD; Fusion files are linked on MakerWorld. Exact part orientation was not independently inspected. The README recommends a brim where adhesion is marginal.

The creator documents built racks/shelves and X1C trials with PLA and ABS, while explicitly saying extensive load-bearing tests were not performed. No PETG capacity evidence was found.

Licensing is explicitly split: software [MIT](https://raw.githubusercontent.com/kellerlabs/homeracker/main/LICENSE), models and creative assets under `/models/` [CC BY-SA 4.0](https://raw.githubusercontent.com/kellerlabs/homeracker/main/models/LICENSE). The latter requires attribution and marking changes when shared, and the specified ShareAlike terms for shared adaptations. The README warns that other MakerWorld uploads can use different licenses.

**Transfer:** use independent junction pieces and make supported starts an explicit design requirement. Do not adopt the 15 mm grid or printed locking pins wholesale: they conflict with the existing measured T1 interface and M5 rod scheme, and their strength here is unverified.

## 3. OpenNode ON-1: rod-tied frame comparator

[Creator README](https://raw.githubusercontent.com/NodleCode/OpenNode/main/README.md) documents two printed frames, a base plate, three approximately 4 mm threaded rods, washers/nuts, and optional fourth rear rod. M3 heat-set inserts attach equipment. It links editable [Onshape CAD](https://cad.onshape.com/documents/316cd9ed33c458cd73b28ece/w/db9c7f5ecd3d8b58a5007c36/e/6799c84f86b383e823056bb1) and [MakerWorld print files](https://makerworld.com/en/models/1073247#profileId-1063589). Those external CAD contents/license statements were not independently inspected.

The creator specifies a 250 mm square bed, ASA reference prints, and automatic tree supports. A print-orientation image and physical assembly photos are supplied. This design is explicitly not support-free. No load rating or quantitative stiffness test was found.

[Repository license](https://raw.githubusercontent.com/NodleCode/OpenNode/main/LICENSE): BSD 3-Clause, copyright Nodle, 2025. Retain notices/conditions/disclaimer in source and reproduce them for binary redistribution; names cannot endorse derived products without permission. This confirms the repository license, not a separately verified external CAD license.

**Transfer:** separate frame pieces tied with accessible metal hardware, with the rod playing a defined connection role. Do not treat rods alone as diagonal bracing. Our existing vertical M5 tie rods can remain, but the extension still needs explicit top-frame and lateral-load connections.

## Supplementary hybrid: ButterflyRack

[Creator README](https://raw.githubusercontent.com/axiopaladin/ButterflyRack/main/README.md) uses four metal rack rails and printed top/bottom corner brackets linked with double dovetail spacers. Hardware is sixteen M6 x 16 bolts/nuts. Largest printed dimension is 4.5 inches. It gives assembly photos and a width/depth selection scheme. A print orientation/support specification was not found in the README. Integration review located [LICENSE.txt](https://github.com/axiopaladin/ButterflyRack/blob/main/LICENSE.txt), confirming CERN-OHL-W-2.0, and editable FreeCAD/STEP files. Useful architecture comparator if buying rails becomes acceptable.

## Recommended direction for the T1 extension

Keep the proven T1 mount interface and accepted fit clearances. Separate rails, columns, and bracing/frame connections enough that each can print in an orientation with supported starts and accessible fasteners. Rackstack gives the clearest implementation precedent. HomeRacker supports the design principle of independent, explicitly printable nodes. OpenNode confirms rod-tied assemblies are a practical construction pattern, but does not establish the current extension's strength.

Before another full upright print: resolve floating bosses/ribs, select the rail/column interface, complete top and lateral connections, check assembly/tool access, and inspect the actual sliced layers. None of the sources establishes safe payload for this particular PETG extension.
