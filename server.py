"""
知识图谱后端服务 - 数据持久化 + 前端页面托管
启动方式: python server.py
打开浏览器访问: http://127.0.0.1:8765
API:
  GET  /api/data       - 获取图谱数据（默认画布）
  POST /api/data       - 保存图谱数据（默认画布）
  GET  /api/canvases   - 获取画布列表
  POST /api/canvases   - 创建新画布或融合画布
  GET  /api/canvas/{id} - 获取画布数据
  POST /api/canvas/{id} - 保存画布数据
  DELETE /api/canvas/{id} - 删除画布
  GET  /api/merged/{id} - 获取融合画布数据
  GET  /api/settings   - 获取设置
  POST /api/settings   - 保存设置
  GET  /api/backups    - 列出备份
  POST /api/backup     - 手动创建备份
  POST /api/restore    - 从备份恢复
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
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
DATA_FILE = os.path.join(DATA_DIR, "graph-data.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
CANVASES_FILE = os.path.join(DATA_DIR, "canvases.json")
MERGED_DIR = os.path.join(DATA_DIR, "merged")

os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(MERGED_DIR, exist_ok=True)

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


def make_backup(canvas_id="default"):
    """自动备份当前数据，保留最近 20 份"""
    if canvas_id == "default":
        data_file = DATA_FILE
    else:
        data_file = os.path.join(DATA_DIR, f"{canvas_id}.json")

    if not os.path.exists(data_file):
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"backup_{canvas_id}_{ts}.json")
    shutil.copy2(data_file, backup_path)
    _cleanup_backups(canvas_id)
    return backup_path


def _cleanup_backups(canvas_id="default", keep=20):
    """保留最近 keep 份备份"""
    backups = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.startswith(f"backup_{canvas_id}_") and f.endswith(".json")]
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


def get_canvas_file(canvas_id):
    """获取画布文件路径"""
    if canvas_id == "default":
        return DATA_FILE
    return os.path.join(DATA_DIR, f"{canvas_id}.json")


def load_canvases():
    """加载画布索引"""
    if os.path.exists(CANVASES_FILE):
        return safe_read_json(CANVASES_FILE)
    # 如果不存在，创建默认索引
    return {
        "canvases": [{
            "id": "default",
            "title": "默认画布",
            "created": now_str(),
            "updated": now_str(),
            "nodeCount": 0,
            "linkCount": 0
        }],
        "currentCanvasId": "default"
    }


def save_canvases(data):
    """保存画布索引"""
    safe_write_json(CANVASES_FILE, data)


def update_canvas_meta(canvas_id, title=None, node_count=None, link_count=None):
    """更新画布元数据"""
    data = load_canvases()
    for cv in data["canvases"]:
        if cv["id"] == canvas_id:
            if title is not None:
                cv["title"] = title
            if node_count is not None:
                cv["nodeCount"] = node_count
            if link_count is not None:
                cv["linkCount"] = link_count
            cv["updated"] = now_str()
            break
    save_canvases(data)


def load_merged_canvas(merged_id):
    """加载融合画布数据"""
    merged_file = os.path.join(MERGED_DIR, f"{merged_id}.json")
    if os.path.exists(merged_file):
        return safe_read_json(merged_file)
    return None


def save_merged_canvas(merged_id, data):
    """保存融合画布数据"""
    merged_file = os.path.join(MERGED_DIR, f"{merged_id}.json")
    safe_write_json(merged_file, data)


def get_merged_canvas_file(merged_id):
    """获取融合画布文件路径"""
    return os.path.join(MERGED_DIR, f"{merged_id}.json")


def init_default_canvas():
    """初始化默认画布（如果需要迁移）"""
    global DATA_FILE

    # 如果 default.json 已存在，无需迁移
    default_file = os.path.join(DATA_DIR, "default.json")
    if os.path.exists(default_file):
        return

    # 如果 graph-data.json 存在，迁移到 default.json
    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, default_file)

    # 创建画布索引
    data = {
        "canvases": [{
            "id": "default",
            "title": "默认画布",
            "created": now_str(),
            "updated": now_str(),
            "nodeCount": 0,
            "linkCount": 0
        }],
        "currentCanvasId": "default"
    }
    save_canvases(data)


# 启动时初始化
init_default_canvas()


class APIHandler(BaseHTTPRequestHandler):
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

    def _valid_canvas_id(self, canvas_id):
        """验证画布 ID 格式（防止路径穿越）"""
        return bool(re.match(r'^[a-zA-Z0-9_-]+$', canvas_id))

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_GET(self):
        path = urlparse(self.path).path

        # API 路由
        if path == "/api/data":
            # 兼容旧接口，返回默认画布数据
            default_file = os.path.join(DATA_DIR, "default.json")
            data = safe_read_json(default_file) if os.path.exists(default_file) else safe_read_json(DATA_FILE)
            self._send_json(200, {"ok": True, "data": data, "updated": now_str()})

        elif path == "/api/canvases":
            # 获取画布列表
            canvases_data = load_canvases()
            self._send_json(200, {
                "ok": True,
                "canvases": canvases_data["canvases"],
                "currentCanvasId": canvases_data.get("currentCanvasId", "default")
            })

        elif path.startswith("/api/canvas/"):
            # 获取指定画布数据
            canvas_id = path.replace("/api/canvas/", "")
            if not self._valid_canvas_id(canvas_id):
                self._send_json(400, {"error": "Invalid canvas ID"})
                return
            canvas_file = get_canvas_file(canvas_id)
            if not os.path.exists(canvas_file):
                self._send_json(404, {"error": "Canvas not found"})
                return
            data = safe_read_json(canvas_file)
            self._send_json(200, {"ok": True, "data": data, "updated": now_str()})

        elif path.startswith("/api/merged/"):
            # 获取融合画布数据
            merged_id = path.replace("/api/merged/", "")
            if not self._valid_canvas_id(merged_id):
                self._send_json(400, {"error": "Invalid merged canvas ID"})
                return
            merged_data = load_merged_canvas(merged_id)
            if not merged_data:
                self._send_json(404, {"error": "Merged canvas not found"})
                return
            self._send_json(200, {"ok": True, "data": merged_data, "updated": now_str()})

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
            default_file = os.path.join(DATA_DIR, "default.json")
            data = safe_read_json(default_file) if os.path.exists(default_file) else safe_read_json(DATA_FILE)
            nodes = data.get("nodes", []) if data else []
            links = data.get("links", []) if data else []
            self._send_json(200, {
                "ok": True,
                "nodeCount": len(nodes),
                "linkCount": len(links),
                "fileSize": os.path.getsize(default_file) if os.path.exists(default_file) else 0,
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
            # 兼容旧接口，保存到默认画布
            make_backup("default")
            default_file = os.path.join(DATA_DIR, "default.json")
            safe_write_json(default_file, req.get("data", req))
            self._send_json(200, {"ok": True, "saved": now_str()})

        elif path == "/api/canvases":
            # 创建新画布或融合画布
            canvas_id = req.get("id", "")
            title = req.get("title", canvas_id)
            is_merged = req.get("isMerged", False)
            source_canvases = req.get("sourceCanvases", [])

            if not canvas_id or not self._valid_canvas_id(canvas_id):
                self._send_json(400, {"error": "Invalid canvas ID"})
                return

            canvases_data = load_canvases()
            if any(cv["id"] == canvas_id for cv in canvases_data["canvases"]):
                self._send_json(400, {"error": "Canvas ID already exists"})
                return

            if is_merged:
                # 创建融合画布
                merged_data = {
                    "type": "merged",
                    "sourceCanvases": source_canvases,
                    "crossCanvasLinks": [],
                    "nodes": [],  # 融合画布不存储节点
                    "links": []
                }
                save_merged_canvas(canvas_id, merged_data)

                # 添加到索引，标记为融合画布
                canvases_data["canvases"].append({
                    "id": canvas_id,
                    "title": title,
                    "created": now_str(),
                    "updated": now_str(),
                    "nodeCount": 0,
                    "linkCount": 0,
                    "isMerged": True,
                    "sourceCanvases": source_canvases
                })
            else:
                # 创建普通画布
                canvas_file = get_canvas_file(canvas_id)
                safe_write_json(canvas_file, {"nodes": [], "links": []})

                canvases_data["canvases"].append({
                    "id": canvas_id,
                    "title": title,
                    "created": now_str(),
                    "updated": now_str(),
                    "nodeCount": 0,
                    "linkCount": 0
                })
            save_canvases(canvases_data)

            self._send_json(200, {"ok": True, "canvas": {"id": canvas_id, "title": title, "isMerged": is_merged}})

        elif path.startswith("/api/canvas/") and not path.endswith("/delete"):
            # 保存画布数据
            canvas_id = path.replace("/api/canvas/", "")
            if not self._valid_canvas_id(canvas_id):
                self._send_json(400, {"error": "Invalid canvas ID"})
                return

            canvas_file = get_canvas_file(canvas_id)
            data = req.get("data", req)
            safe_write_json(canvas_file, data)

            # 更新元数据
            update_canvas_meta(canvas_id,
                node_count=len(data.get("nodes", [])),
                link_count=len(data.get("links", [])))

            self._send_json(200, {"ok": True, "saved": now_str()})

        elif path.startswith("/api/merged/") and not path.endswith("/delete"):
            # 保存融合画布的跨画布连接
            merged_id = path.replace("/api/merged/", "")
            if not self._valid_canvas_id(merged_id):
                self._send_json(400, {"error": "Invalid merged canvas ID"})
                return

            merged_data = load_merged_canvas(merged_id)
            if not merged_data:
                self._send_json(404, {"error": "Merged canvas not found"})
                return

            # 更新跨画布连接
            merged_data["crossCanvasLinks"] = req.get("crossCanvasLinks", [])
            save_merged_canvas(merged_id, merged_data)

            self._send_json(200, {"ok": True, "saved": now_str()})

        elif path == "/api/settings":
            safe_write_json(SETTINGS_FILE, req.get("data", req))
            self._send_json(200, {"ok": True, "saved": now_str()})

        elif path == "/api/backup":
            path = make_backup("default")
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
            make_backup("default")  # 恢复前先备份当前版本
            shutil.copy2(src, os.path.join(DATA_DIR, "default.json"))
            self._send_json(200, {"ok": True, "restored": name})

        else:
            self._send_json(404, {"error": "Not Found"})

    def do_DELETE(self):
        path = urlparse(self.path).path

        if path.startswith("/api/canvas/"):
            canvas_id = path.replace("/api/canvas/", "")
            if not self._valid_canvas_id(canvas_id):
                self._send_json(400, {"error": "Invalid canvas ID"})
                return

            if canvas_id == "default":
                self._send_json(400, {"error": "Cannot delete default canvas"})
                return

            canvases_data = load_canvases()
            if not any(cv["id"] == canvas_id for cv in canvases_data["canvases"]):
                self._send_json(404, {"error": "Canvas not found"})
                return

            # 删除画布文件
            canvas_file = get_canvas_file(canvas_id)
            if os.path.exists(canvas_file):
                os.remove(canvas_file)

            # 从索引移除
            canvases_data["canvases"] = [cv for cv in canvases_data["canvases"] if cv["id"] != canvas_id]
            if canvases_data.get("currentCanvasId") == canvas_id:
                canvases_data["currentCanvasId"] = "default"
            save_canvases(canvases_data)

            self._send_json(200, {"ok": True, "deleted": canvas_id})

        elif path.startswith("/api/merged/"):
            merged_id = path.replace("/api/merged/", "")
            if not self._valid_canvas_id(merged_id):
                self._send_json(400, {"error": "Invalid merged canvas ID"})
                return

            merged_file = get_merged_canvas_file(merged_id)
            if os.path.exists(merged_file):
                os.remove(merged_file)

            # 从索引移除
            canvases_data = load_canvases()
            canvases_data["canvases"] = [cv for cv in canvases_data["canvases"] if cv["id"] != merged_id]
            save_canvases(canvases_data)

            self._send_json(200, {"ok": True, "deleted": merged_id})
        else:
            self._send_json(404, {"error": "Not Found"})

    def log_message(self, format, *args):
        print(f"[{now_str()}] {args[0]}")


PORT = 8765
print(f"知识图谱后端服务启动: http://localhost:{PORT}")
print(f"数据目录: {DATA_DIR}")
print(f"API: 多画布支持 - /api/canvases, /api/canvas/{'{id}'}")
HTTPServer(("127.0.0.1", PORT), APIHandler).serve_forever()
