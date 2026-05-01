import logging
import multiprocessing
import sys
import time
from dataclasses import dataclass

from config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("launcher")


# ─────────────────────────────────────────────────────────────
# Цільові функції для дочірніх процесів
# ─────────────────────────────────────────────────────────────

def _run_bot() -> None:
    """Точка входу бота у дочірньому процесі."""
    import asyncio
    from database import init_db
    from bot import BotApp

    async def _main():
        await init_db()
        await BotApp(config).run()

    asyncio.run(_main())


def _run_web() -> None:
    """Точка входу веб-застосунку у дочірньому процесі."""
    from web.app import create_app
    app = create_app()
    app.run(host=config.web_host, port=config.web_port, debug=False, use_reloader=False)


# ─────────────────────────────────────────────────────────────
# Клас запускача
# ─────────────────────────────────────────────────────────────

@dataclass
class _ManagedProcess:
    """Обгортка над multiprocessing.Process з зручним логуванням."""
    name: str
    target: callable
    process: multiprocessing.Process = None

    def start(self) -> None:
        self.process = multiprocessing.Process(
            target=self.target, name=self.name, daemon=True
        )
        self.process.start()
        logger.info("✅ %s запущено  (PID: %d)", self.name, self.process.pid)

    def stop(self) -> None:
        if self.process and self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=5)
            logger.info("⏹  %s зупинено", self.name)

    def is_alive(self) -> bool:
        return bool(self.process and self.process.is_alive())


class ApplicationLauncher:
    """Керує запуском і зупинкою бота та веб-застосунку."""

    def __init__(self) -> None:
        self._workers = [
            _ManagedProcess("Telegram Bot", _run_bot),
            _ManagedProcess("Web App",      _run_web),
        ]

    def start(self) -> None:
        """Запускає всі компоненти і чекає на завершення або Ctrl+C."""
        logger.info("=" * 48)
        logger.info("  Receipt Tracker — запуск")
        logger.info("  Веб: http://%s:%d", config.web_host, config.web_port)
        logger.info("=" * 48)

        for w in self._workers:
            w.start()

        try:
            while all(w.is_alive() for w in self._workers):
                time.sleep(1)

            # Якщо один з процесів упав — логуємо
            for w in self._workers:
                if not w.is_alive():
                    logger.error("❌ %s несподівано завершився!", w.name)
        except KeyboardInterrupt:
            logger.info("\nОтримано Ctrl+C, зупиняємо...")
        finally:
            self._stop_all()

    def _stop_all(self) -> None:
        for w in self._workers:
            w.stop()
        logger.info("✅ Всі компоненти зупинено.")


# ─────────────────────────────────────────────────────────────
# Точка входу
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    multiprocessing.set_start_method("spawn" if sys.platform == "win32" else "fork")
    ApplicationLauncher().start()
