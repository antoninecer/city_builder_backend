# app/services/city.py

import json
import time
import uuid
from app.redis_client import redis_client

INVITE_TTL_SECONDS = 60 * 60 * 24  # 24h


# -----------------------------------------------------------------------------
# Redis keys
# -----------------------------------------------------------------------------
def _city_meta_key(city_id: str) -> str:
    return f"city:{city_id}:meta"


def _user_city_key(user_id: str) -> str:
    return f"user:{user_id}:city"


def _invite_key(token: str) -> str:
    return f"invite:{token}"


# -----------------------------------------------------------------------------
# City init / lookup
# -----------------------------------------------------------------------------
async def init_city_for_user(user_id: str) -> str:
    """
    Backward compatible:
    - první city má city_id == user_id
    - user je owner
    """
    city_id = user_id
    meta_key = _city_meta_key(city_id)

    exists = await redis_client.exists(meta_key)
    if not exists:
        meta = {
            "city_id": city_id,
            "owner_id": user_id,
            "members": {
                user_id: "owner",
            },
            "created_at": time.time(),
        }
        await redis_client.set(meta_key, json.dumps(meta))

    await redis_client.set(_user_city_key(user_id), city_id)
    return city_id


async def get_city_id_for_user(user_id: str) -> str:
    city_id = await redis_client.get(_user_city_key(user_id))
    if city_id:
        return city_id.decode() if isinstance(city_id, bytes) else city_id

    # auto-create city (legacy behavior)
    return await init_city_for_user(user_id)


async def get_city_meta(city_id: str) -> dict:
    raw = await redis_client.get(_city_meta_key(city_id))
    if not raw:
        raise ValueError(f"City meta not found for city_id={city_id}")
    return json.loads(raw)


async def can_modify_city(user_id: str, city_id: str) -> bool:
    meta = await get_city_meta(city_id)

    if user_id == meta.get("owner_id"):
        return True

    role = meta.get("members", {}).get(user_id)
    return role == "editor"


# -----------------------------------------------------------------------------
# INVITES
# -----------------------------------------------------------------------------
async def create_invite(city_id: str, created_by: str, role: str = "editor") -> str:
    if role not in ("editor", "viewer"):
        raise ValueError("Invalid role")

    token = uuid.uuid4().hex
    payload = {
        "city_id": city_id,
        "role": role,
        "created_by": created_by,
        "created_at": time.time(),
        "expires_at": time.time() + INVITE_TTL_SECONDS,
    }

    await redis_client.set(
        _invite_key(token),
        json.dumps(payload),
        ex=INVITE_TTL_SECONDS,
    )
    return token


async def accept_invite(token: str, user_id: str) -> str:
    raw = await redis_client.get(_invite_key(token))
    if not raw:
        raise ValueError("Invite not found or expired")

    invite = json.loads(raw)
    city_id = invite["city_id"]
    role = invite["role"]

    meta_key = _city_meta_key(city_id)
    raw_meta = await redis_client.get(meta_key)
    if not raw_meta:
        raise ValueError("City does not exist")

    meta = json.loads(raw_meta)

    # already member → idempotent accept
    if user_id in meta.get("members", {}):
        await redis_client.set(_user_city_key(user_id), city_id)
        return city_id

    meta["members"][user_id] = role

    pipe = redis_client.pipeline(transaction=True)
    pipe.set(meta_key, json.dumps(meta))
    pipe.set(_user_city_key(user_id), city_id)
    pipe.delete(_invite_key(token))
    await pipe.execute()

    return city_id
