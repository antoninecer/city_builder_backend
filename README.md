# City Builder Backend — Development README

Backend for an isometric, tile-based city builder with expandable world, dev-friendly debugging, and future-ready monetization primitives.

> Code is in English. This README is the shared contract for frontend/backend while we iterate fast.

---

## Overview

This backend is designed to:
- store buildings in **world coordinates** centered around `(0,0)`
- support **world expansion in rings** (+1 radius per expansion)
- remain production-safe (locking, controlled DEV endpoints)
- be ready for future monetization (gems, speed-ups, paid expansion) without schema rewrites

---

## Core World Model

### Coordinates
- All buildings use **WORLD tile coordinates** `(x, y)`.
- `(0,0)` is the **center** (starting townhall position).
- Default starting world:
  - `radius = 3`
  - bounds: `x ∈ [-3..3]`, `y ∈ [-3..3]`
  - grid: `7×7`

### World metadata returned by API
`GET /city/{user_id}` returns:

```json
"world": {
  "radius": 3,
  "grid": {"w": 7, "h": 7},
  "bounds": {"min_x": -3, "max_x": 3, "min_y": -3, "max_y": 3},
  "anchor": "topleft"
}
Expansion
Expansion happens in rings.

One expansion step = +1 radius → adds one row/column on each side.

Area grows quadratically with radius.

Current endpoint:

POST /city/{user_id}/expand with JSON body { "steps": 1 } (default 1)

Pricing model (future): cost should grow faster than linear (exponential / polynomial).
Backend already exposes the primitive; later we’ll bind it to gems / payments.

Buildings
Building record (current API shape)
Each building in buildings contains:

json
Copy code
{
  "type": "townhall",
  "level": 1,
  "x": 0,
  "y": 0,
  "upgrade_start": null,
  "upgrade_end": null,
  "rotation": null
}
Footprint & Rotation (future-ready)
Backend already has footprint + rotatable in server config.

Current implementation:

all buildings are 1×1

rotation is accepted/stored but currently not used for geometry

Planned:

footprints like 2×1, 2×2, 3×1, 4 tiles total

rotation 0/90/180/270 swaps width/height for rectangular buildings

placement checks will validate:

all covered tiles are in bounds

all covered tiles are free (collision by footprint)

Townhall rule
Exactly one townhall.

Cannot be demolished.

Reset always recreates only townhall at (0,0).

Player State
Resources (current)
Stored in Redis hash player:{user_id}:

json
Copy code
{
  "gold": number,
  "wood": number,
  "last_collect": timestamp
}
Gems (planned)
We will add:

json
Copy code
"gems": number
as premium currency for microtransactions.

Lazy Progress Model
GET /city/{user_id} also performs:

finishing upgrades when upgrade_end <= now

applying idle production since last_collect (unless unlimited dev mode is enabled)

updating last_collect

All under a per-user Redis lock to prevent race conditions.

Development Mode
Why
We don’t want to wait hours/days for resources during UI iteration and gameplay experiments.

Enable Dev Mode
bash
Copy code
export ALLOW_DEV_ENDPOINTS=1
export DEV_UNLIMITED_RESOURCES=1
# optional:
export DEV_DEFAULT_GOLD=99999999
export DEV_DEFAULT_WOOD=99999999
export DEFAULT_WORLD_RADIUS=3

# restart backend after setting env vars
Unlimited Resources Mode
When DEV_UNLIMITED_RESOURCES=1:

gold/wood costs are not deducted

validation still runs (bounds, collisions, etc.)

ideal for placement/UI iteration and footprint experiments

DEV endpoint safety
DEV endpoints are disabled by default.

If ALLOW_DEV_ENDPOINTS is not set, dev endpoints should be unavailable.

In production: keep disabled or add auth/IP allowlist.

API Summary (curl)
Health
bash
Copy code
curl -s http://localhost:8002/
New game
bash
Copy code
curl -s -X POST http://localhost:8002/new_game \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"test123"}'
Get city state
bash
Copy code
curl -s http://localhost:8002/city/test123 | jq
Place building
bash
Copy code
curl -s -X POST http://localhost:8002/city/test123/place \
  -H 'Content-Type: application/json' \
  -d '{"building_type":"farm","x":1,"y":0}'
Upgrade building
bash
Copy code
curl -s -X POST http://localhost:8002/city/test123/upgrade \
  -H 'Content-Type: application/json' \
  -d '{"building_id":"farm_123"}'
Demolish building
bash
Copy code
curl -s -X POST http://localhost:8002/city/test123/demolish \
  -H 'Content-Type: application/json' \
  -d '{"building_id":"farm_123"}'
Expand world (+1 ring)
bash
Copy code
curl -s -X POST http://localhost:8002/city/test123/expand \
  -H 'Content-Type: application/json' \
  -d '{"steps":1}'
DEV Endpoints (curl)
Reset player (townhall-only)
Resets player state:

resources reset (or huge if unlimited)

buildings cleared

only townhall_0 at (0,0)

world radius back to default (DEFAULT_WORLD_RADIUS)

bash
Copy code
curl -s -X POST http://localhost:8002/dev/reset/test123 \
  -H 'Content-Type: application/json' \
  -d '{"wipe": true}'
Grant resources (debug)
Canonical endpoint:

bash
Copy code
curl -s -X POST http://localhost:8002/dev/grant/test123 \
  -H 'Content-Type: application/json' \
  -d '{"gold":100000,"wood":100000,"mode":"add"}'
Alias for convenience/scripts:

bash
Copy code
curl -s -X POST http://localhost:8002/dev/resources/test123 \
  -H 'Content-Type: application/json' \
  -d '{"gold":100000,"wood":100000,"mode":"set"}'
Wipe player (delete keys, no recreate)
bash
Copy code
curl -s -X POST http://localhost:8002/dev/wipe/test123
Set world radius directly (DEV)
Accepts either JSON body or query param:

bash
Copy code
# JSON body
curl -s -X POST http://localhost:8002/dev/world/set_radius/test123 \
  -H 'Content-Type: application/json' \
  -d '{"radius":3}'

# OR query param
curl -s -X POST "http://localhost:8002/dev/world/set_radius/test123?radius=3"
Testing
Smoke test script
We keep a bash script (example bt2.sh) that:

creates a new game

reads world radius/bounds/resources

expands radius by +1

places farms to 4 corners of the expanded world

prints final buildings and world summary

(optional) wipes the user at the end

Recommended cleanup after tests:

bash
Copy code
curl -s -X POST http://localhost:8002/dev/wipe/<USER_ID>
Frontend Integration Notes
World rendering
Frontend should:

render a grid based on world.bounds / world.radius

treat (0,0) as the logical center

map world coords → screen coords consistently (isometric transform)

later support shifting the viewport

UX / Menu (hamburger)
Frontend should add a small in-game menu with actions:

Reset game (DEV) → /dev/reset/{user_id}

Wipe user (DEV) → /dev/wipe/{user_id}

Switch user / logout (client-side)

Buy gems (future)

Buy world expansion (future)

Speed-up upgrades (future)

Monetization (Planned / “Gems-ready”)
Add premium currency (gems)
Add gems field to player resources in Redis

Return gems from GET /city/{user_id}

DEV grant must support setting gems

Purchase ledger (important)
To be monetization-safe we need:

a purchase endpoint that verifies receipts (Stripe/App Store/Google Play later)

immutable ledger record:

purchase_id, provider, user_id, amount, gems_granted, timestamp, idempotency_key

Spend gems on
World expansion (+1 radius per purchase)

Speed-ups:

finish upgrade instantly

reduce remaining time

Future: premium buildings / cosmetics

All spend operations must be under UserLock.

Philosophy
Backend should never slow down iteration.
Dev mode exists so frontend/gameplay can evolve quickly, safely, and without hacks.

pgsql
Copy code

Chceš to ještě doplnit o dvě praktické věci, co se budou hodit “zítra”?
1) **Sekce “Known differences vs spec”** (že `gems` zatím nejsou v API, footprint není v payloadu buildingu, jen v server configu).  
2) **Krátký “Ops” odstavec**: jak pustit 2 instance backendu nad jedním Redisem + proč lock TTL chrání před paralelními requesty.

Když řekneš jen “jo přidej”, dopíšu to rovnou do toho README textu.
::contentReference[oaicite:0]{index=0}




