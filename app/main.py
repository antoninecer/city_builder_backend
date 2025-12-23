from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple, List, Iterable

from fastapi import FastAPI, HTTPException, Request, Body, Query

from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.redis_client import redis_client, close_redis




# -----------------------------------------------------------------------------
# Logging (readable + request-id)
# -----------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DEBUG_DUMP = os.getenv("DEBUG_DUMP", "0") == "1"  # when 1, logs more (careful in prod)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("city-backend")


def _client_ip(req: Request) -> str:
    # behind ALB/nginx use X-Forwarded-For
    xff = req.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if req.client:
        return req.client.host
    return "unknown"


# -----------------------------------------------------------------------------
# Redis per-user lock (atomic)
# -----------------------------------------------------------------------------
LOCK_TTL_MS = int(os.getenv("USER_LOCK_TTL_MS", "8000"))
LOCK_WAIT_MS = int(os.getenv("USER_LOCK_WAIT_MS", "2500"))
LOCK_RETRY_SLEEP_MS = int(os.getenv("USER_LOCK_RETRY_SLEEP_MS", "35"))

_UNLOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""

class ExpandGemsRequest(BaseModel):
    steps: int = 1

class CreditGemsRequest(BaseModel):
    user_id: str
    gems: int
    provider: str = "dev"
    purchase_id: Optional[str] = None

def _ledger_key(user_id: str) -> str:
    return f"ledger:{user_id}"

def _idempo_key(user_id: str, op: str, key: str) -> str:
    return f"idempo:{user_id}:{op}:{key}"

def _expand_cost_gems(current_radius: int, steps: int = 1) -> int:
    # simple non-linear growth, placeholder for monetization tuning
    base = 10  # radius 3->4
    r = max(0, int(current_radius))
    s = max(1, int(steps))
    total = 0.0
    for i in range(s):
        rr = r + i
        total += base * (1.55 ** max(0, rr - 3))
    return int(round(total))


class UserLock:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.key = f"lock:player:{user_id}"
        self.token = uuid.uuid4().hex
        self.acquired = False

    async def __aenter__(self):
        deadline = time.time() + (LOCK_WAIT_MS / 1000.0)
        while time.time() < deadline:
            ok = await redis_client.set(self.key, self.token, nx=True, px=LOCK_TTL_MS)
            if ok:
                self.acquired = True
                return self
            await _sleep_ms(LOCK_RETRY_SLEEP_MS)

        raise HTTPException(status_code=409, detail="Player is locked (try again).")

    async def __aexit__(self, exc_type, exc, tb):
        if not self.acquired:
            return
        try:
            await redis_client.eval(_UNLOCK_LUA, 1, self.key, self.token)
        except Exception as e:
            # lock expires by TTL anyway; not fatal
            log.warning(f"lock release failed user={self.user_id} err={e}")


async def _sleep_ms(ms: int) -> None:
    import asyncio
    await asyncio.sleep(ms / 1000.0)


# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------
app = FastAPI(title="City Builder Backend", version="0.3.0")


