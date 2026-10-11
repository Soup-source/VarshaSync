#!/usr/bin/env python3
"""
VarshaSync engine.py - standalone maths check for the dashboard.

Calculates, from the command line and with the Python standard library only:
  1. Whiplash Index                 W = surge / (mean_preceding_14_days + 1.0)
  2. Sanitation Vulnerability Score S = 0.45*P_norm + 0.35*C_choke + 0.20*D_depression
  3. Rain-to-Reserve Autonomy       days = min(yield, tank) / (people * 135)

Every number printed here is computed with the same rules as index.html, so you
can cross-check the website against this script.

Examples
--------
  python engine.py demo
  python engine.py analyze
  python engine.py analyze --extra 1:3 4:2 2:2        # add live tickets per ward
  python engine.py whiplash --surge 94.5 --prev-mean 0.23
  python engine.py sanitation --surge 70 --complaints 8 --low-lying
  python engine.py autonomy --roof 120 --rain 60 --runoff 0.85 --people 4 --tank 3000
  python engine.py verify
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Constants (kept identical to index.html)
# --------------------------------------------------------------------------
WHIPLASH_CRITICAL = 20.0      # W > 20  -> Critical Flip Alert
WHIPLASH_MODERATE = 8.0       # 8 <= W <= 20 -> Moderate Inundation Advisory
PRECIP_MAX_MM = 100.0         # surge rainfall that maps to P_norm = 100
CHOKE_MAX = 10                # complaints that map to C_choke = 100
BOIL_WATER_THRESHOLD = 70.0   # S_risk above this -> boil-water advisory
FIRST_FLUSH_FACTOR = 0.90     # 10% of roof wash is discarded
LITRES_PER_PERSON_DAY = 135   # IS:1172 domestic standard
PRECEDING_DAYS = 14

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------
# Module 1 - Hydroclimatic whiplash (Swain et al., 2018)
# --------------------------------------------------------------------------
def mean_preceding(precip: list[float], surge_index: int, days: int = PRECEDING_DAYS) -> float:
    """Average daily rain over the `days` days before the surge day.

    A very low average means the ground has been dry and hard (a rainfall
    deficit), which is what makes a sudden downpour run off instead of soaking in.
    """
    window = precip[max(0, surge_index - days):surge_index]
    return sum(window) / len(window) if window else 0.0


def whiplash_index(surge_mm: float, prev_mean_mm: float) -> float:
    """W = surge / (mean preceding 14-day rain + 1.0)."""
    return surge_mm / (prev_mean_mm + 1.0)


def whiplash_level(w: float) -> str:
    if w > WHIPLASH_CRITICAL:
        return "CRITICAL FLIP ALERT (severe rain-flood risk)"
    if w >= WHIPLASH_MODERATE:
        return "MODERATE INUNDATION ADVISORY (low streets may fill)"
    return "SAFE BASELINE (normal rain)"


def whiplash_key(w: float) -> str:
    """Short level code used by the web dashboard: crit / mod / safe."""
    if w > WHIPLASH_CRITICAL:
        return "crit"
    return "mod" if w >= WHIPLASH_MODERATE else "safe"


# --------------------------------------------------------------------------
# Module 2 - Sanitation vulnerability (Curriero et al., 2001)
# --------------------------------------------------------------------------
def sanitation_score(surge_mm: float, complaints: int, low_lying: bool) -> dict:
    """S = 0.45*P + 0.35*C + 0.20*D, each part scaled to 0-100."""
    p = min(max(surge_mm / PRECIP_MAX_MM, 0.0), 1.0) * 100.0
    c = min(max(complaints / CHOKE_MAX, 0.0), 1.0) * 100.0
    d = 100.0 if low_lying else 0.0
    score = 0.45 * p + 0.35 * c + 0.20 * d
    return {
        "p_norm": p,
        "c_choke": c,
        "d_depression": d,
        "rain_part": 0.45 * p,
        "choke_part": 0.35 * c,
        "low_part": 0.20 * d,
        "score": score,
        "boil_water": score > BOIL_WATER_THRESHOLD,
    }


# --------------------------------------------------------------------------
# Module 4 - Rainwater harvesting autonomy (Campisano et al., 2017)
# --------------------------------------------------------------------------
def harvest_yield(roof_m2: float, rain_mm: float, runoff: float) -> float:
    """Yield (L) = roof area (m2) x rain (mm) x Cr x 0.90 first-flush factor."""
    return roof_m2 * rain_mm * runoff * FIRST_FLUSH_FACTOR


def autonomy(roof_m2: float, rain_mm: float, runoff: float, people: int, tank_l: float) -> dict:
    y = harvest_yield(roof_m2, rain_mm, runoff)
    daily = people * LITRES_PER_PERSON_DAY
    stored = min(y, tank_l)
    return {
        "yield_l": y,
        "daily_use_l": daily,
        "stored_l": stored,
        "overflow_l": max(0.0, y - tank_l),
        "spare_room_l": max(0.0, tank_l - y),
        "days": stored / daily if daily else 0.0,
    }


# --------------------------------------------------------------------------
# Roof materials (runoff coefficient Cr) used by Module 4
# --------------------------------------------------------------------------
RUNOFF = {"concrete": 0.85, "tile": 0.75, "metal": 0.90, "thatch": 0.60}


# --------------------------------------------------------------------------
# Module 5 - Citizen reports: automatic severity ranking (Goodchild, 2007)
# --------------------------------------------------------------------------
TICKET_BASE = {"sewage": 45, "sump": 40, "canal": 30, "drain": 25}
WARNING_WORDS = re.compile(
    r"overflow|smell|stink|foul|sewage|child|kid|school|hospital|fever|vomit|diarr|cholera|drink"
    r"|\u0ba4\u0bc1\u0bb0\u0bcd\u0ba8\u0bbe\u0bb1\u0bcd\u0bb1\u0bae\u0bcd|\u0b95\u0bc1\u0bb4\u0ba8\u0bcd\u0ba4\u0bc8|\u0baa\u0bb3\u0bcd\u0bb3\u0bbf|\u0bae\u0bb0\u0bc1\u0ba4\u0bcd\u0ba4\u0bc1\u0bb5\u0bae\u0ba9\u0bc8|\u0b95\u0bbe\u0baf\u0bcd\u0b9a\u0bcd\u0b9a\u0bb2\u0bcd"
    r"|\u0926\u0941\u0930\u094d\u0917\u0902\u0927|\u092c\u0926\u092c\u0942|\u092c\u091a\u094d\u091a\u0947|\u0938\u094d\u0915\u0942\u0932|\u0905\u0938\u094d\u092a\u0924\u093e\u0932|\u092c\u0941\u0916\u093e\u0930|\u0926\u0938\u094d\u0924",
    re.IGNORECASE,
)


def ticket_severity(ticket_type: str, people: int, description: str, ward_score: float) -> float:
    """0-100 danger score: problem type + 25% of the ward's current risk + people affected + warning words."""
    score = (
        TICKET_BASE.get(ticket_type, 20)
        + 0.25 * ward_score
        + min(max(people, 0), 200) / 200 * 15
        + (10 if WARNING_WORDS.search(description or "") else 0)
    )
    return min(max(score, 0.0), 100.0)


