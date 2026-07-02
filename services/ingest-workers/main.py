import asyncio
import logging
from pdf_worker import main as pdf_main
from vision_worker import main as vision_main
from video_worker import main as video_main
from sensor_worker import main as sensor_main
from embedding_worker import main as embedding_main

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def run_all():
    logger.info("Starting all ingest workers...")
    await asyncio.gather(
        pdf_main(),
        vision_main(),
        video_main(),
        sensor_main(),
        embedding_main(),
    )


if __name__ == "__main__":
    asyncio.run(run_all())
