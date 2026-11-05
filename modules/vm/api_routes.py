"""
/api/vm/* — the virtual machines module.

GET  /status — the hypervisor: installed, set up, answering, whether the processor's
               virtualization works, the pools it can go on, its machines, and the job
POST /setup  — {"pool": "tank"}: install the hypervisor if needed and set it up (a job)
"""

import asyncio
import importlib.util
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.deps import authenticate, get_config, get_engine


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
    return _start_job("setup", lambda: vm.setup(engine, config, pool))
