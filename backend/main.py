"""
═══════════════════════════════════════════════════════════════
  Zone 1 Crime Intelligence System — FastAPI Backend
  झोन 1 गुन्हे गुप्तचर प्रणाली — Aurangabad City Police
═══════════════════════════════════════════════════════════════
"""
import os
import json
import asyncio
import shutil
import threading
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import database as db
import auth
import excel_sync

# ─── Paths ────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")
DATA_DIR = os.path.join(BASE_DIR, "data")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")

PORT = 5000

# ─── Excel path (user configurable) ──────────────────────────
EXCEL_PATH: Optional[str] = None

# ─── SSE clients ─────────────────────────────────────────────
sse_queues: list[asyncio.Queue] = []

# ─── Data cache (5s TTL) ─────────────────────────────────────
data_cache = {"data": None, "ts": 0}

# ─── File watcher ─────────────────────────────────────────────
watcher_observer = None


# ═══════════════════════════════════════════════════════════════
#  Settings
# ═══════════════════════════════════════════════════════════════

def load_settings():
    global EXCEL_PATH
    try:
        if os.path.isfile(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r") as f:
                settings = json.load(f)
            if settings.get("excelPath") and os.path.isfile(settings["excelPath"]):
                EXCEL_PATH = settings["excelPath"]
    except Exception:
        print("  [Settings] Using default Excel path")


def save_settings():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump({"excelPath": EXCEL_PATH}, f, indent=2)


# ═══════════════════════════════════════════════════════════════
#  File Watcher (watchdog)
# ═══════════════════════════════════════════════════════════════

def start_watcher():
    global watcher_observer
    stop_watcher()
    if not EXCEL_PATH or not os.path.isfile(EXCEL_PATH):
        return

    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    debounce_timer = {"ref": None}

    class ExcelHandler(FileSystemEventHandler):
        def on_modified(self, event):
            if excel_sync.get_is_syncing():
                return
            if event.src_path.replace("\\", "/") != EXCEL_PATH.replace("\\", "/"):
                return

            if debounce_timer["ref"]:
                debounce_timer["ref"].cancel()

            def do_reload():
                print(f"  [Watcher] Excel file changed externally — re-syncing...")
                excel_sync.load_excel_into_db(EXCEL_PATH)
                notify_clients_sync()

            debounce_timer["ref"] = threading.Timer(1.0, do_reload)
            debounce_timer["ref"].start()

    observer = Observer()
    observer.schedule(ExcelHandler(), path=os.path.dirname(EXCEL_PATH), recursive=False)
    observer.daemon = True
    observer.start()
    watcher_observer = observer


def stop_watcher():
    global watcher_observer
    if watcher_observer:
        watcher_observer.stop()
        watcher_observer = None


def notify_clients_sync():
    """Push SSE event to all connected clients (thread-safe)."""
    global data_cache
    data_cache = {"data": None, "ts": 0}
    msg = json.dumps({"type": "data-updated", "timestamp": datetime.utcnow().isoformat() + "Z"})
    for q in list(sse_queues):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


# ═══════════════════════════════════════════════════════════════
#  Lifespan (startup / shutdown)
# ═══════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    global EXCEL_PATH
    # ── Startup ──
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    load_settings()
    db.init_db()
    auth.init_auth_db(db.get_db())

    if EXCEL_PATH:
        count = db.get_record_count()
        if count == 0:
            print("  [Startup] Saved data source found — loading...")
            excel_sync.load_excel_into_db(EXCEL_PATH)
        else:
            print(f"  [Startup] Database has {count} records — ready")
        start_watcher()
    else:
        print("  [Startup] No data source connected — go to Data page to connect")

    yield  # app is now running

    # ── Shutdown ──
    stop_watcher()


# ═══════════════════════════════════════════════════════════════
#  FastAPI App
# ═══════════════════════════════════════════════════════════════

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Zone 1 Crime Intelligence System", lifespan=lifespan)
app.state.limiter = limiter

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": "Too many requests, please try again later."})


# ═══════════════════════════════════════════════════════════════
#  Auth Dependency
# ═══════════════════════════════════════════════════════════════

def get_current_user(request: Request) -> dict:
    token = request.cookies.get("z1cis_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = auth.verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_editor(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] not in ("editor",):
        raise HTTPException(status_code=403, detail="Editor access required")
    return user


# ═══════════════════════════════════════════════════════════════
#  Static Files & Auth Redirect
# ═══════════════════════════════════════════════════════════════

# Serve static assets without auth
app.mount("/css", StaticFiles(directory=os.path.join(FRONTEND_DIR, "css")), name="css")
app.mount("/js", StaticFiles(directory=os.path.join(FRONTEND_DIR, "js")), name="js")
app.mount("/img", StaticFiles(directory=os.path.join(FRONTEND_DIR, "img")), name="img")
app.mount("/fonts", StaticFiles(directory=os.path.join(FRONTEND_DIR, "fonts")), name="fonts")
app.mount("/libs", StaticFiles(directory=os.path.join(FRONTEND_DIR, "libs")), name="libs")


@app.get("/login.html", response_class=HTMLResponse)
async def serve_login():
    return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))


