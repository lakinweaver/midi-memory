"""Registering capture clients and watching what they are doing."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from midi_memory.server.clients import NAME_MAX, ClientError

router = APIRouter(prefix="/api/clients", tags=["clients"])


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)


class ClientRename(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)


def _registry(request: Request):
    return request.app.state.clients


@router.get("")
async def list_clients(request: Request) -> dict:
    return {"clients": _registry(request).listing()}


@router.post("", status_code=201)
async def create_client(request: Request, payload: ClientCreate) -> dict:
    """Register a client and issue its secret.

    The secret is returned here and nowhere else -- only its hash is stored, so
    there is no way to show it again. Losing it means issuing a new one, which
    is a button away and costs nothing.
    """
    try:
        client, secret = _registry(request).create(payload.name)
    except ClientError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"client": client, "secret": secret}


@router.post("/{client_id}/secret")
async def rotate_secret(request: Request, client_id: str) -> dict:
    try:
        secret = _registry(request).rotate_secret(client_id)
    except ClientError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"client": request.app.state.db.get_client(client_id), "secret": secret}


@router.patch("/{client_id}")
async def rename_client(request: Request, client_id: str,
                        payload: ClientRename) -> dict:
    try:
        return {"client": _registry(request).rename(client_id, payload.name)}
    except ClientError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{client_id}/revoke")
async def revoke_client(request: Request, client_id: str) -> dict:
    try:
        return {"client": _registry(request).set_revoked(client_id, True)}
    except ClientError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{client_id}/restore")
async def restore_client(request: Request, client_id: str) -> dict:
    try:
        return {"client": _registry(request).set_revoked(client_id, False)}
    except ClientError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{client_id}")
async def delete_client(request: Request, client_id: str) -> dict:
    try:
        _registry(request).delete(client_id)
    except ClientError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": client_id}
