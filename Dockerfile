# 1. 极其克制的底座：拒绝几 GB 的完整 Ubuntu，使用几百 MB 的 slim 精简版 Python 镜像
FROM python:3.11-slim

# 2. 设定容器内的工作绝对路径
WORKDIR /app

# 3. 【降维打击的细节：利用分层缓存优化构建速度】
# 不要上来就把整个项目 Copy 进去！先单独 Copy 依赖文件。
# 这样只要你不改 requirements.txt，下次构建镜像时 pip install 这一层会被瞬间跳过（Hit Cache）！
COPY requirements.txt .

# 4. 替换国内清华源并安装依赖（极其贴合国内网络环境的实战操作）
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 5. 最后，才把你的 FastAPI 业务代码全部 Copy 进容器
COPY . .

# 6. 暴露 8000 端口（这只是个声明，真正的映射在 compose 里做了）
EXPOSE 8000

# 7. 容器启动时的绝杀命令：Uvicorn 引擎拉起！
# 注意：host 必须是 0.0.0.0，绝不能是 127.0.0.1，否则宿主机绝对访问不到！
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]