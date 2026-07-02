import aio_pika
import os
import json

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

class RabbitMQClient:
    def __init__(self):
        self.connection = None
        self.channel = None

    async def connect(self):
        self.connection = await aio_pika.connect_robust(RABBITMQ_URL)
        self.channel = await self.connection.channel()
        
    async def publish_message(self, queue_name: str, payload: dict):
        if not self.channel:
            await self.connect()
        
        await self.channel.declare_queue(queue_name, durable=True)
        message = aio_pika.Message(
            body=json.dumps(payload).encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT
        )
        await self.channel.default_exchange.publish(
            message,
            routing_key=queue_name
        )
        
    async def publish_event(self, exchange_name: str, routing_key: str, payload: dict):
        if not self.channel:
            await self.connect()
            
        exchange = await self.channel.declare_exchange(exchange_name, aio_pika.ExchangeType.TOPIC, durable=True)
        message = aio_pika.Message(
            body=json.dumps(payload).encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT
        )
        await exchange.publish(message, routing_key=routing_key)

rabbitmq = RabbitMQClient()
