# 一对多私聊多租户设计

状态：**M1、M2 已实施（2026-11-24）**；M3 未实施
日期：2026-11-24
目标：一个 QQ 机器人账号，同时服务多个私聊用户；每个用户有独立的角色、提示词、音色、关系状态、记忆与聊天数据；不做群聊。

> M2 实施说明：提示词按用户存放于 `private/users/<id>/prompts/`，owner 原有提示词已迁移到
> `private/users/1/prompts/`；`users.bot_name` 支持每用户角色名；`voice_profiles` 按 user_id
> 隔离，运行时按用户缓存 TTS；管理台「提示词」「语音工作室」可切换用户，聊天页显示当前角色名。
> API key、聊天/视觉/ASR 模型与媒体目录仍全局共用；音色参考文件仍存放在共享的
> `private/voice/profiles/`（档案本身按用户隔离）。

---

## 1. 现状与差距

当前是**单用户单角色**架构：

- `Settings.owner_qq` 只接受一个 QQ 号；
- `messages / facts / tags / topics / state / stats / scheduled_thoughts / decisions / voice_profiles`
  全部没有用户维度；
- `ContextBuilder`、`state`、`Scheduler`、`prompts`、TTS 档案都是全局的；
- OneBot 适配器把回复固定发给 `OWNER_QQ`；
- `EventBus` 把每轮 outgoing 广播给所有订阅者（WebChat + OneBot），没有按用户路由；
- Web 管理台只有一个聊天测试台和一套全局配置。

要支持「多个私聊用户、每人独立角色和数据」，需要引入**多租户（multi-tenant）**模型。

### 非目标

- 不做群聊、群友印象、群文件等群生态功能；
- 不做公开注册的 SaaS；仍是自托管、本机管理；
- 第一阶段不做按用户计费/配额结算（预留字段）。

---

## 2. 总体架构

```
NapCat 反向 WS
   │  私聊事件 { user_id(QQ), message, ... }
   ▼
OneBotAdapter
   ├─ 按 platform_id 查 users 表
   ├─ pending/blocked → 忽略（可记录 last_contact_at）
   └─ active → engine.handle_user_message(user_id, ...)
                 │
                 ├─ ContextBuilder(user_id)
                 │    ├─ 该用户的最近消息 / 记忆 / 标签 / 话题 / 关系状态
                 │    ├─ 该用户的角色提示词 + bot_name
                 │    └─ 全局 LLM 模型与密钥
                 ├─ TTS(user_id) → 该用户当前音色
                 └─ TurnResult(user_id)
                       └─ EventBus
                            ├─ OneBot：只发给该 user_id 对应的 QQ
                            └─ WebChat：只推给当前选中的用户会话

Scheduler（全局循环）
   └─ 遍历 active 用户 → engine.handle_proactive(user_id, thought)
        └─ TurnResult(user_id) → 只发给该用户
```

核心原则：

1. **所有对话侧数据按 `user_id` 隔离**；
2. **模型/密钥/媒体理解等昂贵资源全局共享**；
3. **每个用户可配置独立的角色提示词、bot_name、音色档案**；
4. **管理台仍是本机单操作者**，但能看到并管理所有用户。

---

## 3. 数据模型

### 3.1 users

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL DEFAULT 'qq',
    platform_id TEXT NOT NULL,              -- QQ 号
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user',      -- owner / user
    status TEXT NOT NULL DEFAULT 'pending', -- pending / active / blocked
    bot_name TEXT NOT NULL DEFAULT '',      -- 该用户看到的角色名
    proactive_enabled INTEGER,              -- NULL=继承全局
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_contact_at REAL,
    UNIQUE(platform, platform_id)
);
CREATE INDEX idx_users_status ON users(status);
```

- 迁移时把现有 `OWNER_QQ` 写成 `role='owner', status='active'`，`bot_name` 继承当前设置；
- 未知 QQ 首次发消息时可插入 `pending` 行（只记录 QQ/时间，不存正文），管理台可批准；
- `status='pending'` 或 `blocked` 一律不回复。

### 3.2 对话数据加 user_id

| 表 | 改动 |
|---|---|
| messages | `user_id NOT NULL`；唯一约束改 `(user_id, source_id)`；索引 `(user_id, ts)` |
| facts | `user_id`；索引 `(user_id, active)` |
| tags | 主键改 `(user_id, tag)` |
| topics | 唯一约束改 `(user_id, name)` |
| state | 主键改 `(user_id, key)` |
| stats | 主键改 `(user_id, key)` |
| scheduled_thoughts | `user_id`；索引 `(user_id, status, due_at)` |
| decisions | `user_id`；管理台按用户查看 |
| voice_profiles | `user_id`；每个用户各自 active |

`messages_fts` 的 FTS 内容表保持不变；所有 FTS 查询 join messages 后加 `m.user_id = ?`。

### 3.3 角色提示词

推荐**按用户目录**保存覆盖文件，而不是塞进数据库：

```
private/users/<user_id>/
  ├─ prompts/
  │   ├─ persona.md
  │   ├─ principles.md
  │   ├─ tool_protocol.md
  │   ├─ proactive_cue.md
  │   ├─ fallback_error.md
  │   └─ history/
  ├─ media/{images,videos,frames,tmp,logs}
  ├─ voice/
  │   ├─ profiles/<uuid>/reference.mp3
  │   └─ out/tts/
  └─ archive/
