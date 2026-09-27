# Hybrid rack references

Research date: 2026-09-24. Primary creator repositories inspected. No CAD changed. None of the inspected documentation supplies a quantified load test with mass, duration, deflection, and temperature.

## 1. ButterflyRack: strongest reusable connection architecture

[Creator repository](https://github.com/axiopaladin/ButterflyRack), [FreeCAD source](https://github.com/axiopaladin/ButterflyRack/tree/main/FreeCAD), [STEP exports](https://github.com/axiopaladin/ButterflyRack/tree/main/STEP), [license](https://github.com/axiopaladin/ButterflyRack/blob/main/LICENSE.txt).

- Four metal rack rails connect two printed rectangular end frames. Each frame uses four corner brackets and separate dovetail butterfly spacers. Width and depth vary through the spacers.
- Hardware: four rails, sixteen M6x16 bolts, sixteen M6 nuts. Half the corner brackets must be mirrored. Retention of frame dovetails relies on tight fit, with optional glue.
- Native files verified: `CornerBracket.FCStd`, `Butterfly0000.FCStd`, `Butterfly2113.FCStd`, and STEP equivalents. Explicit CERN-OHL-W-2.0 license.
- Creator states largest printed dimension is 4.5 inches, easily within A1 capacity. Filament, layer settings, supports, and print orientation are not specified in the README.
- Build evidence: assembly photographs and completed handled rack published with instructions. No quantified load rating found.
- Best transferable idea: independently replaceable corner nodes and width/depth spacers, with metal rails providing the equipment interfaces. Do not inherit its press-fit strength assumptions without testing.

## 2. OpenNode: transverse threaded-rod frame

[Creator repository](https://github.com/NodleCode/OpenNode), [license](https://github.com/NodleCode/OpenNode/blob/main/LICENSE), [linked Onshape CAD](https://cad.onshape.com/documents/316cd9ed33c458cd73b28ece/w/db9c7f5ecd3d8b58a5007c36/e/6799c84f86b383e823056bb1).

- Two printed frames and a base plate are tied together with three 260 mm #8 UNC rods, nuts, and washers; an optional fourth rod ties the rear top. Equipment attaches through M3 heat-set inserts or self-tapping screws.
- ASA reference prints on a P1S; the creator explicitly specifies automatic tree supports and 250x250 mm minimum bed. A recommended orientation image exists, but its geometry was not visually inspected here. Do not treat it as a support-free design.
- STL, 3MF, and extender assets exist in the repository; editable CAD is linked externally through Onshape, whose contents were not opened. Repository license is BSD-3-Clause.
- Build evidence: illustrated assembly procedure and prototype/extension images. No quantified load test found.
- Best transferable idea: external nuts and transverse rods produce accessible frame ties, avoiding hidden internal fastening features. Its 3U-derived layout and #8 hardware are not directly interchangeable with this 8U T1 extension or M5 stock.

## 3. Parametric 10 Inch Homelab Rack: extrusion alternative, weaker reuse evidence

[Creator repository](https://github.com/MartinStudiosDev/Parametric-10In-Homelab-Rack).

- Creator describes a vertically parametric 2020 aluminum extrusion rack with printed components. Height is selected through a spreadsheet that generates the BOM.
- The repository actually contains a RAR archive, despite README references to a ZIP. README claims parametric CAD, STL, STEP, BOM, and assembly instructions inside. Archive contents were not inspected, so exact fasteners, printable orientation, source format, and filament remain unverified.
- `Homelab.PNG` is published as build evidence, but was not visually inspected. No quantified load test appears in the README.
- No license file appears in the repository tree. Reuse permission is unknown; do not copy its geometry on the assumption that a public repository grants a license.
- Best transferable idea: retain metal extrusion as the long structural member and print only adapters and equipment interfaces. Lower priority for direct CAD reuse than ButterflyRack.

## Application to the current T1 extension

These are design inferences, not claims made by the referenced creators. Given the supplied 281x200 mm envelope, 355.6 mm rise, approximately 236 mm equipment hole spacing, 400 mm M5 rods, and 256 mm A1 volume:

1. Keep the physically accepted T1 attachment geometry as an interface datum. A different frame architecture still needs a new adapter above that datum; old fit acceptance cannot automatically validate a replacement adapter.
2. Borrow ButterflyRack's separate corner nodes and horizontal spacers. Put brace and crossmember attachments on accessible outer faces. That lets a brace or frame member change without reprinting a full-height upright.
3. A 281 mm one-piece front frame exceeds a bed axis. Split horizontal frames at dedicated mechanical joints, or use cut metal members. Do not depend on diagonal packing without checking the complete brimmed envelope.
4. Replace inaccessible internal roofs and nut features with open channels, externally loaded nuts, or separate caps. Confirm actual layer support after slicing; a successful joint coupon does not validate features absent from that coupon.
5. M5 tie rods can retain printed segments axially, but their existence does not establish lateral stiffness. Include deliberate rear and side shear restraint through diagonal braces or suitable panels, and define attachment locations before releasing uprights.
6. A hybrid using continuous metal rails or 2020 extrusion removes most tall printed structural material and vertical splice print risk. It adds purchased parts and changes adapter geometry. The existing M5 rods need not be consumed solely because they were purchased.
7. Settle width, depth, diagonal restraint, top frame, and tool access in the assembled CAD before any full upright print. Keep 236 mm as an approximate input until checked against an actual mounted equipment interface.

Recommendation: use ButterflyRack as the primary connection/reference source; borrow OpenNode's accessible hardware approach. Prefer adapting ideas to the tested T1 mount rather than attempting to stack a complete unrelated rack directly onto it. Do not prescribe production print settings until revised geometry is sliced.
