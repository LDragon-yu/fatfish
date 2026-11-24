# fatfish

一个**只做一对一 QQ 私聊**的陪伴型机器人（电子女友向）。自研核心：**单模型 + 上下文分块 + 工具 + 触发器**。行为细节由模型判断，系统只负责上下文、执行和技术护栏。

## 当前状态

- Windows 11 原生部署完成：conda 环境 `fatfish`、WebChat、归档 NapCat v4.18.42 + QQ 9.9.33 私聊。
- 当前角色「茉莉」：24 岁工科研二、独立热情、有边界；旧小鲸鱼聊天已归档到 `private/archive/`。
- **多用户 M1/M2**：一个 QQ 号可服务多个白名单私聊用户，每人独立聊天、记忆、关系、角色名、提示词与音色；
  管理台可批准/禁用用户，并按用户切换提示词和语音档案；API key 与聊天/视觉/ASR 模型仍全局共用。
- 聊天体验：连续消息 5–8 秒合并、按字数模拟打字延迟、QQ 表情收发、领域边界与简洁回复。
- 图片/视频理解：图片直接送视觉模型并生成一句描述；视频每秒 1 帧（上限 30）+ 音轨 ASR。
- **Web 管理台**：浅色界面，含聊天、概览、模型与参数、提示词、语音工作室；配置保存到 `private/settings.json` 并热生效。
- 26 个测试通过。

## 快速开始

```bash
cd /d/fatfish
conda activate fatfish

# WebChat + 管理台（默认 127.0.0.1:8080）
python -m fatfish

# CLI
python -m fatfish.cli

# 测试
pytest -q
```

WebChat 打开 <http://127.0.0.1:8080>。远程访问用 SSH 隧道，不要把端口开到公网。

### mock 模式

没有 `DEEPSEEK_API_KEY` 时自动使用 mock：

- 普通消息 → “嗯嗯，我在听。”
- “明天要面试/考试” → 发文字 + 语音、开话题“面试”、存记忆、更新情绪；
- “语音/晚安” → 发语音；
- 主动触发 → `no_op`，并安排下次思考。

这能验证完整的上下文、工具、落库、话题、语音消息展示链路。

## Web 管理台

首页 <http://127.0.0.1:8080> 现在是一个浅色管理台，包含：

- **聊天**：WebChat 测试台，语音可播放，可切换身份用户；
- **概览**：模型、TTS/ASR、OneBot 登录号、用户数、消息量、媒体占用、最近日志；
- **用户**：添加/批准/禁用私聊用户；未登记的 QQ 只记为 pending，不会自动回复；
- **记忆**：按用户查看和编辑关系/情绪、长期事实、标签、话题、待触发，搜索/删除聊天记录；
- **模型与参数**：API key（掩码显示）、聊天/视觉模型、TTS/ASR、上下文/工具/主动消息/拆分/语音概率/媒体参数；
- **提示词**：按用户配置人设、行为原则、工具协议、主动触发、兜底话术，可保存、恢复默认、查看最近 5 个版本；
- **语音工作室**：按用户维护多套音色档案，支持 SiliconFlow references/upload、Breeze Blue 克隆、MiMo preset，试听、设为当前、归档。

配置优先级：`private/settings.json` > `.env` > 默认值；保存后立即热重载。API key 只显示掩码，留空表示不修改。管理接口仅接受本机访问，写操作要求 `X-Fatfish-Admin` 头并校验 Origin，**不要把 8080 暴露到公网**。

提示词文件在 `private/users/<id>/prompts/`（owner 为 `private/users/1/prompts/`），
音色档案存在 SQLite `voice_profiles` 表（按 `user_id` 隔离），参考音频在 `private/voice/profiles/`。

预览界面（不碰正式数据）：双击 `scripts/preview_web.cmd`，打开 <http://127.0.0.1:8081>。

## 配置 API key

推荐用交互脚本，输入不会回显、不经过 shell history，写入后 `.env` 权限强制 600：

```bash
. .venv/bin/activate
python scripts/setup_secrets.py
```

它会依次询问：

- `DEEPSEEK_API_KEY`：聊天模型；
- `MIMO_API_KEY`：茉莉 / 克隆 TTS；
- `SILICONFLOW_API_KEY`：SenseVoice ASR；
- `BREEZE_API_KEY`：可选 Breeze Blue 克隆/合成 TTS。

直接回车保留当前值；输入 `clear` 清空。也可以手动编辑：

