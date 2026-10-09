from fastapi import APIRouter

from app.api.v1 import (
    agent,
    alarms,
    auth,
    dashboard,
    devices,
    elders,
    health,
    outings,
    rules,
    simulator,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(elders.router)
api_router.include_router(devices.router)
api_router.include_router(health.router)
api_router.include_router(outings.router)
api_router.include_router(alarms.router)
api_router.include_router(agent.router)
api_router.include_router(rules.router)
api_router.include_router(simulator.router)
api_router.include_router(dashboard.router)
