# Rack extension audit, 2026-09-24

## Verdict

The existing source was a corner prototype and schematic frame, not a complete
load-qualified rack extension. This revision corrects four assembly defects
and supplies a matched corner prototype and small fit coupons. It preserves
the 355.6 mm added height, measured T1 interface, accepted 30.3 mm locating
channel, and accepted 6.2 mm M5 insert pilot.

The available mounting pattern contains seven complete three-hole U positions,
with partial positions at 15.7 and 339.9 mm. It is not eight usable U.

## Measurement update

The user corrected their center-to-center measurement to **236 mm** (an estimate).
Current CAD uses this value, assuming symmetric rails within the recorded
281 mm chassis width: X=22.5 and 258.5 mm. Rack insert axes are now independent
of the tie rods. Bosses are trimmed to the 29 mm column width, preserving the
223 mm clear opening and leaving 3.15 mm from the insert OD to the inner edge.
The separate Wyse cradle has a 234 mm nominal pattern, a 1 mm per-side mismatch;
actual slot clearance/fit remains to be checked. No spacing-gauge print is needed.

## Findings and changes

| Priority | Finding in the incoming CAD | Resolution / remaining evidence |
| --- | --- | --- |
| Critical | Extension rack holes are 252 mm apart, but the existing `wyse5070_extended_t1` cradle uses a 254 mm ear span with 10 mm hole offsets, giving 234 mm. | Corrected the extension to the user-measured 236 mm. Decoupled insert and rod axes and mirrored right-side bosses. The old 234 mm gauge is historical; accessory slot fit remains to be checked. |
| Critical | Mount and lower upright intersected by 908.709 mm³ after the larger insert bosses were added. | Move the mount shoulder from 25 to 36.35 mm, between complete bosses; relieve the spigot behind the rack bosses. Revised intersection is zero. |
| Critical | Lower and upper uprights intersected by 697.978 mm³; an insert was also split at the 177.8 mm joint. | Move the splice to 169.85 mm and relieve the male spigot. Preserve all existing hole centers. Revised intersection is zero, with every insert entirely within one part. |
| Critical | Top washer recess was above the narrow nut pocket. A 20 mm washer could not reach the claimed 344.8 mm seat. | Open a 20.5 mm loading path down to the seat. The washer now bears below the nut; insertion sweep and supporting material below the seat are checked. |
| High | Bottom retainer had a hex bore but a round exterior, so it could rotate with the nut. | Add a 4 mm wide external key and matching mount slot with 0.2 mm clearance. Kernel checks prove fit and interference when rotated 20 degrees. Printed key strength remains untested. |
| High | The first M4 head recess locally overlaps the washer-pocket projection: its seat is at 11.5 mm and the cavity roof at 11 mm. | Only 0.5 mm of local roof separates them in that overlap. The existing screw/interface dimensions were retained; local support needs section review or a thicker seat with a correspondingly longer screw before assigning load capacity. |
| High | Frame segments only overlap in the schematic assembly; there are no developed frame joints or rear diagonal. | Still open. Design connected perimeter members and a removable rear diagonal before a full rack trial. Overlapping CAD solids are not physical fasteners. |
| High | Full-rack export differs from the buildable corner prototype. | Added `--prototype-only` export with an assembled corner STEP. Root-level STLs and the legacy full-rack assembly are not a matched revision-2 production kit. |
| High | Lid/handle attachment and four corner variants are incomplete. | Still open. Mirror/locate against actual attachment coordinates, including the asymmetric front/rear Y pairs. Do not simply copy the front mount to the back. |
| Medium | The repeated measured gaps sum to 44.50 mm, while nominal U pitch is 44.45 mm. | Preserve the existing measured pattern pending a longer-span check: discrepancy is 0.05 mm per repeat, 0.40 mm over eight repeats. No silent conversion to another pitch. |
| Medium | A column-only opening check overlooks other parts. | Columns leave 223 mm. Schematic 30 mm side-frame members leave 221 mm, and local mounting flanges protrude further near the base. Check actual equipment envelopes at their mounting heights; the 223 mm figure is not a complete-assembly clearance proof. |
| High | No rated payload, thermal creep evidence, or complete-rack stability result exists. | Still open. Solid validity and clearance tests establish geometry only. |

## Payload and placement

The user identified the Dell Wyse 5070 Extended, Dell Wyse 5070 slim,
HP EliteDesk 800 G3 Mini, and TP-Link TL-SG108E, with possible future additions.
These identities were checked against `../../HARDWARE.md` from the rack-extension
directory. That inventory does not contain measured masses. It also lists an
external 3.5-inch drive for the slim Wyse and external power supplies; their
placement and weight must be included if they move upstairs.

