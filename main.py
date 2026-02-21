import os
import uuid
import aiofiles
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any

# ================= 配置区 =================
MAX_RECORD_COUNT = 10  # 严格限制最多 10 条记录
UPLOAD_DIR = "./temp_uploads"  # 临时文件存储目录

# 启动时确保上传目录存在
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="LAN Sync Server")

# 允许跨域（方便局域网内不同设备的浏览器直接访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= 状态管理 =================
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        # 使用列表而不是 deque，方便我们在剔除旧元素时触发硬盘文件的物理删除
        self.history: List[Dict[str, Any]] = []

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
        if len(self.history) >= MAX_RECORD_COUNT:
            # 弹出最老的一条记录
            oldest_msg = self.history.pop(0)
            
            # 如果最老的记录是文件类型，执行物理删除，绝不挤占昂贵的固态硬盘
            if oldest_msg.get("msg_type") == "file":
                filepath = oldest_msg.get("filepath")
                if filepath and os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                        print(f"[存储释放] 已物理删除过期文件: {filepath}")
                    except Exception as e:
                        print(f"[清理失败] {e}")

        self.history.append(message)
        await self.broadcast(message)

manager = ConnectionManager()

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

if __name__ == "__main__":
    import uvicorn
    # 监听 0.0.0.0 以允许局域网内的其他设备（手机、笔记本）访问
    print("🚀 局域网通信主节点启动中...")
    uvicorn.run(app, host="0.0.0.0", port=8000)