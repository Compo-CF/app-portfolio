"""Turn a world GeoJSON into the compact SVG the dashboard draws.

Run once by hand; the output is committed. Coastlines do not change often
enough to justify fetching 250KB of them on every page load, and the page
deliberately has no CDN.

    python scripts/build_map.py path/to/world.geojson

Three things happen here:

  * **Projection.** Equirectangular, which is the honest choice for this job.
    It is not area-preserving, but nothing here depends on comparing the size
    of countries — the circles carry the numbers. Anything fancier would be
    decoration bought with complexity.

  * **Simplification.** Coordinates are snapped to a grid and consecutive
    duplicates dropped. At a quarter of a degree the outlines still read at
    the size this is drawn, and the file falls by roughly four fifths.

  * **Re-keying.** The source identifies countries by ISO alpha-3; Apple's
    sales reports use alpha-2. Translated here, once, so the page can match a
    country code directly rather than carry a lookup table.
"""

import json
import sys
from pathlib import Path

import pycountry

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "world.json"

# Pixels per degree. 4 gives a 1440x720 canvas — enough that the outlines read
# at full width, coarse enough that rounding removes most of the points.
SCALE = 4
WIDTH, HEIGHT = 360 * SCALE, 180 * SCALE

# Rings smaller than this in projected units are dropped: islands that would
# render as a single pixel, at the cost of a few hundred bytes each.
MIN_RING_AREA = 6


def project(lon, lat):
    return round((lon + 180) * SCALE), round((90 - lat) * SCALE)


def ring_path(coords):
    """One closed ring as an SVG path fragment, or None if too small."""
    points = []
    for lon, lat in coords:
        p = project(lon, lat)
        if not points or p != points[-1]:      # drop points rounding merged
            points.append(p)
    if len(points) < 4:
        return None

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if (max(xs) - min(xs)) * (max(ys) - min(ys)) < MIN_RING_AREA:
        return None

    head = f"M{points[0][0]} {points[0][1]}"
    tail = "".join(f"L{x} {y}" for x, y in points[1:])
    return head + tail + "Z"


def feature_path(geometry):
    rings = []
    kind = geometry.get("type")
    if kind == "Polygon":
        rings = geometry.get("coordinates", [])
    elif kind == "MultiPolygon":
        rings = [r for poly in geometry.get("coordinates", []) for r in poly]
    return "".join(filter(None, (ring_path(r) for r in rings)))


def alpha2(feature):
    """ISO alpha-2, or None for anything ISO does not recognise.

    Disputed and non-standard entries exist in most world datasets. They are
    skipped rather than guessed at, because a wrong code would silently paint
    the wrong country.
    """
    code = feature.get("id") or ""
    try:
        match = pycountry.countries.get(alpha_3=code)
    except LookupError:
        return None
    return match.alpha_2 if match else None


def main(source):
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    paths, skipped = {}, []

    for feature in data.get("features", []):
        code = alpha2(feature)
        if not code:
            skipped.append(feature.get("id") or feature.get("properties", {}).get("name"))
            continue
        d = feature_path(feature.get("geometry") or {})
        if d:
            paths[code] = d

    # Names travel with the shapes so the page can label a country without
    # shipping a second lookup table that could drift out of step with them.
    names = {}
    for code in paths:
        match = pycountry.countries.get(alpha_2=code)
        if match:
            names[code] = getattr(match, "common_name", None) or match.name

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"viewBox": f"0 0 {WIDTH} {HEIGHT}",
                    "names": names, "paths": paths},
                   separators=(",", ":")),
        encoding="utf-8",
    )
    size = OUT.stat().st_size / 1024
    print(f"wrote {OUT} — {len(paths)} countries, {size:.0f} KB")
    if skipped:
        print(f"  skipped {len(skipped)} without an ISO alpha-2: "
              f"{', '.join(map(str, skipped[:8]))}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python scripts/build_map.py <world.geojson>")
    main(sys.argv[1])
