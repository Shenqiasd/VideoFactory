# TODOS

延后的改进项目，按优先级排列。来源：2026-03-20 CEO + Eng Review。

---

## P2: YouTube 频道自动监控接入主流程

**What:** 将已有的 `src/source/youtube_monitor.py` 接入 orchestrator，实现频道新视频自动拉取和任务创建。

**Why:** 当前每个视频需要手动提交 URL。接入频道监控后可实现「设置后忘掉」的自动化——新视频发布时自动创建任务并开始处理。

**Pros:** 大幅减少手动操作，实现真正的自动化搬运流水线。

**Cons:** 需要设计重复检测（同一视频不重复创建任务）、调度策略（新视频优先级、批量到达时的处理顺序）、以及错误处理（频道不可用时的退避策略）。

**Context:** `youtube_monitor.py` 已实现频道 RSS/API 轮询和新视频检测。需要接入 `workers/orchestrator.py` 的任务创建流程，在 `run_loop` 中增加频道检查步骤。建议用 `task.source_url` 做幂等去重。

**Effort:** M (human: ~3 days / CC: ~30min)
**Depends on:** 无

---

## P3: API 限流中间件

**What:** 给 FastAPI 加请求限流，防止 API 滥用消耗翻译/TTS 额度。

**Why:** 当前任何人可以无限制提交任务，一个脚本就能耗尽所有 API 额度。虽然目前仅本机运行风险低，但一旦部署到服务器就需要防护。

**Pros:** 防止意外或恶意的 API 额度耗尽，保护付费服务。

**Cons:** 当前仅本机使用，优先级不高。

**Context:** FastAPI 生态有 `slowapi` 等现成限流库，基于 IP 的限流只需几行代码。建议对 `/api/production/submit-and-run` 和 `/api/tasks/create` 等创建型端点做限流（如 10 次/分钟），对读取端点不限流。

**Effort:** S (human: ~4h / CC: ~15min)
**Depends on:** 无

---

## P3: PostgreSQL 迁移

**What:** 将数据持久层从 SQLite 迁移到 PostgreSQL，支持多实例部署和真正的并发写入。

**Why:** SQLite 单文件数据库在多实例部署时无法共享，即使加了 RLock 也只能保护单进程内的并发。要实现水平扩展（多个 worker 实例），必须迁移到支持网络访问的数据库。

**Pros:** 彻底解决并发问题、支持多实例部署、支持更复杂的查询和索引。

**Cons:** 需要部署和维护 PostgreSQL 实例，增加运维复杂度。需要修改 Database 类的所有 SQL 语句（SQLite 语法差异），需要数据迁移脚本。

**Context:** 当前已用 `threading.RLock` 解决了单实例并发问题。迁移 PG 是规模化的前提。建议使用 `asyncpg` 或 `SQLAlchemy` 做 ORM 层，便于未来切换。Docker Compose 中已可以加 PG 服务。`database.py` 共 416 行，迁移工作量可控。

**Effort:** L (human: ~2 weeks / CC: ~2h)
**Depends on:** Docker 容器化先完成
