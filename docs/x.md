```shell
# 注册模型 run id
curl -X POST http://localhost:12300/models/register \
  -H "Content-Type: application/json" \
  -d '{
  "run_id": "ma-job-proxy-test2",
  "model_name": "/Temp/bucket-siye-green-guiyang-code/MindForge-Coder/models/Qwen3-Coder-30B-A3B-Instruct",
  "url": "http://7.242.105.47:8206",
  "api_key": "sk-1234"
}'

# 查询模型
curl http://localhost:12300/models

# 下发请求
curl -X POST http://localhost:12300/s/ma-job-proxy-test2,sample_001,task_001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-1234" \
  -d '{
    "model": "/Temp/bucket-siye-green-guiyang-code/MindForge-Coder/models/Qwen3-Coder-30B-A3B-Instruct",
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
  -H "x-session-id: ma-job-proxy-test2,sample_001,task_001" \
  -H "Authorization: Bearer sk-1234" \
  -d '{
    "model": "/Temp/bucket-siye-green-guiyang-code/MindForge-Coder/models/Qwen3-Coder-30B-A3B-Instruct",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      }
    ]
  }'

# 删除模型
curl -X DELETE http://localhost:12300/models?model_name=/Temp/bucket-siye-green-guiyang-code/MindForge-Coder/models/Qwen3-Coder-30B-A3B-Instruct


# 注册模型
curl -X POST http://localhost:12300/models/register \
  -H "Content-Type: application/json" \
  -d '{
  "model_name": "/Temp/bucket-siye-green-guiyang-code/MindForge-Coder/models/Qwen3-Coder-30B-A3B-Instruct",
  "url": "http://7.242.105.47:8206",
  "api_key": "sk-1234"
}'

# 下发请求
curl -X POST http://localhost:12300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-session-id: ,sample_001,task_001" \
  -H "Authorization: Bearer sk-1234" \
  -d '{
    "model": "/Temp/bucket-siye-green-guiyang-code/MindForge-Coder/models/Qwen3-Coder-30B-A3B-Instruct",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      }
    ]
  }'

```
