# fatfish 架构

## 1. 总览

```text
                 ┌──────────────────────────────┐
触发器 ─────────▶│ 上下文构建器                  │
- 用户消息       │ 人设 / 行为原则 / 关系 / 标签 │
- schedule_think │ 最近聊天 / 记忆 / 话题 / 时间 │
- 话题到期       └──────────────┬───────────────┘
                                ▼
                       ┌─────────────────┐
                       │ 主模型（一次调用）│
                       │ deepseek-flash   │
                       └────────┬────────┘
                                ▼
                   工具调用（send_* / schedule / topic / tag ...）
                                ▼
                       ┌─────────────────┐
                       │ 执行器           │
                       │ 发送 / 延迟 / 落库│
                       │ 定时 / 状态更新   │
                       └────────┬────────┘
                                ▼
              WebChat / NapCat OneBot 11 / CLI
```

没有独立“导演模型”。模型在每次调用中同时决定“说什么”和“要不要说”。

## 2. 目录

```text
fatfish/
├── fatfish/
│   ├── config.py       # 配置
│   ├── db.py           # SQLite schema / 访问
│   ├── models.py       # Outgoing / LLMResponse / TurnResult
│   ├── prompts.py      # 人设、行为原则、工具协议
│   ├── context.py      # 上下文分块与历史
│   ├── state.py        # 情绪/关系/统计
│   ├── llm.py          # DeepSeek 客户端 + mock
│   ├── tools.py        # 工具定义与执行
│   ├── engine.py       # 一轮对话/主动消息
│   ├── scheduler.py    # 定时触发器
│   ├── web.py          # WebChat 测试台
│   └── cli.py          # CLI 测试台
├── docs/
├── scripts/            # TTS / ASR / 启动脚本
├── tests/
└── private/            # 数据、密钥、语音样本（git 忽略）
```

## 3. 一轮对话

### 3.1 用户消息

```text
用户消息
→ 落库（source_id 去重）
→ 状态 tick（情绪衰减、统计）
→ 上下文构建
→ 主模型（tools）
→ 执行工具（发消息、更新话题/标签/状态、安排下次）
→ 工具结果回填，模型可继续调用
→ 落库 + 发布给 WebChat/QQ
```

### 3.2 主动消息

```text
scheduled_thought 到期
→ 上下文构建（trigger=proactive，附带 reason/topic）
→ 主模型
→ 要么 send_*，要么 no_op + schedule_think
→ 标记 thought 完成
```

## 4. 上下文分块

稳定块（尽量不变，吃缓存）：

1. `身份/人设`
2. `行为原则`
3. `工具协议`
4. 工具 JSON schema（由 API 传入）

动态块（每次变化）：

1. `关系状态`：亲密/信任/熟悉/兴趣/依赖/不满
2. `用户画像`：标签、行为统计、最近状态
3. `最近聊天`：最近 N 条，合并连续同角色消息
4. `长期记忆`：FTS5 检索到的相关事实/原文
5. `未完成话题`：open/waiting
6. `待触发念头`：已安排的 schedule_think / 话题检查
7. `现在`：时间、上次互动、活跃时段、她的情绪/精力、触发器

详细规则见 `docs/CONTEXT_AND_TOOLS.md`。

## 5. 数据模型

```sql
messages(id, ts, role, text, channel, source_id UNIQUE, meta)
messages_fts(text)                       -- FTS5 trigram

facts(id, ts, kind, topic, content, importance, source_message_id, active)
facts_fts(content)                       -- FTS5 trigram

topics(id, name UNIQUE, status, importance, last_mentioned, next_check_at, meta)

tags(id, tag UNIQUE, value, confidence, evidence, source, updated_at)

state(key, value, updated_at)            -- mood / relationship / last_tick
stats(key, value, updated_at)            -- active_hours / counts / latencies

scheduled_thoughts(id, due_at, reason, topic, status, created_at, updated_at)
decisions(id, ts, trigger, summary, decision, model)
```

- `messages.role`: user / assistant。
- `messages.channel`: text / voice / image。
- `topics.status`: open / waiting / closed。
- `scheduled_thoughts.status`: pending / processing / done / skipped。
- `state.mood` / `state.relationship`: JSON。
- 所有数据本地 SQLite；音频不长期保存。

## 6. 工具