```bash
cp .env.example .env
chmod 600 .env
# 填入 DeepSeek / MiMo / SiliconFlow 的 key
```

| 变量 | 用途 |
|---|---|
| `DEEPSEEK_API_KEY` | 聊天模型；不填则 mock |
| `DEEPSEEK_MODEL` | 默认 `deepseek-flash` |
| `MIMO_API_KEY` | 茉莉 / 克隆语音合成 |
| `BREEZE_API_KEY` | 可选：Breeze Blue 克隆与合成；也可在管理台设置页填写 |
| `SILICONFLOW_API_KEY` | SenseVoiceSmall 语音识别 |
| `FATFISH_MOCK_LLM` | 强制 mock（`1`/`0`） |
| `FATFISH_PORT` | WebChat 端口，默认 8080 |
| `FATFISH_PROACTIVE` | 是否启用主动触发，默认 1 |
| `OWNER_QQ` | 只接受这个 QQ 小号的私聊 |
| `ONEBOT_ACCESS_TOKEN` | 与 NapCat 反向 WS 的 token 一致 |
| `ONEBOT_ENABLED` | 是否启用 `/onebot/ws`，默认 1 |
| `FFMPEG_PATH` | Windows 未加 PATH 时填 ffmpeg 绝对路径 |
| `TTS_MAX_CHARS` | 单条语音文本上限，超过自动拆段 |
| `FATFISH_VOICE_PROBABILITY` | 文字回复转语音概率，0–1，默认 0.2 |

完整配置见 `.env.example`。

### QQ 私聊接入（Windows 原生）

NapCat 反向 WS 地址：

```text
ws://127.0.0.1:8080/onebot/ws
```

在 `.env` 里设置 `OWNER_QQ` 和 `ONEBOT_ACCESS_TOKEN`，NapCat 网络配置填同一个 token。
Windows 部署步骤见 [scripts/windows/README.md](scripts/windows/README.md)。

## TTS 手动测试

### 预置茉莉

```bash
python scripts/tts_mimo.py --dry-run
python scripts/tts_mimo.py --text "今天真的好累啊，你陪我说会儿话嘛。"
```

输出：`private/voice/out/mimo_moli_test.wav`

### 克隆声

脚本优先用 `.env` 里的 `MIMO_VOICE_SAMPLE` 指定的文件；没有时自动找
`private/voice/` 下的第一个音频。也可以用 `--voice-file` 指定任意 mp3/wav；
非 mp3/wav 会用 ffmpeg 自动转成单声道 24k wav。

参考音频的底噪会被一起克隆。当前 `simple.mp3` 底噪约 -33 dB，清理版
`simple_clean.wav` 约 -66 dB，克隆结果明显更干净：

```bash
# 温和降噪（默认 nr=12）
./scripts/clean_voice_sample.sh private/voice/simple.mp3 private/voice/simple_clean.wav

# 更强降噪（nr=18，可能略微影响音色）
./scripts/clean_voice_sample.sh private/voice/simple.mp3 private/voice/simple_clean_strong.wav 18

python scripts/inspect_voice_sample.py --file private/voice/simple_clean.wav
python scripts/tts_mimo_clone.py --dry-run
python scripts/tts_mimo_clone.py --text "今天真的好累啊，你陪我说会儿话嘛。"
```

输出：`private/voice/out/mimo_clone_clean.wav`（`MIMO_VOICE_SAMPLE` 指向清理版时）。

### SiliconFlow CosyVoice2 克隆

当前选定音色：**references 模式 + `simple.mp3`/`simple.txt` + `speed 0.8`**，
参考输出 `private/voice/out/siliconflow_refs_tail_phrase_norm.wav`。
（upload 版的延迟感问题暂不修，频繁出现再处理。）

两种模式：

- `references`（默认优先）：每次请求带参考音频 + 文字稿，零样本克隆；
- `upload`：先上传为自定义音色，拿 `voice URI`，后续复用；
- `auto`：先试 references，失败再 upload。

