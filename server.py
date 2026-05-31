"""
知识图谱后端服务 - 数据持久化 + 前端页面托管
启动方式: python server.py
打开浏览器访问: http://127.0.0.1:8765
API:
  GET  /api/data       - 获取图谱数据
  POST /api/data       - 保存图谱数据
  GET  /api/settings   - 获取设置
  POST /api/settings   - 保存设置
  GET  /api/backups    - 列出备份
  POST /api/backup     - 手动创建备份
  POST /api/restore    - 从备份恢复
"""

import os
import json
import shutil
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
DATA_FILE = os.path.join(DATA_DIR, "graph-data.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

os.makedirs(BACKUP_DIR, exist_ok=True)

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

# 写锁，防止并发写入数据损坏
_write_lock = threading.Lock()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_backup():
    """自动备份当前数据，保留最近 20 份"""
    if not os.path.exists(DATA_FILE):
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"backup_{ts}.json")
    shutil.copy2(DATA_FILE, backup_path)
    _cleanup_backups()
    return backup_path


def _cleanup_backups(keep=20):
    """保留最近 keep 份备份"""
    backups = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.startswith("backup_") and f.endswith(".json")]
    )
    while len(backups) > keep:
        os.remove(os.path.join(BACKUP_DIR, backups[0]))
        backups.pop(0)


def safe_read_json(filepath, default=None):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def safe_write_json(filepath, data):
    with _write_lock:
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, filepath)


class APIHandler(BaseHTTPRequestHandler):
    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, filepath):
        """安全地 serve 一个静态文件"""
        # 路径穿越防护
        real = os.path.realpath(filepath)
        if not real.startswith(os.path.realpath(ROOT_DIR)):
            self.send_error(403, "Forbidden")
            return
        if not os.path.isfile(real):
            self.send_error(404, "File Not Found")
            return
        ext = os.path.splitext(real)[1].lower()
        mime = MIME.get(ext, "application/octet-stream")
        with open(real, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_GET(self):
        path = urlparse(self.path).path

        # API 路由
        if path == "/api/data":
            data = safe_read_json(DATA_FILE)
            self._send_json(200, {"ok": True, "data": data, "updated": now_str()})
        elif path == "/api/settings":
            data = safe_read_json(SETTINGS_FILE)
            self._send_json(200, {"ok": True, "data": data, "updated": now_str()})
        elif path == "/api/backups":
            backups = sorted(
                [f for f in os.listdir(BACKUP_DIR) if f.startswith("backup_") and f.endswith(".json")],
                reverse=True
            )
            self._send_json(200, {"ok": True, "backups": backups})
        elif path == "/api/stats":
            data = safe_read_json(DATA_FILE)
            nodes = data.get("nodes", []) if data else []
            links = data.get("links", []) if data else []
            self._send_json(200, {
                "ok": True,
                "nodeCount": len(nodes),
                "linkCount": len(links),
                "fileSize": os.path.getsize(DATA_FILE) if os.path.exists(DATA_FILE) else 0,
                "backupCount": len([f for f in os.listdir(BACKUP_DIR) if f.startswith("backup_")]),
                "updated": now_str()
            })
        else:
            # 静态文件：/ → index.html，其他按路径映射
            if path == "/":
                filepath = os.path.join(ROOT_DIR, "knowledge-graph.html")
            else:
                filepath = os.path.join(ROOT_DIR, path.lstrip("/"))
            self._serve_file(filepath)

    def do_POST(self):
        path = urlparse(self.path).path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        if path == "/api/data":
            make_backup()
            safe_write_json(DATA_FILE, req.get("data", req))
            self._send_json(200, {"ok": True, "saved": now_str()})
        elif path == "/api/settings":
            safe_write_json(SETTINGS_FILE, req.get("data", req))
            self._send_json(200, {"ok": True, "saved": now_str()})
        elif path == "/api/backup":
            path = make_backup()
            if path:
                self._send_json(200, {"ok": True, "backup": os.path.basename(path)})
            else:
                self._send_json(400, {"error": "No data to backup"})
        elif path == "/api/restore":
            name = req.get("name", "")
            src = os.path.join(BACKUP_DIR, name)
            if not os.path.exists(src):
                self._send_json(404, {"error": "Backup not found"})
                return
            make_backup()  # 恢复前先备份当前版本
            shutil.copy2(src, DATA_FILE)
            self._send_json(200, {"ok": True, "restored": name})
        else:
            self._send_json(404, {"error": "Not Found"})

    def log_message(self, format, *args):
        print(f"[{now_str()}] {args[0]}")


PORT = 8765
print(f"知识图谱后端服务启动: http://localhost:{PORT}")
print(f"数据目录: {DATA_DIR}")
print(f"API: GET/POST /api/data  |  GET /api/backups  |  POST /api/backup  |  POST /api/restore")
HTTPServer(("127.0.0.1", PORT), APIHandler).serve_forever()