def severity_level(score: float) -> str:
    if score >= 70:
        return "crit"
    if score >= 50:
        return "high"
    return "med" if score >= 30 else "low"


# --------------------------------------------------------------------------
# File helpers
# --------------------------------------------------------------------------
def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_extra(items: list[str] | None) -> dict[int, int]:
    extra: dict[int, int] = {}
    for item in items or []:
        try:
            ward, count = item.split(":")
            extra[int(ward)] = int(count)
        except ValueError:
            sys.exit(f"Bad --extra value '{item}'. Use WARD:COUNT, for example 1:3")
    return extra


def find_surge(daily: list[dict]) -> tuple[int, float, float]:
    """Return (surge_index, surge_mm, mean_preceding_14d).

    If the file declares meta.surge_day we trust it, otherwise use the wettest day.
    """
    precip = [float(d["precip_mm"]) for d in daily]
    idx = max(range(len(precip)), key=lambda i: precip[i])
    return idx, precip[idx], mean_preceding(precip, idx)


# --------------------------------------------------------------------------
# Printing
# --------------------------------------------------------------------------
def rule(title: str = "") -> None:
    print("\n" + "=" * 64)
    if title:
        print(title)
        print("=" * 64)


def print_whiplash(surge: float, prev_mean: float) -> float:
    w = whiplash_index(surge, prev_mean)
    print(f"  Storm-day rain            : {surge:8.2f} mm")
    print(f"  Mean of preceding 14 days : {prev_mean:8.3f} mm/day")
    print(f"  Whiplash Index  W         : {w:8.2f}   = {surge:.2f} / ({prev_mean:.3f} + 1.0)")
    print(f"  Verdict                   : {whiplash_level(w)}")
    return w


