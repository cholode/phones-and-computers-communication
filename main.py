import asyncio
import json
import os
import threading
import uuid
import sys
import socket
from collections import deque
import aiofiles
from sqlalchemy import desc, or_
from database import database, messages_table
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Deque

from database import database

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

# 启动时确保上传目录存在


''' 加入心跳测试，僵尸回收 '''






# 假设 database 是你之前定义的异步数据库连接池对象
# database = Database(DATABASE_URL, min_size=5, max_size=20)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        # 使用列表而不是 deque，方便我们在剔除旧元素时触发硬盘文件的物理删除
        self.history: Deque[Dict[str, Any]] = deque()

    async def load_history_from_db(self):
        try:
            # 极其严谨的 SQL 查询：按主键 ID 倒序查最新的 10 条
            query = messages_table.select().order_by(desc(messages_table.c.id)).limit(MAX_RECORD_COUNT)
            records = await database.fetch_all(query)

            # 因为是倒序查出来的，装进队列时必须反转回来，保证时间轴是顺的
            for row in reversed(records):
                # 将数据库返回的 Record 对象组装成前端需要的 JSON 格式
                msg = {
                    "id": row["file_id"] or str(uuid.uuid4()),  # 文本消息可能没 file_id
                    "msg_type": row["msg_type"],
                    "content": row["content"],
                    "filename": row["filename"],
                    # filepath 和 download_url 我们按需重新拼接
                    "filepath": os.path.join(UPLOAD_DIR, f"{row['file_id']}_{row['filename']}") if row["msg_type"] == "file" else None,
                    "download_url": f"/files/{row['file_id']}_{row['filename']}" if row["msg_type"] == "file" else None
                }
                self.history.append(msg)

            print(f"✅ 状态机预热完毕：成功从 MySQL 唤醒 {len(self.history)} 条历史记录！")
        except Exception as e:
            print(f"❌ 预热失败，请检查数据库: {e}")


    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # 新设备接入时，立刻把当前的至多 10 条历史记录全量推送过去
        try:
            await websocket.send_json({"type": "connect", "data": list(self.history)})
        except Exception as e:
            print(f"发送历史记录失败: {e}")

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
        """核心业务链路：双写架构（Write-Through Cache）"""
        # 1. 极其冷酷的异步落盘（存入 MySQL）
        try:
            query = messages_table.insert().values(
                file_id=message.get("id"),
                filename=message.get("filename"),
                msg_type=message.get("msg_type"),
                content=message.get("content")
            )
            await database.execute(query)
        except Exception as e:
            print(f"[警告] 消息落库失败: {e}")

        # 2. 更新内存窗口并全网广播
        self.history.append(message)
        await self.broadcast(message)

        # 3. 滑动窗口与极其严厉的物理回收
        if len(self.history) > MAX_RECORD_COUNT:
            # 修复了你之前的 off-by-one 差一错误
            try:
                oldest_msg = self.history.popleft()
            except Exception as e:
                print(f"[清理灾难] 过期记录回收失败: {e}")


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 【点火阶段】服务器启动前，建立底层异步连接池
    print("🚀 正在强力打通 MySQL 异步连接池...")
    await database.connect()
    print("✅ MySQL 连接池就绪，网关全功率开启！")
    await manager.load_history_from_db()



    # 打印启动面板
    print("\n" + "=" * 60)
    print("🚀 局域网极速传 - 服务器已成功启动！\n")
    print("🚀 局域网极速传 (Docker 完全体) - 服务器已成功启动！\n")
    print("👉 后端已绑定虚拟网卡 0.0.0.0:8000，等待宿主机 NAT 流量穿透...")
    print("⚠️ 请在物理机终端使用 `ipconfig` 查看真实局域网 IP。")
    print("=" * 60 + "\n")

    print("\n⚠️ 提示：如果前端点击【雷达扫描】找不到，请检查 Windows 防火墙是否放行。")
    print("=" * 60 + "\n")

    yield  # 这里是 FastAPI 正常运行的时间

    # 【熄火阶段】服务器关闭时，优雅断开所有 TCP 连接
    print("🛑 正在优雅销毁 MySQL 连接池...")
    await database.disconnect()


# 把 lifespan 挂载到你的 app 上
app = FastAPI(title="LAN Sync Server",lifespan=lifespan)


# 允许跨域（方便局域网内不同设备的浏览器直接访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= 状态管理 =================


@app.get("/search")
async def search_history(keyword: str = Query(..., min_length=1, description="搜索关键字")):
    """大厂级模糊查询：同时穿透文本内容与文件名称"""
    try:
        # 1. 构建全表扫描模糊匹配模式 (前后加 % 代表包含该子串)
        search_pattern = f"%{keyword}%"

        # 2. 极其严谨的复合查询树
        # 使用 ilike (大小写不敏感)，并用 or_ 同时检索文件名和聊天内容
        query = messages_table.select().where(
            or_(
                messages_table.c.content.ilike(search_pattern),
                messages_table.c.filename.ilike(search_pattern)
            )
        ).order_by(
            # 按时间倒序，最新的搜索结果排在最前面
            desc(messages_table.c.id)
        ).limit(50)  # 工业级防爆盾：强制限制最多返回 50 条，防止恶意查询打爆内存

        # 3. 异步执行，不阻塞网关
        records = await database.fetch_all(query)

        # 4. 数据清洗与组装
        results = []
        for row in records:
            results.append({
                "id": row["file_id"],
                "msg_type": row["msg_type"],
                "content": row["content"],
                "filename": row["filename"],
                "filepath": os.path.join(UPLOAD_DIR, f"{row['file_id']}_{row['filename']}") if row[
                                                                                                   "msg_type"] == "file" else None,
                "download_url": f"/files/{row['file_id']}_{row['filename']}" if row["msg_type"] == "file" else None
            })

        return {"status": "success", "count": len(results), "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"数据库检索异常: {str(e)}")


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
                "content": data,
                "filename": None,
                "filepath": None
            }
            await manager.add_message(msg)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

        pass  # 怎加断线重连机制


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
        "filepath": filepath,  # 后端专用的物理路径（用于将来删除）
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000,reload=True)