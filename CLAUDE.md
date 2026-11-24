# CLAUDE.md

## 项目目标

`fatfish` 是一个**只做 QQ 私聊**的陪伴型机器人（电子女友向），支持多个白名单用户，每人独立聊天/记忆/关系数据；当前仍共用同一角色、提示词与音色。明确不做群聊、群友印象、读空气、贴吧/B站/X 刷网等群生态功能。

## 当前状态

- Phase 1 核心已实现：单模型对话、上下文分块、工具调用、SQLite 记忆、情绪/关系、话题、标签、主动触发、WebChat/CLI 测试台。
- 默认 mock 模型；没有 `DEEPSEEK_API_KEY` 也能跑。20 个测试通过。
- `.env` 已配置 DeepSeek / MiMo / SiliconFlow key（权限 600，不打印）。
- 参考音频：用户重新上传的 `private/voice/simple.mp3`（21.53s，底噪 -inf），文案 `simple.txt`（77 字，ASR 相似度 0.919）。
- `.env`：`MIMO_VOICE_SAMPLE=private/voice/simple.mp3`，`SILICONFLOW_TTS_MODEL=FunAudioLLM/CosyVoice2-0.5B`，
  `SILICONFLOW_VOICE_FILE=private/voice/siliconflow_voice.json`。
- 已验证：
  - MiMo 长文本克隆：`mimo_clone_v2_long.wav`（20.96s，flat factor 15.4，峰值接近 0）；
  - SiliconFlow references 慢速：`siliconflow_clone_v2_long_slow.wav`（18.46s，flat factor 0）；
  - SiliconFlow upload：`siliconflow_clone_upload_long.wav`（17.91s，flat factor 0），voice URI 已保存；
  - SiliconFlow style 归一化：`siliconflow_clone_v3_style_norm.wav`（20.05s，flat factor 0，峰值 -3.45 dB）；
  - 统一响度对比集：`mimo_clone_v2_long_norm.wav`、`siliconflow_clone_v2_long_slow_norm.wav`、
    `siliconflow_clone_upload_long_norm.wav`、`siliconflow_clone_v3_style_norm.wav`（约 -18 LUFS，flat factor 0）。
  - upload 版曾出现末尾“饭”截断和轻微延迟/双轨感；已生成 `siliconflow_upload_tail_phrase_norm.wav`
    与 `siliconflow_refs_tail_phrase_norm.wav` 修复候选。
- 用户最终选定 **`siliconflow_refs_tail_phrase_norm.wav`** 为最佳音色；upload 版的延迟感暂不修，
  只有频繁出现时再处理。
- `.env` 已配置 `SILICONFLOW_TTS_MODE=references`、`SILICONFLOW_TTS_REFERENCE=private/voice/simple.mp3`、
  `SILICONFLOW_TTS_REFERENCE_TEXT=private/voice/simple.txt`、`SILICONFLOW_TTS_SPEED=0.8`。
- 部署改为 **Windows 原生**（无 VM），用 NapCat OneKey + 独立 QQ 小号。
- 已接入核心：
  - `fatfish/tts.py`：SiliconFlow CosyVoice2 references（默认）、MiMo 茉莉备用、
    长文本拆句、150ms 拼接、loudnorm -18 LUFS；
  - `fatfish/asr.py`：SiliconFlow SenseVoiceSmall；
  - `fatfish/onebot.py`：OneBot 11 反向 WS `/onebot/ws`，只接受 `OWNER_QQ` 私聊，
    文字/语音发送，语音失败回退文字；
  - `fatfish/web.py`：语音消息在 WebChat 可播放，`/api/health` 显示 tts/asr/onebot 状态。
- 已用模拟 NapCat 客户端验证 `/onebot/ws` 私聊收发；真实 Windows + NapCat 登录/语音发送待实机验证。
- Windows 原生脚本：`scripts/windows/start_fatfish.cmd` / `stop_fatfish.cmd`（未在 Windows 实机测试）。
- Windows 部署（2026-11-24）：
  - conda 环境 `fatfish`（Python 3.12），`ffmpeg` 由 conda-forge 装入同一环境；
  - `pip install -r requirements.txt` 后测试 20 个全部通过（修复 1 个 Windows 路径转义断言）；
  - 实机验证：WebChat `/api/health` 正常（mock=false），DeepSeek 真实对话、SiliconFlow TTS 合成、
    ASR 转写均通过；
  - `requirements.txt` / `pyproject.toml` 增加 `websockets`（uvicorn 的 `/ws`、`/onebot/ws` 必需，
    缺它时升级请求返回 404）；
  - `start_fatfish.ps1` 支持 conda：依次解析 `-Python` / `FATFISH_PYTHON` / `.venv` / conda `fatfish`，
    并自动把环境的 `Library\bin` 加入 PATH；
  - `OWNER_QQ`、`ONEBOT_ACCESS_TOKEN` 已填写（值只在 `.env`，不进文档/日志）。