def print_sanitation_table(surge: float, wards: list[dict], extra: dict[int, int]) -> list[str]:
    print(f"  Storm-day rain used: {surge:.1f} mm (P_norm = rain / {PRECIP_MAX_MM:.0f} mm, max 100)")
    print(f"  Complaints = baseline chokes + open reports + --extra tickets (C_choke = n / {CHOKE_MAX}, max 100)\n")
    header = f"  {'Ward':<20}{'Complaints':>11}{'Low?':>6}{'P':>8}{'C':>8}{'D':>6}{'S_risk':>9}   Advisory"
    print(header)
    print("  " + "-" * (len(header) - 2))
    advisory: list[str] = []
    for w in wards:
        complaints = w["baseline_chokes"] + w.get("seed_open_reports", 0) + extra.get(w["id"], 0)
        r = sanitation_score(surge, complaints, w["low_lying"])
        flag = "BOIL WATER" if r["boil_water"] else "-"
        if r["boil_water"]:
            advisory.append(f"Ward {w['id']} ({w['name']['en']})")
        print(
            f"  {w['id']}. {w['name']['en']:<17}{complaints:>11}{'yes' if w['low_lying'] else 'no':>6}"
            f"{r['rain_part']:>8.1f}{r['choke_part']:>8.1f}{r['low_part']:>6.1f}{r['score']:>9.1f}   {flag}"
        )
    print("\n  Columns P, C, D are the weighted parts (0.45*P + 0.35*C + 0.20*D); they add up to S_risk.")
    return advisory


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_whiplash(a: argparse.Namespace) -> int:
    rule("Module 1 - Sudden Weather Flip (Whiplash Index)")
    print_whiplash(a.surge, a.prev_mean)
    return 0


def cmd_sanitation(a: argparse.Namespace) -> int:
    rule("Module 2 - Dirty Water Alert (Sanitation Vulnerability Score)")
    r = sanitation_score(a.surge, a.complaints, a.low_lying)
    print(f"  P_norm (rain)       : {r['p_norm']:6.1f}  x 0.45 = {r['rain_part']:5.1f}")
    print(f"  C_choke (drains)    : {r['c_choke']:6.1f}  x 0.35 = {r['choke_part']:5.1f}")
    print(f"  D_depression (low)  : {r['d_depression']:6.1f}  x 0.20 = {r['low_part']:5.1f}")
    print(f"  S_risk              : {r['score']:6.1f} / 100")
    print("  Advisory            : " + ("BOIL-WATER ADVISORY (score above 70)" if r["boil_water"] else "None"))
    return 0


def cmd_autonomy(a: argparse.Namespace) -> int:
    rule("Module 4 - Saved Rainwater (Rain-to-Reserve Autonomy)")
    r = autonomy(a.roof, a.rain, a.runoff, a.people, a.tank)
    print(f"  Yield     : {a.roof:g} m2 x {a.rain:g} mm x {a.runoff:g} x {FIRST_FLUSH_FACTOR} = {r['yield_l']:,.0f} L")
    print(f"  Daily use : {a.people} people x {LITRES_PER_PERSON_DAY} L = {r['daily_use_l']:,.0f} L/day")
    print(f"  Stored    : min({r['yield_l']:,.0f}, {a.tank:,.0f}) = {r['stored_l']:,.0f} L")
    print(f"  Autonomy  : {r['days']:.2f} days")
    if r["overflow_l"] > 0:
        print(f"  Note      : about {r['overflow_l']:,.0f} L would overflow. A bigger tank would store more.")
    else:
        print(f"  Note      : the tank still has about {r['spare_room_l']:,.0f} L of room.")
    return 0


