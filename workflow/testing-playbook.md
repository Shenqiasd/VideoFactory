# 测试作业手册

## A. 最小必跑（每次改动）
1. 单元/接口/模板测试
```bash
./.venv/bin/python -m pytest -q
```

## B. 服务联调（改动任务链路或页面时）
1. 启动并检查服务
```bash
bash scripts/start_all.sh restart
bash scripts/start_all.sh status
```

2. 健康检查
```bash
curl -sS -m 5 http://127.0.0.1:9000/api/health
curl -sS -m 3 http://127.0.0.1:8866/health
curl -sS -m 3 http://127.0.0.1:8877/health
```

3. 关键页面可达
```bash
curl -sS -m 5 -o /dev/null -w "GET / -> %{http_code}\n" http://127.0.0.1:9000/
curl -sS -m 5 -o /dev/null -w "GET /tasks -> %{http_code}\n" http://127.0.0.1:9000/tasks
```

## C. 链路回归（建议）
1. 创建任务并查看状态
```bash
curl -sS -X POST http://127.0.0.1:9000/api/tasks/ \
  -H "Content-Type: application/json" \
  -d source_url:https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

2. 轮询详情
```bash
curl -sS http://127.0.0.1:9000/api/tasks/<task_id>
```

## D. 通过标准
- `pytest` 全绿
- API/Worker/当前代理服务可连通
- 目标 scope 行为与预期一致
- 无新增高优先级回归
