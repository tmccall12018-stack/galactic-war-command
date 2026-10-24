#!/usr/bin/env python3
"""Galactic War snapshot collector for Helldivers 2.

Uses the public, unofficial community API at api.helldivers2.dev.
Stores timestamped raw snapshots and normalized planet metrics in SQLite,
then exports cumulative CSVs for analysis. Standard-library only.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_URL = os.getenv("HD2_API_BASE", "https://api.helldivers2.dev").rstrip("/")
DATA_DIR = Path(os.getenv("HD2_DATA_DIR", Path(__file__).resolve().parent / "data"))
DB_PATH = Path(os.getenv("HD2_DB_PATH", DATA_DIR / "galactic_war.sqlite3"))
CLIENT = os.getenv("HD2_API_CLIENT", "GalacticWarPlanningCollector/0.1")
CONTACT = os.getenv(
    "HD2_API_CONTACT",
    "https://github.com/tmccall12018-stack/galactic-war-command"
)
DEFAULT_INTERVAL = int(os.getenv("HD2_INTERVAL_SECONDS", "900"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def api_get(path: str) -> Any:
    url = f"{BASE_URL}{path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": f"{CLIENT}/0.1 (community research collector)",
        "X-Super-Client": CLIENT,
    }
    if CONTACT:
        headers["X-Super-Contact"] = CONTACT
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read()
            if response.status != 200:
                raise RuntimeError(f"API returned HTTP {response.status} for {path}")
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            wait = exc.headers.get("Retry-After", "unknown")
            raise RuntimeError(f"Rate limited by API (Retry-After: {wait}); lower collection frequency") from exc
        message = exc.read(500).decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code} requesting {path}: {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error requesting {path}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"API returned invalid JSON for {path}") from exc


def first(mapping: dict[str, Any], *keys: str) -> Any:
    # APIs may use camelCase or PascalCase; matching is case-insensitive.
    lowered = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        val = lowered.get(key.lower())
        if val is not None:
            return val
    return None


def find_array(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = first(payload, key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            api_war_time INTEGER,
            war_id INTEGER,
            impact_multiplier REAL,
            total_planet_players INTEGER,
            total_planets INTEGER NOT NULL,
            raw_status_json TEXT NOT NULL,
            raw_info_json TEXT,
            raw_assignments_json TEXT,
            UNIQUE(captured_at)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS planet_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            war_id INTEGER,
            planet_index INTEGER NOT NULL,
            planet_name TEXT,
            sector_index INTEGER,
            owner INTEGER,
            health REAL,
            max_health REAL,
            players INTEGER,
            regen_per_second REAL,
            regen_pct_per_hour REAL,
            health_remaining_pct REAL,
            raw_status_json TEXT NOT NULL,
            UNIQUE(captured_at, planet_index)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_planet_time ON planet_snapshots(planet_index, captured_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_time ON snapshots(captured_at)")


def ensure_list(value: Any) -> list[dict[str, Any]]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def collect_once() -> None:
    init_db()
    # The live WarID endpoint protects this script from a future war-season change.
    war_payload = api_get("/raw/api/WarSeason/current/WarID")
    if not isinstance(war_payload, dict):
        raise RuntimeError("Unexpected WarID response shape; expected an object")
    war_id = first(war_payload, "id", "warId", "warID")
    if war_id is None:
        raise RuntimeError(f"Couldn't identify current war ID in API response: {war_payload}")
    war_id = int(war_id)

    status = api_get(f"/raw/api/WarSeason/{war_id}/Status")
    info = api_get(f"/raw/api/WarSeason/{war_id}/WarInfo")
    assignments = api_get(f"/raw/api/v2/Assignment/War/{war_id}")
    # The raw WarInfo contains maxHealth and routing but not consistently display names;
    # use the convenience catalog endpoints to join names and sector labels.
    try:
        catalog_payload = api_get("/api/v1/planets")
        catalog = find_array(catalog_payload, "planets", "items")
        if not catalog and isinstance(catalog_payload, list):
            catalog = catalog_payload
    except Exception as exc:
        print(f"Warning: planet name catalog unavailable; continuing with numeric IDs: {exc}", file=sys.stderr)
        catalog = []
    try:
        sectors_payload = api_get("/api/v1/sectors")
        sectors = find_array(sectors_payload, "sectors", "items")
        if not sectors and isinstance(sectors_payload, list):
            sectors = sectors_payload
    except Exception as exc:
        print(f"Warning: sector catalog unavailable: {exc}", file=sys.stderr)
        sectors = []
    if not isinstance(status, dict) or not isinstance(info, dict):
        raise RuntimeError("Unexpected API payload; status/info should be JSON objects")

    statuses = find_array(status, "planetStatus", "planets")
    infos = find_array(info, "planetInfos", "planets")
    if not statuses:
        raise RuntimeError("No planetStatus array found; upstream schema may have changed")

    info_by_idx: dict[int, dict[str, Any]] = {}
    for p in infos:
        idx = first(p, "index", "planetIndex", "indexID")
        if idx is not None:
            info_by_idx[int(idx)] = dict(p)
    for p in catalog:
        idx = first(p, "index", "planetIndex", "indexID", "id")
        if idx is not None:
            info_by_idx.setdefault(int(idx), {}).update(p)
    sector_names: dict[int, str] = {}
    for sector in sectors:
        sec_id = first(sector, "index", "sectorIndex", "id")
        sec_name = first(sector, "name", "sectorName")
        if sec_id is not None and sec_name is not None:
            sector_names[int(sec_id)] = str(sec_name)

    captured_at = utc_now()
    api_time = first(status, "time")
    impact_multiplier = first(status, "impactMultiplier")
    rows = []
    for p in statuses:
        idx = first(p, "index", "planetIndex", "planetID", "indexID")
        if idx is None:
            continue
        idx = int(idx)
        meta = info_by_idx.get(idx, {})
        health = first(p, "health")
        max_health = first(meta, "maxHealth")
        players = first(p, "players")
        regen = first(p, "regenPerSecond", "regerPerSecond")
        try:
            regen_pct_hour = float(regen) * 3600 / float(max_health) * 100 if regen is not None and max_health else None
        except (ValueError, TypeError, ZeroDivisionError):
            regen_pct_hour = None
        try:
            health_remaining_pct = float(health) / float(max_health) * 100 if health is not None and max_health else None
        except (ValueError, TypeError, ZeroDivisionError):
            health_remaining_pct = None
               raw_sector = first(meta, "sector", "sectorIndex", "sectorID")
        sector_index = None
        sector_name = None

        if isinstance(raw_sector, (int, float)):
            sector_index = int(raw_sector)
            sector_name = sector_names.get(sector_index)
        elif isinstance(raw_sector, str):
            sector_name = raw_sector
                planet_name = first(meta, "name", "planetName", "planet_name")
        if sector_name and not first(meta, "sectorName"):
            meta["sectorName"] = sector_name
        rows.append((
            captured_at, war_id, idx, planet_name,
            sector_index,
            first(p, "owner", "currentOwner"), health, max_health, players, regen,
            regen_pct_hour, health_remaining_pct,
            json.dumps(p, ensure_ascii=False, separators=(",", ":")),
        ))

    # Reject empty/obviously malformed pulls rather than record misleading zeroes.
    if not rows:
        raise RuntimeError("No normalizable planet rows found; no snapshot written")
    total_players = sum(int(r[9]) for r in rows if r[9] is not None)

    with sqlite3.connect(DB_PATH) as con:
        con.execute("""INSERT OR IGNORE INTO snapshots
            (captured_at, api_war_time, war_id, impact_multiplier, total_planet_players,
             total_planets, raw_status_json, raw_info_json, raw_assignments_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (captured_at, api_time, war_id, impact_multiplier, total_players, len(rows),
             json.dumps(status, ensure_ascii=False, separators=(",", ":")),
             json.dumps({"warInfo": info, "planetCatalog": catalog, "sectorCatalog": sectors}, ensure_ascii=False, separators=(",", ":")),
             json.dumps(assignments, ensure_ascii=False, separators=(",", ":"))))
        con.executemany("""INSERT OR IGNORE INTO planet_snapshots
            (captured_at, war_id, planet_index, planet_name, sector_index, owner, health,
             max_health, players, regen_per_second, regen_pct_per_hour, health_remaining_pct,
             raw_status_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)

    export_csvs()
    print(f"[{captured_at}] saved {len(rows)} planets; {total_players:,} planet-deployed divers; war {war_id}; impact multiplier={impact_multiplier}")
    print(f"Database: {DB_PATH}")


def export_csvs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        for table, filename in [("snapshots", "galaxy_snapshots.csv"), ("planet_snapshots", "planet_snapshots.csv")]:
            cur = con.execute(f"SELECT * FROM {table} ORDER BY captured_at")
            cols = [d[0] for d in cur.description]
            with (DATA_DIR / filename).open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(cols)
                for row in cur:
                    writer.writerow([row[c] for c in cols])


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Helldivers 2 Galactic War snapshots")
    parser.add_argument("--once", action="store_true", help="Collect one snapshot, then exit (default)")
    parser.add_argument("--loop", action="store_true", help="Keep collecting every interval")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="Seconds between snapshots; default 900 (15 min)")
    args = parser.parse_args()
    if args.interval < 300:
        print("Warning: intervals under 300 seconds are not recommended; respect API limits.", file=sys.stderr)
    if not args.loop:
        try:
            collect_once()
            return 0
        except Exception as exc:
            print(f"Collection failed: {exc}", file=sys.stderr)
            return 1
    while True:
        started = time.monotonic()
        try:
            collect_once()
        except KeyboardInterrupt:
            print("Stopping collector.")
            return 0
        except Exception as exc:
            print(f"[{utc_now()}] Collection failed: {exc}", file=sys.stderr)
        elapsed = time.monotonic() - started
        time.sleep(max(1, args.interval - int(elapsed)))


if __name__ == "__main__":
    raise SystemExit(main())
