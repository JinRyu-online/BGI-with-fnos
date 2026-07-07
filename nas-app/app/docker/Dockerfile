# NAS 应用容器镜像。
# 基于 python:3.12-slim，安装 FastAPI + Jinja2 + 扫描/轮询所需依赖，
# 拷贝 app/ 源码到 /app，以 uvicorn 启动。
FROM python:3.12-slim

# fnOS 注入的应用路径环境变量（见 docs/开发方案.md §5.2）。
# etc  -> 配置持久化（config.json）
# var  -> 运行数据（app.log、历史）
ENV TRIM_PKGETC=/etc/app \
    TRIM_PKGVAR=/var/app \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先装依赖（利用 Docker 层缓存）
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 拷贝应用源码
COPY app/ /app/

# 容器内监听 8000；host 网络模式下直接占用宿主 8000 端口，
# fnOS 统一网关反代或在应用中心直接跳转。
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
