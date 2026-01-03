# City Builder Backend — Dev README (Contract + Roadmap)

Backend for an isometric, tile-based city builder.  
Redis is the source of truth. Code in English.  
This README is the **FE/BE contract** and the **single source of truth**.

---

## 0) Status (today)

### Working now
- World is centered at `(0,0)` with **radius-based bounds**  
  (default `radius=3` ⇒ `7×7`)
- Buildings are stored in **world tile coordinates** (negative coords allowed)
- Placement uses **footprint-aware collision + bounds checks**  
  (ready for 2×1, 2×2…)
- `rotation` is stored (currently not affecting geometry)
- Player resources: **gold, wood, gems, last_collect**
- `GET /city/{user_id}` returns a **catalog**  
  (frontend does NOT hardcode building config)
- Monetization primitive exists: **expand world by spending gems**
  - `Idempotency-Key`
  - immutable-ish **ledger entry**
  - atomic Redis `MULTI/EXEC`
  - protected by **per-user lock**

### Safety note
- `POST /city/{user_id}/expand_gems` is currently gated by  
  `ALLOW_DEV_ENDPOINTS`
- For v1.0 monetization this will move behind a proper feature flag / auth

---

## 1) Core world model

### Coordinates
- Buildings use WORLD tile coordinates `(x,y)`
- `(0,0)` is the center (Townhall start)

Default world:
- `radius = 3`
- bounds: `x ∈ [-3..3]`, `y ∈ [-3..3]`
- grid: `7×7`

### World payload (`GET /city/{user_id}`)