def cmd_analyze(a: argparse.Namespace) -> int:
    rain = load_json(Path(a.rain))
    wards = load_json(Path(a.wards))["wards"]
    daily = rain["daily"]
    idx, surge, prev_mean = find_surge(daily)

    rule(f"Analysis of {a.rain}")
    print(f"  Storm day found: day {daily[idx].get('day', idx + 1)}")
    dry_days = [d for d in daily[:idx] if d["precip_mm"] < 2.0]
    print(f"  Days before the storm with rain under 2 mm: {len(dry_days)} of {idx}")
    w = print_whiplash(surge, prev_mean)

    rule("Dirty-water risk by ward")
    advisory = print_sanitation_table(surge, wards, parse_extra(a.extra))
    print()
    if advisory:
        print("  BOIL-WATER ADVISORY for: " + "; ".join(advisory))
    else:
        print("  No ward is above the boil-water line (70).")
    if a.json:
        out = {"whiplash_index": w, "surge_mm": surge, "prev_mean_mm": prev_mean, "advisory_wards": advisory}
        print("\n" + json.dumps(out, indent=2))
    return 0


def cmd_demo(_: argparse.Namespace) -> int:
    """The same three presets as the website's demo buttons."""
    wards = load_json(HERE / "wards_data.json")["wards"]
    scenarios = [
        ("Whiplash Event", None, None, {}),
        ("Baseline Rain", 18.0, 4.0, {}),
        ("Gutter Emergency", 70.0, 6.0, {1: 3, 4: 2, 2: 2}),
    ]
    rain = load_json(HERE / "rainfall_data.json")["daily"]
    for name, surge, prev, extra in scenarios:
        rule(f"Scenario: {name}")
        if surge is None:
            _, surge, prev = find_surge(rain)
        print_whiplash(surge, prev)
        print()
        print_sanitation_table(surge, wards, extra)
    return 0


