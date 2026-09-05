# NAS 应用容器镜像。
# 基于 python:3.12-slim，安装 FastAPI + Jinja2 + psutil + httpx + websockets，
# 拷贝 app/ 源码到 /app，以 uvicorn 启动。
# 持久化数据经 docker-compose 挂载到 /data（BGI_DATA_DIR）。
FROM python:3.12-slim

# tzdata：slim 镜像默认不带；不装它 ENV TZ 不生效，定时任务会按 UTC 触发（差 8 小时）
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV BGI_DATA_DIR=/data \
    TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先装依赖（利用 Docker 层缓存）
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 拷贝应用源码
COPY app/ /app/

# 容器内监听 8000；host 网络模式下直接占用宿主 8000 端口。
EXPOSE 8000

# --log-level info: 让 bgi_trigger.* logger 的 INFO 级日志(关键节点/扫描诊断)输出到 stdout,
# fnOS appcenter 通过 `docker logs` 可看到。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
