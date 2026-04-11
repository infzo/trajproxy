FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# # 安装系统依赖（添加 git）
# RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt ./requirements.txt

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

# 复制脚本
COPY scripts/tools/download_tokenizer.py ./download_tokenizer.py
COPY scripts/docker-compose/entrypoint.sh ./scripts/entrypoint.sh

# 复制 models 目录（如果存在）
RUN mkdir -p ./models
COPY models/ ./models/

# 复制项目文件，保持 traj_proxy/ 包结构
COPY traj_proxy/ ./traj_proxy/

# 暴露端口
EXPOSE 12300-12320

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:$PYTHONPATH

# 赋予执行权限
RUN chmod +x ./scripts/entrypoint.sh

# 启动命令 - 统一入口：初始化数据库 + 启动应用
CMD ["./scripts/entrypoint.sh"]