def _serve_protected(filename: str, request: Request, admin_only: bool = False) -> Response:
    token = request.cookies.get("z1cis_token")
    if not token:
        return RedirectResponse(url="/login.html", status_code=302)
    user = auth.verify_token(token)
    if not user:
        return RedirectResponse(url="/login.html", status_code=302)
    if admin_only and user["role"] != "admin":
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(os.path.join(FRONTEND_DIR, filename))


@app.get("/")
async def serve_root(request: Request):
    return _serve_protected("index.html", request)


@app.get("/index.html")
async def serve_index(request: Request):
    return _serve_protected("index.html", request)


@app.get("/station.html")
async def serve_station(request: Request):
    return _serve_protected("station.html", request)


@app.get("/data.html")
async def serve_data(request: Request):
    return _serve_protected("data.html", request)


@app.get("/admin.html")
async def serve_admin(request: Request):
    return _serve_protected("admin.html", request, admin_only=True)


# ═══════════════════════════════════════════════════════════════
#  Auth Routes (Public)
# ═══════════════════════════════════════════════════════════════

# ─── KeyAuth Configuration ───────────────────────────────────
KEYAUTH_CONFIG = {
    "name": "police dashbord",
    "ownerid": "TjmhS30O6y",
    "secret": "c6a9a7055eabdc4cfb42d44cd698a0f1020ca322127f5d4146664966cfab040c",
    "version": "1.0",
}


