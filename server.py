#!/usr/bin/env python3
"""
VarshaSync server.py - the Python backend.

All calculations (Whiplash Index, Sanitation Vulnerability Score, rainwater
autonomy, ticket ranking) and all stored data (rainfall, wards, citizen
reports) live here. The web page (index.html: HTML, CSS, JavaScript) only
shows what this server sends back.

Uses only the Python standard library, so nothing needs installing.

Run:
    python server.py                 then open http://localhost:8000
    python server.py --port 9000
    python server.py --open          (also opens your browser)
"""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import engine

HERE = Path(__file__).resolve().parent
RAIN = engine.load_json(HERE / "rainfall_data.json")
WARDS = engine.load_json(HERE / "wards_data.json")
WARD_LIST = WARDS["wards"]
WARD_IDS = {w["id"] for w in WARD_LIST}
MAX_BODY = 64 * 1024

PATTERN = [1.2, 0.3, 0.9, 1.5, 0.0, 0.7, 1.8, 0.4, 1.1, 0.6, 1.3, 0.2, 1.6, 0.9,
           0.4, 1.6, 0.8, 0.2, 1.2, 0.6, 1.9, 0.3, 0.9, 1.4, 0.5, 1.1, 0.7, 2.4]


def clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def generated_series(mean: float, surge: float) -> list[dict]:
    base = clamp(40 - mean * 1.6, 27, 40)
    out = [{"p": round(mean * PATTERN[i], 2), "t": round(base + math.sin(i * 0.7) * 1.3, 1)} for i in range(28)]
    out.append({"p": round(surge, 2), "t": round(base - 11, 1)})
    out.append({"p": round(surge * 0.6, 2), "t": round(base - 12, 1)})
    return out


def file_series() -> tuple[list[dict], int]:
    series = [{"p": d["precip_mm"], "t": d["temp_max_c"]} for d in RAIN["daily"]]
    idx, _, _ = engine.find_surge(RAIN["daily"])
    return series, idx


class Ledger:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.tickets: list[dict] = []
        self.seq = 0
        self.last_surge = file_series()[0][file_series()[1]]["p"]
        self.reset("whiplash")

    @staticmethod
    def _mk(tid: int, kind: str, ward: int, people: int, desc: str, minutes_ago: int, status: str = "pending") -> dict:
        return {"id": tid, "type": kind, "ward": ward, "people": people, "desc": desc,
                "ts": int((time.time() - minutes_ago * 60) * 1000), "status": status}

    def reset(self, scenario: str) -> None:
        with self.lock:
            mk = self._mk
            self.tickets = [
                mk(1, "sewage", 4, 60, "Sewage is overflowing near the school lane and smells bad", 45),
                mk(2, "canal", 1, 25, "Canal near the bridge is full of plastic", 130),
                mk(3, "drain", 2, 10, "Drain grate blocked with leaves", 300),
                mk(4, "sump", 5, 35, "Water from the community sump looks brown", 620),
                mk(5, "drain", 3, 8, "Small blockage outside the temple", 2000, "resolved"),
            ]
            self.seq = 5
            if scenario == "gutter":
                extra = [
                    ("sewage", 1, 80, "Sewage overflow at the riverside lane, strong smell", 12),
                    ("canal", 1, 40, "Canal is overflowing into homes", 20),
                    ("drain", 1, 15, "Main drain blocked by plastic", 31),
                    ("sewage", 4, 90, "Manhole is overflowing near the school, children walking through it", 9),
                    ("sump", 4, 50, "Sump water smells bad after the rain", 25),
                    ("drain", 2, 20, "Market drain is blocked with waste", 40),
                    ("canal", 2, 12, "Canal behind the bus stand is full", 55),
                ]
                for kind, ward, people, desc, mins in extra:
                    self.seq += 1
                    self.tickets.append(mk(self.seq, kind, ward, people, desc, mins))

    def pending_in(self, ward_id: int) -> int:
        return sum(1 for t in self.tickets if t["ward"] == ward_id and t["status"] == "pending")


