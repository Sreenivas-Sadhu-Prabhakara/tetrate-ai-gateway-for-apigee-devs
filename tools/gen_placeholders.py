#!/usr/bin/env python3
"""tools/gen_placeholders.py — emit abstract Mayari "moon" hero placeholders.

Text-free, mood-only orientation art (engineer-audience rule: hero images never
carry technical information). The real PNGs are generated later from
assets/heroes/PROMPTS.md and dropped in beside these; until then the site renders
these SVGs. Re-run any time:

    python3 tools/gen_placeholders.py

Output: assets/heroes/splash.svg and part-1.svg … part-7.svg

Mayari is the moon goddess; the motif is a cool moon over a midnight horizon with a
scatter of stars, the inverse of the companion Apigee course's Apolaki sun.
"""

from pathlib import Path

HEROES = Path(__file__).resolve().parent.parent / "assets" / "heroes"

# (filename, width, height, moon centre x%, moon radius, phase 0..1 (0=full,>0 crescent), star count)
SLOTS = [
    ("splash", 1200, 400, 0.74, 150, 0.0, 60),
    ("part-1", 1200, 300, 0.80, 95, 0.55, 26),
    ("part-2", 1200, 300, 0.22, 90, 0.40, 30),
    ("part-3", 1200, 300, 0.82, 100, 0.25, 28),
    ("part-4", 1200, 300, 0.28, 85, 0.10, 24),
    ("part-5", 1200, 300, 0.78, 95, 0.0, 34),
    ("part-6", 1200, 300, 0.24, 90, 0.30, 26),
    ("part-7", 1200, 300, 0.50, 120, 0.0, 44),
]


def stars(w, h, count, avoid_cx, avoid_cy, avoid_r):
    # deterministic pseudo-scatter (no RNG, keeps regen reproducible)
    out = []
    for i in range(count):
        x = int((i * 97 + 31) % w)
        y = int((i * 53 + 17) % int(h * 0.72))
        # skip stars that would land on the moon
        dx, dy = x - avoid_cx, y - avoid_cy
        if dx * dx + dy * dy < (avoid_r + 14) ** 2:
            continue
        r = 0.6 + (i % 4) * 0.45
        op = 0.25 + 0.5 * ((i * 7) % 5) / 4
        col = "#EAF2FF" if i % 5 else "#9FE0FF"
        out.append(f'<circle cx="{x}" cy="{y}" r="{r:.2f}" fill="{col}" opacity="{op:.2f}"/>')
    return "\n  ".join(out)


def make(name, w, h, cxf, r, phase, star_count):
    cx = int(w * cxf)
    cy = int(h * 0.40)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid slice" role="img" aria-label="Abstract Mayari moon motif">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#0B0E16"/>
      <stop offset="60%" stop-color="#0D1220"/>
      <stop offset="100%" stop-color="#10182B"/>
    </linearGradient>
    <radialGradient id="moon" cx="42%" cy="38%" r="60%">
      <stop offset="0%" stop-color="#F2F6FF"/>
      <stop offset="45%" stop-color="#CFDBF2"/>
      <stop offset="80%" stop-color="#8E9FC6"/>
      <stop offset="100%" stop-color="#5A6890"/>
    </radialGradient>
    <radialGradient id="halo" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#8FB3FF" stop-opacity="0.30"/>
      <stop offset="55%" stop-color="#4FE3C1" stop-opacity="0.10"/>
      <stop offset="100%" stop-color="#4FE3C1" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{w}" height="{h}" fill="url(#sky)"/>
  <g>
  {stars(w, h, star_count, cx, cy, r)}
  </g>
  <circle cx="{cx}" cy="{cy}" r="{int(r * 1.9)}" fill="url(#halo)"/>
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#moon)"/>
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#EAF2FF" stroke-width="1" opacity="0.35"/>
  {_crescent(cx, cy, r, phase)}
  <!-- horizon haze -->
  <rect x="0" y="{int(h * 0.76)}" width="{w}" height="{int(h * 0.24)}" fill="#070910" opacity="0.6"/>
</svg>
"""
    (HEROES / f"{name}.svg").write_text(svg, encoding="utf-8")


def _crescent(cx, cy, r, phase):
    """A shadow disc offset to the right carves a crescent; phase 0 = full moon."""
    if phase <= 0.01:
        # full moon — a couple of faint craters instead
        return (
            f'<circle cx="{cx - r//3}" cy="{cy - r//4}" r="{max(3, r//8)}" fill="#7E8DB8" opacity="0.30"/>'
            f'<circle cx="{cx + r//4}" cy="{cy + r//5}" r="{max(2, r//11)}" fill="#7E8DB8" opacity="0.25"/>'
        )
    offset = int(r * (0.7 + phase))
    return f'<circle cx="{cx + offset}" cy="{cy - int(r*0.12)}" r="{r}" fill="#0D1220"/>'


def main():
    HEROES.mkdir(parents=True, exist_ok=True)
    for slot in SLOTS:
        make(*slot)
    print(f"Wrote {len(SLOTS)} hero placeholders into {HEROES}")


if __name__ == "__main__":
    main()
