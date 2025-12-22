City Builder Backend – Development README
Overview

This backend powers an isometric city builder game with an expandable world, tile-based placement, and future-ready monetization mechanics (microtransactions, speed-ups, world expansion).

The backend is designed to:

support infinite world expansion

store buildings in world coordinates

be safe in production, but fast to iterate in development

Core World Model
Coordinates

All buildings use WORLD coordinates (x, y)

(0, 0) is the center of the world

Default starting world size:

minX = -3
maxX = +3
minY = -3
maxY = +3


→ total 7×7 playable area

World Expansion

Expansion happens in rings

1 expansion = +1 tile on each side

Area grows quadratically

Pricing model (future):

cost grows faster than linear (exponential / polynomial)

Expansion will later be purchasable via premium currency (gems)

Buildings
General Rules

Each building has:

{
  "type": "townhall",
  "level": 1,
  "x": 0,
  "y": 0,
  "footprint": { "w": 1, "h": 1 },
  "rotation": 0
}

Footprint (Future-ready)

footprint.w, footprint.h

rotation planned (0, 90, 180, 270)

current implementation:

only 1×1

rotation = 0

Townhall

Only one townhall

Cannot be demolished

Always placed at (0,0) on reset

Player State
Resources
{
  "gold": number,
  "wood": number,
  "gems": number,
  "last_collect": timestamp
}


gold, wood → soft currency

gems → premium currency (microtransactions)

Game Reset Rules
Reset Behavior

On reset:

player resources reset

all buildings deleted

ONLY ONE building remains:

townhall at (0,0)

No farms, no lumbermills, no defaults.

Development Mode

Development-only endpoints are disabled by default.

Enable Dev Mode
export ALLOW_DEV_ENDPOINTS=1
export DEV_UNLIMITED_RESOURCES=1


Restart backend after setting env vars.

DEV Endpoints (curl)
Reset Player

Deletes all buildings and recreates only townhall at (0,0).

curl -X POST http://localhost:8002/dev/reset/test123

Add Resources (Debug)

Add arbitrary amounts of resources.

curl -X POST http://localhost:8002/dev/resources/test123 \
  -H "Content-Type: application/json" \
  -d '{
    "gold": 100000,
    "wood": 100000,
    "gems": 1000
  }'

Unlimited Resources Mode

When DEV_UNLIMITED_RESOURCES=1:

no gold / wood is deducted

validation still runs

ideal for:

testing placement

UI iteration

footprint experiments

Production Safety

DEV endpoints:

require ALLOW_DEV_ENDPOINTS=1

otherwise return 404

Unlimited mode:

only active when explicitly enabled

Per-user Redis lock prevents race conditions

API Summary
Get City State
curl http://localhost:8002/city/test123

Place Building
curl -X POST http://localhost:8002/city/test123/place \
  -H "Content-Type: application/json" \
  -d '{
    "building_type": "farm",
    "x": 1,
    "y": 0
  }'

Upgrade Building
curl -X POST http://localhost:8002/city/test123/upgrade \
  -H "Content-Type: application/json" \
  -d '{
    "building_id": "farm_123"
  }'

Demolish Building
curl -X POST http://localhost:8002/city/test123/demolish \
  -H "Content-Type: application/json" \
  -d '{
    "building_id": "farm_123"
  }'

Monetization – Planned

Gems (premium currency)

Speed up:

upgrades

construction

world expansion

Paid world expansion

Paid instant actions

Backend is already structured to support this without schema rewrites.

Philosophy

Backend should never slow down iteration.
ev mode exists so frontend and gameplay can evolve fast, safely, and without hacks.

# City Builder Backend — Dev notes / README (work-in-progress)

> Goal: keep a clear, shared contract between frontend and backend while we iterate fast (3 AM friendly).
> Backend code is written in English. This README is for humans (CZ).

---

## 1) Current state (what works now)

### Core endpoints
- `GET /city/{user_id}`
  - Returns:
    - `resources` (gold, wood)
    - `buildings` dict keyed by building_id
    - `world` metadata (radius/grid/bounds/anchor)
    - `server_time`
  - Also performs **lazy progress**:
    - finishes upgrades when `upgrade_end` is due
    - adds idle production since `last_collect`

