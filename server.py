"""
知识图谱后端服务
启动: python server.py
访问: http://127.0.0.1:8765
API:
  GET  /api/data       获取图谱数据
  POST /api/data       保存图谱数据
  GET  /api/settings   获取设置
  POST /api/settings   保存设置
  GET  /api/backups    列出备份
  POST /api/backup     手动创建备份
  POST /api/restore    从备份恢复
  GET  /api/stats      获取统计信息
"""

import os
import json
import shutil
import signal
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
DATA_FILE = os.path.join(DATA_DIR, "graph-data.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
MAX_BACKUPS = 5

os.makedirs(BACKUP_DIR, exist_ok=True)

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

_write_lock = threading.Lock()
_server = None
_last_backup_time = 0
BACKUP_INTERVAL = 300


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_backup(force=False):
    global _last_backup_time
    if not os.path.exists(DATA_FILE):
        return None
    now = datetime.now().timestamp()
    if not force and (now - _last_backup_time) < BACKUP_INTERVAL:
        return None
    _last_backup_time = now
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"backup_{ts}.json")
    shutil.copy2(DATA_FILE, dst)
    _cleanup_backups()
    return dst


def _cleanup_backups():
    backups = sorted(
        f for f in os.listdir(BACKUP_DIR)
        if f.startswith("backup_") and f.endswith(".json")
    )
    for old in backups[:max(0, len(backups) - MAX_BACKUPS)]:
        os.remove(os.path.join(BACKUP_DIR, old))


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def write_json(path, data):
    with _write_lock:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def shutdown(signum=None, frame=None):
    if _server:
        _server.shutdown()


class Handler(BaseHTTPRequestHandler):
    CORS_HEADERS = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        for k, v in self.CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, filepath):
        real = os.path.realpath(filepath)
        if not real.startswith(os.path.realpath(ROOT_DIR)):
            self.send_error(403)
            return
        if not os.path.isfile(real):
            self.send_error(404)
            return
        ext = os.path.splitext(real)[1].lower()
        mime = MIME_TYPES.get(ext, "application/octet-stream")
        with open(real, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self):
        self._json(200, {"ok": True})

    def do_GET(self):
        path = urlparse(self.path).path
        routes = {
            "/api/data": self._get_data,
            "/api/settings": self._get_settings,
            "/api/backups": self._get_backups,
            "/api/stats": self._get_stats,
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            fp = os.path.join(ROOT_DIR, "knowledge-graph.html" if path == "/" else path.lstrip("/"))
            self._file(fp)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._json(400, {"error": "Invalid JSON"})
            return
        routes = {
            "/api/data": self._post_data,
            "/api/settings": self._post_settings,
            "/api/backup": self._post_backup,
            "/api/restore": self._post_restore,
        }
        handler = routes.get(path)
        if handler:
            handler(req)
        else:
            self._json(404, {"error": "Not Found"})

    def _get_data(self):
        self._json(200, {"ok": True, "data": read_json(DATA_FILE), "updated": now_str()})

    def _get_settings(self):
        self._json(200, {"ok": True, "data": read_json(SETTINGS_FILE), "updated": now_str()})

    def _get_backups(self):
        backups = sorted(
            (f for f in os.listdir(BACKUP_DIR) if f.startswith("backup_") and f.endswith(".json")),
            reverse=True,
        )
        self._json(200, {"ok": True, "backups": backups})

    def _get_stats(self):
        data = read_json(DATA_FILE)
        nodes = data.get("nodes", []) if data else []
        links = data.get("links", []) if data else []
        self._json(200, {
            "ok": True,
            "nodeCount": len(nodes),
            "linkCount": len(links),
            "fileSize": os.path.getsize(DATA_FILE) if os.path.exists(DATA_FILE) else 0,
            "backupCount": sum(1 for f in os.listdir(BACKUP_DIR) if f.startswith("backup_")),
            "updated": now_str(),
        })

    def _post_data(self, req):
        make_backup()
        write_json(DATA_FILE, req.get("data", req))
        self._json(200, {"ok": True, "saved": now_str()})

    def _post_settings(self, req):
        write_json(SETTINGS_FILE, req.get("data", req))
        self._json(200, {"ok": True, "saved": now_str()})

    def _post_backup(self, _req):
        path = make_backup(force=True)
        if path:
            self._json(200, {"ok": True, "backup": os.path.basename(path)})
        else:
            self._json(400, {"error": "No data to backup"})

    def _post_restore(self, req):
        name = req.get("name", "")
        src = os.path.join(BACKUP_DIR, name)
        if not os.path.exists(src):
            self._json(404, {"error": "Backup not found"})
            return
        make_backup(force=True)
        shutil.copy2(src, DATA_FILE)
        self._json(200, {"ok": True, "restored": name})

    def log_message(self, fmt, *args):
        print(f"[{now_str()}] {args[0]}")


PORT = 8765

if __name__ == "__main__":
    _server = HTTPServer(("127.0.0.1", PORT), Handler)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print(f"知识图谱服务启动: http://localhost:{PORT}")
    print(f"数据目录: {DATA_DIR}")
    print("按 Ctrl+C 停止服务")
    try:
        _server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _server.server_close()
        print("服务已停止")