LEDGER = Ledger()


def ward_scores(surge: float, custom_wards: list[dict] | None = None) -> list[dict]:
    target_wards = custom_wards if custom_wards is not None else WARD_LIST
    rows = []
    for w in target_wards:
        complaints = w["baseline_chokes"] + LEDGER.pending_in(w["id"])
        r = engine.sanitation_score(surge, complaints, w["low_lying"])
        rows.append({"id": w["id"], "complaints": complaints, "rain": r["rain_part"], "choke": r["choke_part"],
                     "low": r["low_part"], "score": r["score"], "boil": r["boil_water"]})
    return rows


def rank_tickets(surge: float, custom_wards: list[dict] | None = None) -> list[dict]:
    by_ward = {r["id"]: r["score"] for r in ward_scores(surge, custom_wards)}
    out = []
    for t in LEDGER.tickets:
        ward_score = by_ward.get(t["ward"], 40.0)
        sev = engine.ticket_severity(t["type"], t["people"], t["desc"], ward_score)
        out.append(dict(t, sev=sev, level=engine.severity_level(sev)))
    return out


def analyze(payload: dict) -> dict:
    inp = payload.get("input") or {}
    if inp.get("scenario") == "whiplash":
        series, idx = file_series()
    else:
        mean = clamp(float(inp.get("mean", 4.0)), 0, 10)
        surge_in = clamp(float(inp.get("surge", 18.0)), 0, 150)
        series, idx = generated_series(mean, surge_in), 28

    precip = [d["p"] for d in series]
    surge = precip[idx]
    prev_mean = engine.mean_preceding(precip, idx)
    w = engine.whiplash_index(surge, prev_mean)
    LEDGER.last_surge = surge

    tank_in = payload.get("tank") or {}
    rt = tank_in.get("rt", "concrete")
    cr = engine.RUNOFF.get(rt, engine.RUNOFF["concrete"])
    a = engine.autonomy(
        clamp(float(tank_in.get("roof", 120)), 1, 5000),
        clamp(float(tank_in.get("rain", 60)), 0, 1000),
        cr,
        int(clamp(float(tank_in.get("occ", 4)), 1, 200)),
        clamp(float(tank_in.get("cap", 3000)), 1, 1_000_000),
    )

    wards = ward_scores(surge)
    return {
        "series": series,
        "metrics": {"idx": idx, "from": max(0, idx - engine.PRECEDING_DAYS), "surge": surge,
                    "mean": prev_mean, "W": w, "level": engine.whiplash_key(w)},
        "wards": wards,
        "advisory": [r["id"] for r in wards if r["boil"]],
        "tank": {"y": a["yield_l"], "daily": a["daily_use_l"], "stored": a["stored_l"],
                 "over": a["overflow_l"], "room": a["spare_room_l"], "days": a["days"]},
        "tickets": rank_tickets(surge),
    }