The user has tested a corner mount and has not printed uprights. The test result
was not quantified in this exchange. Preserve that part as a comparison sample.
The revision-2 mount has the same aluminum seating interface and M4 attachment
geometry, but a taller shoulder and keyed pocket; it needs its own fit check.

Place heavier devices in the lowest complete extension positions. The existing
Extended-Wyse cradle uses 2U. Allocate space from the actual other cradles,
cables, and ventilation clearances rather than treating seven U as seven free
device slots. Full-depth supports connected to front and rear rails would spread
equipment loads across the four columns. Front-only mounts introduce bending
that an axial tie-rod check does not cover.

Measure the complete intended upstairs payload, including mounts, power bricks,
cables, and any drive. Record an explicit allowance for future additions before
setting the working load and ballast-test load. No numerical load rating is
assigned by this audit.

## Geometry and load path

| Datum or part | Revision 2 |
| --- | --- |
| Original / relocated lid plane | 0 / 355.6 mm |
| Mount to lower-upright bearing plane | 36.35 mm |
| Lower to upper-upright bearing plane | 169.85 mm |
| Mount bounding box including locating lips | 34.3 × 68 × 51.35 mm |
| Lower upright bounding box including spigot | 29 × 30 × 155.5 mm |
| Upper upright bounding box | 29 × 30 × 185.75 mm |
| Bottom / top washer bearing planes | 11 / 344.8 mm |
| Illustrative assembled rod tips | 0.2 / 355.4 mm |
| Nominal rod length from these tips | 355.2 mm, not a final cut instruction |
| Insert end / tie-bore front wall | 9.3 / 11.75 mm from rack face |
| Boss-to-bore geometric separation | 2.45 mm |

The nut loads the washer, which bears on the end block. The block transfers
compression into the column walls and bearing shoulders, then the lower block
and aluminum seat. The rod clamps those interfaces. Relieved spigots locate
the parts and resist lateral movement; their strength and joint stiffness need
physical proof. See [actual mesh sections](corner_sections.png).

## Physical mount-joint result

The user printed the revised mount-spigot and upright-socket coupons and
reported: "they fit good, very slightly loose" and "the M5 rod passes through."
Retain the current joint clearance. This confirms the coupon fit and rod
passage; it does not establish full-column stiffness or retained clamp load.

## Print sequence and acceptance

1. The direct 236 mm measurement supersedes the gauge step. Print
   `phase2_mount_spigot_fit_coupon.stl` and
   `phase2_mount_socket_fit_coupon.stl`. They must seat by hand with no binding,
   visible shoulder gap, or insert interference.
2. Print `phase2_splice_lower_fit_coupon.stl` and
   `phase2_splice_upper_fit_coupon.stl`. Test the same conditions with the
   actual M5 rod and adjacent inserts installed.
3. Print `phase2_top_hardware_coupon.stl`. Confirm the actual washer drops
   to the floor, the nut and tightening tool fit, and rod protrusion clears
   the lid plane.
4. Print the revised mount and keyed retainer. Load hardware from below,
   verify seating and both M4 screws, and confirm the key prevents rotation
   during assembly. The pocket roof and base need a slicer/support review;
   hardware cavities must remain accessible for support removal.
5. After the physical accessory fit and those checks pass,
   regenerate the affected prototypes and print one matched lower/upper upright pair. Heat-set
   inserts using the accepted pilot, verify the rod path, and measure the
   assembled bearing planes before cutting rods.
6. Complete frame connections and bracing before testing a populated rack.
   Use ballast, with the proposed installed weight distribution and operating
   temperature. Inspect at seating, 1 hour, 24 hours, and 7 days. Record joint
   gaps, permanent lean, washer indentation, cracks, and nut/retainer movement.
   Define lateral and eccentric load magnitudes and allowable displacement
   before this test. Increasing torque alone does not demonstrate retained
   clamp force. Free-standing stability also requires the complete lower-rack
   contents, footprint, and center of gravity.

## Verification

- 39 offline parameter/seam tests pass in the pinned Nix development environment.
- Eight Build123d kernel regressions pass, 47 tests total.
- Both revised joint collision volumes are below 0.00001 mm³.
- The assembled corner accepts the modeled M5 rod and all 23 insert pilots.
- All 12 STLs (including the historical 234 mm gauge) are watertight single-component meshes; see
  [mesh checks](mesh_checks.json). All fit the 256 mm printer envelope.
- Build123d 0.13.0, Python 3.13; Ruff formatting/lint and Pyright pass.
- Repository `just fmt-check` passes. `just check-changed --select-only`
  routes to the full unrelated infrastructure suite because of existing dirty
  CI/justfile paths; that full suite was not run for this CAD audit.

The unchanged PRD v0.6.4 and older revision locks are historical design records.
This audit and the current parametric source describe the revised prototype.