- `POST /new_game`
  - Creates a new user + initial state in Redis (resources + default buildings + world)
  - If `user_id` provided and already exists → 409.

- `POST /city/{user_id}/place`
  - Places a building at integer tile coords (x,y).
  - Validates position not occupied.
  - Charges gold unless unlimited dev mode is enabled.

- `POST /city/{user_id}/upgrade`
  - Starts upgrade if not already upgrading.
  - Charges gold unless unlimited dev mode.

- `POST /city/{user_id}/demolish`
  - Removes building by id.
  - Disallows demolish of `townhall`.
  - Optionally refunds gold (currently partial, dev-friendly).

### Coordinate model (world)
- We are standardizing around **world coordinates centered around (0,0)**.
- Default “playable” area is a **7×7** grid:
  - `radius = 3`
  - bounds: `min_x=-3..max_x=3`, `min_y=-3..max_y=3`
  - `grid.w = grid.h = 2*radius + 1 = 7`
- Townhall should be at/near the center (0,0).
  *(If existing Redis data has old coords, use RESET to clean it.)*

### Buildings model (current)
Each building record includes at least:
- `type` (townhall/farm/lumbermill/house/barracks)
- `level`
- `x`, `y` (tile coords)
- `upgrade_start`, `upgrade_end` (nullable)
- `rotation` (nullable; placeholder for future rotation)
- Future: footprint metadata will be added, but **for now everything is 1×1**.

---

## 2) Dev mode (fast testing)

### Why
We cannot wait days for gold/wood to test UI and mechanics. Dev endpoints allow:
- resetting a player
- adding resources
- running with “unlimited resources”

### Environment variables
Backend reads env flags (names may differ based on latest code; keep consistent):
- `ALLOW_DEV_ENDPOINTS=1`
  Enables DEV-only endpoints. If not set, dev endpoints should return 404/403.
- `DEV_UNLIMITED_RESOURCES=1`
  Makes costs effectively free (place/upgrade won’t reduce gold).
- Optional defaults (if implemented):
  - `DEV_DEFAULT_GOLD=99999999`
  - `DEV_DEFAULT_WOOD=99999999`

### Expected DEV endpoints (contract)
These are the endpoints we want / use via curl (implementation may already exist or is planned):
- `POST /dev/reset/{user_id}`
  - wipes player resources + buildings + world to defaults
  - keeps consistent world radius/grid
- `POST /dev/resources/{user_id}`
  - adds/subtracts resources
  - body example: `{ "gold": 100000, "wood": 100000 }`
- `POST /dev/unlock/{user_id}` (optional)
  - clears lock if debugging stuck locks (rare)

> NOTE: DEV endpoints must never be enabled in production without auth / IP allowlist.

---

## 3) How to run locally