```

- 代码里的默认提示词仍是兜底；
- 每个用户的覆盖文件独立，支持现有的保存/恢复默认/最近 5 个历史版本；
- `prompts.py` 的函数改为接收 `base_dir`（由 user_id 解析），不再固定 `settings.data_dir / prompts`；
- 现有 `private/prompts/*` 与 `private/voice/*`、`private/media/*` 迁移到 `private/users/1/`。

### 3.4 音色

- `voice_profiles` 加 `user_id`，每个用户有自己的一套档案与 active 标记；
- 参考音频存 `private/users/<user_id>/voice/profiles/`；
- TTS 输出存 `private/users/<user_id>/voice/out/tts/`；
- 音频路由 `GET /api/audio/{user_id}/{name}`，服务端校验请求的 WebChat 会话/管理员是否有权访问；
- 允许多个用户复用同一套全局音色（比如都用作者本人的克隆音），也允许每人上传自己的参考音频（需要本人同意）。

---

## 4. 运行时改造

### 4.1 Engine

```python
async def handle_user_message(
    self,
    user_id: int,
    text: str,
    *,
    channel: str = "text",
    source_id: str | None = None,
    media: list[dict] | None = None,
) -> TurnResult

async def handle_proactive(
    self, user_id: int, thought: dict | None = None
) -> TurnResult
```

- `TurnResult` 增加 `user_id: int`；
- 每次调用解析一次 `UserContext`：`bot_name`、提示词目录、音色档案、主动开关；
- `Engine.lock` 第一阶段保持全局串行；后续可换成 per-user 锁 + 全局并发上限；
- `ToolExecutor` 增加 `user_id`，所有 `remember / update_tag / open_topic / update_state / schedule_think` 写入都带用户；
- `context.build(user_id, ...)`；`state.tick/load_state/apply_deltas` 带 user_id；
- `db.log_decision(user_id, ...)`。

### 4.2 ContextBuilder

所有查询带 `user_id`：

- `recent_messages(user_id, limit)`
- `search_facts(user_id, query)`
- `search_messages(user_id, query)`
- `list_topics(user_id, ...)`
- `list_pending_thoughts(user_id, ...)`
- `get_behavior(user_id)` / `profile_summary(user_id)`

动态块里「关于他」的标签、记忆、活跃时段都只取当前用户的数据。

### 4.3 state / stats

- 函数签名统一加 `user_id`；
- `ensure_ready(db, user_id)` 为每个用户初始化；
- 新用户初始关系值：兴趣 80、亲密 30、信任 50、熟悉 35、依赖 15（与当前茉莉一致）；
- `DEFAULT_BEHAVIOR` 每个用户一份。

### 4.4 Scheduler

- 调度循环仍全局，但每次 `run_once` 遍历 `users(status='active')`：
  - 跳过该用户正在处理的轮次（per-user busy）；
  - `due_thoughts(user_id)` → `engine.handle_proactive(user_id, thought)`；
  - 每个用户的待触发、主动消息互不影响；
- 首次接触/重启时按用户 seed 一条待触发；
- 定时消息路由到该用户的 QQ。

### 4.5 OneBot 适配器

- `parse_private_event` 不再拒绝非 owner；改为：
  ```python
  user = await users.resolve(db, "qq", event["user_id"])
  if not user or user["status"] != "active":
      return  # 可记录 pending 的最后联系时间，不存正文
  ```
- `_handle_message` 调 `engine.handle_user_message(user["id"], ...)`；
- `_send_outgoing` 按 `item/result.user_id` 查 platform_id，发给对应 QQ，不再用 `OWNER_QQ`；
- 语音失败回退文字同样按用户；
- 同一 NapCat WS 连接服务所有用户，保持单连接。

### 4.6 EventBus / 路由

- `TurnResult.user_id` 是路由键；
- OneBot forward loop 只处理属于自己连接上用户的 outgoing；
- WebChat WebSocket 连接时选择 `user_id`（管理员测试台），只接收该用户的历史与结果；
- 任何 outgoing 不得广播给无关用户的 WS。

### 4.7 TTS

- `Runtime` 维护 `tts_cache: dict[user_id, TTSService]`；
- 用户音色变更时只刷新该用户的缓存；
- `Engine._run_turn` 通过 `runtime.tts_for(user_id)` 取服务；
- 预览音色仍管理员操作，按用户/档案调用。

---

## 5. 管理后台

新增「用户」页面：

- 用户列表：QQ、状态（pending/active/blocked）、角色名、当前音色、消息数、最近联系；
- 添加用户（QQ 号）、批准/禁用/删除；
- 每个用户的配置入口：
  - 角色名（bot_name）
  - 提示词编辑（persona/principles/tool_protocol/proactive_cue/fallback_error）
  - 音色档案（上传/试听/设为当前/归档）
  - 关系/情绪状态查看与重置
  - 聊天记录查看、归档、导出、清空
- 全局页面（模型与密钥、媒体、QQ、日志）保持不变；
- WebChat 页增加用户选择器（仅管理员本机使用），默认 owner。

安全：多用户后管理台能看所有人的私聊，建议在 M1 前加**管理员密码登录**（本地 session），至少不能只靠「仅本机」。

---

## 6. 安全与隐私

- 只服务白名单内 `active` 用户；pending/blocked 不回复；
- 所有查询强制带 `user_id`，禁止跨用户检索；写单元测试覆盖「用户 A 的上下文绝不包含用户 B 的消息/记忆/标签」；
- 媒体、音色、音频路由做归属校验；
- 删除用户时可选择删除其消息、记忆、媒体、音色档案；
- 管理操作写审计日志（管理员、用户、动作、时间），不记录消息正文；
- 日志中只出现 user_id/QQ 尾号，不打印消息内容；
- 每个用户可单独归档/重置，不影响其他人。

---

## 7. 迁移方案

1. **备份**：复制 `private/fatfish.db` 到 `private/backup/pre-multiuser-<时间>.db`；备份 `private/prompts`、`voice`、`media`；
2. 创建 `users`，插入 owner（现有 OWNER_QQ，bot_name=茉莉，status=active）；
3. 重建带 `(user_id)` 主键/唯一约束的表（state、stats、tags、topics、messages、voice_profiles），其余表 `ALTER TABLE ADD COLUMN user_id DEFAULT 1`；
4. 所有现有行 `user_id=1`；
5. 把 `private/prompts`、`private/voice`、`private/media` 移到 `private/users/1/`；
6. 更新 FTS 索引（`INSERT INTO messages_fts(messages_fts) VALUES('rebuild')`）；
7. 启动新版本，跑迁移校验：各表行数、owner 历史条数、音频路径可访问；
8. 回滚：停止服务，恢复备份 DB 与目录。

迁移脚本放 `scripts/migrate_multiuser.py`，默认预览，`--yes` 执行；迁移前强制备份。

---

## 8. 分阶段实施

### M1：隔离地基（同角色多用户）

- users 表 + owner 迁移 + pending 记录；
- 所有数据加 user_id；
- Engine/Context/state/ToolExecutor/Scheduler 按用户；
- OneBot 多用户路由、白名单；
- 管理台「用户」列表与批准/禁用；
- WebChat 用户选择；
- 所有用户暂时共用茉莉人设与音色；
- 测试：双用户隔离、路由、调度、迁移。

### M2：每用户独立角色与音色（已实施）

- 按用户提示词目录 + 管理台编辑（`private/users/<id>/prompts/`）；
- 每用户 bot_name、音色档案、TTS 缓存（`Runtime.tts_for`）；
- 每用户归档/重置（`archive_chat.py` 支持 user）；
- 管理台提示词页的用户选择与角色名；语音工作室的用户选择。

### M3：完善

- 管理员密码登录；
- 每用户主动开关、配额/限速；
- 每用户导出/删除、统计；
- 音色克隆的同意记录与来源标注；
- 可选：用户自助申请 + 管理员审批流程。

---

## 9. 测试计划

- **隔离**：两个用户各自写入消息/记忆/标签/关系，A 的 ContextBuilder 结果不包含 B 的任何内容；搜索/归档/删除互不影响；
- **路由**：模拟两个 QQ 事件，确认回复分别发到对应 QQ；WebChat 只收到所选用户；
- **调度**：两个用户的待触发互不串线；主动消息路由正确；
- **提示词/音色**：用户 A 的 persona 与音色不影响 B；
- **迁移**：用当前 DB 副本跑迁移脚本，逐表核对行数与 owner 数据；
- **安全**：pending/blocked 不回复；音频越权访问 403/404；
- **回归**：现有 26 个测试改造成带 user_id 的版本，全部通过。

---

## 10. 开放问题

1. 新用户默认角色：通用模板，还是复制一份茉莉？
2. 陌生人首次私聊：直接忽略，还是创建 pending 并在管理台提示？
3. 音色：所有用户共用作者本人克隆音，还是允许每用户独立音色（涉及同意与隐私）？
4. 是否在 M1 就加管理员密码？（多用户私聊数据都在一个后台，建议加）
5. 每用户主动消息默认开还是关？
6. 是否需要按用户的消息/费用配额？
7. WebChat 的定位：仅 owner 测试，还是管理员可模拟任意用户？

---

## 11. 推荐路径

先做 **M1**：多用户隔离、路由、白名单、后台用户管理，角色仍共用茉莉。验证无串数据后，再做 **M2** 的每人独立角色与音色。M1 是风险最高但收益最大的地基；M2 主要是配置层与 UI。