def fetch_nearby_localities(lat: float, lon: float) -> list[dict]:
    """Dynamically finds the current locality and 4 surrounding zones using reverse geocoding."""
    import urllib.parse

    # Probe current position and 4 surrounding compass directions (~3-6 km away)
    probes = [
        (lat, lon, "Central Zone"),
        (lat + 0.035, lon, "North Zone"),
        (lat - 0.035, lon, "South Zone"),
        (lat, lon + 0.040, "East Zone"),
        (lat, lon - 0.040, "West Zone"),
    ]

    discovered = []
    seen = set()

    for idx, (plat, plon, fallback_dir) in enumerate(probes, start=1):
        name_en = f"{fallback_dir} (Ward {idx})"
        url = (
            f"https://api.bigdatacloud.net/data/reverse-geocode-client?"
            f"latitude={plat:.5f}&longitude={plon:.5f}&localityLanguage=en"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "VarshaSync/1.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                res = json.load(r)
                # Pick the most specific geographic name available
                cand = res.get("locality") or res.get("city") or res.get("principalSubdivision") or ""
                district = res.get("localityInfo", {}).get("administrative", [{}])
                sub = ""
                if len(district) > 2:
                    sub = district[2].get("name", "")
                
                full = f"{cand} ({sub})".strip(" ()") if cand and sub and cand != sub else (cand or sub)
                if full and full not in seen:
                    name_en = f"{full} (W-{idx})"
                    seen.add(full)
        except Exception:
            pass

        is_low = (idx in (1, 4))
        discovered.append({
            "id": idx,
            "name": {"en": name_en, "ta": name_en, "hi": name_en},
            "elevation_m": round(max(2.0, 16.0 - (idx * 2.3)), 1),
            "low_lying": is_low,
            "population": 22000 + (idx * 4100),
            "drainage": {
                "type": "open_channel" if is_low else "covered_drain",
                "length_km": 4.5 + idx * 0.5,
                "capacity_mm_per_hr": 25 if is_low else 45,
                "last_desilted_months_ago": 6 + idx
            },
            "baseline_chokes": 2 + (idx % 3),
            "seed_open_reports": 0
        })

    return discovered

    for idx, (plat, plon, fallback) in enumerate(probes, start=1):
        url = f"https://geocoding-api.open-meteo.com/v1/search?latitude={plat:.4f}&longitude={plon:.4f}&count=1"
        name_en = f"{fallback} (Zone {idx})"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "VarshaSync/1.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                res = json.load(r).get("results")
                if res and res[0].get("name"):
                    c_name = res[0]["name"]
                    c_admin = res[0].get("admin2") or res[0].get("admin1") or ""
                    full = f"{c_name}, {c_admin}".strip(", ")
                    if full not in seen:
                        name_en = full
                        seen.add(full)
        except Exception:
            pass

        is_low = (idx in (1, 4))
        discovered.append({
            "id": idx,
            "name": {"en": name_en, "ta": name_en, "hi": name_en},
            "elevation_m": round(max(2.0, 14.0 - (idx * 2.0)), 1),
            "low_lying": is_low,
            "population": 16000 + (idx * 3000),
            "drainage": {"type": "covered_drain" if not is_low else "open_channel", "last_desilted_months_ago": 6 + idx},
            "baseline_chokes": 2 + (idx % 4),
            "seed_open_reports": 0
        })

    return discovered


def analyze_live(data: dict) -> dict:
    lat = float(data.get("lat", 13.08))
    lon = float(data.get("lon", 80.27))

    # Read dynamic tank parameters from frontend sliders (with defaults)
    tank_cfg = data.get("tank", {})
    roof_area = float(tank_cfg.get("roof", 120))
    occupants = int(tank_cfg.get("people", 4))
    tank_cap = float(tank_cfg.get("cap", 3000))

    # True Live API: Fetches preceding 14 days up to today's live precipitation
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        f"&past_days=14&forecast_days=1&daily=precipitation_sum,temperature_2m_max&timezone=auto"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "VarshaSyncLive/2.0 (resilience-monitor)"}
    )

    custom_series = []
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            m_data = json.load(r)["daily"]
            p_list = [float(v) if v is not None else 0.0 for v in m_data.get("precipitation_sum", [])]
            t_list = [float(v) if v is not None else 32.0 for v in m_data.get("temperature_2m_max", [])]
            custom_series = [{"p": round(p, 2), "t": round(t, 1)} for p, t in zip(p_list, t_list)]
    except Exception as e:
        print(f"[Warning] Live weather fetch failed ({e}). Defaulting to current fair-weather baseline.")
        custom_series = [{"p": 0.0, "t": 32.5} for _ in range(14)]
        custom_series.append({"p": 0.0, "t": 33.0})

    idx = len(custom_series) - 1
    surge = custom_series[idx]["p"]
    prev_mean = engine.mean_preceding([d["p"] for d in custom_series], idx)
    
    # Live WPS Calculation
    w = engine.whiplash_index(surge, prev_mean) if hasattr(engine, 'whiplash_index') else round(surge / max(prev_mean, 0.1), 1)
    LEDGER.last_surge = surge

    # Locality scoring
    try:
        dynamic_wards = fetch_nearby_localities(lat, lon)
        if not dynamic_wards:
            raise ValueError("Empty localities list")
    except Exception as e:
        print(f"[Warning] Localities fetch failed ({e}). Using regional defaults.")
        dynamic_wards = [
            {"id": "w1", "name": "Sector 1 - Central Zone", "elev": 15, "soil": "clay", "drain": "fair", "septic": 35, "pop": 85000},
            {"id": "w2", "name": "Sector 2 - Lowland Canal", "elev": 4, "soil": "alluvial", "drain": "poor", "septic": 75, "pop": 110000},
            {"id": "w3", "name": "Sector 3 - Outer Residential", "elev": 22, "soil": "sandy loam", "drain": "good", "septic": 25, "pop": 60000},
            {"id": "w4", "name": "Sector 4 - Commercial Market", "elev": 10, "soil": "clay loam", "drain": "fair", "septic": 50, "pop": 95000},
        ]

    calc_wards = ward_scores(surge, dynamic_wards)
    
    # Calculate autonomy dynamically with slider values
    runoff_coeff = engine.RUNOFF.get("concrete", 0.85) if hasattr(engine, 'RUNOFF') and isinstance(engine.RUNOFF, dict) else 0.85
    a = engine.autonomy(roof_area, surge, runoff_coeff, occupants, tank_cap)

    return {
        "series": custom_series,
        "metrics": {
            "idx": idx,
            "from": max(0, idx - 14),
            "surge": surge,
            "mean": prev_mean,
            "W": w,
            "level": engine.whiplash_key(w) if hasattr(engine, 'whiplash_key') else ("Nominal" if w < 3 else "Warning")
        },
        "dynamic_wards": dynamic_wards,
        "wards": calc_wards,
        "advisory": [r["id"] for r in calc_wards if r.get("boil", False)],
        "tank": {
            "y": a["yield_l"], "daily": a["daily_use_l"], "stored": a["stored_l"],
            "over": a["overflow_l"], "room": a["spare_room_l"], "days": a["days"]
        },
        "tickets": rank_tickets(surge, dynamic_wards),
    }
    
