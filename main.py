import os
import json
import time
import asyncio
import base64
import socket
import httpx
import websockets
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import uvicorn

load_dotenv()

app = FastAPI(title="CDP Harness Portal")
templates = Jinja2Templates(directory="templates")
import pathlib
if pathlib.Path("static").exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production-secret-key-xyz")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "harness123")
CDP_HOST   = os.environ.get("CDP_HOST", "100.113.104.72")
CDP_PORT   = int(os.environ.get("CDP_PORT", "19222"))

serializer = URLSafeTimedSerializer(SECRET_KEY)

SESSION_COOKIE = "harness_session"
SESSION_MAX_AGE = 3600 * 8  # 8 hours

def make_session_token(username: str) -> str:
    return serializer.dumps(username, salt="session")

def verify_session_token(token: str):
    try:
        return serializer.loads(token, salt="session", max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

def get_current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return verify_session_token(token)

def require_auth(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return None

# ── CDP helpers ────────────────────────────────────────────────────────────────

async def cdp_list_targets():
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(f"http://{CDP_HOST}:{CDP_PORT}/json/list")
        return r.json()

async def cdp_version():
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(f"http://{CDP_HOST}:{CDP_PORT}/json/version")
        return r.json()

async def cdp_new_tab(url: str = "about:blank"):
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(f"http://{CDP_HOST}:{CDP_PORT}/json/new?{url}")
        return r.json()

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@app.post("/login")
async def do_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USER and password == ADMIN_PASS:
        token = make_session_token(username)
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, max_age=SESSION_MAX_AGE, samesite="lax")
        return resp
    return templates.TemplateResponse(request=request, name="login.html", context={"error": "Invalid credentials"})

@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    redir = require_auth(request)
    if redir:
        return redir
    
    try:
        version = await cdp_version()
        targets = await cdp_list_targets()
        cdp_ok = True
    except Exception as e:
        version = {"Browser": "Unreachable", "error": str(e)}
        targets = []
        cdp_ok = False
    
    ts_ip = None
    try:
        import subprocess
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            ts_ip = result.stdout.strip()
    except Exception:
        pass

    return templates.TemplateResponse(request=request, name="control.html", context={
        "cdp_ok": cdp_ok,
        "version": version,
        "targets": targets,
        "cdp_host": CDP_HOST,
        "cdp_port": CDP_PORT,
        "ts_ip": ts_ip,
    })

@app.get("/api/targets")
async def api_targets(request: Request):
    redir = require_auth(request)
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        targets = await cdp_list_targets()
        return JSONResponse(targets)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/navigate")
async def api_navigate(request: Request):
    redir = require_auth(request)
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    target_id = body.get("targetId")
    url = body.get("url", "about:blank")
    try:
        ws_url = f"ws://{CDP_HOST}:{CDP_PORT}/devtools/page/{target_id}"
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
            result = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        return JSONResponse({"ok": True, "result": result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/screenshot")
async def api_screenshot(request: Request):
    redir = require_auth(request)
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    target_id = body.get("targetId")
    try:
        ws_url = f"ws://{CDP_HOST}:{CDP_PORT}/devtools/page/{target_id}"
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "jpeg", "quality": 70}}))
            result = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        data = result.get("result", {}).get("data", "")
        return JSONResponse({"ok": True, "image": data})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/evaluate")
async def api_evaluate(request: Request):
    redir = require_auth(request)
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    target_id = body.get("targetId")
    expression = body.get("expression", "document.title")
    try:
        ws_url = f"ws://{CDP_HOST}:{CDP_PORT}/devtools/page/{target_id}"
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expression, "returnByValue": True}}))
            result = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        return JSONResponse({"ok": True, "result": result.get("result", {})})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/new-tab")
async def api_new_tab(request: Request):
    redir = require_auth(request)
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    url = body.get("url", "about:blank")
    try:
        tab = await cdp_new_tab(url)
        return JSONResponse({"ok": True, "tab": tab})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Connection Health Test ──────────────────────────────────────────────────────