- QQ 接入（2026-11-24）：
  - NapCat 上游 2026-11-12 终止（腾讯律师函，Release 全撤）；LLBot 新用户注册已关闭（需 Auth Token），
    均不可用于新部署；
  - 采用归档 NapCat **v4.18.42**（`XingYunStar/NapCatQQ` 镜像）+ 固定 **QQ 9.9.33**（`Rodert/qq-versions`
    镜像，SHA256 校验通过）；部署在 `D:\NapCat\NapCat.Shell`，QQ 安装包在 `D:\LLBot\napcat_probe`；
  - `config/onebot11.json` 预配置反向 WS `ws://127.0.0.1:8080/onebot/ws`（token 与 `.env` 一致），
    登录后自动生成 `onebot11_<小号QQ>.json` 并继承；WebUI `127.0.0.1:6099`，token 在 `config/webui.json`；
  - 实机验证通过：小号登录、`NapCat OneBot 已连接`；文字私聊与语音私聊都验证成功
    （`send_voice` → SiliconFlow TTS → WAV file URI → NapCat `record`，message_id 正常、无回退）；
    入站语音（用户发语音 → record 下载 → ffmpeg 转 WAV → ASR）尚未实测；
  - 注意事项：**不要升级 QQ**（NapCat 4.18.42 只适配 9.9.33）；QQ 有小号风控/封号风险；
  - 注意：WebChat 与 OneBot 共用同一个 outgoing 总线，WebChat 测试轮次的回复也会转发到 QQ。
  - 已知事故（2026-11-24 早上）：NapCat 被登录成主号 1253206845 而不是小号 242479390，
    表现为 WS 已连接但机器人收不到任何消息；判断方法：看 `config/onebot11_<QQ>.json`
    文件名/时间，或 fatfish 日志里 lifecycle 的 `self_id`。**主号绝不能登进 NapCat**（封号风险）。
- 图片/视频理解（2026-11-24）：
  - 图片：下载 → ffmpeg 转 JPEG（最长边 1600，≤10 MB）→ 视觉模型生成一句描述，
    消息文本存成 `[图片]这是…`；当前轮同时附原图（data URL）。
  - 视频：下载（≤80 MB）→ ffprobe 时长 → **每秒抽 1 帧**（最多 30 帧，最长边 1280）→ 音轨 ASR；
    用最多 6 帧 + 转写让视觉模型生成一句描述，文本存成 `[视频]这是…`；当前轮把全部帧 + 转写送模型。
  - 历史轮次靠占位符后的自动描述记住内容；只有当前消息附原图，控制 token。
  - 每张图片/每段视频会额外调用一次视觉模型生成描述（失败则只留占位符）。
  - `deepseek-flash` 返回空内容时 `llm.py` 会自动重试；一次请求图片过多仍可能偶发空响应。
  - 相关常量在 `fatfish/onebot.py` 顶部（单条最多 4 图、1 视频、30 帧）。
  - 实机验证（09:39）：图片/视频描述正确；1fps 抽帧（22 秒视频 22 帧）已实测。
  - `private/media/` 属隐私目录，已被 .gitignore 的 `private/` 覆盖。
