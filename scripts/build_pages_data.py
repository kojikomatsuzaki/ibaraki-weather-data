#!/usr/bin/env python3

import json
from pathlib import Path

RAW_DIR = Path("data/raw/rainfall")
OUT_DIR = Path("docs/data")


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def latest_value(item):
    values = item.get("dataList") or []
    return values[-1] if values else None


def compact_station(path):
    src = load_json(path)
    table = src.get("data", {}).get("tableData", {})
    items = {}
    for item in table.get("itemDataList", []):
        latest = latest_value(item)
        if latest:
            items[str(item.get("dataItemId"))] = latest

    station = src.get("station", {})
    return {
        "staId": station.get("staId"),
        "staName": station.get("staName"),
        "cityName": station.get("cityName"),
        "officeName": station.get("officeName"),
        "riverName": station.get("riverName"),
        "lat": station.get("staLat"),
        "lon": station.get("staLon"),
        "obsTime": table.get("obsTime"),
        "resolvedLatestTime": src.get("resolved_latest_time"),
        "fallbackSteps": src.get("fallback_steps"),
        "sourceUrl": src.get("source_url"),
        "hourRain": items.get("30"),
        "cumulativeRain": items.get("70"),
    }


def main():
    snapshots = []
    for day in RAW_DIR.iterdir():
        if not day.is_dir() or not day.name.isdigit():
            continue
        for snap in day.iterdir():
            if snap.is_dir() and snap.name.isdigit() and (snap / "_manifest.json").exists():
                snapshots.append(snap)

    if not snapshots:
        raise SystemExit("No rainfall snapshots found")

    snapshot = max(snapshots, key=lambda p: p.name)
    manifest = load_json(snapshot / "_manifest.json")
    stations = []
    for path in sorted(snapshot.glob("*.json")):
        if path.name.startswith("_"):
            continue
        stations.append(compact_station(path))

    unavailable_stations = manifest.get("unavailable_stations") or []

    payload = {
        "alpha": True,
        "snapshot": snapshot.name,
        "retrievedAt": manifest.get("retrieved_at"),
        "rainStaLatestTime": manifest.get("rain_sta_latest_time"),
        "requested": manifest.get("requested"),
        "success": manifest.get("success"),
        "unavailableCount": manifest.get("unavailable", len(unavailable_stations)),
        "fallbackUsed": manifest.get("fallback_used"),
        "stations": stations,
        "unavailable": unavailable_stations,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "latest.json").open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
