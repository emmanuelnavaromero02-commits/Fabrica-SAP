from __future__ import annotations

import logging

import httpx

from fabrica.blackboard.service import Board
from fabrica.catalog import stage_machine
from fabrica.config import get_settings
from fabrica.db.models import RunState
from fabrica.db.session import session_scope

log = logging.getLogger(__name__)


class WebhookNotifier:
    def __init__(self, url: str, http: httpx.AsyncClient | None = None) -> None:
        self.url = url
        self.http = http or httpx.AsyncClient(timeout=10)

    async def send(self, text: str) -> bool:
        try:
            resp = await self.http.post(self.url, json={"text": text})
            resp.raise_for_status()
        except httpx.HTTPError:
            log.warning("No se pudo enviar la notificación a %s", self.url, exc_info=True)
            return False
        return True


_notifier: WebhookNotifier | None = None


def configured_notifier() -> WebhookNotifier | None:
    global _notifier
    url = get_settings().notify_webhook_url
    if not url:
        return None
    if _notifier is None or _notifier.url != url:
        _notifier = WebhookNotifier(url)
    return _notifier


async def status_message(req_id: int) -> str | None:
    async with session_scope() as s:
        board = Board(s)
        req = await board.requirement(req_id)
        link = f"{get_settings().public_url}/?requisito={req.id}"
        label = stage_machine().get(req.stage).label
        if req.state == RunState.WAITING_GATE:
            roles = " o ".join(stage_machine().get(req.stage).roles)
            return f"🔔 #{req.id} {req.title}: espera decisión de {roles} en {label}. {link}"
        if req.state == RunState.BLOCKED:
            return f"⛔ #{req.id} {req.title}: en pausa en {label}, requiere intervención. {link}"
        return None


async def announce(req_id: int, notifier: WebhookNotifier | None = None) -> bool:
    notifier = notifier or configured_notifier()
    if notifier is None:
        return False
    text = await status_message(req_id)
    return await notifier.send(text) if text else False