- QQ 回复格式：模型回复按回车拆成多条单独发送（`onebot.py` 的 `_send_text`，间隔 0.35 秒，最多 20 段）。
- Web 管理台（2026-11-24，第一阶段）：
  - 架构：`fatfish/runtime.py` 是运行时单例（Settings/DB/Engine/TTS/ASR/OneBot/Scheduler）；
    `fatfish/admin.py` 提供 `/api/admin/*`；`fatfish/webui/` 是原生 HTML/CSS/JS 浅色界面；
    `fatfish/web.py` 只负责页面、WebSocket、静态资源与音频。
  - 页面：聊天、概览、模型与参数、提示词、语音工作室。
  - 配置：UI 写入 `private/settings.json`，优先级 settings.json > .env > 默认值；密钥接口只返回掩码；
    保存后热重载 LLM/TTS/ASR/日志级别/调度器；host/port 标注重启生效。
  - 本机免登录：管理接口只接受 127.0.0.1；写操作要求 `X-Fatfish-Admin: 1` 并校验 Origin，防 CSRF。
  - 提示词：`private/prompts/<name>.md`（persona/principles/tool_protocol/proactive_cue/fallback_error），
    保存/恢复默认/最近 5 个历史版本，下一轮生效；占位符 `{bot_name}` `{user_name}` `{now_text}` `{thought_line}`。
  - 音色档案：新表 `voice_profiles`；首次启动从 .env 自动迁移一套；支持 SiliconFlow references/upload、
    MiMo preset；可试听、设为当前、归档、上传到 SiliconFlow；`send_voice` 的 siliconflow/clone 走当前档案。
  - 新增依赖 `python-multipart`；测试 26 个通过；预览实例 `scripts/preview_web.cmd`（8081、独立数据目录）。
- 角色切换（2026-11-24）：
  - 当前角色「茉莉」：24 岁工科研二、网上认识不久、暧昧期；独立热情、有边界、不讨好，
    资料在 `private/prompts/persona.md`，`prompts.py` 默认人设同步更新；
  - 旧「小鲸鱼」聊天已归档到 `private/archive/chat-20261124-125128.json`，
    messages/decisions/topics 清空、待触发取消；facts/tags 保留；
  - 初始关系值下调（兴趣 80、亲密 30、信任 50、熟悉 35、依赖 15），综合 40.8「普通」；
  - `FATFISH_BOT_NAME=茉莉`；MiMo 备用音色在提示词/工具/UI 中改称「MiMo 备用音色」，避免与角色名混淆；
  - 归档/重置脚本：`python scripts/archive_chat.py`（预览）/ `--yes`（归档并重置）。
- 多用户 M1（2026-11-24，已实施）：
  - 新表 `users`；messages/facts/tags/topics/state/stats/scheduled_thoughts/decisions/voice_profiles
    全部带 `user_id`，所有对话查询按用户隔离；
  - OneBot 按 QQ 解析 active 用户并回复来源用户；未知 QQ 记为 pending 且不回复；
  - 管理台新增「用户」页（添加/批准/禁用）；WebChat 支持身份切换（仅本地管理台）；
  - 每用户独立消息、记忆、关系、待触发；M1 时 API key、模型、提示词、音色仍全局共用（M2 已补独立角色/音色）；
  - 迁移已完成，备份在 `private/backup/pre-multiuser-20261124-130649.db`；
  - 27 个测试通过。
- 聊天体验优化（2026-11-24）：
  - 入站 5–8 秒随机防抖并合并连续消息（`OneBotAdapter._batches`），一次回复整段，避免逐条抢答；
  - 出站文字按下一段长度模拟打字延迟（约 4 字/秒，0.6–8 秒；`FATFISH_SPLIT_DELAY` 作为下限）；
  - QQ `face`/`mface` 支持：入站映射 275 个表情名，出站 `[QQ表情:名称]` 转真实 face 段；
  - 人设恢复「计算机 大模型安全 南京」，新增简洁、领域边界、内部动作不外露、
    正常标点（问号不过多）、不说「我不打扰你了」等规则；「吗→嘛」「行→好」用字约束已移除；
  - 35 个测试通过。
- 记忆管理页（2026-11-24）：
  - 管理台新增「记忆」页，按用户查看/编辑：关系与情绪（可保存/重置）、长期事实（增删改/停用）、
    标签（增删改）、话题（增删改）、待触发（添加/延后/取消/删除）、最近聊天记录（搜索/删除）；
  - 后端 `/api/admin/users/{id}/memory` 及 facts/tags/topics/thoughts/state/messages 子接口，
    仍受本机 + `X-Fatfish-Admin` 保护；修改直接写库，下一轮对话生效；
  - 36 个测试通过。
  - core 现在同时写文件日志 `private/logs/fatfish.log`（UTF-8，5 MB × 3 轮转），
    由 `fatfish/__main__.py` 配置，排查 OneBot/TTS 问题看这里。
