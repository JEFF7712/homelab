# Revision 4 prototype: fit-test stage

This is a matched prototype set, not production approval. Do not mix older rails or uprights with it. The next action is the small M4 test plate below. Full-frame printing waits for physical acceptance.

## Print this first

Open [the M4 fit-test project](slicing/m4_pilot_coupon/m4_pilot_coupon.3mf) in OrcaSlicer. It contains the marked pilot coupon and a separate 6 mm clamp spacer. The STLs are already oriented. Use the included A1 0.4 mm / PETG settings if they match your printer, filament and textured PEI plate.

- Approximately 15 g and one hour, including the spacer; exact slicer estimates are in `slicing/summary.json`.
- 0.20 mm layers, six walls, six top/bottom layers, 25% gyroid.
- 250 C nozzle / 80 C bed, matching the installed PETG profile temperature choices.
- No supports for this test. A 3 mm outer brim is included.

Pilot identification uses the small notches on the top beside each hole:

| Notches | Pilot diameter |
| --- | ---: |
| One | 5.6 mm |
| Two | 5.8 mm, current design candidate |
| Three | 6.0 mm |

Start with the two-notch hole and your confirmed 6 mm OD x 6 mm long M4 heat-set insert. Heat-set it flush and let it cool. Put an M4x16 screw through the printed 6 mm spacer into the insert, then snug it by hand. The spacer gives the same 10 mm screw projection used at the side-frame joints, with an 11 mm bore behind it.

Report whether the insert seats flush without splitting or bulging, whether the screw pulls the spacer firmly against the coupon, and whether the insert turns or pulls out. If 5.8 mm is too tight or loose, compare the other pilots. Do not tighten the bare 16 mm screw directly into the coupon without the spacer: it would bottom out.

Do not print the separate `m4_test_spacer.3mf` as well; that spacer is already included on the test plate.

## What changed

- Added two upper side beams. Each is attached to the front and rear posts with two vertically separated M4 screws per post. Their inner ledges support the original lid at Z=355.6 mm and reproduce the recorded handle-hole coordinates.
- The lid uses four M4x20 screws of the same head style as stock. Your reported stock screw projection is about 3 mm below the assembled handle/acrylic stack; adding 4 mm length gives approximately 7 mm projection. Confirm actual engagement using the lid coupon before full assembly. The bore is 11 mm deep and the insert is 6 mm long.
- Corrected M4 insert length from the inherited 9.5 mm assumption to your confirmed 6 mm, and recorded 6 mm OD. The 5.8 mm pilot remains a candidate until this print test passes.
- Preserved the corrected diagonal screw bearing planes. Removed the 0.5 mm gap between the diagonal lap faces so they can clamp in contact.
- Moved the M3 rail fasteners into the 15.9 mm gaps. The previous head pockets touched the M5 pilots and produced non-watertight rail STLs. Rack-hole positions and the accepted 6.2 mm M5 pilot are unchanged. The minimum nominal separation between the 6.5 mm M3 head pocket and the 6.7 mm M5 insert envelope is now 1.35 mm.
- Exported the existing keyed bottom retainer into this set. Four identical prints are used; rotate the right-side retainers 180 degrees to align their keys.
- Print exports are positioned on Z=0 and oriented explicitly. Rails print back-face down; crossbar counterbores face up; upper side beams print on their outer side plates; the diagonal halves use opposite flat faces.

See [the assembly preview](assembly.png) and `prototype_assembly.step`. The STEP includes a reference lid and nominal-length tie rods. It is not a rod cutting instruction. Fit the actual clamp stack before cutting rods.

## Checks performed

- Targeted CAD, dimensional, seam and new upper-frame regression suites.
- Pairwise printed-part interference, hardware/printed-part checks including both diagonal halves, lid clearance, and positive bearing/contact checks for the new beams and diagonal lap.
- `mesh_checks.json`: watertightness, winding and connected-component checks on the exported STLs.
- OrcaSlicer 2.4.2 generated toolpaths for the exported parts. `slicing/summary.json` contains material, time, support use and warnings. Input STL/profile hashes are recorded beside the sliced projects.
- Selected actual toolpath layers were plotted and inspected, including the upright socket roofs and the mount shoulder transitions. The images are in `slicing/*_layers.png`.

