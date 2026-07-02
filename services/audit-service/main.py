import asyncio
import os
import json
import aio_pika
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from shared.models.database import AuditEvent
import uuid

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://aether:aether@localhost:5432/aether")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def process_event(message: aio_pika.IncomingMessage):
    async with message.process():
        try:
            payload = json.loads(message.body.decode())
            event = AuditEvent(
                org_id=uuid.UUID(payload.get("org_id")),
                user_id=uuid.UUID(payload.get("user_id")) if payload.get("user_id") else None,
                event_type=payload.get("event_type"),
                entity_type=payload.get("entity_type"),
                entity_id=uuid.UUID(payload.get("entity_id")) if payload.get("entity_id") else None,
                payload=payload.get("payload"),
                ip_address=payload.get("ip_address")
            )
            
            async with AsyncSessionLocal() as session:
                session.add(event)
                await session.commit()
                print(f"Processed audit event: {event.event_type}")
        except Exception as e:
            print(f"Failed to process audit event: {e}")

async def main():
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        
        # We declare a topic exchange
        exchange = await channel.declare_exchange("audit.events", aio_pika.ExchangeType.TOPIC, durable=True)
        
        # Declare queue and bind it
        queue = await channel.declare_queue("audit.queue", durable=True)
        await queue.bind(exchange, routing_key="audit.#")
        
        print("Audit Service connected to RabbitMQ. Waiting for events...")
        await queue.consume(process_event)
        
        # Wait until termination
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
