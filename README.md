# City Builder Backend — Dev README (Contract + Roadmap)

Backend for an isometric, tile-based city builder.
Redis is the source of truth. Code in English. This README is the FE/BE contract.

---

## 0) Status (today)

**Working now:**
- World is centered at `(0,0)` with **radius-based bounds** (default `radius=3` ⇒ `7×7`).
- Buildings are stored in **world tile coordinates** (negative coords allowed).
- Placement uses **footprint-aware collision + bounds checks** (ready for 2×1, 2×2…).
- `rotation` is stored (currently not affecting geometry).
- Player resources include **gold, wood, gems, last_collect**.
- `GET /city/{user_id}` returns a **catalog** (FE does not hardcode building config).
- Monetization primitive exists: **expand world by spending gems** with:
  - `Idempotency-Key`
  - immutable-ish **ledger entry**
  - atomic Redis `MULTI/EXEC` (pipeline `transaction=True`)
  - all under **per-user lock**.

**Safety note (today):**
- `POST /city/{user_id}/expand_gems` is currently gated by `ALLOW_DEV_ENDPOINTS` for testing.
  - For v1.0 monetization: move this behind a proper feature flag / auth (see roadmap).

---

## 1) Core world model

### Coordinates
- Buildings use WORLD tile coordinates `(x,y)`.
- `(0,0)` is the center (Townhall start).

Default world:
- `radius = 3`
- bounds: `x ∈ [-3..3]`, `y ∈ [-3..3]`
- grid: `7×7`