The meshes and toolpaths are verified geometrically; printed strength and repeatability are not established. No printer was started and nothing was published.

## Supports and profile notes

The full uprights and inverted mounts use snug normal supports from the build plate. The socket roofs still bridge over cavities, so they are not released as support-free parts. Supports inside the socket are intended to be removed through its open end before assembly. Physical removal and the resulting fit need checking.

The rails, side beams, crossbars and diagonal pieces slice without generated supports in the supplied orientations. Small horizontal bore roofs still need physical print confirmation. The 251 mm crossbars are sliced individually without a brim because they have only 5 mm total nominal bed-axis margin.

Orca emits its `bed_temperature_too_high_than_filament` warning with the inherited Generic PETG 80 C bed setting. The supported taller parts also carry traditional-timelapse warnings. These are recorded, not hidden. Review the temperature against your actual filament guidance; the files do not claim warning-free slicing. Slicer success does not prove layer adhesion or support removability.

## Following fit tests, after the M4 pilot passes

1. `rail_fit_coupon` plus `rail_column_coupon`: revised M3 position and rebate, M5 insert fit, and M5 rod clearance. These replace the earlier rail coupons because the M3 position changed.
2. `lid_fit_coupon` plus `top_post_coupon`: two upper-beam screws, beam seating, M4 insert setting, top washer/tool access, and the lid/handle screw stack. Use the original acrylic and handle on the coupon to check projection and alignment. These parts are test sections, not final frame parts.
3. `mount_front_left` and `mount_rear_left`: candidate final mounts checking the asymmetric hole patterns, transverse-member relief, underside hardware loading and spigot seating. They can be reused if the physical tests pass and the geometry is retained. Check right-hand placement before printing the remaining pair.

Full columns and beams remain on hold until these interfaces pass. The existing M5 washer/nyloc dimensions are retained. A complete assembled load and retained-clamp test is still required; the handles are not approved for lifting the loaded rack.

## Hardware planning

For the complete prototype, excluding any hardware already owned:

- Four M5 tie rods, eight measured M5 washers, eight M5 nyloc nuts; cut lengths remain subject to the assembled measurement.
- Eight M4x16 base mounting screws.
- Eighteen M4x16 frame screws into inserts: four side-restraint, four rear-crossbar, two diagonal-anchor, eight upper-beam.
- Two M4 lap bolts with matching nuts/washers. Confirm the assembled lap stack and protrusion; these fasteners are not included in the current hardware envelopes.
- Four M4x20 lid/handle screws, same head style as the originals, subject to the coupon check.
- Twenty-two confirmed 6x6 mm M4 inserts, including four lid inserts. Pilot size awaits the first test.
- Fourteen M3 rail screws and nuts, using the hardware from the accepted clamping test; confirm the revised coupon before ordering or printing the full rails.
- M5 rack inserts as needed for equipment positions. The geometry provides 23 holes per front rail line; the source defines seven complete standard U plus partial positions, not eight usable U.

The hardware envelopes cover the tie rods and selected M4 frame screws/inserts. They do not yet represent every M3 screw, lap nut/washer, M5 nut/washer or handle shape. Physical hardware access remains part of the fit checks.

## Source and reproduction

`top.py` defines upper beams and lid datums. `release.py` is the common positioned assembly and print export definition. `test_release.py` checks the complete printed assembly. `slice_review.py` resolves the locally installed Orca presets and slices without sending jobs. `inspect_slices.py` reads the actual generated paths. `validate_meshes.py` checks the STLs independently.

`manifest.json` records per-file quantity, dimensions, orientation and prototype status. Print quantities in that manifest are for planning only, not an instruction to print the full set now.