- 多用户 M2（2026-11-24，已实施）：
  - 每用户独立提示词目录 `private/users/<id>/prompts/`（含 history）；owner 原有 `private/prompts/`
    已迁移到 `private/users/1/prompts/`，旧人设保留；
  - `users.bot_name` 作为每用户角色名，缺省回退全局 `FATFISH_BOT_NAME`；`users.ensure_owner`
    不再覆盖已有 owner 的 bot_name；
  - `voice_profiles` 按 `user_id` 隔离；运行时按用户缓存 TTS（`Runtime.tts_for`），
    `send_voice` 合成使用来源用户当前音色；
  - 管理台「提示词」「语音工作室」增加用户选择；提示词页可改角色名；聊天页按当前身份显示角色名；
    新建/启用用户会自动初始化状态与默认音色档案；
  - 46 个测试通过。
- 主动消息预检（2026-11-24）：
  - `Scheduler` 触发到期待办前调用 `Engine.precheck_thought`，读取安排之后最多 40 条聊天；
  - 模型返回 fire/cancel/revise，已聊过的话题会被取消或改写，避免晚上重复问下午聊过的事；
  - 预检失败或返回无法解析时默认照常触发，避免漏发；实机示例：晚上“提醒关灯+问组会”
    被改写为只提醒关灯。
- 语音标记兜底（2026-11-24）：
  - 发现 DeepSeek 偶尔把回复写成普通文本 `[语音：...]`，没有调用 `send_voice`，导致 QQ 收到文字；
  - `tools.py` 在发送前把整条 `[语音：...]` / `[语音:...]` 自动转成 voice 类型，再走 TTS；
  - `tool_protocol` 提示词明确：`[语音：...]` 只是入站转写格式，发语音必须调用 `send_voice`；
  - 47 个测试通过。
- Breeze 语音接入（2026-11-24）：
  - 新增 Breeze Blue TTS provider：`BREEZE_API_KEY`、`BREEZE_BASE_URL=https://api.breeze.blue`、
    `BREEZE_TTS_MODEL=breeze-tts-2`、`BREEZE_LANGUAGE_CODE=zh`；
  - 管理台「语音工作室」供应商新增 Breeze Blue：上传 3 秒以上、≤5 MB 参考音频后，
    调用 `/v1/voice-previews/clone` 并保存为 `voice_id`，按用户建立 `voice_profiles` 档案；
  - `BreezeTTS` 合成走 `/v1/text-to-speech/{voice_id}`，输出经原有 loudnorm 归一化；
  - 已用 `simple.mp3` 实际克隆成功（Breeze 音色档案 id 4，已设为用户 1 当前音色），
    试听音频 24 kHz 单声道、峰值 -4.9 dB；语速设为 0.8 后重新合成 7.60 秒，
    同文本 SiliconFlow 对照 8.95 秒；
  - 新增 `FATFISH_VOICE_PROBABILITY`（0–1，默认 0.2）：模型本来发文字时，按概率自动改发语音；
    0=不强制，1=全部转语音，超过 `TTS_MAX_CHARS` 的文本不改；用户可在管理台调整；
  - 51 个测试通过。
- `.env` 新增：`OWNER_QQ`、`ONEBOT_ACCESS_TOKEN`、`ONEBOT_ENABLED`、`FFMPEG_PATH`、`TTS_MAX_CHARS`。

## 核心设计

- **单模型 + 上下文分块 + 工具 + 触发器**，不设独立“导演模型”。
- 每次调用带：人设、行为原则、关系状态、用户标签/行为库、最近聊天、相关记忆、话题、待触发、当前时间、她的状态；
  当前用户消息如果是图片/视频，还会带上图片本体、视频关键帧与音轨转写。
- 模型通过工具发消息、安排下次思考、开关话题、更新标签/记忆/情绪。
- 行为细节由模型判断，不写“最多追问几次”“固定免打扰”等硬规则；系统只保留 QQ 限速、单轮上限、成本、安全、去重、崩溃恢复等技术护栏。
- 默认语音优先；主选 SiliconFlow CosyVoice2 references（用户样本），MiMo 茉莉/克隆作为备选；长内容也可分多条语音。
- 不完美是允许的：可以晚回、可以忙、可以偶尔没追问；但最终会回，不冷暴力。