Backend:
```bash
cd /opt/city_builder_backend
source venv/bin/activate

export ALLOW_DEV_ENDPOINTS=1
export DEV_UNLIMITED_RESOURCES=1

uvicorn app.main:app --reload --host 0.0.0.0 --port 8002
i4) Quick testing with curl
Get city state
curl -s http://127.0.0.1:8002/city/test123 | jq

New game (new user)
curl -s -X POST http://127.0.0.1:8002/new_game \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"test_new"}' | jq

Place building
curl -s -X POST http://127.0.0.1:8002/city/test123/place \
  -H 'Content-Type: application/json' \
  -d '{"building_type":"farm","x":1,"y":0}' | jq

Upgrade building
curl -s -X POST http://127.0.0.1:8002/city/test123/upgrade \
  -H 'Content-Type: application/json' \
  -d '{"building_id":"farm_0"}' | jq

Demolish building
curl -s -X POST http://127.0.0.1:8002/city/test123/demolish \
  -H 'Content-Type: application/json' \
  -d '{"building_id":"farm_1766137750438"}' | jq

Reset player (DEV)
curl -s -X POST http://127.0.0.1:8002/dev/reset/test123 | jq

Add resources (DEV)
curl -s -X POST http://127.0.0.1:8002/dev/resources/test123 \
  -H 'Content-Type: application/json' \
  -d '{"gold":1000000,"wood":1000000}' | jq

5) Frontend integration notes (what must be fixed / done)
5.1 API base + CORS

Frontend must call the correct backend host.
Current issue observed:

frontend at https://isocity.api.ventureout.cz

backend fetch to https://city.api.ventureout.cz/city/test123

error: CORS blocked / missing Access-Control-Allow-Origin or 502 Bad Gateway

Checklist:

Ensure frontend cfg.API_BASE points to the right domain.

Ensure backend CORS middleware includes the exact origin(s):

https://isocity.api.ventureout.cz

plus local dev origins if needed

Ensure reverse proxy routes /city/* to the correct upstream (avoid 502).

5.2 World centering + viewport shifting

Frontend requirements:

show 7×7 viewport centered around (0,0)

allow shifting viewport by tile increments (dx/dy)

correctly map world coords to viewport coords

Backend contract:

GET /city/{user_id} returns world.radius, world.bounds, world.grid.

Frontend uses these to:

compute initial view offset so that (0,0) is centered

clamp movement if we enforce bounds (optional)

later: expand world radius as a purchase

5.3 UI controls (hamburger / debug menu)

Frontend should add a minimal “game menu”:

Reset game (DEV only) → calls /dev/reset/{user_id}

Add resources (DEV only) → calls /dev/resources/{user_id}

Logout / switch user

Buy gems (future)

Buy world expansion (future)

Speed-up upgrade / production (future)

Also keep existing contextual menus:

empty tile click → build menu

building click → upgrade/demolish menu + info

6) Roadmap: Monetization (“gems-ready” plan)

We are not implementing payments now, but we want the backend to be ready.

6.1 Add premium currency (“gems”)

Backend changes:

Add gems to player resources (Redis hash):

e.g. gems: int

Return gems in GET /city/{user_id} response.

DEV endpoint to set gems quickly.

6.2 Shop + ledger (important)

To be monetization-safe, we need a simple ledger:

POST /shop/purchase (server-to-server in the future)

validates a purchase token/receipt (Stripe/App Store/Google Play later)

credits gems

writes an immutable record:

purchase_id, provider, amount, gems_granted, timestamp, user_id

Even before real payments, we can “simulate purchases” with DEV endpoints.

6.3 Spend gems on:

World expansion

Each purchase increases radius by +1 (adds one ring around)

Cost should grow non-linearly (e.g. exponential or superlinear)

Backend must store per-user world state in Redis:

world.radius

derived bounds/grid

Endpoint:

POST /world/expand (cost in gems, validates user has gems)

Speed-up (upgrade timers)

Spend gems to finish upgrade instantly or reduce remaining time

Endpoint:

POST /upgrade/speedup with building_id (+ maybe “minutes to skip”)

Future: premium buildings / skins

6.4 Anti-abuse / consistency

All spend operations must be under UserLock.

Use idempotency keys for purchases.

Never trust client-side gem balance.

7) Footprint + rotation (future design, but plan now)

We will support footprints up to 4 tiles soon.
For now:

all buildings are 1×1

rotation exists as nullable placeholder

Planned design:

building config adds:

footprint: { w, h }

rotatable: bool

When placing a building:

check that all covered tiles are inside bounds

check that all covered tiles are free

Rotation:

for 2×1 / 3×1 etc, rotation swaps width/height.

store rotation = 0/90/180/270 or N/E/S/W

8) Known issues to keep in mind

If frontend points to wrong domain, you will see:

502 in Network tab (proxy issue)

CORS errors

If old Redis state has coordinates outside new bounds:

buildings “disappear” from 7×7 viewport

solution: DEV reset and rebuild with new coordinate model

9) Next steps (tomorrow morning checklist)

Fix frontend API_BASE + CORS + reverse proxy (stop 502/CORS).

Add hamburger menu with:

reset (DEV)

add resources (DEV)

logout / change user

Make world shifting stable in frontend:

keep viewport offsets

render all buildings in view

Start footprint groundwork:

store rotation, add config fields, keep all 1×1 for now

Monetization prep:

add gems field

define purchase ledger model + endpoints (even stubbed)