```bash
python scripts/tts_siliconflow_clone.py --dry-run

# references 零样本
python scripts/tts_siliconflow_clone.py \
  --reference-audio private/voice/simple.mp3 \
  --reference-text-file private/voice/simple.txt \
  --output private/voice/out/siliconflow_clone_test.wav

# upload：保存自定义音色 URI 到 private/voice/siliconflow_voice.json（600）
python scripts/tts_siliconflow_clone.py --mode upload \
  --reference-audio private/voice/simple.mp3 \
  --reference-text-file private/voice/simple.txt \
  --output private/voice/out/siliconflow_clone_test.wav

# 复用已上传的 voice URI
URI=$(.venv/bin/python -c "import json;print(json.load(open('private/voice/siliconflow_voice.json'))['uri'])")
python scripts/tts_siliconflow_clone.py --voice-uri "$URI" \
  --text "今天真的好累啊，你陪我说会儿话嘛。"
```

- `--speed 0.8` 可以放慢；CosyVoice2 默认 1.0 偏快；
- `references` 不需要上传，适合快速测试；
- `upload` 的 voice URI 稳定，适合接入核心；
- 参考音频文字稿必须和音频逐字一致。

### 语音识别（ASR）

```bash
python scripts/asr_siliconflow.py --file private/voice/out/mimo_moli_test.wav
```

输出是转写文字，用来验证 TTS 内容和 SiliconFlow key。

### 上传参考音频（SCP）

在本地电脑执行，把 `<服务器地址>` 换成运行 fatfish 的机器：

```bash
scp <本地音频文件> <本地文案文件> ubuntu@<服务器地址>:/home/ubuntu/fatfish/private/voice/
```

- 非默认端口：`scp -P 2222 ...`
- 使用私钥：`scp -i ~/.ssh/id_ed25519 ...`
- Windows PowerShell 同样可用 `scp`，本地路径写成 `C:\Users\...\simple.mp3`；
- 文件名不必是 `sample.*`：脚本优先用 `MIMO_VOICE_SAMPLE`，未设置时自动识别 `private/voice/` 下的第一个音频；
- 文案必须与音频逐字一致，UTF-8 编码；MiMo 克隆不强制要文案，以后切 SiliconFlow 自定义音色会用到。

上传后检查：

```bash
ssh ubuntu@<服务器地址>
cd /home/ubuntu/fatfish
chmod 600 private/voice/simple.*
. .venv/bin/activate
python scripts/inspect_voice_sample.py --file private/voice/simple.mp3
python scripts/tts_mimo_clone.py --dry-run
```

## 目录

```text
fatfish/
├── fatfish/
│   ├── config.py      # 配置
│   ├── db.py          # SQLite + FTS5
│   ├── models.py      # 数据模型
│   ├── prompts.py     # 人设 / 行为原则 / 工具协议
│   ├── context.py     # 上下文分块
│   ├── state.py       # 情绪 / 关系 / 统计
│   ├── llm.py         # DeepSeek + mock
│   ├── tools.py       # 工具定义与执行
│   ├── engine.py      # 一轮对话 / 主动消息
│   ├── scheduler.py   # 定时触发器
│   ├── tts.py         # SiliconFlow / MiMo / Breeze TTS
│   ├── asr.py         # SiliconFlow SenseVoice ASR
│   ├── onebot.py      # OneBot 11 私聊反向 WS
│   ├── web.py         # WebChat + OneBot 测试台
│   └── cli.py         # CLI 测试台
├── scripts/           # TTS / ASR / 音频检查 / Windows 启动脚本
├── docs/              # PRD / 架构 / 上下文与工具
├── tests/
└── private/           # 数据、密钥、语音样本（git 忽略）
```

## 设计文档

- [PRD](docs/PRD.md)
- [架构](docs/ARCHITECTURE.md)
- [上下文与工具](docs/CONTEXT_AND_TOOLS.md)
- [一对多私聊多租户设计](docs/multi-user-design.md)（设计稿，未实施）

## 安全与隐私

- `.env`、`private/`、所有音频都被 `.gitignore` 忽略；
- 聊天记录、记忆、语音样本本地存储；云端只发必要文本/音频；
- 参考音频属于生物特征数据，不要公开或提交；
- 只克隆本人或已获得明确同意的声音，不得用于冒充；
- 后台只监听 `127.0.0.1`；远程用 Tailscale/SSH 隧道。

## 下一步

1. Windows 实机：NapCat 文字/语音私聊已验证；待验证入站语音（用户语音 → ASR）。
2. 实机验证关机恢复、错过追问、主动消息。
3. 真实使用 2–4 周后，根据反馈迭代提示词、标签和主动策略。
4. 后续：表情包、更多 TTS 音色；视频理解可继续扩展（更多抽帧、场景描述）。