@app.post("/api/login")
@limiter.limit("5/15minutes")
async def login(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    # ─── KeyAuth.cc API Integration ──────────────────────────
    user = None
    key_auth_error = None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            form_data = {
                "name": KEYAUTH_CONFIG["name"],
                "ownerid": KEYAUTH_CONFIG["ownerid"],
                "secret": KEYAUTH_CONFIG["secret"],
                "version": KEYAUTH_CONFIG["version"],
                "type": "login",
                "username": username,
                "pass": password,
            }
            resp = await client.post("https://keyauth.win/api/1.2/", data=form_data)
            auth_json = resp.json()

            if auth_json.get("success"):
                user = {"id": 9999, "username": username, "role": "admin"}
            else:
                key_auth_error = auth_json.get("message", "Unknown error")
    except Exception as e:
        print(f"  [KeyAuth API Warning] Not configured or unreachable: {e}")

    # ─── Fallback to Local Auth (SQLite) ─────────────────────
    if not user:
        clean_username = username.lower()[:50]
        clean_password = password[:128]
        user = auth.authenticate_user(clean_username, clean_password)

    if not user:
        err = f"KeyAuth: {key_auth_error}" if key_auth_error else "Invalid username or password"
        raise HTTPException(status_code=401, detail=err)

    token = auth.generate_token(user)
    response = JSONResponse(content={"success": True, "user": {"username": user["username"], "role": user["role"]}})
    response.set_cookie(
        key="z1cis_token",
        value=token,
        httponly=True,
        secure=False,  # set True when using HTTPS
        samesite="lax",
        max_age=8 * 60 * 60,
        path="/",
    )
    return response


@app.post("/api/logout")
async def logout():
    response = JSONResponse(content={"success": True})
    response.delete_cookie("z1cis_token", path="/")
    return response


@app.get("/api/me")
async def me(user: dict = Depends(get_current_user)):
    return {"username": user["username"], "role": user["role"]}


# ═══════════════════════════════════════════════════════════════
#  Admin API — User Management
# ═══════════════════════════════════════════════════════════════

@app.get("/api/users")
async def list_users(user: dict = Depends(require_admin)):
    return auth.get_all_users()


@app.post("/api/users")
async def create_user(request: Request, user: dict = Depends(require_admin)):
    body = await request.json()
    try:
        new_user = auth.create_user(
            body.get("username", "").strip().lower(),
            body.get("password", ""),
            body.get("role", "").lower(),
        )
        return JSONResponse(status_code=201, content=new_user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/users/{user_id}/role")
async def update_role(user_id: int, request: Request, user: dict = Depends(require_admin)):
    body = await request.json()
    try:
        updated = auth.update_user_role(user_id, body.get("role", "").lower())
        return updated
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/users/{user_id}")
async def delete_user_endpoint(user_id: int, user: dict = Depends(require_admin)):
    try:
        auth.delete_user(user_id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ═══════════════════════════════════════════════════════════════
#  Data API — Read
# ═══════════════════════════════════════════════════════════════

@app.get("/api/data")
async def get_data(user: dict = Depends(get_current_user)):
    import time
    now = time.time()
    if data_cache["data"] and (now - data_cache["ts"]) < 5:
        return data_cache["data"]
    data = db.get_data_summary()
    data_cache["data"] = data
    data_cache["ts"] = now
    return data


@app.get("/api/records")
async def get_records(
    request: Request,
    page: int = 1,
    limit: int = 50,
    year: Optional[str] = None,
    month: Optional[str] = None,
    station: Optional[str] = None,
    crimeType: Optional[str] = None,
    search: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    filters = {}
    if year:
        filters["year"] = year
    if month:
        filters["month"] = month
    if station:
        filters["station"] = station
    if crimeType:
        filters["crimeType"] = crimeType
    if search:
        filters["search"] = search
    return db.get_records_paginated(page, limit, filters)


@app.get("/api/settings")
async def get_settings(user: dict = Depends(get_current_user)):
    return {
        "excelPath": EXCEL_PATH,
        "connected": bool(EXCEL_PATH),
        "recordCount": db.get_record_count(),
        "dbPath": os.path.join(DATA_DIR, "crime_data.db"),
    }


@app.get("/api/export")
async def export_data(user: dict = Depends(get_current_user)):
    export_path = os.path.join(DATA_DIR, "export_crime_data.xlsx")
    excel_sync.export_to_excel(export_path)
    return FileResponse(
        path=export_path,
        filename="Crime_Data_Export.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/filter-options")
async def get_filter_options(user: dict = Depends(get_current_user)):
    data = db.get_data_summary()
    return data["filters"]


# ═══════════════════════════════════════════════════════════════
#  Data API — Write (Editor Only)
# ═══════════════════════════════════════════════════════════════

@app.post("/api/records")
async def add_record(request: Request, user: dict = Depends(require_editor)):
    body = await request.json()
    record = db.add_record(body)
    if EXCEL_PATH:
        excel_sync.write_back_to_excel(EXCEL_PATH)
    notify_clients_sync()
    return JSONResponse(status_code=201, content=record)


@app.put("/api/records/{record_id}")
async def update_record(record_id: int, request: Request, user: dict = Depends(require_editor)):
    body = await request.json()
    record = db.update_record(record_id, body)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    if EXCEL_PATH:
        excel_sync.write_back_to_excel(EXCEL_PATH)
    notify_clients_sync()
    return record


@app.delete("/api/records/{record_id}")
async def delete_record(record_id: int, user: dict = Depends(require_editor)):
    changes = db.delete_record(record_id)
    if changes == 0:
        raise HTTPException(status_code=404, detail="Record not found")
    if EXCEL_PATH:
        excel_sync.write_back_to_excel(EXCEL_PATH)
    notify_clients_sync()
    return {"success": True}


@app.post("/api/upload-excel")
async def upload_excel(file: UploadFile = File(...), user: dict = Depends(require_editor)):
    global EXCEL_PATH
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".xlsx", ".xls"):
        raise HTTPException(status_code=400, detail="Only .xlsx and .xls files are allowed")

    dest_path = os.path.join(DATA_DIR, file.filename)
    with open(dest_path, "wb") as f:
        content = await file.read()
        f.write(content)

    EXCEL_PATH = dest_path
    save_settings()

    result = excel_sync.load_excel_into_db(dest_path)
    if result["success"]:
        start_watcher()
        notify_clients_sync()
        return {"success": True, "count": result["count"], "path": dest_path}
    else:
        raise HTTPException(status_code=500, detail=result["error"])


@app.post("/api/reload")
async def reload_data(user: dict = Depends(require_editor)):
    if not EXCEL_PATH:
        raise HTTPException(status_code=400, detail="No data source connected")
    result = excel_sync.load_excel_into_db(EXCEL_PATH)
    if result["success"]:
        notify_clients_sync()
        return {"success": True, "count": result["count"]}
    else:
        raise HTTPException(status_code=500, detail=result["error"])


@app.post("/api/connect")
async def connect_source(request: Request, user: dict = Depends(require_editor)):
    global EXCEL_PATH
    body = await request.json()
    file_path = body.get("filePath", "").strip().strip('"')
    if not file_path:
        raise HTTPException(status_code=400, detail="No file path provided")
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found at that path")

    EXCEL_PATH = file_path
    save_settings()

    result = excel_sync.load_excel_into_db(file_path)
    if result["success"]:
        start_watcher()
        notify_clients_sync()
        return {"success": True, "count": result["count"], "path": file_path}
    else:
        raise HTTPException(status_code=500, detail=result["error"])


@app.post("/api/disconnect")
async def disconnect_source(user: dict = Depends(require_editor)):
    global EXCEL_PATH
    EXCEL_PATH = None
    db.clear_all()
    if os.path.isfile(SETTINGS_PATH):
        os.remove(SETTINGS_PATH)
    stop_watcher()
    notify_clients_sync()
    return {"success": True}


# ═══════════════════════════════════════════════════════════════
#  SSE Events
# ═══════════════════════════════════════════════════════════════

@app.get("/api/events")
async def sse_events(request: Request):
    # Auth check
    token = request.cookies.get("z1cis_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = auth.verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    sse_queues.append(queue)

    async def event_generator():
        try:
            yield {"data": json.dumps({"type": "connected"})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {"data": msg}
                except asyncio.TimeoutError:
                    # Send keepalive comment to prevent connection timeout
                    yield {"comment": "keepalive"}
        finally:
            if queue in sse_queues:
                sse_queues.remove(queue)

    return EventSourceResponse(event_generator())


# ═══════════════════════════════════════════════════════════════
#  Start Server
# ═══════════════════════════════════════════════════════════════

def get_local_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    import sys
    import io
    # Force UTF-8 output on Windows
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    local_ip = get_local_ip()
    print("")
    print("  +----------------------------------------------+")
    print("  |   Zone 1 Crime Intelligence System           |")
    print("  |   Aurangabad City Police                     |")
    print("  +----------------------------------------------+")
    print(f"  |   Local:   http://localhost:{PORT}           |")
    print(f"  |   Network: http://{local_ip}:{PORT}".ljust(48) + "|")
    print("  |   Backend: Python FastAPI + Uvicorn          |")
    print("  |   Database: SQLite (WAL mode)                |")
    print("  |   Auth:    JWT + bcrypt + KeyAuth            |")
    print("  |   Security: CORS, Rate-limit                 |")
    print("  +----------------------------------------------+")
    print("")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="info",
    )
