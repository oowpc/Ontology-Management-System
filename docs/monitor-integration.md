# DataAgent Monitor 接入 OMS

OMS 与 `datagent-monitor` 保持独立部署：监控服务负责采集、证据、基线和 diff；
OMS 负责事件收件箱、分诊、Issue 和闭环。

## 1. Dashboard 只读代理

- `/monitor`：来源健康、最近事件、最近运行
- `GET /api/monitor/dashboard`
- `GET /api/monitor/health`

配置：

```env
DATAAGENT_MONITOR_URL=http://127.0.0.1:8765
DATAAGENT_MONITOR_TIMEOUT=8
```

## 2. Webhook 事件入站

OMS：

```http
POST /api/monitor/events
```

- 本机 loopback 默认允许，不需要 token。
- 远程调用必须配置 `DATAAGENT_MONITOR_WEBHOOK_TOKEN`，并带
  `Authorization: Bearer ...`。
- 以 `source_event_id` / `fingerprint` 幂等去重。
- 写入 `monitor_events`，进入 `/monitor` 收件箱。

datagent-monitor `var/products.yaml`：

```yaml
integrations:
  webhooks:
    - id: oms
      url: http://127.0.0.1:8020/api/monitor/events
      timeout_seconds: 10
      max_attempts: 5
```

生产跨主机应使用 HTTPS，并配置 `authorization_env`。

## 3. 收件箱与 Issue

- `GET /api/monitor/events`
- `GET /api/monitor/events/{id}`
- `POST /api/monitor/events/{id}/issue`：转成当前库 Issue
- `POST /api/monitor/events/{id}/status`：triaged / ignored / resolved
- `POST /api/monitor/sync`：从远端 dashboard 补拉最近事件

Issue 的标题、描述、前后值、来源 URL 自动生成；事件保存 `issue_id`，
可从 `/monitor` 跳转 `/issues/{id}`。

## 边界

- OMS 不写监控数据库。
- OMS 不替代监控服务的每日采集/证据/重试。
- 监控服务不可用时，OMS 页面降级，但本地事件仍可查看和处理。
- 当前没有自动把所有事件转 Issue；由审核员/管理员在收件箱分诊。
