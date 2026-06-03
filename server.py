"""
知识图谱后端服务 - 数据持久化 + 前端页面托管
启动方式: python server.py
打开浏览器访问: http://127.0.0.1:8765
API:
  GET  /api/data          - 获取当前文件图谱数据
  POST /api/data          - 保存当前文件图谱数据
  GET  /api/files         - 列出所有 graph 文件
  POST /api/files         - 创建新的空 graph 文件
  DELETE /api/files/{id}  - 删除指定 graph 文件
  POST /api/files/import  - 导入 graph 文件
  GET  /api/settings      - 获取设置
  POST /api/settings      - 保存设置
"""

import os
import re
import json
import shutil
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, "data")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
DEFAULT_FILE = os.path.join(DATA_DIR, "default.json")

os.makedirs(DATA_DIR, exist_ok=True)

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

_write_lock = threading.Lock()

GRAPH_FILE_RE = re.compile(r'^[a-zA-Z0-9_-]+\.json$')


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def list_graph_files():
    """扫描 data/ 下所有 graph 文件（排除 settings.json 和备份/索引文件）"""
    files = []
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname in ('settings.json', 'canvases.json'):
            continue
        if fname.startswith('backup_') or fname.startswith('merged_'):
            continue
        fpath = os.path.join(DATA_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            data = safe_read_json(fpath)
            files.append({
                "id": fname,
                "title": fname.replace('.json', ''),
                "nodeCount": len(data.get("nodes", [])),
                "linkCount": len(data.get("links", [])),
                "updated": datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M:%S")
            })
        except Exception:
            pass
    return files


def ensure_default():
    """确保 default.json 存在"""
    if not os.path.exists(DEFAULT_FILE):
        safe_write_json(DEFAULT_FILE, {"nodes": [], "links": []})


class APIHandler(BaseHTTPRequestHandler):

    def _get_data_file(self):
        """从查询参数获取目标文件，默认 default.json"""
        qs = parse_qs(urlparse(self.path).query)
        fname = qs.get('file', [None])[0]
        if fname and GRAPH_FILE_RE.match(fname):
            return os.path.join(DATA_DIR, fname)
        return DEFAULT_FILE

    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, filepath):
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

        if path == "/api/data":
            df = self._get_data_file()
            data = safe_read_json(df)
            self._send_json(200, {"ok": True, "data": data, "updated": now_str()})

        elif path == "/api/files":
            files = list_graph_files()
            self._send_json(200, {"ok": True, "files": files})

        elif path == "/api/settings":
            data = safe_read_json(SETTINGS_FILE)
            self._send_json(200, {"ok": True, "data": data, "updated": now_str()})

        else:
            if path == "/":
                filepath = os.path.join(ROOT_DIR, "knowledge-graph.html")
            else:
                filepath = os.path.join(ROOT_DIR, path.lstrip("/"))
            self._serve_file(filepath)

    def do_POST(self):
        path = urlparse(self.path).path
        cl = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(cl) if cl else b"{}"
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        if path == "/api/data":
            df = self._get_data_file()
            safe_write_json(df, req.get("data", req))
            self._send_json(200, {"ok": True, "saved": now_str()})

        elif path == "/api/files":
            fname = req.get("id", "")
            title = req.get("title", fname)
            if not fname:
                fname = "graph_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            if not fname.endswith('.json'):
                fname += '.json'
            if not GRAPH_FILE_RE.match(fname):
                self._send_json(400, {"error": "Invalid file name"})
                return
            fpath = os.path.join(DATA_DIR, fname)
            if os.path.exists(fpath):
                self._send_json(400, {"error": "File already exists"})
                return
            safe_write_json(fpath, {"nodes": [], "links": []})
            self._send_json(200, {"ok": True, "file": {"id": fname, "title": title}})

        elif path == "/api/files/import":
            data = req.get("data", {})
            fname = req.get("name", "")
            if not fname:
                fname = "import_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            if not fname.endswith('.json'):
                fname += '.json'
            if not GRAPH_FILE_RE.match(fname):
                self._send_json(400, {"error": "Invalid file name"})
                return
            fpath = os.path.join(DATA_DIR, fname)
            if os.path.exists(fpath):
                base = fname[:-5]
                fname = base + "_" + datetime.now().strftime("%H%M%S") + ".json"
                fpath = os.path.join(DATA_DIR, fname)
            safe_write_json(fpath, data)
            self._send_json(200, {"ok": True, "file": {"id": fname, "title": fname.replace('.json', '')}})

        elif path == "/api/settings":
            safe_write_json(SETTINGS_FILE, req.get("data", req))
            self._send_json(200, {"ok": True, "saved": now_str()})

        else:
            self._send_json(404, {"error": "Not Found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/files/"):
            fname = path.replace("/api/files/", "")
            if not GRAPH_FILE_RE.match(fname) or fname == 'default.json':
                self._send_json(400, {"error": "Cannot delete this file"})
                return
            fpath = os.path.join(DATA_DIR, fname)
            if os.path.exists(fpath):
                os.remove(fpath)
            self._send_json(200, {"ok": True, "deleted": fname})
        else:
            self._send_json(404, {"error": "Not Found"})

    def log_message(self, format, *args):
        print(f"[{now_str()}] {args[0]}")


if __name__ == "__main__":
    ensure_default()
    PORT = 8765
    print(f"知识图谱后端服务启动: http://localhost:{PORT}")
    print(f"数据目录: {DATA_DIR}")
    print(f"API: /api/data  /api/files  /api/settings")
    HTTPServer(("127.0.0.1", PORT), APIHandler).serve_forever()