### World payload (GET /city/{user_id})
```json
"world": {
  "radius": 3,
  "grid": {"w": 7, "h": 7},
  "bounds": {"min_x": -3, "max_x": 3, "min_y": -3, "max_y": 3},
  "anchor": "topleft"
}
2) Buildings
Building record returned by API
json
Copy code
{
  "type": "farm",
  "level": 1,
  "x": 1,
  "y": 0,
  "upgrade_start": null,
  "upgrade_end": null,
  "rotation": 0,
  "footprint": {"w": 1, "h": 1}
}
Footprint + rotation rules
Server config (BUILDING_CONFIG) defines:

footprint {w,h}

rotatable

Placement already validates:

all footprint tiles are inside bounds

none collide with existing buildings

Rotation is stored but geometry is currently treated as unrotated.
Next: apply rotation transform (swap w/h for 90/270 when rotatable).

Townhall rules
Exactly one townhall_0.

Cannot be demolished.

Reset recreates townhall at (0,0).

3) Player state + lazy progress
Player resources (Redis)
Stored in Redis hash player:{user_id}:

json
Copy code
{
  "gold": number,
  "wood": number,
  "gems": number,
  "last_collect": timestamp
}
Lazy progress on GET /city/{user_id}
finishes upgrades when upgrade_end <= now

applies idle production since last_collect (unless unlimited dev mode)

updates last_collect

all protected by per-user lock

4) API (core)
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
  -d '{"building_type":"farm","x":1,"y":0,"rotation":0}'
Upgrade / demolish
bash
Copy code
curl -s -X POST http://localhost:8002/city/test123/upgrade \
  -H 'Content-Type: application/json' \
  -d '{"building_id":"farm_123"}'

curl -s -X POST http://localhost:8002/city/test123/demolish \
  -H 'Content-Type: application/json' \
  -d '{"building_id":"farm_123"}'
Expand world (gold primitive)
bash
Copy code
curl -s -X POST http://localhost:8002/city/test123/expand \
  -H 'Content-Type: application/json' \
  -d '{"steps":1}'
5) Monetization primitives (v0.x)
A) Spend gems → expand world
Endpoint:

POST /city/{user_id}/expand_gems

requires header: Idempotency-Key: <unique>

Behavior:

spends gems

expands radius

writes ledger entry to ledger:{user_id}

stores exact response to idempo:{user_id}:expand_gems:<key> (TTL 7 days)

atomic Redis transaction (MULTI/EXEC) + per-user lock

Example:

bash
Copy code
IDEM="expand-123"
curl -s -X POST http://localhost:8002/city/test123/expand_gems \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: ${IDEM}" \
  -d '{"steps":1}'
Ledger inspection:

bash
Copy code
redis-cli LRANGE "ledger:test123" 0 10
6) Development mode
Enable:

bash
Copy code
export ALLOW_DEV_ENDPOINTS=1
export DEV_UNLIMITED_RESOURCES=1
# optional:
export DEV_DEFAULT_GOLD=99999999
export DEV_DEFAULT_WOOD=99999999
export DEV_DEFAULT_GEMS=999999
export DEFAULT_WORLD_RADIUS=3
# restart backend
Rules:

DEV_UNLIMITED_RESOURCES=1 ⇒ gold/wood not deducted, but validation still runs.

DEV endpoints disabled by default.

DEV endpoints:

bash
Copy code
# reset (townhall-only)
curl -s -X POST http://localhost:8002/dev/reset/test123 \
  -H 'Content-Type: application/json' \
  -d '{"wipe": true}'

# grant resources (including gems)
curl -s -X POST http://localhost:8002/dev/grant/test123 \
  -H 'Content-Type: application/json' \
  -d '{"gold":100000,"wood":100000,"gems":1000,"mode":"set"}'

# wipe user
curl -s -X POST http://localhost:8002/dev/wipe/test123
7) Roadmap (versions, minimal ballast)
v0.4 — FE contract stable (next)
Goal: smooth frontend work without refactors.

Apply rotation to footprints (swap w/h for 90/270 when rotatable).

Add 1–2 real footprint buildings (e.g. warehouse 2×2, longhouse 2×1).

Ensure catalog fully describes build menu (costs, footprint, rotatable, max_level).

v0.5 — Monetization primitives pack
Goal: same spend pattern reused everywhere.

Add speed-up primitive (spend gems):

POST /city/{user_id}/speedup_upgrade

modes:

finish instantly

reduce remaining time by X seconds

idempotency + ledger + atomic transaction + lock (same pattern as expand_gems)

v1.0 — Monetization fast launch (small but real)
Goal: earn money ASAP with minimal scope.

Gems top-up (server-side stub + later Stripe/App stores):

POST /shop/credit_gems (idempotent, ledger “credit”)

for launch you can gate it + simulate provider

Spend gems:

expand world (already done)

speed-up upgrades (from v0.5)

Move spend endpoints out of DEV:

replace ALLOW_DEV_ENDPOINTS gating with:

ENABLE_SPEND_ENDPOINTS=1 feature flag

later auth/token

Definition of done v1.0:

credit gems → spend gems (expand/speedup) works end-to-end

ledger contains credits + spends

idempotency prevents double-charges

v1.1+ (after revenue signal)
Better economy balancing (rates, costs, caps, sinks)

Premium-only cosmetics (skins, decorations) (cheap to build, high perceived value)

“Boosters” (time-limited production multipliers) via gems (ledger + idempo)

Simple “events” framework (daily bonuses, limited-time offers)

8) Ops: running 2 backend instances
You can run multiple backend instances against one Redis:

Redis is the source of truth

per-user lock prevents concurrent writes for the same player

lock TTL prevents deadlocks if one instance dies mid-request

Example:

bash
Copy code
uvicorn app.main:app --host 0.0.0.0 --port 8002
uvicorn app.main:app --host 0.0.0.0 --port 8003
Put a reverse proxy / LB in front → safe.

9) Parking lot (ideas we keep, not implementing now)
Portals between players’ cities (Nexus/Realm travel)

Quest hub buildings (tavern → quest board, factions)

Multiplayer modes using city as “overworld map”

Auto-upgrade queues (premium slots)

Premium buildings / unique production chains

yaml
Copy code

---

### Co je “první pro rychlou monetizaci v1.0” podle toho README?
1) **Credit gems** endpoint (i kdyby zatím jen “simulace nákupu” přes DEV/feature flag) + ledger “credit”  
2) **Speed-up** endpoint (stejný pattern jako expand_gems)  
3) Přepnout gating: `ALLOW_DEV_ENDPOINTS` pryč ze spend endpointů → `ENABLE_SPEND_ENDPOINTS=1` (+ později auth)

Když chceš, napíšu ti hned i **minimální specifikaci a přesné endpointy** pro `POST /shop/credit_gems` a `POST /city/{user_id}/speedup_upgrade` tak, aby to bylo konzistentní s tvým `ledger + idempotency` stylem a aby to FE mohlo rovnou napojit.
::contentReference[oaicite:0]{index=0}




DEVELOPER MODE



Thinking


