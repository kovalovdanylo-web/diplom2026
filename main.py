import asyncio
import logging

from config import config
from database import init_db
from bot import BotApp


async def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG if config.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger(__name__).info("Ініціалізація бази даних...")
    await init_db()

    app = BotApp(config)
    await app.run()


if __name__ == "__main__":
    asyncio.run(main())
