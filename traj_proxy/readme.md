# TrajProxy

```shell
curl --location 'http://0.0.0.0:4000/v1/chat/completions' \
    --header 'Content-Type: application/json' \
    -H "Authorization: Bearer sk-1234" \
    --data '{
    "model": "qwen3.5-2b",
    "messages": [
        {
        "role": "user",
        "content": "what llm are you"
        }
    ]
}'


curl --location 'http://traj_proxy:8000/v1/chat/completions' \
    --header 'Content-Type: application/json' \
    -H "Authorization: Bearer sk-1234" \
    --data '{
    "model": "qwen3.5-2b",
    "messages": [
        {
        "role": "user",
        "content": "what llm are you"
        }
    ]
}'

curl --location 'http://0.0.0.0:4000/v1/messages' \
  --header 'x-api-key: sk-1234' \
  --header 'anthropic-version: 2023-06-01' \
  --header 'Content-Type: application/json' \
  --data '{
    "model": "qwen3.5-2b",
    "max_tokens": 1024,
    "messages": [
      {
        "role": "user",
        "content": "what llm are you"
      }
    ]
  }'

curl http://127.0.0.1:8000/v1/proxy-core

```