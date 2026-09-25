from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from fabrica.api.deps import Who
from fabrica.db.models import Client
from fabrica.db.session import session_scope
from fabrica.domain.schemas import ClientIn, ClientOut

router = APIRouter(prefix="/api/clients", tags=["clientes"])


@router.get("", response_model=list[ClientOut])
async def list_clients(who: Who) -> list[Client]:
    async with session_scope() as s:
        rows = await s.scalars(select(Client).order_by(Client.name.asc()))
        return list(rows.all())


@router.post("", response_model=ClientOut, status_code=201)
async def create_client(data: ClientIn, who: Who) -> Client:
    async with session_scope() as s:
        client = Client(name=data.name, code=data.code.upper())
        s.add(client)
        await s.flush()
        return client
