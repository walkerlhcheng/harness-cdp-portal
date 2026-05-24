import os
import json
import time
import asyncio
import base64
import socket
import httpx
import websockets
from python_socks.async_.asyncio import Proxy as SocksProxy
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
# When Tailscale is active, route CDP traffic through its SOCKS5 proxy
SOCKS5_PROXY = "socks5://127.0.0.1:1055" if os.environ.get("TS_AUTHKEY") else None

async def _ws_connect(url: str, **kwargs):
    """websockets.connect wrapper that routes through Tailscale SOCKS5 proxy when active."""
    if not SOCKS5_PROXY:
        return websockets.connect(url, **kwargs)
    import re as _re
    m = _re.match(r"wss?://([^:/]+):?(\d+)?", url)
    host = m.group(1) if m else url
    port = int(m.group(2)) if m and m.group(2) else 80
    proxy = SocksProxy.from_url(SOCKS5_PROXY)
    sock = await proxy.connect(dest_host=host, dest_port=port)
    return websockets.connect(url, sock=sock, **kwargs)


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
    async with httpx.AsyncClient(timeout=5, proxy=SOCKS5_PROXY) as client:
        r = await client.get(f"http://{CDP_HOST}:{CDP_PORT}/json/list")
        return r.json()

async def cdp_version():
    async with httpx.AsyncClient(timeout=5, proxy=SOCKS5_PROXY) as client:
        r = await client.get(f"http://{CDP_HOST}:{CDP_PORT}/json/version")
        return r.json()

async def cdp_new_tab(url: str = "about:blank"):
    async with httpx.AsyncClient(timeout=5, proxy=SOCKS5_PROXY) as client:
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
        async with await _ws_connect(ws_url) as ws:
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
        async with await _ws_connect(ws_url) as ws:
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
        async with await _ws_connect(ws_url) as ws:
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
        async with httpx.AsyncClient(timeout=5, proxy=SOCKS5_PROXY) as client:
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
        async with httpx.AsyncClient(timeout=5, proxy=SOCKS5_PROXY) as client:
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
        async with await _ws_connect(ws_debug_url, open_timeout=5) as ws:
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
            async with await _ws_connect(page_ws, open_timeout=5) as ws:
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
        async with await _ws_connect(cdp_ws_url) as cdp_ws:
            async def forward_to_cdp():
                async for msg in websocket.iter_text():
                    await cdp_ws.send(msg)
            
            async def forward_to_client():
                async for msg in cdp_ws:
                    await websocket.send_text(msg)
            
            await asyncio.gather(forward_to_cdp(), forward_to_client())
    except (WebSocketDisconnect, Exception):
        pass

# ── Sample Tasks ────────────────────────────────────────────────────────────────

@app.post("/api/tasks/server-fetch")
async def task_server_fetch(request: Request):
    """Sample Task 1 — No browser automation.
    The Railway container directly fetches a URL using httpx and returns
    status, headers, and a content preview. No Chrome / CDP involved.
    """
    redir = require_auth(request)
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    url = body.get("url", "https://example.com")
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(url)
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        return JSONResponse({
            "ok": True,
            "task": "server-fetch",
            "url": str(r.url),
            "status": r.status_code,
            "elapsed_ms": elapsed_ms,
            "content_type": r.headers.get("content-type", ""),
            "content_length": len(r.content),
            "preview": r.text[:300],
        })
    except Exception as e:
        return JSONResponse({"ok": False, "task": "server-fetch", "error": str(e)}, status_code=500)


@app.post("/api/tasks/browser-scrape")
async def task_browser_scrape(request: Request):
    """Sample Task 2 — Browser automation via CDP.
    Opens a new Chrome tab, navigates to a URL, waits for load,
    captures the page title + screenshot via CDP.
    """
    redir = require_auth(request)
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    url = body.get("url", "https://example.com")
    try:
        # 1. Open a new tab via CDP HTTP API
        tab = await cdp_new_tab(url)
        target_id = tab.get("id")
        if not target_id:
            return JSONResponse({"ok": False, "error": "Failed to open tab"}, status_code=500)

        # 2. Wait briefly for page to load
        await asyncio.sleep(2)

        # 3. Extract page title via CDP WebSocket
        ws_url = f"ws://{CDP_HOST}:{CDP_PORT}/devtools/page/{target_id}"
        async with await _ws_connect(ws_url) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                      "params": {"expression": "document.title", "returnByValue": True}}))
            title_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            title = title_resp.get("result", {}).get("result", {}).get("value", "")

            # 4. Capture screenshot
            await ws.send(json.dumps({"id": 2, "method": "Page.captureScreenshot",
                                      "params": {"format": "jpeg", "quality": 70}}))
            shot_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            screenshot_b64 = shot_resp.get("result", {}).get("data", "")

        return JSONResponse({
            "ok": True,
            "task": "browser-scrape",
            "target_id": target_id,
            "url": url,
            "title": title,
            "screenshot": screenshot_b64,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "task": "browser-scrape", "error": str(e)}, status_code=500)


@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
