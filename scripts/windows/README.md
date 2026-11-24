# Windows 原生部署

不虚拟化，直接跑：

```text
Windows 11
├─ NapCat OneKey（内置独立 QQ，登录小号）
├─ Python 核心（本仓库）
├─ ffmpeg
└─ start_fatfish.cmd / stop_fatfish.cmd
```

你本机不装 QQ 大号，所以不需要虚拟机隔离。

## 1. 安装依赖

- Python 3.12（安装时勾选 Add to PATH）。
- ffmpeg：`winget install Gyan.FFmpeg`，或下载后把 `bin` 加进 PATH；
  如果不在 PATH，在 `.env` 里设置 `FFMPEG_PATH=C:\ffmpeg\bin\ffmpeg.exe`。
- 项目放到例如 `C:\fatfish`。

```powershell
cd C:\fatfish
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

也可以不用 venv，改用 conda（推荐装 ffmpeg 时一起用 conda）：

```powershell
conda create -n fatfish -c conda-forge --override-channels python=3.12 pip -y
conda activate fatfish
pip install -r requirements.txt
conda install -c conda-forge ffmpeg -y
```

`start_fatfish.ps1` 依次查找 `-Python` 参数、`FATFISH_PYTHON` 环境变量、`.venv`、
conda 名为 `fatfish` 的环境；用 conda 环境时会自动把环境的 `Library\bin` 加进 PATH，
conda 装的 ffmpeg 无需再改 `FFMPEG_PATH`。

## 2. 配置

```powershell
.\.venv\Scripts\python scripts\setup_secrets.py
```

然后编辑 `.env`，补上：

```text
OWNER_QQ=你的QQ小号
ONEBOT_ACCESS_TOKEN=随便一串本地 token（NapCat 里填同一个）
FFMPEG_PATH=ffmpeg
```

`SILICONFLOW_*` 已配置为 references 模式，使用 `private/voice/simple.mp3` / `simple.txt`。
把这两个文件从服务器复制到 Windows 的 `C:\fatfish\private\voice\`，**不要改名**。
`.env` 可以重新跑 `setup_secrets.py` 生成，也可以单独从服务器复制。

## 3. 安装并配置 QQ / NapCat（2026-11 之后）

NapCat 上游已于 2026-11-12 终止，官方 Release 全部撤下，OneKey 一键包里的下载地址已失效。
当前可用方案：**归档版 NapCat v4.18.42 + 固定 QQ 9.9.33**（完全本地运行，无在线授权）。

本机已完成的布局：

```text
D:\LLBot\napcat_probe\QQ_9.9.33_260813_x64_01.exe   # QQ 安装包（SHA256 已校验）
D:\NapCat\NapCat.Shell\                               # 归档 NapCat
  ├─ config\onebot11.json           # 反向 WS 模板（含 token）
  ├─ config\onebot11_<小号QQ>.json  # 登录后自动生成并继承
  └─ config\webui.json              # WebUI 127.0.0.1:6099，token 见文件
```

安装步骤：

1. 安装 `QQ_9.9.33_260813_x64_01.exe`。**不要升级 QQ**，NapCat 4.18.42 适配该版本。
2. 双击 `D:\NapCat\NapCat.Shell\launcher.bat`，会请求管理员权限并注入 QQ。
3. QQ 登录界面用小号扫码；NapCat 控制台会显示 WebUI 地址。
4. 反向 WS 已在 `config\onebot11.json` 预配置为 `ws://127.0.0.1:8080/onebot/ws`，
   token 与 `.env` 的 `ONEBOT_ACCESS_TOKEN` 一致，登录后自动连接。

注意：LLBot（原 LLOneBot）目前已关闭新用户注册（启动需 Auth Token），不适合新部署；
SnowCat 是新起的社区续作，可作为后续备选。

NapCat 的 WebUI 和反向 WS 都只在本机，不要暴露公网。

## 4. 启动

```powershell
cd C:\fatfish
.\scripts\windows\start_fatfish.cmd -NapCatDir "D:\NapCat\NapCat.Shell"
```

如果 NapCat/QQ 已经在运行（比如你已经手动启动并扫码），加 `-NoNapCat` 只启动 core，
避免重复注入 QQ：

```powershell
.\scripts\windows\start_fatfish.cmd -NoNapCat
```

也可以先设置环境变量，之后直接双击 `start_fatfish.cmd`：

```powershell
setx FATFISH_NAPCAT_DIR "D:\NapCat\NapCat.Shell"
```

脚本会：

1. 启动 NapCat（新窗口）；
2. 启动 `python -m fatfish`（新窗口，日志在这里）；
3. 等 WebChat 起来后打开 `http://127.0.0.1:8080`。

停止：双击 `stop_fatfish.cmd`，或关闭两个窗口。

## 5. 验收

1. 浏览器打开 `http://127.0.0.1:8080`，先确认 WebChat 能聊天、语音能播放；
2. QQ 小号给机器人发一条私聊文字；
3. 看 core 窗口是否出现 `NapCat OneBot 已连接`；
4. 机器人回复文字；再试语音；
5. 如果文字回复了但语音没声音，看 core 窗口的 TTS 报错。

## 6. 长期运行

- 插电，关闭睡眠/休眠，合盖设为“不采取任何操作”；
- 可选：任务计划程序在登录时运行 `start_fatfish.cmd`；
- 锁 QQ/NapCat 版本，关闭 QQ 自动更新；保留安装包；
- 定期备份 `private/`（聊天、记忆、语音样本）和 NapCat 配置；
- 用 QQ 小号；第三方协议端有风控和 ToS 风险。

## 常见问题

- **OneBot 连接被拒**：检查 `OWNER_QQ`、`ONEBOT_ACCESS_TOKEN` 是否和 NapCat 一致，端口 8080 是否被占用。
- **语音发送失败**：core 会自动回退文字；看 core 窗口里 OneBot 的返回。不同 NapCat 版本对 `record` 格式支持不同，必要时我们再转 SILK。
- **没有声音**：检查 `SILICONFLOW_API_KEY`、`private/voice/simple.mp3`、`simple.txt` 和 ffmpeg。
- **QQ 掉线**：在 NapCat 窗口重新扫码；不要和日常大号共用。
