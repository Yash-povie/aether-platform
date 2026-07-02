import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://aether:aether@postgres:5432/aether")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379")
    RABBITMQ_URL: str = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
    JWT_PRIVATE_KEY: str = os.getenv("JWT_PRIVATE_KEY", "dummy_private")
    JWT_PUBLIC_KEY: str = os.getenv("JWT_PUBLIC_KEY", "dummy_public")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "RS256")
    JWT_EXPIRY_MINUTES: int = int(os.getenv("JWT_EXPIRY_MINUTES", "60"))
    
    class Config:
        env_file = ".env"

settings = Settings()