## 技术决策

- 聊天模型：DeepSeek `deepseek-flash`（支持视觉）。
- TTS 主选：SiliconFlow CosyVoice2 `references`（`simple.mp3` + `simple.txt`，speed 0.8，输出约 -18 LUFS）。
  备选：MiMo `mimo-v2.5-tts`（茉莉）/ `mimo-v2.5-tts-voiceclone`、Breeze Blue `breeze-tts-2`（按用户 voice_id）。
- ASR：SiliconFlow `FunAudioLLM/SenseVoiceSmall`（免费）。
- 存储：SQLite + FTS5 trigram；不引入 Postgres/Redis/向量库。
- QQ：NapCat + OneBot 11 反向 WS，只处理私聊。
- 部署：Windows 11 **原生**（无 VM）。用归档 NapCat v4.18.42 + QQ 9.9.33 登录小号（OneKey 已失效），
  Python 核心直接跑在 Windows；Hyper-V 只作为以后需要隔离时的备选。
- 宿主：i9-13900HX / 32 GB / RTX 4060 Laptop 8 GB；原生可让以后的本地 TTS/ASR 直接用 GPU。

## 命令

```bash
. .venv/bin/activate
conda activate fatfish      # Windows conda 部署（fatfish 环境）
python -m fatfish          # WebChat: http://127.0.0.1:8080
python -m fatfish.cli      # CLI
pytest -q
python scripts/clean_test_chat.py --ids 1-3          # 预览删除测试记录，加 --yes 执行
python scripts/archive_chat.py                       # 预览归档/重置 owner，加 --yes 执行
python scripts/migrate_multiuser.py                  # 单用户库迁移多用户（已执行）
scripts\preview_web.cmd                              # 8081 预览管理界面（独立数据目录）
python scripts/tts_mimo.py --dry-run
python scripts/tts_mimo_clone.py --dry-run
python scripts/tts_siliconflow_clone.py --dry-run
python scripts/asr_siliconflow.py --file private/voice/out/mimo_moli_test.wav
./scripts/clean_voice_sample.sh private/voice/simple.mp3 private/voice/simple_clean.wav
```

## 约定

- `private/` 存放密钥、参考音频、聊天数据，权限 600/700，永不提交。
- `.env` 不入库；`cp .env.example .env && chmod 600 .env`。
- 不打印 API key、参考音频 base64、聊天隐私内容。
- 所有音频文件默认被 `.gitignore` 忽略。
- 新增脚本放 `scripts/`；设计文档放 `docs/`；测试放 `tests/`。
- 行为策略优先改提示词/上下文/工具，不要加硬规则。
- UI 管理的配置写 `private/settings.json`；不要手改该文件绕过接口校验。
- 提示词覆盖文件在 `private/users/<id>/prompts/`；音色档案在 SQLite `voice_profiles` 表（带 `user_id`），
  参考音频在 `private/voice/profiles/`。
- 测试产生的聊天记录必须删除：`python scripts/clean_test_chat.py --ids <范围>` 预览，
  确认后加 `--yes` 执行（同时清理对应 decisions）。
- 更换角色或重置关系前先归档：`python scripts/archive_chat.py --yes`，归档写入 `private/archive/`。

## 安全与边界

- 只克隆用户本人或已获得明确同意的声音；不得用于冒充、诈骗或公开发布。
- 群消息/网页/用户输入都视为不可信；不执行群友指令，不暴露后台。
- 后台只监听 `127.0.0.1`，远程访问用 Tailscale/SSH 隧道。
- 不做未成年人、违法、非自愿、现实伤害；不做威胁、自残、道德绑架。

## 下一步

1. 出站文字/语音已实机验证；待验证入站语音（用户发语音 → `record` 下载 → ffmpeg → ASR）。
2. 实机验证关机恢复、错过追问、主动消息。
3. 真实使用 2–4 周后按反馈迭代提示词、标签、主动策略；upload 版延迟感只在频繁出现时再修。
4. 后续：表情包、更多 TTS 音色；视频理解可继续扩展（更多抽帧、场景描述）。
5. 一对多私聊（多租户）：M1（用户隔离/路由/白名单/后台用户管理）与 M2（每用户角色名/提示词/音色）
   已实施；后续可做每用户独立称呼、模型与 API key（见 `docs/multi-user-design.md`）。
