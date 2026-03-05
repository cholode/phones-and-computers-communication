import asyncio
import json
import os
import threading
import uuid
import sys
import socket
from collections import deque

import aiofiles
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Deque


# 获取程序当前真正所在的物理路径
if getattr(sys, 'frozen', False):
    # 如果是打包后的 EXE 运行，获取 EXE 所在的真实文件夹
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # 如果是直接跑 Python 脚本
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# HTML 文件和上传文件夹，全部严格指向 EXE 同级目录
UPLOAD_DIR = os.path.join(BASE_DIR, "temp_uploads")

# 启动时确保上传文件夹存在
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ================= 配置区 =================
MAX_RECORD_COUNT = 10  # 严格限制最多 10 条记录
UPLOAD_DIR = "./temp_uploads"  # 临时文件存储目录

# 启动时确保上传目录存在
os.makedirs(UPLOAD_DIR, exist_ok=True)

#历史记录保存路径
HISTORY_FILENAME = "history_snapshot.json"
app = FastAPI(title="LAN Sync Server")

# 允许跨域（方便局域网内不同设备的浏览器直接访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

''' 加入心跳测试，僵尸回收 '''

# ================= 状态管理 =================
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        # 使用列表而不是 deque，方便我们在剔除旧元素时触发硬盘文件的物理删除
        self.history: Deque[Dict[str, Any]] = deque()
        self._lock = asyncio.Lock()
        self._load_history()

    def _load_history(self):
        """启动时重载状态"""
        if os.path.exists(HISTORY_FILENAME):
            try:
                with open(HISTORY_FILENAME, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for msg in data:
                        self.history.append(msg)
                print(f"已加载成功！")
            except Exception as e:
                print(f"文件{HISTORY_FILENAME}已损坏,{e}")

    async def _save_history_snapshot(self):
        """通过异步锁存入文件"""
        async with self._lock:
            try:
                async with aiofiles.open(HISTORY_FILENAME, "w", encoding="utf-8") as f:
                    snapshot_data = json.dumps(list(self.history), ensure_ascii=False)
                    await f.write(snapshot_data)
            except Exception as e:
                print(f"落盘失败，{e}")



    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # 新设备接入时，立刻把当前的至多 10 条历史记录全量推送过去
        await websocket.send_json({"type": "history", "data": self.history})

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        # 广播给局域网内的所有在线设备
        for connection in self.active_connections:
            try:
                await connection.send_json({"type": "new_message", "data": message})
            except Exception:
                pass

    async def add_message(self, message: dict):
        """核心逻辑：滑动窗口与硬盘垃圾回收"""

        self.history.append(message)
        await self.broadcast(message)

        if len(self.history) >= MAX_RECORD_COUNT:
            # 弹出最老的一条记录
            try:
                oldest_msg = self.history.popleft()
                if oldest_msg.get("msg_type") == "file":
                    filepath = oldest_msg.get("filepath")
                    if filepath and os.path.exists(filepath):
                        try:
                            os.remove(filepath)
                            print(f"[存储释放] 已物理删除过期文件: {filepath}")
                        except Exception as e:
                            print(f"[清理失败] {e}")
            except IndexError:
                pass
            # 如果最老的记录是文件类型，执行物理删除，绝不挤占昂贵的固态硬盘

        asyncio.create_task(self._save_history_snapshot())



manager = ConnectionManager()

# 【新增】：响应局域网扫描的 Ping 接口
@app.get("/ping")
async def ping():
    # 自动获取运行该服务器的计算机名称（例如："张三的拯救者" 或 "MacBook-Pro"）
    hostname = socket.gethostname()
    return {"status": "ok", "server_name": hostname}
# ================= 路由接口 =================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket：专职负责文本传输与状态同步，协议头开销极小"""
    await manager.connect(websocket)
    try:
        while True:
            # 接收客户端发来的无长度限制的文本消息
            data = await websocket.receive_text()
            
            msg = {
                "id": str(uuid.uuid4()),
                "msg_type": "text",
                "content": data
            }
            await manager.add_message(msg)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

        pass #怎加断线重连机制

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """HTTP 流式上传：专职对付不限大小的巨型文件"""
    file_id = str(uuid.uuid4())
    safe_filename = f"{file_id}_{file.filename}"
    filepath = os.path.join(UPLOAD_DIR, safe_filename)

    # 采用 aiofiles 异步流式分块写入
    # 彻底杜绝把大文件读进 RAM，保护极其珍贵的物理内存
    try:
        async with aiofiles.open(filepath, 'wb') as out_file:
            while content := await file.read(64 * 1024):  # 每次仅读写 64KB Chunk
                await out_file.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件流式写入失败: {str(e)}")

    # 组装文件消息体
    file_msg = {
        "id": file_id,
        "msg_type": "file",
        "filename": file.filename,
        "filepath": filepath,           # 后端专用的物理路径（用于将来删除）
        "download_url": f"/files/{safe_filename}"  # 供前端局域网下载的 URI
    }
    
    # 压入队列并广播，触发可能的硬盘回收逻辑
    await manager.add_message(file_msg)
    
    return {"status": "success", "file_id": file_id}

@app.get("/files/{filename}")
async def download_file(filename: str):
    """利用底层系统的直接发送，实现零拷贝级下载"""
    filepath = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在或已被系统回收")
    
    # FileResponse 在底层会自动优化为适合系统的 sendfile 调用
    return FileResponse(path=filepath, filename=filename.split("_", 1)[-1])


# ... (上面的 FastAPI 路由代码保持不变) ...

def get_all_ips():
    """获取本机所有非 127.0.0.1 的局域网 IP"""
    ips = []

    # 1. 尝试使用常规方法获取所有绑定的 IP
    try:
        hostname = socket.gethostname()
        _, _, ip_list = socket.gethostbyname_ex(hostname)
        for ip in ip_list:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    # 2. 结合 UDP 探测法作为补充（优先级更高）
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        udp_ip = s.getsockname()[0]
        if not udp_ip.startswith("127.") and udp_ip not in ips:
            # UDP 探测出来的往往优先级比较高，插到最前面
            ips.insert(0, udp_ip)
    except Exception:
        pass
    finally:
        try:
            if s:
                s.close()
        except:
            pass

    return ips


if __name__ == "__main__":
    import uvicorn

    available_ips = get_all_ips()

    # 打印启动面板
    print("\n" + "=" * 60)
    print("🚀 局域网极速传 - 服务器已成功启动！\n")
    print("👉 手机/其他电脑请尝试使用以下地址连接（通常是 192.168 开头）：")

    if not available_ips:
        print(" - http://127.0.0.1:8000 (仅限本机访问)")
    else:
        for ip in available_ips:
            print(f" - http://{ip}:8000")

    print("\n⚠️ 提示：如果前端点击【雷达扫描】找不到，请检查 Windows 防火墙是否放行。")
    print("=" * 60 + "\n")

    # 启动 FastAPI 服务，允许局域网所有设备访问
    uvicorn.run(app, host="0.0.0.0", port=8000)