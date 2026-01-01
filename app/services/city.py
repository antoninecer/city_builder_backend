import json
from app.redis_client import redis_client

# Redis keys helpers
def _city_meta_key(city_id: str) -> str:
    return f"city:{city_id}:meta"

def _user_city_key(user_id: str) -> str:
    return f"user:{user_id}:city"


def init_city_for_user(user_id: str) -> str:
    """
    Backward compatible:
    - user_id == city_id
    - owner == user
    """
    city_id = user_id

    meta_key = _city_meta_key(city_id)
    if not redis_client.exists(meta_key):
        meta = {
            "city_id": city_id,
            "owner_id": user_id,
            "members": [user_id],
        }
        redis_client.set(meta_key, json.dumps(meta))

    redis_client.set(_user_city_key(user_id), city_id)
    return city_id


def get_city_id_for_user(user_id: str) -> str:
    city_id = redis_client.get(_user_city_key(user_id))
    if city_id:
        return city_id.decode() if isinstance(city_id, bytes) else city_id

    # backward compatibility path
    return init_city_for_user(user_id)


def get_city_meta(city_id: str) -> dict:
    raw = redis_client.get(_city_meta_key(city_id))
    if not raw:
        raise ValueError(f"City meta not found for city_id={city_id}")
    return json.loads(raw)


def can_modify_city(user_id: str, city_id: str) -> bool:
    meta = get_city_meta(city_id)
    return (
        user_id == meta["owner_id"]
        or user_id in meta.get("members", [])
    )
