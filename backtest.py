"""Backtest: run the VarshaSync flip detector on ANY place and ANY past dates.
Data: Open-Meteo geocoding + historical archive (free, no key). Needs internet. Standard library only."""
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
import engine

COLOURS = {"RED": "#c62828", "ORANGE": "#e65100", "YELLOW": "#f2a900", "GREEN": "#2e7d32", "Unknown": "#777777"}


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def find_place(text):
    """Accepts a place name ('Mumbai') or coordinates ('19.07, 72.87'). Returns (lat, lon, label)."""
    parts = text.replace(" ", "").split(",")
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1]), "(%s, %s)" % (parts[0], parts[1])
        except ValueError:
            pass
    url = "https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(text)
    results = fetch_json(url).get("results")
    if not results:
        raise ValueError("Place not found: try a bigger nearby city or type latitude,longitude")
    p = results[0]
    label = ", ".join(x for x in (p["name"], p.get("admin1"), p.get("country")) if x)
    return p["latitude"], p["longitude"], label


def parse_date(text):
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError("Use the date format YYYY-MM-DD, for example 2015-11-25")


def get_archive(lat, lon, start, end):
    """Downloads from 15 days before 'start' so the detector has history on day one."""
    url = ("https://archive-api.open-meteo.com/v1/archive?latitude=%s&longitude=%s"
           "&start_date=%s&end_date=%s&daily=precipitation_sum&timezone=auto"
           % (lat, lon, start - timedelta(days=15), end))
    data = fetch_json(url)
    vals = [v if v is not None else 0.0 for v in data["daily"]["precipitation_sum"]]
    return data["daily"]["time"], vals


def backtest(dates, vals, show_from):
    """Run the detector as if it were each day, using only data up to that day."""
    rows = []
    for i in range(engine.DRY_WINDOW, len(vals)):
        if dates[i] < show_from:
            continue
        res = engine.detect_flip(vals[:i + 1])          # no peeking at the future
        rows.append((dates[i], vals[i], res["tier"]))
    return rows


def report(title, rows):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)
    print("%-12s %10s   %s" % ("Date", "Rain (mm)", "Detector says"))
    for d, mm, tier in rows:
        if not tier.startswith("GREEN"):
            print("%-12s %10.1f   %s" % (d, mm, tier))
    extreme = [r for r in rows if r[1] > engine.EXTREME_MM]
    peak = max(rows, key=lambda r: r[1])
    print("\nSUMMARY")
    print("Days with over %d mm: %d" % (engine.EXTREME_MM, len(extreme)))
    flagged = [r for r in extreme if r[2].split(" ")[0] in ("RED", "ORANGE")]
    print("Of those, flagged RED or ORANGE on the day: %d" % len(flagged))
    flips = [r for r in rows if r[2].startswith("RED")]
    print("True monsoon flips (RED): %d %s" % (len(flips), [r[0] for r in flips]))
    print("Wettest day: %s with %.1f mm, detector said: %s" % peak)
    before = [r for r in rows if r[0] < peak[0] and not r[2].startswith("GREEN")]
    if before:
        print("First non-green warning before the peak: %s (%s)" % (before[0][0], before[0][2]))
    else:
        print("No warning before the peak day (the detector reacts on the day of extreme rain).")


def chart(title, rows):
    try:
        import tkinter as tk
    except ImportError:
        print("(tkinter not available, skipping chart)")
        return
    win = tk.Tk()
    win.title(title)
    w, h = 900, 380
    c = tk.Canvas(win, width=w, height=h, bg="white")
    c.pack()
    top = max(max(r[1] for r in rows), 1)
    bw = (w - 60) / len(rows)
    for i, (d, mm, tier) in enumerate(rows):
        bar = mm / top * (h - 80)
        x = 40 + i * bw
        c.create_rectangle(x, h - 40 - bar, x + bw - 1, h - 40, fill=COLOURS[tier.split(" ")[0]], outline="")
        if i % max(1, len(rows) // 12) == 0:
            c.create_text(x, h - 25, text=d[5:], font=("Arial", 7), anchor="w")
    c.create_text(10, 10, anchor="nw", text=title + "  (bar height = daily rain, max %.0f mm)" % top,
                  font=("Arial", 11, "bold"))
    for k, (name, col) in enumerate([("GREEN normal", COLOURS["GREEN"]), ("YELLOW watch", COLOURS["YELLOW"]),
                                     ("ORANGE extreme rain", COLOURS["ORANGE"]), ("RED monsoon flip", COLOURS["RED"])]):
        c.create_rectangle(40 + k * 190, 40, 54 + k * 190, 54, fill=col, outline="")
        c.create_text(60 + k * 190, 47, text=name, anchor="w", font=("Arial", 9))
    win.mainloop()


if __name__ == "__main__":
    try:
        place = input("Place name (or latitude,longitude): ").strip()
        lat, lon, label = find_place(place)
        start = parse_date(input("Start date (YYYY-MM-DD): "))
        end = parse_date(input("End date   (YYYY-MM-DD): "))
        if end <= start:
            raise ValueError("End date must be after the start date")
        if (end - start).days > 400:
            raise ValueError("Please pick a range of 400 days or less")
        print("Downloading rain for %s ..." % label)
        dates, vals = get_archive(lat, lon, start, end)
    except ValueError as e:
        print("Input problem:", e)
        sys.exit(1)
    except Exception as e:
        print("Could not download data (%s). Check your internet connection and that the dates are in the past "
              "(the archive lags about 5 days)." % e)
        sys.exit(1)
    title = "%s, %s to %s" % (label, start, end)
    rows = backtest(dates, vals, str(start))
    report(title, rows)
    chart(title, rows)