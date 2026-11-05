"""
/api/vm/* — the virtual machines module.

GET  /status — the hypervisor: installed, set up, answering, whether the processor's
               virtualization works, the pools it can go on, its machines, and the job
POST /setup  — {"pool": "tank"}: install the hypervisor if needed and set it up (a job)
POST   /machines                — {name, cpus, memory_mb, disks: [{size_gb}], nics: [{bridge}]}
POST   /machines/{name}/{action} — start, stop (the guest shuts itself down) or poweroff
POST   /machines/{name}/install — {iso}: start a stopped machine from an image, this once
DELETE /machines/{name}         — remove a stopped machine; ?destroy_disks=true also destroys its disks
WS     /machines/{name}/console — its screen: the first message is {"auth": "<base64 user:password>"},
                                  the answer {"ok": true}, then the machine's VNC stream
POST   /isos?name=<file>        — upload an image, as the request's body
POST   /isos/download           — {url, name}: fetch an image (a job)
DELETE /isos/{name}             — remove an image
"""

import asyncio
import base64
import importlib.util
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from api.deps import authenticate, get_config, get_engine, is_administrator, verify_credentials


def _load(stem: str):
    name = f"freedomnas_vm_{stem}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{stem}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


vm = _load("vm")
router = APIRouter()

_lock = threading.Lock()
_job: dict = {"kind": None, "state": "idle", "error": None, "started": None, "finished": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error_text(exc: Exception) -> str:
    text = str(exc)
    marker = "Stderr:"
    return text.split(marker, 1)[1].strip() if marker in text else text


def _run(kind: str, work) -> None:
    try:
        work()
        _job.update(state="done", error=None)
    except Exception as exc:
        vm.logger.error("VM %s failed: %s", kind, exc)
        _job.update(state="failed", error=_error_text(exc))
    finally:
        _job["finished"] = _now()


def _start_job(kind: str, work):
    with _lock:
        if _job["state"] == "running":
            return JSONResponse(status_code=409, content={"detail": f"A {_job['kind']} is already running"})
        _job.update(kind=kind, state="running", error=None, started=_now(), finished=None)
    threading.Thread(target=_run, args=(kind, work), daemon=True).start()
    return {"job": dict(_job)}


@router.get("/status")
async def status(username: str = Depends(authenticate)):
    result = await asyncio.to_thread(vm.status, get_engine(), get_config())
    result["job"] = dict(_job)
    return result


@router.post("/setup")
async def setup(body: dict, username: str = Depends(authenticate)):
    engine, config = get_engine(), get_config()
    try:
        pool = await asyncio.to_thread(vm.check_pool, engine, body.get("pool"))
    except vm.VmError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return _start_job("setup", lambda: vm.setup(engine, config, pool, username))


async def _call(func, *args):
    try:
        return await asyncio.to_thread(func, *args)
    except vm.VmError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": _error_text(exc)})


@router.post("/machines")
async def create_machine(body: dict, username: str = Depends(authenticate)):
    return await _call(vm.create_machine, get_engine(), get_config(), body.get("name"), body)


@router.post("/machines/{name}/install")
async def install_machine(name: str, body: dict, username: str = Depends(authenticate)):
    result = await _call(vm.install_machine, get_engine(), get_config(), name, body.get("iso"))
    return result if isinstance(result, JSONResponse) else {"name": name, "iso": body.get("iso")}


@router.post("/machines/{name}/{action}")
async def machine_action(name: str, action: str, username: str = Depends(authenticate)):
    result = await _call(vm.machine_action, get_engine(), get_config(), name, action)
    return result if isinstance(result, JSONResponse) else {"name": name, "action": action}


@router.post("/isos")
async def upload_iso(request: Request, name: str = "", username: str = Depends(authenticate)):
    target = await _call(vm.iso_target, get_engine(), get_config(), name)
    if isinstance(target, JSONResponse):
        return target
    part = target.with_name(target.name + vm.PART)
    try:
        with part.open("wb") as out:
            async for chunk in request.stream():
                await asyncio.to_thread(out.write, chunk)
        part.rename(target)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": _error_text(exc)})
    finally:
        part.unlink(missing_ok=True)
    return {"name": target.name, "size": target.stat().st_size}


@router.post("/isos/download")
async def download_iso(body: dict, username: str = Depends(authenticate)):
    engine, config = get_engine(), get_config()
    try:
        await asyncio.to_thread(vm.download_target, engine, config, body.get("url"), body.get("name"))
    except vm.VmError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return _start_job("download", lambda: vm.download_iso(engine, config, body.get("url"), body.get("name")))


@router.delete("/isos/{name}")
async def remove_iso(name: str, username: str = Depends(authenticate)):
    result = await _call(vm.remove_iso, get_engine(), get_config(), name)
    return result if isinstance(result, JSONResponse) else {"removed": name}


async def _console_login(websocket: WebSocket) -> bool:
    try:
        message = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=30))
        username, _, password = base64.b64decode(message.get("auth", "")).decode().partition(":")
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, AttributeError, KeyError):
        return False
    return bool(username) and await asyncio.to_thread(verify_credentials, username, password) and is_administrator(username)


async def _browser_to_machine(websocket: WebSocket, writer) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        data = message.get("bytes")
        if data:
            writer.write(data)
            await writer.drain()


async def _machine_to_browser(websocket: WebSocket, reader) -> None:
    while data := await reader.read(65536):
        await websocket.send_bytes(data)


@router.websocket("/machines/{name}/console")
async def console(websocket: WebSocket, name: str):
    await websocket.accept()
    if not await _console_login(websocket):
        await websocket.send_text(json.dumps({"error": "Invalid credentials"}))
        await websocket.close(code=4401)
        return
    try:
        port = await asyncio.to_thread(vm.console_port, get_engine(), get_config(), name)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except (vm.VmError, OSError) as exc:
        await websocket.send_text(json.dumps({"error": str(exc) or "The console does not answer"}))
        await websocket.close(code=1011)
        return
    await websocket.send_text(json.dumps({"ok": True}))
    tasks = [asyncio.create_task(_browser_to_machine(websocket, writer)),
             asyncio.create_task(_machine_to_browser(websocket, reader))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        writer.close()
        try:
            await websocket.close()
        except RuntimeError:
            pass


@router.delete("/machines/{name}")
async def remove_machine(name: str, destroy_disks: bool = False, username: str = Depends(authenticate)):
    result = await _call(vm.remove_machine, get_engine(), get_config(), name, destroy_disks)
    return result if isinstance(result, JSONResponse) else {"removed": name, "disks_destroyed": destroy_disks}
