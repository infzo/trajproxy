## 常用命令

```shell
docker build --platform linux/amd64 -t traj-proxy-x86:0.0.6 -f dockers/Dockerfile .

## 查询模型
curl http://localhost:12345/models

## 查询模型
curl http://localhost:12300/v1/models

# 注册模型 run id
curl -X POST http://localhost:12345/models/register \
  -H "Content-Type: application/json" \
  -d '{
  "run_id": "ma-job-proxy-test2",
  "model_name": "qwen3.5-2b",
  "url": "http://host.docker.internal:8000/v1",
  "api_key": "sk-1234"
}'

# 下发请求
curl -X POST http://localhost:12345/s/ma-job-proxy-test2,sample_001,task_001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-1234" \
  -d '{
    "model": "qwen3.5-2b",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      }
    ]
  }'

# 下发请求
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-sandbox-traj-id: ma-job-proxy-test2,sample_001,task_001" \
  -H "Authorization: Bearer sk-1234" \
  -d '{
    "model": "qwen3.5-2b",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      }
    ]
  }'

# 下发请求
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-sandbox-traj-id: simple_session" \
  -H "Authorization: Bearer sk-1234" \
  -d '{
    "model": "qwen3.5-2b,ma-job-proxy-test2",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      }
    ]
  }'

# 删除模型
curl -X DELETE "http://localhost:12345/models?model_name=qwen3.5-2b&run_id=ma-job-proxy-test2"

```
