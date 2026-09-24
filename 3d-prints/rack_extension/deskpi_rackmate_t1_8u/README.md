# DeskPi RackMate T1 extension: 8U height, 7U mounting capacity

Parametric Build123d source per PRD `deskpi_rackmate_t1_8u_extension_prd_v0.6.4.md`.
Preliminary only: solids exist for fit checks, but the audit gates below
block any production claim.

## Layout

* `params.py`: source of truth (measured T1 values + Path A stack math)
* `model.py`: Build123d solids plus `--export` and `--audit`
* `test_model.py`: offline unit tests, no CAD kernel required

## Audited prototype revision (2026-09-24)

Use the matched files in [`../audit_rev2/`](../audit_rev2/). The root-level
STLs predate this audit. Do not mix their mounts or uprights with revision 2.
The tested corner mount keeps its T1 seating, locating lips, M4 hole positions,
and screw-seat height, but the revised upper shoulder and nut pocket need a
new physical fit check. Uprights had not been printed at the time of this audit.

See [the audit](../audit_rev2/AUDIT.md), [actual CAD sections](../audit_rev2/corner_sections.png),
and [assembled corner STEP](../audit_rev2/corner_assembly.step).

The mount shoulder is now at 36.35 mm and the middle splice at 169.85 mm.
Every insert boss belongs wholly to one part. Spigots clear the insert bosses,
the top washer loads down onto its bearing seat, and the bottom retainer has
an external key. The assembled lid plane remains 355.6 mm.

The user measured approximately **236 mm** between corresponding left/right
rack-hole centers. The CAD now uses that spacing, with insert centers at
X=22.5 and 258.5 mm in the symmetric 281 mm envelope. Tie rods retain their
original axes. Bosses are clipped to the column envelope to preserve the
223 mm clear opening. The earlier 234 mm gauge is no longer required.
The existing Wyse cradle still uses 234 mm; confirm its slot clearance against
the physical rack before changing that separate design.

Print the revised mount and splice fit-coupon pairs, then the top hardware
coupon, before committing to full uprights. The small coupons check geometry;
they do not establish a payload rating. Use the same PETG profile and upright
orientation intended for the matching full part. Verify actual screw access,
washer/nut insertion, and removal of any supports from the hardware pockets.

## Phase 2 corner prototype

Print `phase2_corner_fit_coupon.stl` first to verify the shortened lips clear
the transverse T1 member while both M4 holes remain aligned.

`phase2_corner_mount.stl` contains the accepted T1 interface, reinforced lower
end block, underside-loadable M5 hardware pocket, and a keyed upright spigot.
Its locating lips stop clear of the transverse T1 member. Load the washer and
nyloc from below, then install `phase2_bottom_nut_retainer.stl` flush around the
nut before placing the mount on the T1. Both M4 attachment screws remain
accessible from above. `phase2_lower_upright.stl` slides over the mount spigot
and provides the male middle splice.
`phase2_upper_upright.stl` contains the mating socket, upper upright, and
reinforced upper end block. These are fit-test parts, not production approval.

The mount uses M4 x 16 screws against 11.5 mm recessed head seats, providing
4.5 mm engagement in the measured T1 threads.

Print the mount first and verify both M4 screws remain accessible. Then print
the lower upright vertically from its mount-socket end. Print the upper upright
vertically from its seam end only after both lower joints pass inspection.

## M5 rack-insert pilot coupon

`m5_insert_pilot_coupon.stl` tests the measured 7.9 mm long, 6.7 mm maximum-OD
M5 inserts in blind 9.3 mm holes. With the 3 mm marker hole at the left, the
three pilots are 5.8, 6.0, and 6.2 mm from left to right. Print flat in the same
PETG profile intended for the uprights. Do not print uprights until one pilot is
physically accepted.

Physical result: 6.2 mm accepted on 2026-09-24. The 5.8 and 6.0 mm pilots would
not accept the insert. Production rack bosses use M5 x 0.8 inserts, 9.3 mm boss
depth, and a 6.2 mm pilot.

Print `m5_rack_boss_coupon.stl` upright before either full upright. It reproduces
the production wall, horizontal boss, pilot, and M5 tie-rod clearance. Accept it
only if the insert sets flush without wall damage, holds an M5 screw, and leaves
the tie-rod channel clear.

After the single-boss coupon passes, print `m5_rack_pitch_coupon.stl` upright.
It contains one complete production three-hole rack pattern with the actual
column wall, M5 bosses, and tie-rod channel. Verify center spacing, three insert
fits, equipment-screw access, and straightness before either full upright.

## Iterate

```sh
PYTHONPATH=3d-prints/rack_extension python -m unittest deskpi_rackmate_t1_8u.test_model deskpi_rackmate_t1_8u.test_seam -v
LD_LIBRARY_PATH=<nix GL libs> uv run --python 3.13 --with build123d==0.13.0 \
  python -m deskpi_rackmate_t1_8u.model --prototype-only --export-dir 3d-prints/rack_extension/audit_rev2 --audit
```

NixOS note: OCP needs system GL/X C libraries. Resolve them from the
pinned nixpkgs (`libglvnd`, `libx11`, `gcc-lib`, `zlib`, `expat`) and
export via `LD_LIBRARY_PATH`; see the adjacent `wyse5070_extended_t1`
README for the same pattern.

The explicit CAD regression suite is `deskpi_rackmate_t1_8u.test_cad` in the
same Build123d environment. It checks actual solids, joint intersections,
washer insertion, full-column rod passage, insert pilots, shoulder contact,
and retainer key engagement. Geometry checks do not prove printed strength.

## Blocking production gates

G1 seam drawing, G2 corner end-block section, G3 interference sections,
G4 creep/retained-clamp validation, G5 final rod length from measured
seats, G6 anti-tip hardware, G7 full frame connections, rear bracing, lid/handle mounts, four-corner placement,
and rack alignment/pitch. The historical M-series dimensional records are
closed in `params.py`, but that does not close these assembly checks.
`model.audit_gates()` is the machine-readable list.
