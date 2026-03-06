import os
import sqlalchemy
from databases import Database

# 1. 极其动态的环境变量寻址
# 这里的 "db" 就是你在 docker-compose.yml 里给 MySQL 容器起的名字！
# 如果环境变量没配，就默认连本地（防崩溃兜底）
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+aiomysql://root:root_password_123@db:3306/lansync_db"
)

# 2. 实例化异步连接池（Connection Pool）
# 它会在内存里极其聪明地维护几十个长连接，绝不频繁握手挥手
database = Database(DATABASE_URL, min_size=5, max_size=20)

# 3. 极其严谨的元数据管家（用于定义表结构）
metadata = sqlalchemy.MetaData()

# 4. 定义你的第一张核心表：局域网文件/消息记录表
# 我们不写容易引发 N+1 性能灾难的臃肿 ORM，我们用极致轻量的 SQLAlchemy Core
messages_table = sqlalchemy.Table(
    "messages",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("file_id", sqlalchemy.String(50), nullable=True),
    sqlalchemy.Column("filename", sqlalchemy.String(255), nullable=True),
    sqlalchemy.Column("msg_type", sqlalchemy.String(20), nullable=False),  # 'text' 或 'file'
    sqlalchemy.Column("content", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

# 5. 极其暴力的建表引擎（同步模式仅在启动时用一次）
# 它会自动探测 MySQL 里有没有这张表，没有就瞬间建好，有就跳过
engine = sqlalchemy.create_engine(
    DATABASE_URL.replace("mysql+aiomysql", "mysql+pymysql")  # 建表时的 DDL 操作允许用同步驱动
)
metadata.create_all(engine)