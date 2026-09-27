"""Summarize Orca results and plot selected actual toolpath layers."""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path

FIELDS = re.compile(r"([XYZEIJ])(-?\d*\.?\d+)")


def layers(path: Path) -> dict:
    output = defaultdict(list)
    x = y = 0.0
    height = None
    feature = ""
    for line in path.open():
        if line.startswith("; Z_HEIGHT:"):
            height = float(line.split(":")[1])
        elif line.startswith("; FEATURE:"):
            feature = line.split(":", 1)[1].strip()
        elif line.startswith(("G0 ", "G1 ", "G2 ", "G3 ")):
            fields = {k: float(v) for k, v in FIELDS.findall(line.split(";")[0])}
            nx, ny = fields.get("X", x), fields.get("Y", y)
            if height is not None and fields.get("E", 0) > 0 and (nx != x or ny != y):
                if line.startswith(("G2 ", "G3 ")):
                    cx, cy = x + fields.get("I", 0), y + fields.get("J", 0)
                    start = math.atan2(y - cy, x - cx)
                    end = math.atan2(ny - cy, nx - cx)
                    sweep = (end - start) % (2 * math.pi)
                    if line.startswith("G2 "):
                        sweep -= 2 * math.pi
                    count = max(2, math.ceil(abs(sweep) / 0.08))
                    radius = math.hypot(x - cx, y - cy)
                    previous = (x, y)
                    for i in range(1, count + 1):
                        angle = start + sweep * i / count
                        point = (
                            cx + radius * math.cos(angle),
                            cy + radius * math.sin(angle),
                        )
                        output[height].append((previous, point, feature))
                        previous = point
                else:
                    output[height].append(((x, y), (nx, ny), feature))
            x, y = nx, ny
    return output


def main() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    root = Path("rev4")
    report = {}
    selections = {
        "m4_pilot_coupon": (0.2, 9.0, 12.0),
        "lower_front_left": (0.2, 11.8, 12.2),
        "upper_front_left": (0.2, 21.8, 22.2),
        "top_side_beam_left": (0.2, 6.2, 19.0),
        "mount_front_left": (0.2, 11.8, 12.2),
        "lid_fit_coupon": (0.2, 6.2, 19.0),
    }
    for project in sorted((root / "slicing").glob("*/*.3mf")):
        name = project.stem
        with zipfile.ZipFile(project) as archive:
            xml = ET.fromstring(archive.read("Metadata/slice_info.config"))
            meta = {
                item.attrib["key"]: item.attrib["value"]
                for item in xml.findall(".//plate/metadata")
            }
            warnings = [item.attrib for item in xml.findall(".//warning")]
        paths = layers(project.parent / "plate_1.gcode")
        features = sorted(
            {feature for layer in paths.values() for _, _, feature in layer}
        )
        report[name] = {
            "grams": float(meta["weight"]),
            "minutes": round(float(meta["prediction"]) / 60, 1),
            "outside_bed": meta.get("outside"),
            "support_used": meta.get("support_used"),
            "warnings": warnings,
            "features": features,
            "layers": len(paths),
        }
        if name not in selections:
            continue
        fig, axes = plt.subplots(1, 3, figsize=(14, 5))
        for axis, requested in zip(axes, selections[name], strict=True):
            height = min(paths, key=lambda value: abs(value - requested))
            segments, colors = [], []
            for start, end, feature in paths[height]:
                if feature == "Custom":
                    continue
                segments.append([start, end])
                colors.append(
                    "#0077bb"
                    if "Support" in feature
                    else "#ee7733"
                    if "Bridge" in feature or "Overhang" in feature
                    else "#555555"
                )
            axis.add_collection(LineCollection(segments, colors=colors, linewidths=0.5))
            axis.autoscale()
            axis.set_aspect("equal")
            axis.set_title(f"Z = {height:.2f} mm")
            axis.set_xlabel("X (mm)")
            axis.set_ylabel("Y (mm)")
        fig.suptitle(
            f"{name}: actual Orca paths\nBlue: supports; orange: bridges/overhangs; gray: other extrusion"
        )
        fig.tight_layout()
        fig.savefig(root / "slicing" / f"{name}_layers.png", dpi=150)
        plt.close(fig)
    (root / "slicing" / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    for name, data in report.items():
        print(
            name,
            data["grams"],
            "g",
            data["minutes"],
            "min",
            "support",
            data["support_used"],
            "warnings",
            len(data["warnings"]),
        )


if __name__ == "__main__":
    main()
