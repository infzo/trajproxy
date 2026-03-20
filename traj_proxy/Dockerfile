FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
# RUN apt-get update && apt-get install -y \
#     curl \
#     && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY traj_proxy/requirements.txt ./requirements.txt

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件，保持 traj_proxy/ 包结构
COPY traj_proxy/ ./traj_proxy/

# 暴露端口
EXPOSE 8000-8010
EXPOSE 9000-9005

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:$PYTHONPATH

# 启动命令 - 使用模块方式运行，确保 Python 能找到 traj_proxy 包
CMD ["python", "-m", "traj_proxy.app"]