# -----------------------------------------------------------------------------
# CORS
# -----------------------------------------------------------------------------
ALLOWED_ORIGINS = [
    "https://isocity.api.ventureout.cz",
    "https://city.api.ventureout.cz",
    "http://localhost:3000",
    "http://localhost:8080",
]
extra = os.getenv("CORS_ORIGINS", "")
if extra.strip():
    ALLOWED_ORIGINS.extend([x.strip() for x in extra.split(",") if x.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request logging middleware (rid, ip, ms, status)
@app.middleware("http")
async def request_logger(request: Request, call_next):
    rid = str(uuid.uuid4())
    request.state.rid = rid
    ip = _client_ip(request)
    t0 = time.time()
    status = 500
    try:
        resp = await call_next(request)
        status = resp.status_code
        return resp
    finally:
        dt_ms = (time.time() - t0) * 1000.0
        log.info(
            f"rid={rid} ip={ip} {request.method} {request.url.path} status={status} {dt_ms:.1f}ms"
        )


@app.on_event("shutdown")
async def shutdown_event():
    await close_redis()


# =============================================================================
# === NEW: DEV FLAGS (ENV CONTROLLED) =========================================
# =============================================================================
ALLOW_DEV_ENDPOINTS = os.getenv("ALLOW_DEV_ENDPOINTS", "0") == "1"
DEV_UNLIMITED_RESOURCES = os.getenv("DEV_UNLIMITED_RESOURCES", "0") == "1"

# How much to grant on "reset" if unlimited is enabled (still stored, not virtual)
DEV_DEFAULT_GOLD = float(os.getenv("DEV_DEFAULT_GOLD", "99999999"))
DEV_DEFAULT_WOOD = float(os.getenv("DEV_DEFAULT_WOOD", "99999999"))
DEV_DEFAULT_GEMS = int(os.getenv("DEV_DEFAULT_GEMS", "999999"))


# Default world radius for a fresh player. radius=3 => 7x7 from -3..+3
DEFAULT_WORLD_RADIUS = int(os.getenv("DEFAULT_WORLD_RADIUS", "3"))

# If you want to hard-disable world bounds (true infinite) set to 1.
DEV_DISABLE_WORLD_BOUNDS = os.getenv("DEV_DISABLE_WORLD_BOUNDS", "0") == "1"


# -----------------------------------------------------------------------------
# Building configuration
# -----------------------------------------------------------------------------
# NOTE: footprint is present, currently all 1x1.
# We keep the config structure ready for future (2x1, 2x2, rotations).
BUILDING_CONFIG: Dict[str, Dict[str, Any]] = {
    "townhall": {
        "max_level": 10,
        "default_x": 0,
        "default_y": 0,
        "footprint": {"w": 1, "h": 1},
        "rotatable": False,
        "upgrade_cost_gold": [0, 200, 500, 1000, 2000, 3500, 6000, 9000, 14000, 20000],
        "upgrade_duration": [0, 60, 300, 1800, 3600, 7200, 14400, 28800, 43200, 86400],
    },
    "farm": {
        "max_level": 10,
        "default_x": 1,
        "default_y": 0,
        "footprint": {"w": 1, "h": 1},
        "rotatable": False,
        "upgrade_cost_gold": [0, 100, 300, 600, 1200, 2200, 3600, 5200, 8000, 12000],
        "upgrade_duration": [0, 60, 600, 1800, 3600, 7200, 14400, 28800, 43200, 86400],
        "production_per_hour_gold": [0, 10, 25, 50, 100, 160, 230, 310, 400, 520],
    },
    "lumbermill": {
        "max_level": 10,
        "default_x": 0,
        "default_y": 1,
        "footprint": {"w": 1, "h": 1},
        "rotatable": False,
        "upgrade_cost_gold": [0, 150, 400, 800, 1500, 2600, 4100, 6000, 9000, 13000],
        "upgrade_duration": [0, 60, 600, 1800, 3600, 7200, 14400, 28800, 43200, 86400],
        "production_per_hour_wood": [0, 15, 30, 60, 120, 190, 270, 360, 470, 600],
    },
    "house": {
        "max_level": 10,
        "default_x": -1,
        "default_y": 0,
        "footprint": {"w": 1, "h": 1},
        "rotatable": False,
        "upgrade_cost_gold": [0, 80, 200, 500, 1000, 1800, 2800, 4200, 6500, 10000],
        "upgrade_duration": [0, 60, 300, 1200, 3600, 7200, 14400, 28800, 43200, 86400],
    },
    "barracks": {
        "max_level": 10,
        "default_x": 0,
        "default_y": -1,
        "footprint": {"w": 1, "h": 1},
        "rotatable": False,
        "upgrade_cost_gold": [0, 300, 700, 1500, 3000, 5200, 8200, 12000, 18000, 26000],
        "upgrade_duration": [0, 120, 900, 3600, 7200, 14400, 28800, 43200, 86400, 172800],
    },
}

DEFAULT_RESOURCES = {"gold": 500.0, "wood": 300.0, "gems": 0}

def _build_catalog() -> Dict[str, Any]:
    """
    Returns a frontend-friendly catalog derived from BUILDING_CONFIG.
    FE can render build menu + ghosts without hardcoding server config.
    """
    catalog: Dict[str, Any] = {}
    for b_type, cfg in BUILDING_CONFIG.items():
        fp = cfg.get("footprint") or {"w": 1, "h": 1}

        # build cost convention: we use "level 2 upgrade cost" as build cost (same as /place)
        try:
            build_cost_gold = float(cfg["upgrade_cost_gold"][1])
        except Exception:
            build_cost_gold = 100.0

        catalog[b_type] = {
            "footprint": {"w": int(fp.get("w") or 1), "h": int(fp.get("h") or 1)},
            "rotatable": bool(cfg.get("rotatable", False)),
            "max_level": int(cfg.get("max_level") or 1),
            "build_cost_gold": build_cost_gold,
        }
    return catalog



# =============================================================================
# Pydantic requests
# =============================================================================
class SetRadiusRequest(BaseModel):
    radius: int

class UpgradeRequest(BaseModel):
    building_id: str

class PlaceRequest(BaseModel):
    building_type: str
    x: int
    y: int
    # NOTE: future-proof fields; frontend may send them later
    rotation: Optional[int] = None  # 0/90/180/270 or 0/1 (we ignore for now)


class DemolishRequest(BaseModel):
    building_id: str


class NewGameRequest(BaseModel):
    user_id: Optional[str] = None


# === DEV: grant resources (add or set) ======================================
class DevGrantRequest(BaseModel):
    gold: Optional[float] = None
    wood: Optional[float] = None
    gems: Optional[int] = None
    mode: str = "add"  # "add" or "set"


class DevResetRequest(BaseModel):
    # if true => wipe keys completely and create fresh townhall-only state
    wipe: bool = True


# =============================================================================
# Helpers: safe conversion + normalization/migration
# =============================================================================
def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


# =============================================================================
# === NEW: world model helpers (radius-based) =================================
# =============================================================================
def _player_key(user_id: str) -> str:
    return f"player:{user_id}"


def _city_key(user_id: str) -> str:
    return f"city:{user_id}:buildings"


def _world_key(user_id: str) -> str:
    return f"city:{user_id}:world"


def _default_world() -> Dict[str, Any]:
    # radius=3 => coords -3..+3 => 7x7
    r = DEFAULT_WORLD_RADIUS
    return {
        "radius": r,
        "anchor": "topleft",  # future use; you mentioned topleft anchor
        "created_at": time.time(),
    }


async def _load_world(user_id: str) -> Dict[str, Any]:
    raw = await redis_client.get(_world_key(user_id))
    if not raw:
        w = _default_world()
        await redis_client.set(_world_key(user_id), json.dumps(w))
        return w
    try:
        w = json.loads(raw)
        if not isinstance(w, dict):
            raise ValueError("world not dict")
    except Exception:
        w = _default_world()
        await redis_client.set(_world_key(user_id), json.dumps(w))
        return w

    # normalize
    if "radius" not in w:
        w["radius"] = DEFAULT_WORLD_RADIUS
    w["radius"] = int(w.get("radius") or DEFAULT_WORLD_RADIUS)
    if "anchor" not in w:
        w["anchor"] = "topleft"
    return w


def _world_bounds(radius: int) -> Tuple[int, int, int, int]:
    # returns min_x, max_x, min_y, max_y (inclusive)
    return (-radius, radius, -radius, radius)


def _is_inside_world(x: int, y: int, world: Dict[str, Any]) -> bool:
    if DEV_DISABLE_WORLD_BOUNDS:
        return True
    r = int(world.get("radius") or DEFAULT_WORLD_RADIUS)
    min_x, max_x, min_y, max_y = _world_bounds(r)
    return (min_x <= x <= max_x) and (min_y <= y <= max_y)


# =============================================================================
# === NEW: footprint helpers (currently 1x1, but ready for bigger) ============
# =============================================================================
def _get_footprint_for_type(b_type: str) -> Tuple[int, int]:
    cfg = BUILDING_CONFIG.get(b_type, {})
    fp = cfg.get("footprint") or {"w": 1, "h": 1}
    w = int(fp.get("w") or 1)
    h = int(fp.get("h") or 1)
    if w <= 0:
        w = 1
    if h <= 0:
        h = 1
    return w, h


def _tiles_for_footprint(x: int, y: int, fp_w: int, fp_h: int, rotation: Optional[int] = None) -> List[Tuple[int, int]]:
    """
    Returns list of tiles occupied by a footprint.
    Current convention: x,y is the "top-left tile" of the footprint in world grid coords.
    Rotation is accepted but currently ignored (we treat all as not rotated).
    """
    tiles: List[Tuple[int, int]] = []
    # future: apply rotation (swap w/h etc.)
    for dx in range(fp_w):
        for dy in range(fp_h):
            tiles.append((x + dx, y + dy))
    return tiles


def _building_occupied_tiles(b: Dict[str, Any]) -> List[Tuple[int, int]]:
    b_type = b.get("type") or "townhall"
    fp_w, fp_h = _get_footprint_for_type(b_type)
    x = int(b.get("x") or 0)
    y = int(b.get("y") or 0)
    rot = b.get("rotation")
    return _tiles_for_footprint(x, y, fp_w, fp_h, rot)


def _footprint_fits_world(x: int, y: int, b_type: str, world: Dict[str, Any], rotation: Optional[int] = None) -> bool:
    fp_w, fp_h = _get_footprint_for_type(b_type)
    tiles = _tiles_for_footprint(x, y, fp_w, fp_h, rotation)
    return all(_is_inside_world(tx, ty, world) for (tx, ty) in tiles)


def _footprint_collides(buildings: Dict[str, Dict[str, Any]], x: int, y: int, b_type: str, rotation: Optional[int] = None) -> bool:
    fp_w, fp_h = _get_footprint_for_type(b_type)
    target = set(_tiles_for_footprint(x, y, fp_w, fp_h, rotation))
    for b in buildings.values():
        occ = set(_building_occupied_tiles(b))
        if target.intersection(occ):
            return True
    return False


# =============================================================================
# Existing normalization/migration helpers (kept, just extended)
# =============================================================================
def _normalize_building(building_id: str, b: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure every building has:
      - type, level, x, y, upgrade_start, upgrade_end
    Also keeps fields for future:
      - rotation
    """
    out = dict(b) if isinstance(b, dict) else {}
    b_type = out.get("type")

    # fallback: old objects might have id without type
    if not b_type:
        if building_id.startswith("townhall"):
            b_type = "townhall"
        elif building_id.startswith("farm"):
            b_type = "farm"
        elif building_id.startswith("lumbermill"):
            b_type = "lumbermill"
        elif building_id.startswith("house"):
            b_type = "house"
        elif building_id.startswith("barracks"):
            b_type = "barracks"
        else:
            b_type = "townhall"

    cfg = BUILDING_CONFIG.get(b_type, BUILDING_CONFIG["townhall"])

    out["type"] = b_type
    out["level"] = int(out.get("level") or 1)

    # position (now can be negative; no clamping)
    out["x"] = int(out.get("x") if out.get("x") is not None else cfg.get("default_x", 0))
    out["y"] = int(out.get("y") if out.get("y") is not None else cfg.get("default_y", 0))

    # upgrade fields
    out["upgrade_start"] = out.get("upgrade_start", None)
    out["upgrade_end"] = out.get("upgrade_end", None)

    # handle "" or 0 stored by mistake
    if out["upgrade_start"] in ("", 0):
        out["upgrade_start"] = None
    if out["upgrade_end"] in ("", 0):
        out["upgrade_end"] = None

    if "rotation" not in out or out["rotation"] is None:
        out["rotation"] = 0
    
    # attach footprint for FE contract (stored per building)
    fp_w, fp_h = _get_footprint_for_type(b_type)
    out["footprint"] = {"w": fp_w, "h": fp_h}

    return out


def _normalize_buildings(buildings: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    changed = False
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(buildings, dict):
        return {}, True

    for bid, b in buildings.items():
        nb = _normalize_building(str(bid), b if isinstance(b, dict) else {})
        out[str(bid)] = nb
        if b != nb:
            changed = True

    return out, changed


# =============================================================================
# === NEW: default city buildings (townhall only, at 0,0) =====================
# =============================================================================
def _default_city_buildings() -> Dict[str, Dict[str, Any]]:
    th = BUILDING_CONFIG["townhall"]
    return {
        "townhall_0": {
            "type": "townhall",
            "level": 1,
            "x": int(th["default_x"]),
            "y": int(th["default_y"]),
            "upgrade_start": None,
            "upgrade_end": None,
            "rotation": None,
        }
    }


def _calc_production_per_hour(buildings: Dict[str, Dict[str, Any]]) -> Tuple[float, float]:
    gold_ph = 0.0
    wood_ph = 0.0
    for b in buildings.values():
        b_type = b.get("type")
        level = int(b.get("level") or 1)

        if b_type == "farm":
            rates = BUILDING_CONFIG["farm"].get("production_per_hour_gold", [0])
            gold_ph += float(rates[min(level - 1, len(rates) - 1)])
        elif b_type == "lumbermill":
            rates = BUILDING_CONFIG["lumbermill"].get("production_per_hour_wood", [0])
            wood_ph += float(rates[min(level - 1, len(rates) - 1)])

    return gold_ph, wood_ph


def _finish_upgrades_if_due(now: float, buildings: Dict[str, Dict[str, Any]]) -> bool:
    updated = False
    for b in buildings.values():
        ue = b.get("upgrade_end")
        if ue is not None and now >= float(ue):
            b["level"] = int(b.get("level") or 1) + 1
            b["upgrade_start"] = None
            b["upgrade_end"] = None
            updated = True
    return updated


# =============================================================================
# === NEW: resource helpers (supports DEV unlimited) ==========================
# =============================================================================
def _is_unlimited() -> bool:
    return bool(DEV_UNLIMITED_RESOURCES)


def _can_afford(cost: float, resources: Dict[str, Any]) -> bool:
    if _is_unlimited():
        return True
    return float(resources.get("gold") or 0.0) >= float(cost)


def _charge_gold(cost: float, resources: Dict[str, Any]) -> None:
    if _is_unlimited():
        return
    resources["gold"] = float(resources.get("gold") or 0.0) - float(cost)


# =============================================================================
# Endpoints
# =============================================================================
@app.get("/")
async def root():
    return {
        "message": "City Builder Backend is running.",
        "timestamp": time.time(),
        "dev": {
            "ALLOW_DEV_ENDPOINTS": bool(ALLOW_DEV_ENDPOINTS),
            "DEV_UNLIMITED_RESOURCES": bool(DEV_UNLIMITED_RESOURCES),
            "DEFAULT_WORLD_RADIUS": DEFAULT_WORLD_RADIUS,
            "DEV_DISABLE_WORLD_BOUNDS": bool(DEV_DISABLE_WORLD_BOUNDS),
        },
    }


@app.post("/new_game")
async def new_game(req: Request, body: NewGameRequest):
    """
    Creates a new player and returns user_id + initial state.
    """
    user_id = body.user_id or f"u_{uuid.uuid4().hex[:10]}"
    now = time.time()

    async with UserLock(user_id):
        player_key = _player_key(user_id)
        city_key = _city_key(user_id)
        world_key = _world_key(user_id)

        exists_player = await redis_client.exists(player_key)
        exists_city = await redis_client.exists(city_key)

        if exists_player or exists_city:
            raise HTTPException(status_code=409, detail="user_id already exists")

        
        if _is_unlimited():
            resources = {"gold": DEV_DEFAULT_GOLD, "wood": DEV_DEFAULT_WOOD, "gems": DEV_DEFAULT_GEMS, "last_collect": now}
        else:
            resources = {"gold": DEFAULT_RESOURCES["gold"], "wood": DEFAULT_RESOURCES["wood"], "gems": DEFAULT_RESOURCES["gems"], "last_collect": now}

        buildings = _default_city_buildings()
        world = _default_world()

        pipe = redis_client.pipeline()
        pipe.hset(player_key, mapping=resources)
        pipe.set(city_key, json.dumps(buildings))
        pipe.set(world_key, json.dumps(world))
        await pipe.execute()

    log.info(f"rid={req.state.rid} new_game user_id={user_id}")
    return {"user_id": user_id, "resources": resources, "buildings": buildings, "world": world, "server_time": now}


@app.get("/city/{user_id}")
async def get_city(req: Request, user_id: str):
    """
    Loads city and applies lazy progress:
      - finishes completed upgrades
      - applies idle production since last_collect
    Everything under per-user lock to avoid races.
    """
    now = time.time()
    player_key = _player_key(user_id)
    city_key = _city_key(user_id)
    world_key = _world_key(user_id)

    async with UserLock(user_id):
        resources_raw = await redis_client.hgetall(player_key)
        buildings_raw = await redis_client.get(city_key)

        # === NEW: world load/ensure
        world = await _load_world(user_id)
        radius = int(world.get("radius") or DEFAULT_WORLD_RADIUS)

        created = False

        # init player
        if not resources_raw:
            if _is_unlimited():
                resources = {"gold": DEV_DEFAULT_GOLD, "wood": DEV_DEFAULT_WOOD, "gems": DEV_DEFAULT_GEMS, "last_collect": now}
            else:
                resources = {"gold": DEFAULT_RESOURCES["gold"], "wood": DEFAULT_RESOURCES["wood"], "gems": DEFAULT_RESOURCES["gems"], "last_collect": now}  
            created = True
        else:
            resources = {
                "gold": _safe_float(resources_raw.get("gold"), DEFAULT_RESOURCES["gold"]),
                "wood": _safe_float(resources_raw.get("wood"), DEFAULT_RESOURCES["wood"]),
                "gems": _safe_int(resources_raw.get("gems"), DEFAULT_RESOURCES["gems"]),
                "last_collect": _safe_float(resources_raw.get("last_collect"), now),
            }
            if _is_unlimited():
                resources["gold"] = max(float(resources["gold"]), DEV_DEFAULT_GOLD)
                resources["wood"] = max(float(resources["wood"]), DEV_DEFAULT_WOOD)
                resources["gems"] = max(int(resources["gems"]), DEV_DEFAULT_GEMS)   
            
        # init city
        if not buildings_raw:
            buildings = _default_city_buildings()
            created = True
        else:
            try:
                buildings_loaded = json.loads(buildings_raw)
            except Exception:
                buildings_loaded = {}
            buildings, migrated = _normalize_buildings(buildings_loaded)
            if migrated:
                created = True

        # finish upgrades
        updated = _finish_upgrades_if_due(now, buildings)

        # idle production
        gold_ph, wood_ph = _calc_production_per_hour(buildings)
        last_collect = float(resources.get("last_collect") or now)
        elapsed_hours = max(0.0, (now - last_collect) / 3600.0)

        # In unlimited mode, we still advance last_collect, but do not need to grow resources.
        if not _is_unlimited():
            resources["gold"] = float(resources["gold"]) + elapsed_hours * gold_ph
            resources["wood"] = float(resources["wood"]) + elapsed_hours * wood_ph
        resources["last_collect"] = now

        # store back (atomic batch)
        pipe = redis_client.pipeline()
        pipe.hset(
            player_key,
            mapping={
                "gold": resources["gold"],
                "wood": resources["wood"],
                "gems": resources["gems"],
                "last_collect": resources["last_collect"],
            },
        )
        if updated or created:
            pipe.set(city_key, json.dumps(buildings))
        # world is ensured by _load_world; still keep it up-to-date if missing
        pipe.set(world_key, json.dumps(world))
        await pipe.execute()

        if DEBUG_DUMP:
            min_x, max_x, min_y, max_y = _world_bounds(radius)
            log.info(
                f"rid={req.state.rid} user={user_id} gold_ph={gold_ph} wood_ph={wood_ph} "
                f"elapsed_h={elapsed_hours:.4f} buildings={len(buildings)} "
                f"world_radius={radius} bounds=({min_x},{min_y})..({max_x},{max_y})"
            )

    # === NEW: return world info, so frontend can render 7x7 around (0,0) properly
    r = int(world.get("radius") or DEFAULT_WORLD_RADIUS)
    min_x, max_x, min_y, max_y = _world_bounds(r)
    world_payload = {
        "radius": r,
        "grid": {"w": (2 * r + 1), "h": (2 * r + 1)},
        "bounds": {"min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y},
        "anchor": world.get("anchor", "topleft"),
    }

    return {
        "user_id": user_id,
        "resources": {"gold": round(float(resources["gold"]), 2), "wood": round(float(resources["wood"]), 2), "gems": int(resources["gems"])},
        "buildings": buildings,
        "world": world_payload,
        "catalog": _build_catalog(),
        "server_time": now,
    }


@app.post("/city/{user_id}/upgrade")
async def upgrade_building(req: Request, user_id: str, request: UpgradeRequest):
    """
    Atomic:
      - load state
      - validate
      - charge gold (unless unlimited)
      - set upgrade_start/upgrade_end
      - store back
    """
    now = time.time()
    building_id = request.building_id

    player_key = _player_key(user_id)
    city_key = _city_key(user_id)

    async with UserLock(user_id):
        buildings_raw = await redis_client.get(city_key)
        resources_raw = await redis_client.hgetall(player_key)

        if not buildings_raw or not resources_raw:
            raise HTTPException(status_code=404, detail="Player not found")

        try:
            buildings_loaded = json.loads(buildings_raw)
        except Exception:
            buildings_loaded = {}
        buildings, migrated = _normalize_buildings(buildings_loaded)

        if building_id not in buildings:
            raise HTTPException(status_code=404, detail="Building not found")

        # lazy completion for consistency
        _finish_upgrades_if_due(now, buildings)

        resources = {
            "gold": _safe_float(resources_raw.get("gold"), 0.0),
            "wood": _safe_float(resources_raw.get("wood"), 0.0),
            "last_collect": _safe_float(resources_raw.get("last_collect"), now),
        }

        b = buildings[building_id]
        b_type = b["type"]

        if b_type not in BUILDING_CONFIG:
            raise HTTPException(status_code=400, detail="Unknown building type")

        if b.get("upgrade_end") is not None:
            raise HTTPException(status_code=400, detail="Upgrade already running")

        cfg = BUILDING_CONFIG[b_type]
        current_level = int(b.get("level") or 1)
        next_level = current_level + 1

        if next_level > int(cfg["max_level"]):
            raise HTTPException(status_code=400, detail="Max level reached")

        try:
            cost_gold = float(cfg["upgrade_cost_gold"][next_level - 1])
            duration = float(cfg["upgrade_duration"][next_level - 1])
        except Exception:
            raise HTTPException(status_code=500, detail="Invalid building config")

        if not _can_afford(cost_gold, resources):
            raise HTTPException(status_code=400, detail="Not enough gold")

        _charge_gold(cost_gold, resources)
        b["upgrade_start"] = now
        b["upgrade_end"] = now + duration

        pipe = redis_client.pipeline()
        pipe.hset(player_key, mapping={"gold": resources["gold"]})
        pipe.set(city_key, json.dumps(buildings))
        await pipe.execute()

        if DEBUG_DUMP:
            log.info(
                f"rid={req.state.rid} user={user_id} upgrade {building_id} {b_type} "
                f"{current_level}->{next_level} cost={cost_gold} dur={duration}s unlimited={_is_unlimited()}"
            )

    return {
        "message": f"Upgrade {b_type} ({building_id}) to level {next_level} started",
        "cost_gold": cost_gold,
        "duration_seconds": duration,
        "finish_time": b["upgrade_end"],
        "server_time": now,
        "unlimited": bool(_is_unlimited()),
    }


@app.post("/city/{user_id}/place")
async def place_building(req: Request, user_id: str, request: PlaceRequest):
    """
    Atomic:
      - load state
      - validate position + world bounds + footprint + resources
      - add building
      - charge gold (unless unlimited)
      - store back
    """
    now = time.time()
    building_type = request.building_type
    x = int(request.x)
    y = int(request.y)
    rotation = request.rotation

    if building_type not in BUILDING_CONFIG:
        raise HTTPException(status_code=400, detail="Unknown building type")

    # === NEW: allow negative coords (no x<0/y<0 rejection)
    # bounds are handled by world radius

    player_key = _player_key(user_id)
    city_key = _city_key(user_id)

    async with UserLock(user_id):
        buildings_raw = await redis_client.get(city_key)
        resources_raw = await redis_client.hgetall(player_key)

        if not buildings_raw or not resources_raw:
            raise HTTPException(status_code=404, detail="Player not found")

        # === NEW: load world (radius)
        world = await _load_world(user_id)

        # world bounds check (including footprint)
        if not _footprint_fits_world(x, y, building_type, world, rotation):
            raise HTTPException(status_code=400, detail="Out of world bounds")

        try:
            buildings_loaded = json.loads(buildings_raw)
        except Exception:
            buildings_loaded = {}
        buildings, migrated = _normalize_buildings(buildings_loaded)

        # finish upgrades (consistency)
        _finish_upgrades_if_due(now, buildings)

        resources = {
            "gold": _safe_float(resources_raw.get("gold"), 0.0),
            "wood": _safe_float(resources_raw.get("wood"), 0.0),
            "last_collect": _safe_float(resources_raw.get("last_collect"), now),
        }

        # === NEW: footprint collision check (instead of x/y single-tile check)
        if _footprint_collides(buildings, x, y, building_type, rotation):
            raise HTTPException(status_code=400, detail="Position is occupied")

        cfg = BUILDING_CONFIG[building_type]
        # build cost: use "level 2 cost" as before
        try:
            build_cost_gold = float(cfg["upgrade_cost_gold"][1])
        except Exception:
            build_cost_gold = 100.0

        if not _can_afford(build_cost_gold, resources):
            raise HTTPException(status_code=400, detail="Not enough gold to build")

        new_id = f"{building_type}_{int(now * 1000)}"
        rot = int(rotation) if rotation is not None else 0
        fp_w, fp_h = _get_footprint_for_type(building_type)

        buildings[new_id] = {
            "type": building_type,
            "level": 1,
            "x": x,
            "y": y,
            "upgrade_start": None,
            "upgrade_end": None,
            "rotation": rot,
            "footprint": {"w": fp_w, "h": fp_h},
        }

        _charge_gold(build_cost_gold, resources)

        pipe = redis_client.pipeline()
        pipe.hset(player_key, mapping={"gold": resources["gold"]})
        pipe.set(city_key, json.dumps(buildings))
        await pipe.execute()

        if DEBUG_DUMP:
            fp_w, fp_h = _get_footprint_for_type(building_type)
            log.info(
                f"rid={req.state.rid} user={user_id} place {new_id} type={building_type} "
                f"at=({x},{y}) fp={fp_w}x{fp_h} cost={build_cost_gold} unlimited={_is_unlimited()}"
            )

    return {
        "message": f"Built {building_type} at ({x},{y})",
        "building_id": new_id,
        "cost_gold": build_cost_gold,
        "server_time": now,
        "unlimited": bool(_is_unlimited()),
    }


@app.post("/city/{user_id}/demolish")
async def demolish_building(req: Request, user_id: str, request: DemolishRequest):
    """
    Atomic:
      - load state
      - validate building_id
      - delete building
      - optionally refund part of gold (still works even in unlimited, but refund is informational)
      - store back
    """
    now = time.time()
    building_id = request.building_id

    player_key = _player_key(user_id)
    city_key = _city_key(user_id)

    async with UserLock(user_id):
        buildings_raw = await redis_client.get(city_key)
        resources_raw = await redis_client.hgetall(player_key)

        if not buildings_raw or not resources_raw:
            raise HTTPException(status_code=404, detail="Player not found")

        try:
            buildings_loaded = json.loads(buildings_raw)
        except Exception:
            buildings_loaded = {}
        buildings, migrated = _normalize_buildings(buildings_loaded)

        # finish upgrades (consistency)
        _finish_upgrades_if_due(now, buildings)

        if building_id not in buildings:
            raise HTTPException(status_code=404, detail="Building not found")

        b = buildings[building_id]
        b_type = b.get("type", "unknown")

        # do not allow demolish townhall
        if b_type == "townhall":
            raise HTTPException(status_code=400, detail="Townhall cannot be demolished")

        resources = {
            "gold": _safe_float(resources_raw.get("gold"), 0.0),
            "wood": _safe_float(resources_raw.get("wood"), 0.0),
            "last_collect": _safe_float(resources_raw.get("last_collect"), now),
        }

        # refund (25% of build cost) same logic as /place
        refund_gold = 0.0
        if b_type in BUILDING_CONFIG:
            try:
                base_cost = float(BUILDING_CONFIG[b_type]["upgrade_cost_gold"][1])
            except Exception:
                base_cost = 100.0
            refund_gold = round(base_cost * 0.25, 2)

        # delete
        del buildings[building_id]

        # refund (unless unlimited; in unlimited mode it's not needed but harmless)
        if not _is_unlimited():
            resources["gold"] = float(resources["gold"]) + float(refund_gold)

        pipe = redis_client.pipeline()
        pipe.hset(player_key, mapping={"gold": resources["gold"]})
        pipe.set(city_key, json.dumps(buildings))
        await pipe.execute()

        if DEBUG_DUMP:
            log.info(
                f"rid={req.state.rid} user={user_id} demolish {building_id} type={b_type} refund={refund_gold} unlimited={_is_unlimited()}"
            )

    return {
        "message": f"Demolished {b_type} ({building_id})",
        "refund_gold": refund_gold,
        "server_time": now,
        "unlimited": bool(_is_unlimited()),
    }


# =============================================================================
# === NEW: optional world expansion endpoint (not tied to payments yet) =======
# =============================================================================
class ExpandRequest(BaseModel):
    steps: int = 1  # default expand by 1 ring (one row on each side)


def _expand_cost_gold(current_radius: int, steps: int = 1) -> float:
    """
    Exponential-ish cost curve for expansions (future microtransactions friendly).
    You requested: not linear, grows faster.
    This is a placeholder formula; can be replaced later.
    """
    # base cost for radius 3->4
    base = 250.0
    r = max(0, int(current_radius))
    s = max(1, int(steps))
    # geometric growth
    # cost for each step: base * (1.65 ** (r-3))
    total = 0.0
    for i in range(s):
        rr = r + i
        total += base * (1.65 ** max(0, rr - 3))
    return round(total, 2)


@app.post("/city/{user_id}/expand")
async def expand_world(req: Request, user_id: str, body: ExpandRequest):
    """
    Expand world radius by N steps (each step adds one ring around the map).
    This is the backend primitive you can later bind to payments/gems.
    """
    now = time.time()
    steps = int(body.steps or 1)
    if steps < 1:
        steps = 1
    if steps > 50:
        raise HTTPException(status_code=400, detail="Too many steps")

    player_key = _player_key(user_id)
    world_key = _world_key(user_id)

    async with UserLock(user_id):
        resources_raw = await redis_client.hgetall(player_key)
        if not resources_raw:
            raise HTTPException(status_code=404, detail="Player not found")

        world = await _load_world(user_id)
        r = int(world.get("radius") or DEFAULT_WORLD_RADIUS)

        resources = {
            "gold": _safe_float(resources_raw.get("gold"), 0.0),
            "wood": _safe_float(resources_raw.get("wood"), 0.0),
            "last_collect": _safe_float(resources_raw.get("last_collect"), now),
        }

        cost = _expand_cost_gold(r, steps)

        if not _can_afford(cost, resources):
            raise HTTPException(status_code=400, detail=f"Not enough gold to expand (cost {cost})")

        _charge_gold(cost, resources)

        world["radius"] = int(r + steps)
        world["updated_at"] = now

        pipe = redis_client.pipeline()
        pipe.hset(player_key, mapping={"gold": resources["gold"]})
        pipe.set(world_key, json.dumps(world))
        await pipe.execute()

    return {
        "message": f"World expanded by {steps}",
        "new_radius": int(world["radius"]),
        "cost_gold": cost,
        "server_time": now,
        "unlimited": bool(_is_unlimited()),
    }

ENABLE_SHOP_ENDPOINTS = os.getenv("ENABLE_SHOP_ENDPOINTS", "0") == "1"

@app.post("/shop/credit_gems")
async def shop_credit_gems(req: Request, body: CreditGemsRequest):
    # safety: zatím vypnuté, dokud to nehlídá auth / feature flag
    if not ENABLE_SHOP_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Not Found")

    now = time.time()

    idem = (req.headers.get("Idempotency-Key") or "").strip()
    if not idem:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    if body.gems <= 0:
        raise HTTPException(status_code=400, detail="gems must be > 0")

    user_id = body.user_id
    player_key = _player_key(user_id)

    async with UserLock(user_id):
        # idempotency check
        idk = _idempo_key(user_id, "credit_gems", idem)
        existing = await redis_client.get(idk)
        if existing:
            try:
                return json.loads(existing)
            except Exception:
                pass

        resources_raw = await redis_client.hgetall(player_key)
        if not resources_raw:
            raise HTTPException(status_code=404, detail="Player not found")

        cur_gems = _safe_int(resources_raw.get("gems"), 0)
        new_gems = cur_gems + int(body.gems)

        entry = {
            "id": uuid.uuid4().hex,
            "type": "credit",
            "reason": "purchase_gems",
            "delta": {"gems": int(body.gems)},
            "meta": {
                "provider": body.provider,
                "purchase_id": body.purchase_id,
                "idempotency_key": idem,
            },
            "ts": now,
        }

        resp = {
            "message": "Gems credited",
            "user_id": user_id,
            "gems_added": int(body.gems),
            "gems": int(new_gems),
            "server_time": now,
        }

        pipe = redis_client.pipeline(transaction=True)
        pipe.hset(player_key, mapping={"gems": new_gems})
        pipe.lpush(_ledger_key(user_id), json.dumps(entry))
        pipe.ltrim(_ledger_key(user_id), 0, 999)
        pipe.set(idk, json.dumps(resp), ex=60 * 60 * 24 * 7)
        await pipe.execute()

    return resp

# =============================================================================
# === DEV ENDPOINTS ===========================================================
# =============================================================================
def _require_dev():
    if not ALLOW_DEV_ENDPOINTS:
        raise HTTPException(status_code=403, detail="DEV endpoints are disabled (set ALLOW_DEV_ENDPOINTS=1)")


@app.post("/dev/reset/{user_id}")
async def dev_reset(req: Request, user_id: str, body: DevResetRequest):
    """
    DEV: Reset player state.
    - Deletes player/buildings/world keys (if wipe=True)
    - Creates fresh player with townhall only (0,0) and default radius (7x7 by default)
    - If DEV_UNLIMITED_RESOURCES=1, gives huge resources
    """
    _require_dev()
    now = time.time()

    async with UserLock(user_id):
        player_key = _player_key(user_id)
        city_key = _city_key(user_id)
        world_key = _world_key(user_id)

        if body.wipe:
            await redis_client.delete(player_key)
            await redis_client.delete(city_key)
            await redis_client.delete(world_key)

        if _is_unlimited():
            resources = {"gold": DEV_DEFAULT_GOLD, "wood": DEV_DEFAULT_WOOD, "gems": DEV_DEFAULT_GEMS, "last_collect": now}
        else:
            resources = {"gold": DEFAULT_RESOURCES["gold"], "wood": DEFAULT_RESOURCES["wood"], "gems": DEFAULT_RESOURCES["gems"], "last_collect": now}

        buildings = _default_city_buildings()
        world = _default_world()

        pipe = redis_client.pipeline()
        pipe.hset(player_key, mapping=resources)
        pipe.set(city_key, json.dumps(buildings))
        pipe.set(world_key, json.dumps(world))
        await pipe.execute()

    log.info(f"rid={req.state.rid} DEV reset user_id={user_id} wipe={body.wipe} unlimited={_is_unlimited()}")
    return {
        "status": "ok",
        "message": "Reset done",
        "user_id": user_id,
        "resources": resources,
        "world": world,
        "server_time": now,
    }


@app.post("/dev/grant/{user_id}")
async def dev_grant(req: Request, user_id: str, body: DevGrantRequest):
    """
    DEV: Add or set resources.
    mode="add": increments by gold/wood
    mode="set": sets to gold/wood
    """
    _require_dev()
    now = time.time()

    mode = (body.mode or "add").strip().lower()
    if mode not in ("add", "set"):
        raise HTTPException(status_code=400, detail="mode must be add or set")

    async with UserLock(user_id):
        player_key = _player_key(user_id)
        resources_raw = await redis_client.hgetall(player_key)
        if not resources_raw:
            raise HTTPException(status_code=404, detail="Player not found")

        cur_gold = _safe_float(resources_raw.get("gold"), 0.0)
        cur_wood = _safe_float(resources_raw.get("wood"), 0.0)
        cur_gems = _safe_int(resources_raw.get("gems"), 0)

        g = body.gold
        w = body.wood
        gems = body.gems

        if mode == "add":
            if g is not None:
                cur_gold += float(g)
            if w is not None:
                cur_wood += float(w)
            if gems is not None:
                cur_gems += int(gems)
        else:
            if g is not None:
                cur_gold = float(g)
            if w is not None:
                cur_wood = float(w)
            if gems is not None:
                cur_gems = int(gems)

        await redis_client.hset(player_key, mapping={"gold": cur_gold, "wood": cur_wood, "gems": cur_gems})

    log.info(f"rid={req.state.rid} DEV grant user_id={user_id} mode={mode} gold={body.gold} wood={body.wood}")
    return {
        "status": "ok",
        "user_id": user_id,
        "gold": round(cur_gold, 2),
        "wood": round(cur_wood, 2),
        "gems": cur_gems,
        "server_time": now,
    }

# Alias for convenience / backwards-compat in scripts:
@app.post("/dev/resources/{user_id}")
async def dev_resources_alias(req: Request, user_id: str, body: DevGrantRequest = Body(...)):
    # reuse the same implementation as /dev/grant
    return await dev_grant(req=req, user_id=user_id, body=body)

@app.post("/dev/wipe/{user_id}")
async def dev_wipe(req: Request, user_id: str):
    """
    DEV: Completely remove all keys for the player (no re-create).
    Useful if you want a totally clean slate.
    """
    _require_dev()
    now = time.time()

    async with UserLock(user_id):
        await redis_client.delete(_player_key(user_id))
        await redis_client.delete(_city_key(user_id))
        await redis_client.delete(_world_key(user_id))

    log.info(f"rid={req.state.rid} DEV wipe user_id={user_id}")
    return {"status": "ok", "message": "Wiped", "user_id": user_id, "server_time": now}

@app.post("/dev/world/set_radius/{user_id}")
async def dev_world_set_radius(
    req: Request,
    user_id: str,
    body: Optional[SetRadiusRequest] = Body(default=None),
    radius: Optional[int] = Query(default=None),
):
    if not ALLOW_DEV_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Not Found")

    now = time.time()

    # Accept both JSON body and query param
    r = None
    if body is not None:
        r = body.radius
    if r is None:
        r = radius

    if r is None:
        raise HTTPException(status_code=422, detail="radius is required (use JSON body or ?radius=...)")

    if r < 0:
        raise HTTPException(status_code=400, detail="radius must be >= 0")
    if r > 2000:
        raise HTTPException(status_code=400, detail="radius too large")

    async with UserLock(user_id):
        world = await _load_world(user_id)
        world["radius"] = int(r)
        world["updated_at"] = now
        await redis_client.set(_world_key(user_id), json.dumps(world))

    log.info(f"rid={req.state.rid} DEV set_radius user_id={user_id} radius={r}")
    return {"status": "ok", "user_id": user_id, "world": world, "server_time": now}

@app.post("/city/{user_id}/expand_gems")
async def expand_world_gems(req: Request, user_id: str, body: ExpandGemsRequest):
    if not ALLOW_DEV_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Not Found")

    now = time.time()
    steps = int(body.steps or 1)
    if steps < 1:
        steps = 1
    if steps > 50:
        raise HTTPException(status_code=400, detail="Too many steps")

    idem = (req.headers.get("Idempotency-Key") or "").strip()
    if not idem:
        raise HTTPException(400, "Idempotency-Key header is required")

    player_key = _player_key(user_id)
    world_key = _world_key(user_id)

    async with UserLock(user_id):
        # idempotency check
        idk = _idempo_key(user_id, "expand_gems", idem)
        existing = await redis_client.get(idk)
        if existing:
            # return the stored response (exactly the same)
            try:
                return json.loads(existing)
            except Exception:
                # fallthrough (should not happen)
                pass

        resources_raw = await redis_client.hgetall(player_key)
        if not resources_raw:
            raise HTTPException(status_code=404, detail="Player not found")

        world = await _load_world(user_id)
        r = int(world.get("radius") or DEFAULT_WORLD_RADIUS)

        cur_gems = _safe_int(resources_raw.get("gems"), 0)
        cost = _expand_cost_gems(r, steps)

        if cur_gems < cost:
            raise HTTPException(status_code=400, detail=f"Not enough gems to expand (cost {cost})")

        cur_gems -= cost
        world["radius"] = int(r + steps)
        world["updated_at"] = now

        # ledger entry
        entry = {
            "id": uuid.uuid4().hex,
            "type": "spend",
            "reason": "expand_world",
            "delta": {"gems": -cost},
            "meta": {"steps": steps, "from_radius": r, "to_radius": int(world["radius"])},
            "ts": now,
        }

        # save: resources + world + ledger + idempotency response
        resp = {
            "message": f"World expanded by {steps} (gems)",
            "new_radius": int(world["radius"]),
            "cost_gems": int(cost),
            "gems": int(cur_gems),
            "server_time": now,
        }

        pipe = redis_client.pipeline(transaction=True)
        pipe.hset(player_key, mapping={"gems": cur_gems})
        pipe.set(world_key, json.dumps(world))
        pipe.lpush(_ledger_key(user_id), json.dumps(entry))
        pipe.ltrim(_ledger_key(user_id), 0, 999)
        pipe.set(idk, json.dumps(resp), ex=60 * 60 * 24 * 7)  # keep idempotency 7 days
        await pipe.execute()

    return resp

