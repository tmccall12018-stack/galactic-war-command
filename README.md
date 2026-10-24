# Galactic War Data Collector (Helldivers 2)

This starter kit polls the **public, unofficial** Helldivers 2 community API and stores time-stamped war/planet snapshots locally. It does not control the game or join missions. It is intended to produce evidence for a later liberation-force estimator.

API docs: https://helldivers-2.github.io/api/ and https://www.helldiversstats.com/api-docs

## What it stores

- Snapshot capture time, war season ID, API timestamp, impact multiplier, planet count, and summed planet-deployed divers.
- Per planet: ID, name (when provided), owner, current health, max health, player count, regeneration per second, computed regeneration percentage per hour, and a generic health-remaining percentage.
- Raw status/info/assignment JSON is retained in SQLite so the model can be revised without recollecting old data.
- CSV exports are rewritten after each successful snapshot for quick analysis.

No claims are made yet about mission success rate or difficulty distribution per planet: those are not supplied by the standard live planet snapshot. Planet `health` changes should also not automatically be interpreted as liberation on every campaign type; interpret them alongside ownership, event/defense type, and campaign rules.

## Run it once

Python 3.10+ recommended. No third-party package installs are required.

```bash
python collector.py
```

A `data/` folder will be created containing `galactic_war.sqlite3`, `galaxy_snapshots.csv`, and `planet_snapshots.csv`.

## Run continuously every 15 minutes

```bash
python collector.py --loop --interval 900
```

Leave that process running on a computer that stays awake and connected to the internet. Stop with Ctrl+C. If the API is unavailable or its schema changes, an error is logged and the next interval retries. Do not poll faster than necessary; the upstream API requests a client-identifying `X-Super-Client` header and currently documents a limit of 5 requests per 10 seconds.

Optional environment variables:

- `HD2_API_BASE` – change API base URL if needed.
- `HD2_API_CLIENT` – name sent in `X-Super-Client` (default `GalacticWarPlanningCollector/0.1`).
- `HD2_API_CONTACT` – optional contact string sent in `X-Super-Contact`.
- `HD2_INTERVAL_SECONDS` – default loop interval, 900.
- `HD2_DATA_DIR` / `HD2_DB_PATH` – change storage paths.

## Get a rough trend report

After collecting data for at least 1–3 hours:

```bash
python analyze.py --hours 1 --min-players 100
```

This reports mean observed player deployment, the API-derived regeneration percentage per hour, and the planet health's observed percentage-point movement per hour. It deliberately does **not** yet calculate an exact player requirement: first we must determine from the observations whether health changes are comparable across campaign modes and estimate the marginal influence per diver.

## Schedule it on Windows without GitHub

1. Put this folder somewhere that will stay on your PC.
2. Open Task Scheduler → Create Basic Task.
3. Trigger: Daily; select **Repeat task every 15 minutes** for a duration of **Indefinitely** (or the longest option available).
4. Action: Start a program; Program/script = full path to `python.exe`.
5. Add arguments = full path to `collector.py` (quote paths containing spaces). Start in = this folder.
6. In task Properties, choose “Run whether user is logged on or not” if appropriate, and prevent sleep while collecting if you want uninterrupted measurements.

Alternatively, run `collector.py --loop --interval 900` in a terminal and keep the PC awake.

## Important limits

- This is a **local** collector. It will not run while the computer is off, asleep, or disconnected.
- I cannot keep this process running persistently from the ChatGPT conversation itself.
- The API is community-maintained, not official Arrowhead infrastructure. Its schema or availability can change.
- Current active players are taken from the sum of per-planet player counts. This is not the same as Steam-only concurrent users.
- A useful calibration will need snapshots over multiple player-count levels, resistance levels, liberation and defense types, and several hours. Longer history (a few days and several Major Order cycles) will be more robust.

## GitHub Actions scheduled collector (optional)

The repository includes `.github/workflows/collect.yml`, which runs on a 15-minute schedule and can be triggered manually from **Actions → Collect Galactic War snapshots → Run workflow**. It commits the rolling CSV exports to `data/` so the history can be inspected and downloaded. The SQLite database is cached between runs to preserve raw snapshots for analysis without committing a growing binary database to Git history.

Before relying on this for community operations:

- Run the workflow manually and inspect logs. The first live API call can fail if the upstream endpoint or response schema differs from this starter version.
- GitHub scheduled workflows are not guaranteed to start at an exact minute and may be delayed, especially during high GitHub Actions load.
- A cache is not a durable database: cache eviction or a missed cache can reset SQLite history. CSV history committed to Git remains the durable export; for a production-grade long-running dataset, use an external database or object store.
- Do not commit personal secrets. The current workflow requires no API token.
- Ensure the API's current terms, client-identification, and rate limits permit the collection cadence before enabling ongoing collection.

## Suggested next milestone

After collecting and validating a useful time series, build a calibrated deployment estimator. Use observed changes in planet health, normalized by elapsed time and max health; keep liberation and defense records separate; flag ownership changes, war resets, data gaps, and resistance changes; only fit the player-count-to-impact relationship on windows where campaign mechanics appear stable. Report ranges and observed confidence, not a false-precision exact headcount.
