# Wishclaim · 礼物愿望认领

发布 → 认领锁定（互斥+TTL）→ 核销/释放。

| 服务 | 端口 |
| --- | --- |
| 前端 | 5200 |
| API | 10200 |

```bash
docker compose up --build
pytest backend/app/tests
```

0-1：`wish_comment` / `secret_santa` / `price_cap`。

## 并发胜出钉

同一 open 愿望的并发认领恰好一人写入 claimer，败方得到稳定 409 错误码
（`locked`）且不改动锁。读改写收束在 `claim_lock.claim_critical_section`
（BEGIN IMMEDIATE + 条件 UPDATE 复核门禁谓词）；竞态门禁
（`engines/race_gate`）、写锁（`engines/claim_lock`）、投影
（`modules/claim_pin`）分模块。墙卡 / 详情 / 我的认领三路渲染同一
`pin`（前端 `PinBadge`）。测例见 `app/tests/test_claim_race.py`：
顺序定胜、钩子定胜、栅栏混战各跑一次不变式。