@app.get("/api/connection-health")
async def api_connection_health(request: Request):
    redir = require_auth(request)
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    checks = []
    overall_ok = True

    # ── Check 1: TCP port reachability ──────────────────────────────────────────
    t0 = time.monotonic()
    try:
        loop = asyncio.get_event_loop()
        conn = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: socket.create_connection((CDP_HOST, CDP_PORT), timeout=4)),
            timeout=5
        )
        conn.close()
        tcp_ms = round((time.monotonic() - t0) * 1000)
        checks.append({"id": "tcp", "label": "TCP Port Reachable", "ok": True,
                        "detail": f"{CDP_HOST}:{CDP_PORT} responded in {tcp_ms} ms"})
    except Exception as e:
        tcp_ms = round((time.monotonic() - t0) * 1000)
        checks.append({"id": "tcp", "label": "TCP Port Reachable", "ok": False,
                        "detail": f"Could not connect to {CDP_HOST}:{CDP_PORT} — {e}"})
        overall_ok = False

    # ── Check 2: CDP HTTP /json/version ────────────────────────────────────────
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"http://{CDP_HOST}:{CDP_PORT}/json/version")
        ver = r.json()
        v_ms = round((time.monotonic() - t0) * 1000)
        browser_label = ver.get("Browser", "unknown")
        checks.append({"id": "version", "label": "CDP /json/version", "ok": True,
                        "detail": f"{browser_label} · {v_ms} ms"})
    except Exception as e:
        v_ms = round((time.monotonic() - t0) * 1000)
        checks.append({"id": "version", "label": "CDP /json/version", "ok": False,
                        "detail": f"HTTP GET failed — {e}"})
        overall_ok = False
        ver = {}

    # ── Check 3: CDP HTTP /json/list (count open tabs) ─────────────────────────
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"http://{CDP_HOST}:{CDP_PORT}/json/list")
        targets = r.json()
        pages = [t for t in targets if t.get("type") == "page"]
        l_ms = round((time.monotonic() - t0) * 1000)
        checks.append({"id": "targets", "label": "CDP /json/list (open tabs)", "ok": True,
                        "detail": f"{len(pages)} page target(s) found · {l_ms} ms"})
    except Exception as e:
        l_ms = round((time.monotonic() - t0) * 1000)
        checks.append({"id": "targets", "label": "CDP /json/list (open tabs)", "ok": False,
                        "detail": f"HTTP GET failed — {e}"})
        overall_ok = False
        targets = []

    # ── Check 4: WebSocket handshake ───────────────────────────────────────────
    t0 = time.monotonic()
    ws_debug_url = ver.get("webSocketDebuggerUrl", "")
    if not ws_debug_url:
        ws_debug_url = f"ws://{CDP_HOST}:{CDP_PORT}/json"
    try:
        async with websockets.connect(ws_debug_url, open_timeout=5) as ws:
            ws_ms = round((time.monotonic() - t0) * 1000)
            checks.append({"id": "websocket", "label": "WebSocket Handshake", "ok": True,
                            "detail": f"Connected to {ws_debug_url} in {ws_ms} ms"})
    except Exception as e:
        ws_ms = round((time.monotonic() - t0) * 1000)
        checks.append({"id": "websocket", "label": "WebSocket Handshake", "ok": False,
                        "detail": f"WS connect failed — {e}"})
        overall_ok = False

    # ── Check 5: CDP round-trip (Browser.getVersion command) ───────────────────
    t0 = time.monotonic()
    pages = [t for t in targets if t.get("type") == "page"]
    if pages:
        page_ws = pages[0].get("webSocketDebuggerUrl", f"ws://{CDP_HOST}:{CDP_PORT}/devtools/page/{pages[0]['id']}")
        try:
            async with websockets.connect(page_ws, open_timeout=5) as ws:
                cmd = json.dumps({"id": 99, "method": "Runtime.evaluate",
                                  "params": {"expression": "navigator.userAgent", "returnByValue": True}})
                await ws.send(cmd)
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
            rt_ms = round((time.monotonic() - t0) * 1000)
            ua = resp.get("result", {}).get("result", {}).get("value", "")
            checks.append({"id": "roundtrip", "label": "CDP Command Round-trip", "ok": True,
                            "detail": f"Runtime.evaluate responded in {rt_ms} ms · UA: {ua[:80]}"})
        except Exception as e:
            rt_ms = round((time.monotonic() - t0) * 1000)
            checks.append({"id": "roundtrip", "label": "CDP Command Round-trip", "ok": False,
                            "detail": f"CDP command failed — {e}"})
            overall_ok = False
    else:
        checks.append({"id": "roundtrip", "label": "CDP Command Round-trip", "ok": None,
                        "detail": "Skipped — no open page targets to test against"})

    # ── Check 6: Tailscale reachability ────────────────────────────────────────
    try:
        import subprocess
        result = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            ts_data = json.loads(result.stdout)
            self_ip = ts_data.get("Self", {}).get("TailscaleIPs", ["?"])[0]
            peers = len(ts_data.get("Peer", {}))
            checks.append({"id": "tailscale", "label": "Tailscale VPN Status", "ok": True,
                            "detail": f"Container IP: {self_ip} · {peers} peer(s) in network"})
        else:
            checks.append({"id": "tailscale", "label": "Tailscale VPN Status", "ok": False,
                            "detail": f"tailscale status returned code {result.returncode}"})
    except Exception as e:
        checks.append({"id": "tailscale", "label": "Tailscale VPN Status", "ok": False,
                        "detail": f"Tailscale CLI unavailable — {e}"})

    return JSONResponse({
        "ok": overall_ok,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cdp_host": CDP_HOST,
        "cdp_port": CDP_PORT,
        "checks": checks,
    })

# ── WebSocket CDP proxy ─────────────────────────────────────────────────────────

@app.websocket("/ws/cdp/{target_id}")
async def ws_proxy(websocket: WebSocket, target_id: str):
    """WebSocket proxy: browser frontend <-> Chrome CDP"""
    token = websocket.cookies.get(SESSION_COOKIE)
    if not token or not verify_session_token(token):
        await websocket.close(code=4401)
        return
    
    await websocket.accept()
    cdp_ws_url = f"ws://{CDP_HOST}:{CDP_PORT}/devtools/page/{target_id}"
    
    try:
        async with websockets.connect(cdp_ws_url) as cdp_ws:
            async def forward_to_cdp():
                async for msg in websocket.iter_text():
                    await cdp_ws.send(msg)
            
            async def forward_to_client():
                async for msg in cdp_ws:
                    await websocket.send_text(msg)
            
            await asyncio.gather(forward_to_cdp(), forward_to_client())
    except (WebSocketDisconnect, Exception):
        pass

@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