def cmd_verify(_: argparse.Namespace) -> int:
    """Self-test: known answers worked out by hand."""
    failures = 0

    def check(label: str, got, want, tol: float = 1e-6) -> None:
        nonlocal failures
        ok = (abs(got - want) <= tol) if isinstance(want, (int, float)) and not isinstance(want, bool) else got == want
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got!r}, expected {want!r}")
        failures += 0 if ok else 1

    rule("engine.py self-test")
    check("W(80, 3.0) = 20.0", whiplash_index(80, 3.0), 20.0)
    check("W = 20.0 is Moderate (not Critical)", whiplash_level(20.0).startswith("MODERATE"), True)
    check("W = 20.01 is Critical", whiplash_level(20.01).startswith("CRITICAL"), True)
    check("W = 8.0 is Moderate", whiplash_level(8.0).startswith("MODERATE"), True)
    check("W = 7.99 is Safe", whiplash_level(7.99).startswith("SAFE"), True)
    check("W(18, 4) = 3.6", whiplash_index(18, 4), 3.6)

    r = sanitation_score(70, 8, True)
    check("S(70 mm, 8 complaints, low-lying) = 79.5", r["score"], 79.5)
    check("  ...triggers boil-water advisory", r["boil_water"], True)
    check("S(18 mm, 5 complaints, low-lying) = 45.6", sanitation_score(18, 5, True)["score"], 45.6)
    check("S is capped at 100", sanitation_score(500, 99, True)["score"], 100.0)

    a = autonomy(120, 60, 0.85, 4, 3000)
    check("Yield = 120 x 60 x 0.85 x 0.9 = 5508 L", a["yield_l"], 5508.0)
    check("Daily use = 4 x 135 = 540 L", a["daily_use_l"], 540.0)
    check("Autonomy = 3000 / 540 days", a["days"], 3000 / 540)
    check("Overflow = 2508 L", a["overflow_l"], 2508.0)

    try:
        rain = load_json(HERE / "rainfall_data.json")["daily"]
        precip = [d["precip_mm"] for d in rain]
        check("rainfall_data.json has 30 days", len(precip), 30)
        check("rainfall_data.json: first 28 days all under 2 mm", all(p < 2.0 for p in precip[:28]), True)
        check("rainfall_data.json: storm day above 80 mm", max(precip) > 80.0, True)
        idx, surge, prev = find_surge(rain)
        check("rainfall_data.json: W is Critical", whiplash_index(surge, prev) > 20.0, True)
        wards = load_json(HERE / "wards_data.json")["wards"]
        check("wards_data.json has 5 wards", len(wards), 5)
    except FileNotFoundError as exc:
        print(f"  [SKIP] data file checks ({exc.filename} not found)")

    print(f"\n  {'All checks passed.' if failures == 0 else str(failures) + ' check(s) FAILED.'}")
    return 1 if failures else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="engine.py",
        description="VarshaSync formula engine: Whiplash Index, Sanitation Vulnerability Score, Rain-to-Reserve Autonomy.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    w = sub.add_parser("whiplash", help="Module 1: Whiplash Index for one storm")
    w.add_argument("--surge", type=float, required=True, help="rain on the storm day (mm)")
    w.add_argument("--prev-mean", type=float, required=True, help="mean daily rain of the 14 days before (mm)")
    w.set_defaults(func=cmd_whiplash)

    s = sub.add_parser("sanitation", help="Module 2: dirty-water risk for one ward")
    s.add_argument("--surge", type=float, required=True, help="rain on the storm day (mm)")
    s.add_argument("--complaints", type=int, required=True, help="active drain-choke complaints in the ward")
    s.add_argument("--low-lying", action="store_true", help="ward is in low-lying ground")
    s.set_defaults(func=cmd_sanitation)

    r = sub.add_parser("autonomy", help="Module 4: how long saved rainwater lasts")
    r.add_argument("--roof", type=float, required=True, help="roof area (m2)")
    r.add_argument("--rain", type=float, required=True, help="rainfall caught (mm)")
    r.add_argument("--runoff", type=float, default=0.85, help="runoff coefficient Cr (default 0.85)")
    r.add_argument("--people", type=int, required=True, help="number of occupants")
    r.add_argument("--tank", type=float, required=True, help="tank / sump capacity (litres)")
    r.set_defaults(func=cmd_autonomy)

    an = sub.add_parser("analyze", help="run Modules 1 and 2 on rainfall_data.json + wards_data.json")
    an.add_argument("--rain", default=str(HERE / "rainfall_data.json"))
    an.add_argument("--wards", default=str(HERE / "wards_data.json"))
    an.add_argument("--extra", nargs="*", metavar="WARD:COUNT", help="extra live tickets, e.g. 1:3 4:2")
    an.add_argument("--json", action="store_true", help="also print a JSON summary")
    an.set_defaults(func=cmd_analyze)

    d = sub.add_parser("demo", help="print the three dashboard demo scenarios")
    d.set_defaults(func=cmd_demo)

    v = sub.add_parser("verify", help="run self-tests with hand-checked answers")
    v.set_defaults(func=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

# --------------------------------------------------------------------------
# Compatibility bridge for backtest.py
# --------------------------------------------------------------------------
DRY_WINDOW = PRECEDING_DAYS
EXTREME_MM = 80.0

def detect_flip(vals: list[float]) -> dict:
    idx = len(vals) - 1
    surge = vals[idx]
    prev = mean_preceding(vals, idx)
    w = whiplash_index(surge, prev)
    lvl = whiplash_key(w)
    tier_map = {
        "crit": "RED monsoon flip",
        "mod": "ORANGE extreme rain" if surge > EXTREME_MM else "YELLOW watch",
        "safe": "GREEN normal"
    }
    return {"w": w, "surge": surge, "prev_mean": prev, "tier": tier_map[lvl]}