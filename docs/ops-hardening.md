# OMS 运维加固

> 对应 P0 安全/治理批次；当前 OMS 仍建议只在内网暴露。

## 1. Token 治理

新增 Token 默认 **30 天过期**（`TOKEN_DEFAULT_DAYS`，0=不过期），按 token 每分钟限流（`TOKEN_RATE_LIMIT` 默认 300，`TOKEN_RATE_WINDOW` 默认 60 秒），并在 `api_tokens` 记录 `last_used_at` / `last_used_ip`。

写操作审计新增 `audit_logs.token_name`：agent 用 Bearer Token 写入时，变更历史里能看到对应 Token 名称；Session 写入时为空。

```bash
# 签发 30 天只读 token
curl -b cookie.txt -X POST http://127.0.0.1:8020/api/tokens \
  -H 'Content-Type: application/json' \
  -d '{"name":"readonly-agent","scopes":["read"],"expires_in_days":30}'

# 立即吊销
curl -b cookie.txt -X DELETE http://127.0.0.1:8020/api/tokens/12
```

建议每个 agent 单独签发 Token，不共用 admin Token；生产 admin Token 只用于 KEOL 服务端桥接。

## 2. HTTPS / 反向代理（已落地）

当前机器没有 nginx/root 权限，使用系统自带的 `socat` 做 TLS 终止，OMS/KEOL 都只监听回环：

| 服务 | 对外 | 回环 |
|---|---|---|
| OMS | `https://192.168.120.3:8443` | `http://127.0.0.1:8020` |
| KEOL 加工台 | `https://192.168.120.3:8444` | `http://127.0.0.1:8321` |

systemd user units：`oms.service`、`oms-tls.service`、`keol-console.service`、`keol-tls.service`。

证书：`/home/oowpc/oms-tls/cert.pem`（OMS Internal CA 签发，含 SAN `192.168.120.3`、`ubuntu01`、`127.0.0.1`），
CA 公钥 `ca.crt`，服务器密钥 `key.pem` 权限 600。把 `ca.crt` 导入浏览器/系统信任库后即可消除警告；
未导入时仍会提示自签链。证书有效期 2 年，`/ops` 会显示剩余天数。

```bash
# 查看证书指纹
openssl x509 -in /home/oowpc/oms-tls/cert.pem -noout -fingerprint -sha256

# 服务状态
systemctl --user status oms.service oms-tls.service keol-console.service keol-tls.service
```

TLS 由 `socat` 终止，因此 OMS 侧看到的客户端 IP 是 `127.0.0.1`；Token 的 `last_used_ip` 不是真实客户端 IP。需要真实 IP/HTTP 头时再换 Nginx/Caddy。

## 3. 备份恢复演练

已有备份目录：`data/backups/`。演练命令：

```bash
# 1) 在线一致性备份
sqlite3 data/ontology.db ".backup /tmp/oms-restore-drill.db"

# 2) 校验完整性 + 关键计数
sqlite3 /tmp/oms-restore-drill.db "PRAGMA integrity_check;"
sqlite3 /tmp/oms-restore-drill.db \
  "SELECT COUNT(*) FROM concepts; SELECT COUNT(*) FROM operators;"

# 3) 用恢复库启动临时 OMS（不要覆盖生产）
DB_URL=sqlite:////tmp/oms-restore-drill.db \
  SECRET_KEY=restore-drill CSRF_ENABLED=0 \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18029
```

恢复验收：`PRAGMA integrity_check=ok`，概念/算子计数与备份时一致，`/login` 返回 200；不要直接拿恢复库覆盖在线库，先停服务并备份当前库。

2026-10-28 演练结果：`integrity_check=ok`，3170 概念 / 3390 算子 / 0 规则 / 0 个体，恢复实例 `/login` 返回 200。

自动化：systemd user `oms-backup.timer` 每日 03:20 执行 `scripts/backup_db.py --keep 30`，
备份后自动 `PRAGMA integrity_check`；`/ops` 显示最近备份年龄/份数/大小。恢复仍需按上面的临时库演练流程执行。

## 4. 当前剩余事项

- Nginx/Caddy TLS 需要系统权限和内部证书域名；本次未实际改 443。
- KEOL 8444 已落地 OMS 账号登录与 reviewer/admin 拉取推送限制；CA 公钥已提供，导入浏览器信任库即可消除证书警告。
- 版本回滚目前生成“回滚草稿”，不自动覆盖全局；全局回滚需单独审批/停机备份。

服务器证书 SHA256：`C2:DA:DD:97:92:F7:EA:A2:BB:76:AD:4A:E1:BD:3B:75:D0:71:81:B5:53:50:B0:AD:40:7F:A5:18:9B:C8:E7:F6`（2026-10-28，CA 签发）。
