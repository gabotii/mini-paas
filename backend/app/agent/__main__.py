"""Entry point: `python -m app.agent`."""

from __future__ import annotations

from app.agent.runner import Agent
from app.core.config import get_settings
from app.core.crypto import get_secret_box
from app.core.logging import configure_logging
from app.core.redis import get_sync_redis
from app.db.session import sync_session
from app.docker.service import DockerService
from app.workers.store import DeploymentStore


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    agent = Agent(
        settings=settings,
        docker=DockerService(),
        store=DeploymentStore(sync_session, get_sync_redis(), get_secret_box()),
        sessions=sync_session,
        redis_client=get_sync_redis(),
    )
    agent.install_signal_handlers()
    agent.run()


if __name__ == "__main__":
    main()
