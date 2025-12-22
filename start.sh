#! /bin/bash
cd /opt/city_builder_backend
source venv/bin/activate

export ALLOW_DEV_ENDPOINTS=1
export DEV_UNLIMITED_RESOURCES=1
# volitelné:
export DEV_DEFAULT_GOLD=99999999
export DEV_DEFAULT_WOOD=99999999
# export DEFAULT_WORLD_RADIUS=3   # když chceš default -3..+3
# export DEV_DISABLE_WORLD_BOUNDS=0

uvicorn app.main:app --reload --host 0.0.0.0 --port 8002