def run_backtest(data: dict) -> dict:
    place = str(data.get("place", "Chennai")).strip()
    start_date = str(data.get("start", "2023-11-01")).strip()
    end_date = str(data.get("end", "2023-11-30")).strip()

    parts = place.replace(" ", "").split(",")
    lat, lon, label = None, None, None
    if len(parts) == 2:
        try:
            lat, lon = float(parts[0]), float(parts[1])
            label = f"({lat:.2f}, {lon:.2f})"
        except ValueError:
            pass

    if lat is None:
        geo_url = "https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(place)
        req_geo = urllib.request.Request(geo_url, headers={"User-Agent": "VarshaSync/1.0"})
        with urllib.request.urlopen(req_geo, timeout=15) as r:
            g_res = json.load(r).get("results")
        if not g_res:
            raise ValueError(f"Place '{place}' not found. Try a major city name.")
        lat, lon = g_res[0]["latitude"], g_res[0]["longitude"]
        label = f"{g_res[0]['name']}, {g_res[0].get('admin1', '')}"

    s_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    e_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    if e_dt <= s_dt:
        raise ValueError("End date must be after Start date.")

    buf_start = s_dt - timedelta(days=15)
    arc_url = (
        f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
        f"&start_date={buf_start}&end_date={e_dt}&daily=precipitation_sum,temperature_2m_max&timezone=auto"
    )
    req_arc = urllib.request.Request(arc_url, headers={"User-Agent": "VarshaSync/1.0"})
    with urllib.request.urlopen(req_arc, timeout=20) as r:
        arc_data = json.load(r)["daily"]

    dates = arc_data["time"]
    vals = [v if v is not None else 0.0 for v in arc_data["precipitation_sum"]]

    rows = []
    for i in range(14, len(vals)):
        if dates[i] < start_date:
            continue
        res = engine.detect_flip(vals[:i + 1])
        rows.append({
            "date": dates[i],
            "rain": round(vals[i], 1),
            "w": round(res["w"], 1),
            "tier": res["tier"]
        })

    return {"ok": True, "label": label, "lat": lat, "lon": lon, "rows": rows}