| 工具 | 作用 |
|---|---|
| `send_text` | 发一条文字 |
| `send_voice` | 发一条语音（默认 SiliconFlow 克隆音色；moli 为茉莉） |
| `send_burst` | 一次发多条，带各自延迟 |
| `schedule_think` | 安排下次主动思考 |
| `open_topic` | 开一个话题，可安排到期检查 |
| `update_topic` | 改话题状态/下次检查 |
| `close_topic` | 结束话题，并取消相关待触发 |
| `update_tag` | 记录用户习惯/偏好 |
| `remember` | 存事实/偏好/事件/印象 |
| `forget` | 删除相关记忆/话题 |
| `update_state` | 小幅更新情绪/关系 |
| `no_op` | 现在不说话，可安排下次 |

执行器负责：参数校验、延迟、落库、去重、顺序、异常回填。

## 7. 调度器

- 每 30 秒检查一次 `scheduled_thoughts`。
- 只处理 `pending` 且 `due_at <= now` 的记录。
- 处理前标记 `processing`；成功后 `done`；失败恢复 `pending`。
- 启动时把遗留的 `processing` 恢复为 `pending`。
- 没有待触发记录时，可在有历史消息的情况下播种一条 30 分钟后的“看看要不要主动找他”。
- 用户正在对话时，模型会看到最近消息，可自行 `no_op`；系统不强制静默。

## 8. 模型与提供商

| 用途 | 选型 | 说明 |
|---|---|---|
| 聊天 / 判断 / JSON | DeepSeek `deepseek-flash` | 1M 上下文、支持视觉、缓存便宜 |
| TTS 默认 | SiliconFlow CosyVoice2 `references` | `simple.mp3` + `simple.txt`，speed 0.8 |
| TTS 处理 | 长文本拆句 + 150ms 拼接 + loudnorm | 输出约 -18 LUFS，防截断/削顶 |
| TTS 备用 | MiMo `mimo-v2.5-tts` 茉莉 | 限时免费 |
| ASR | SiliconFlow SenseVoiceSmall | 免费，第一版 |
| 存储 | SQLite + FTS5 | 不引入 Postgres/Redis/向量库 |

## 9. 成本控制

- 稳定前缀（人设/原则/工具）放前面，利用 DeepSeek 自动缓存。
- 最近聊天 + 摘要 + 检索记忆，不塞全量历史。
- 后台判断与主回复共用 `deepseek-flash`；复杂任务再考虑 `pro-`。
- 每日 token/TTS 预算；超预算暂停主动消息，保留正常回复。
- 语音按需生成；默认茉莉限时免费；克隆/付费 TTS 设每日上限。

## 10. 部署

### 开发

```bash
. .venv/bin/activate
python -m fatfish          # WebChat: http://127.0.0.1:8080
python -m fatfish.cli      # CLI
pytest -q
```

无 `DEEPSEEK_API_KEY` 时自动 mock。

### 生产（后续）

- Windows 11 **原生**（无 VM）。
- NapCat OneKey（内置独立 QQ）+ 独立 QQ 小号；Python 核心直接运行。
- `start_fatfish.ps1`：启动 NapCat + 核心 + WebChat、等待 OneBot 连接、提示 QQ 扫码。
- 用 Windows 任务计划/启动脚本托管；数据备份到外部盘。
- Hyper-V 仅作为以后需要隔离日常 QQ 时的备选。

## 11. 安全

- `.env`、`private/` 600/700；不提交。
- 后台只监听 127.0.0.1；远程用 Tailscale/SSH 隧道。
- QQ 只用小号；不执行群友/网页指令。
- 语音样本只用于本人授权的音色克隆；不公开、不冒充。
- 模型输出经过工具白名单；不执行任意代码/命令。

## 12. 语音与 QQ 接入

- `fatfish/tts.py`：SiliconFlow `references` 默认音色；长文本按标点拆段、150ms 静音拼接、
  ffmpeg `loudnorm=I=-18:TP=-2`；MiMo 茉莉作为备用。
- `fatfish/asr.py`：SiliconFlow SenseVoiceSmall；QQ 语音先由 ffmpeg 转 16k 单声道 wav。
- `fatfish/onebot.py`：OneBot 11 反向 WS `/onebot/ws`，只接受 `OWNER_QQ` 私聊；
  `get_record` 取语音；发送 `record` 时用 WAV 文件 URI，失败自动回退文字。
- `fatfish/engine.py`：模型发语音后先合成音频、写回消息 meta，再发布给 WebChat/OneBot；
  TTS 失败时保留文字稿，适配器回退文字。
- `scripts/windows/`：NapCat + 核心 + WebChat 的一键启动/停止脚本。
- 真实 NapCat 若拒绝 WAV `record`，下一步补 SILK 编码；当前先保证文字可用。
