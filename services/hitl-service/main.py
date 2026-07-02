import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from broadcaster import Broadcast
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from shared.models.database import HitlItem
import json

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://aether:aether@localhost:5432/aether")

broadcast = Broadcast(REDIS_URL)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

app = FastAPI(title="Aether HITL Service")

@app.on_event("startup")
async def startup_event():
    await broadcast.connect()

@app.on_event("shutdown")
async def shutdown_event():
    await broadcast.disconnect()

@app.websocket("/api/v1/hitl/ws/{org_id}")
async def hitl_websocket(websocket: WebSocket, org_id: str):
    await websocket.accept()
    channel = f"hitl_{org_id}"
    
    async with broadcast.subscribe(channel=channel) as subscriber:
        try:
            async for event in subscriber:
                await websocket.send_text(event.message)
        except WebSocketDisconnect:
            pass

@app.get("/api/v1/hitl/queue/{org_id}")
async def get_queue(org_id: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(HitlItem).where(HitlItem.org_id == org_id, HitlItem.status == 'pending'))
        items = result.scalars().all()
        return {"items": [item.id for item in items]}

@app.post("/api/v1/hitl/{item_id}/approve")
async def approve_item(item_id: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(HitlItem).where(HitlItem.id == item_id))
        item = result.scalars().first()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
            
        item.status = "approved"
        await session.commit()
        
        # Publish update to Websocket via Redis backplane
        await broadcast.publish(channel=f"hitl_{item.org_id}", message=json.dumps({"item_id": str(item.id), "status": "approved"}))
        
        # In a full setup, this would also publish to RabbitMQ to resume the LangGraph graph
        return {"status": "approved"}

@app.post("/api/v1/hitl/{item_id}/reject")
async def reject_item(item_id: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(HitlItem).where(HitlItem.id == item_id))
        item = result.scalars().first()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
            
        item.status = "rejected"
        await session.commit()
        
        # Publish update to Websocket via Redis backplane
        await broadcast.publish(channel=f"hitl_{item.org_id}", message=json.dumps({"item_id": str(item.id), "status": "rejected"}))
        
        return {"status": "rejected"}