def create_ticket(payload: dict) -> dict:
    kind = payload.get("type")
    try:
        ward = int(payload.get("ward"))
        people = int(clamp(float(payload.get("people", 1)), 1, 5000))
    except (TypeError, ValueError):
        raise ValueError("ward and people must be numbers")
    if kind not in engine.TICKET_BASE:
        raise ValueError("unknown problem type")
    desc = str(payload.get("desc", ""))[:300].strip()
    with LEDGER.lock:
        LEDGER.seq += 1
        tk = {"id": LEDGER.seq, "type": kind, "ward": ward, "people": people, "desc": desc,
              "ts": int(time.time() * 1000), "status": "pending"}
        LEDGER.tickets.append(tk)
    ranked = [t for t in rank_tickets(LEDGER.last_surge) if t["status"] == "pending"]
    ranked.sort(key=lambda t: t["sev"], reverse=True)
    rank = next(i for i, t in enumerate(ranked, start=1) if t["id"] == tk["id"])
    return {"ok": True, "id": tk["id"], "rank": rank, "pending": len(ranked)}


def set_status(payload: dict) -> dict:
    status = payload.get("status")
    if status not in ("pending", "resolved"):
        raise ValueError("status must be pending or resolved")
    with LEDGER.lock:
        for t in LEDGER.tickets:
            if t["id"] == payload.get("id"):
                t["status"] = status
                return {"ok": True}
    raise ValueError("report not found")


class Handler(BaseHTTPRequestHandler):
    server_version = "VarshaSync/1.0"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("JSON object expected")
        return data

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            page = HERE / "index.html"
            if page.exists():
                self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, b"index.html not found next to server.py", "text/plain; charset=utf-8")
        elif path == "/api/bootstrap":
            self._json({"wards": WARDS, "roof_factors": engine.RUNOFF, "rain": RAIN})
        elif path == "/api/health":
            self._json({"ok": True})
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        try:
            data = self._read_json()
            if path == "/api/analyze":
                self._json(analyze(data))
            elif path == "/api/live":
                self._json(analyze_live(data))
            elif path == "/api/backtest":
                self._json(run_backtest(data))
            elif path == "/api/scenario":
                name = data.get("name")
                if name not in ("whiplash", "base", "gutter"):
                    raise ValueError("unknown scenario")
                LEDGER.reset(name)
                self._json({"ok": True})
            elif path == "/api/tickets":
                self._json(create_ticket(data))
            elif path == "/api/tickets/status":
                self._json(set_status(data))
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"ok": False, "error": str(exc)}, 400)

    def log_message(self, fmt: str, *args) -> None:
        if self.path.startswith("/api/"):
            return
        print(f"  {self.address_string()} {fmt % args}")


def main() -> int:
    import os
    env_port = os.environ.get("PORT")
    default_port = int(env_port) if env_port else 8000

    ap = argparse.ArgumentParser(description="VarshaSync Python backend")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=default_port)
    ap.add_argument("--open", action="store_true", help="open the dashboard in your browser")
    args = ap.parse_args()
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"Could not start on port {args.port}: {exc}\nTry: python server.py --port 9000")
        return 1
    url = f"http://localhost:{args.port}"
    print(f"VarshaSync is running at {url}\nPress Ctrl+C to stop.")
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())