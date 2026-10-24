#!/usr/bin/env python3
"""Basic empirical liberation-rate report from collector SQLite data."""
from __future__ import annotations
import argparse
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = Path(__import__('os').getenv('HD2_DB_PATH', HERE / 'data' / 'galactic_war.sqlite3'))


def main() -> int:
    p = argparse.ArgumentParser(description="Summarize measured progress over a recent window")
    p.add_argument("--hours", type=float, default=1.0, help="Window length; default 1 hour")
    p.add_argument("--min-players", type=int, default=100, help="Ignore planet snapshots below this average player count")
    args = p.parse_args()
    if not DB_PATH.exists():
        print(f"No database found at {DB_PATH}. Run collector.py first.")
        return 1
    seconds = max(60, int(args.hours * 3600))
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
          SELECT planet_index, planet_name, captured_at, owner, health, max_health,
                 players, regen_per_second, regen_pct_per_hour, health_remaining_pct
          FROM planet_snapshots
          WHERE julianday(captured_at) >= julianday('now', ?)
          ORDER BY planet_index, captured_at
        """, (f'-{seconds} seconds',)).fetchall()
        # Group by planet and compare first/last samples, adjusting for elapsed actual time.
        groups = {}
        for r in rows:
            groups.setdefault(r['planet_index'], []).append(r)
        print(f"Window: approx {args.hours:g} h | database: {DB_PATH}")
        print("Planet | samples | avg divers | regen %/h | observed health change %/h | status")
        printed = 0
        for idx, series in groups.items():
            if len(series) < 2:
                continue
            first, last = series[0], series[-1]
            if first['health'] is None or last['health'] is None or not first['max_health']:
                continue
            t0 = __import__('datetime').datetime.fromisoformat(first['captured_at'].replace('Z', '+00:00'))
            t1 = __import__('datetime').datetime.fromisoformat(last['captured_at'].replace('Z', '+00:00'))
            elapsed_h = (t1 - t0).total_seconds() / 3600
            if elapsed_h <= 0:
                continue
            avg_players = sum((x['players'] or 0) for x in series) / len(series)
            if avg_players < args.min_players:
                continue
            # Health decreases during liberation on enemy planets; label generic health movement
            # rather than claiming this always equals liberation without checking ownership/context.
            health_change_pct_h = (float(last['health']) - float(first['health'])) / float(first['max_health']) * 100 / elapsed_h
            regen_values = [x['regen_pct_per_hour'] for x in series if x['regen_pct_per_hour'] is not None]
            regen = sum(regen_values) / len(regen_values) if regen_values else float('nan')
            name = last['planet_name'] or f"index {idx}"
            print(f"{name} | {len(series)} | {avg_players:,.0f} | {regen:.3f} | {health_change_pct_h:+.3f} | owner={last['owner']}")
            printed += 1
        if not printed:
            print("Not enough usable data yet. Collect for longer or reduce --min-players.")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
