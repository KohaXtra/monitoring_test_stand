import asyncio
import json
import logging
import random
import sys
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request
from prometheus_fastapi_instrumentator import Instrumentator, metrics

# Одна JSON-строка на запрос в stdout: Alloy забирает её из docker, Loki парсит через `| json`.
logger = logging.getLogger("app")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.propagate = False

app = FastAPI()

# Статусы не группируем (500 vs 503 видно отдельно), бакеты латентности мельче дефолтных 0.1/0.5/1,
# иначе p95 из histogram_quantile получается грубой ступенькой.
instrumentator = Instrumentator(should_group_status_codes=False, excluded_handlers=["/metrics"])
instrumentator.add(
    metrics.default(latency_lowr_buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
)
instrumentator.instrument(app).expose(app)

# Ломалка, которой управляют на лету через /chaos. Действует только на /items/{item_id}.
chaos = {"delay": 0.0, "error_rate": 0.0}


@app.middleware("http")
async def access_log(request: Request, call_next):
    # request_id приходит от nginx (X-Request-ID) — так строки nginx и приложения сшиваются.
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    start = time.perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        status = 500
        response = JSONResponse({"detail": "Internal Server Error"}, status_code=500)
    duration = time.perf_counter() - start

    route = request.scope.get("route")
    if request.url.path != "/metrics":
        logger.info(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "level": "error" if status >= 500 else "warning" if status >= 400 else "info",
            "request_id": request_id,
            "method": request.method,
            "route": route.path if route else "none",  # шаблон маршрута, а не путь с ID
            "path": request.url.path,
            "status": status,
            "duration_ms": round(duration * 1000, 2),
        }, ensure_ascii=False))
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/")
async def root(request: Request):
    host = request.headers.get("Host")
    if host:
        match host:
            case "api.learnnginx.com": return "Это апишка"
            case "docs.learnnginx.com": return "Это дока"
    return request.headers.get("host")


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}


@app.post("/some")
async def some_endpoint(name: str):
    return {"message": f"Hello {name}"}


@app.get("/items/{item_id}")
async def get_item(item_id: int):
    # Небольшой естественный разброс, чтобы перцентили не были плоской линией.
    await asyncio.sleep(random.uniform(0.005, 0.05) + chaos["delay"] * random.uniform(0.5, 1.5))
    if random.random() < chaos["error_rate"]:
        raise HTTPException(status_code=500, detail="chaos")
    return {"id": item_id, "name": f"item-{item_id}"}


@app.get("/boom")
async def boom():
    raise RuntimeError("гарантированное исключение")


@app.get("/chaos")
async def get_chaos():
    return chaos


@app.post("/chaos")
async def set_chaos(delay: float = 0.0, error_rate: float = 0.0):
    chaos["delay"] = delay
    chaos["error_rate"] = error_rate
    return chaos
