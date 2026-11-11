from fastapi import APIRouter, HTTPException

from app.services.graph_service import get_graph, get_graph_for_entity, get_topic_timeline

router = APIRouter(prefix="/api")


@router.get("/graph")
async def graph_overview():
    return get_graph()


@router.get("/graph/entity/{entity_name}")
async def graph_entity(entity_name: str):
    return get_graph_for_entity(entity_name)


@router.get("/topics/{topic_name}/timeline")
async def topic_timeline(topic_name: str):
    data = get_topic_timeline(topic_name)
    if not data:
        return {"topic": topic_name, "events": []}
    return {"topic": topic_name, "events": data}