```json
{
  "world": {
    "radius": 3,
    "grid": { "w": 7, "h": 7 },
    "bounds": {
      "min_x": -3,
      "max_x": 3,
      "min_y": -3,
      "max_y": 3
    },
    "anchor": "topleft"
  }
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
  "footprint": { "w": 1, "h": 1 }
}
Footprint + rotation rules
Server config (BUILDING_CONFIG) defines:

footprint {w,h}

rotatable

Placement validates:

all footprint tiles are inside world bounds

no footprint tile collides with another building

Rotation is stored but geometry is currently treated as unrotated
(next step: swap w/h for 90° / 270° if rotatable)

Townhall rules
Exactly one townhall_0

Cannot be demolished

Reset recreates townhall at (0,0)

3) Player state + lazy progress
Player resources (Redis)
Stored in Redis hash player:{user_id}

json
Copy code
{
  "gold": number,
  "wood": number,
  "gems": number,
  "last_collect": timestamp
}
Lazy progress (GET /city/{user_id})
finishes upgrades when upgrade_end <= now

applies idle production since last_collect

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
Expand world (gold)
bash
Copy code
curl -s -X POST http://localhost:8002/city/test123/expand \
  -H 'Content-Type: application/json' \
  -d '{"steps":1}'
5) Monetization primitives (v0.x)
A) Spend gems → expand world
Endpoint:

bash
Copy code
POST /city/{user_id}/expand_gems
Requires header:

makefile
Copy code
Idempotency-Key: <unique>
Behavior:

spends gems

expands radius

writes ledger entry to ledger:{user_id}

stores exact response in
idempo:{user_id}:expand_gems:<key> (TTL 7 days)

atomic Redis transaction + per-user lock

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
export DEV_DEFAULT_GOLD=99999999
export DEV_DEFAULT_WOOD=99999999
export DEV_DEFAULT_GEMS=999999
export DEFAULT_WORLD_RADIUS=3
Rules:

DEV_UNLIMITED_RESOURCES=1 ⇒ gold/wood not deducted

DEV endpoints disabled by default

DEV endpoints:

bash
Copy code
curl -s -X POST http://localhost:8002/dev/reset/test123 \
  -H 'Content-Type: application/json' \
  -d '{"wipe": true}'

curl -s -X POST http://localhost:8002/dev/grant/test123 \
  -H 'Content-Type: application/json' \
  -d '{"gold":100000,"wood":100000,"gems":1000,"mode":"set"}'

curl -s -X POST http://localhost:8002/dev/wipe/test123
7) Roadmap
v0.4 — FE contract stable
Apply rotation to footprints

Add real footprint buildings (2×1, 2×2)

Ensure catalog fully describes build menu

v0.5 — Monetization primitives pack
Add speed-up primitive:

bash
Copy code
POST /city/{user_id}/speedup_upgrade
Same pattern: idempotency + ledger + lock

v1.0 — Monetization fast launch
Credit gems endpoint

Spend gems: expand + speedup

Replace ALLOW_DEV_ENDPOINTS with ENABLE_SPEND_ENDPOINTS

City Builder Backend — Next Steps (Shared Cities)
Status: 1. 1. 2026
Backend is ready for shared cities, but permissions are not wired yet.

Goal
One city → multiple users

Roles: owner / editor / viewer

Backend decides who can mutate a city

Data model: city_members
Redis key:

css
Copy code
city:{city_id}:members
json
Copy code
{
  "owner": "user_123",
  "members": {
    "user_123": "owner",
    "user_456": "editor",
    "user_789": "viewer"
  },
  "created_at": 1700000000
}
Roles:

owner – full control

editor – build / upgrade / demolish

viewer – read only

get_city_id_for_user()
Today
csharp
Copy code
user_id -> city_id (implicit 1:1)
Target
rust
Copy code
user_id -> city_id (via city_members)
Invite flow (design)
Endpoints:

bash
Copy code
POST /city/{user_id}/invite
POST /invite/accept
Owner creates invite → user accepts → added to city_members

Permission checks
Apply to all mutating endpoints:

python
Copy code
city_id = get_city_id_for_user(user_id)
if not can_modify_city(user_id, city_id):
    raise HTTPException(403)
Locks
Today: UserLock(user_id)

Future: CityLock(city_id)

What NOT to do yet
❌ Auth

❌ UI for roles

❌ Monetization tuning

❌ Refactoring main.py

Implementation order
Create city_members

Update get_city_id_for_user

Implement can_modify_city

Wire permission checks

Add invite flow

Then frontend UX

Summary
Backend is ready.
Next step is not fixing bugs —
it is teaching the backend who is allowed to act.

🧱 BACKEND TODO – architektura budov (future-proof)
🎯 Cíl

oddělit doménu „budovy“ z main.py

umožnit:

snadné přidávání nových budov

různé footprinty

různé produkce / upgrady

zabránit dalšímu bobtnání main.py

📁 Navržená struktura
app/
├── main.py                  # jen API + orchestrace
├── services/
│   ├── city.py               # města, membership, invites
│   ├── buildings.py          # 👈 VŠE kolem budov
│   ├── world.py              # (později) radius, expand, bounds
│   └── economy.py            # (později) produkce, balance


Pravidlo:

main.py NESMÍ znát konkrétní budovy ani jejich config.

🧠 Co patří do services/buildings.py
1️⃣ Building registry (jediný zdroj pravdy)
BUILDING_CONFIG = {
  "farm": {...},
  "lumbermill": {...},
}


typ

max_level

footprint {w,h}

rotatable

upgrade_cost

upgrade_duration

production_xxx

👉 main.py NESMÍ mít BUILDING_CONFIG

2️⃣ Public API (funkce, které main.py smí volat)
get_building_config(type)
get_footprint(type)
get_build_cost(type)
get_upgrade_cost(type, level)
get_upgrade_duration(type, level)
get_production(building)


Main se ptá:

„kolik stojí upgrade?“
ne:
„cfg[‘farm’][…]“

3️⃣ Footprint logika (už tam skoro je – jen přesunout)
tiles_for_footprint(x, y, type, rotation)
footprint_fits_world(...)
footprint_collides(...)


➡️ žádná footprint logika v main.py

4️⃣ Normalizace budovy (MIGRACE)
normalize_building(bid, raw)
normalize_buildings(dict)


doplní:

level

footprint

rotation

zpětná kompatibilita

🏗️ Jak správně PŘIDAT NOVOU BUDOVU (checklist)
✅ Krok 1 – přidat config
BUILDING_CONFIG["market"] = {
  "max_level": 5,
  "footprint": {"w": 2, "h": 2},
  "rotatable": True,
  "upgrade_cost_gold": [...],
  "upgrade_duration": [...],
}


❌ NIC jiného se zatím nemění

✅ Krok 2 – produkce (pokud má)

buď:

gold / wood

nebo speciální efekt (později)

➡️ get_production(building) musí umět nový typ

✅ Krok 3 – frontend dostane automaticky:

footprint

max_level

build_cost

(přes /city → catalog)

🚫 Co se NESMÍ stát

❌ přidat budovu úpravou 5 endpointů

❌ psát if b_type == "farm" v main.py

❌ duplicita cost výpočtů

🔮 Co už je připravené (a je to dobře)

✔ footprint support
✔ negative coords
✔ world bounds
✔ upgrade timers
✔ speedup logic
✔ catalog endpoint

To znamená:

backend je už teď připravený na 2×2, 3×2, rotace i DLC budovy

📌 Doporučení do README (jedna věta)

All building definitions and logic live in services/buildings.py.
main.py must never reference building internals directly.

🧭 Co bych dělal jako další backend krok (až po UX)

vyříznout:

BUILDING_CONFIG

footprint helpers

production calc
z main.py → services/buildings.py