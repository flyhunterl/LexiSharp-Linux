# LexiSharp-linux

LexiSharp-linux 是一款运行于 Linux 桌面的轻量级语音输入工具，围绕“录音 → 识别 → 粘贴”这一流程展开，实现最小成本的语音转文字体验。
项目灵感来源于安卓版LexiSharp。安卓版版本已经是一个非常完善且功能强大的版本！地址：https://github.com/BryceWG/LexiSharp-Keyboard


### 目前项目处于初级开发阶段，暂不提供执行程序.可以通过一键脚本或手动启动方式使用

<img width="479" height="547" alt="image" src="https://github.com/user-attachments/assets/26d7d875-26da-4004-8270-305192cf16f3" />


## 功能概览
- **一键录音**：主界面仅保留“开始/停止录音”按钮，同时可选启用悬浮按钮（置顶、可拖拽）。
- **极速转写**：集成火山引擎大模型录音文件极速版识别 API，并可选择 Soniox 等渠道。
- **结果自动复制/粘贴**：识别文本会自动写入剪贴板；启用自动粘贴后会模拟 `Ctrl+V` 将内容送入目标窗口（默认关闭，Wayland 下需要安装 `wl-clipboard`）。
- **配置简明**：首次启动生成 `~/.lexisharp-linux/config.json` 模板，填入密钥即可使用。

## 环境准备
### 可使用一键脚本配置（推荐）或者手动配置
#### 使用一键脚本

添加权限
```bash
chmod +x setup_lexisharp.sh
```

运行脚本
```bash
./setup_lexisharp.sh
```

####  手动安装
以 Arch/Manjaro 为例：
```bash
sudo pacman -S python python-pip alsa-utils xdotool wl-clipboard xclip tk
```
Debian/Ubuntu：
```bash
sudo apt update && sudo apt install python3 python3-venv python3-pip alsa-utils xdotool wl-clipboard xclip python3-tk
```
Fedora/RHEL：
```bash
sudo dnf install python3 python3-pip python3-virtualenv alsa-utils xdotool wl-clipboard xclip python3-tkinter
```

虚拟环境（推荐）
```bash
python -m venv ~/.venvs/lexisharp
source ~/.venvs/lexisharp/bin/activate
```

Python 依赖
```bash
pip install -r requirements.txt
```

> 若在 Wayland 环境启用 `auto_paste`，请确保已安装 `wl-clipboard`（Arch使用 `paru -S wl-clipboard`安装））。程序会通过 `python-evdev`（已在 `requirements.txt` 中）创建虚拟键盘自动发送 `Ctrl+V`。 


## 识别渠道配置

### 通过设置界面设置
当前版本已支持可视化设置，通过设置按钮进入设置，如需更多的设置可以参考下面手动配置。

### 手动配置
通过 `~/.lexisharp-linux/config.json` 中的 `channel` 字段选择识别服务：

- `volcengine`（默认）：火山引擎大模型录音文件极速版 API。
- `soniox`：Soniox Speech-to-Text Async API（参考 https://soniox.com/docs/stt/get-started）。
- `qwen`：通义千问录音文件识别（Qwen3-ASR/Qwen-Audio-ASR，参考 https://help.aliyun.com/zh/model-studio/qwen-speech-recognition）。

首次运行 `python lexisharp.py` 会生成配置模板，核心字段示例如下（Soniox 相关字段在使用火山引擎时可保持默认）：

```json
{
  "api_url": "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",
  "app_key": "你的AppID",
  "access_key": "你的AccessKey",
  "resource_id": "volc.bigasr.auc_turbo",
  "model_name": "bigmodel",
  "channel": "volcengine",
  "soniox_api_base": "https://api.soniox.com",
  "soniox_api_key": "你的SonioxAPIKey",
  "soniox_model": "stt-async-preview",
  "soniox_language_hints": [],
  "soniox_enable_speaker_diarization": false,
  "soniox_enable_language_identification": false,
  "soniox_context": "",
  "soniox_poll_interval_s": 1.0,
  "soniox_poll_timeout_s": 120.0,
  "auto_paste": true,
  "paste_delay_ms": 200,
  "max_wait_s": 45,
  "log_level": "INFO",
  "arecord_device": "plughw:1,0",
  "start_hotkey": "ctrl+alt+a",
  "stop_hotkey": "ctrl+alt+s",
  "floating_button_enabled": true,
  "floating_button_size": 60,
  "type_delay_ms": 5
}
```

### 火山引擎（volcengine）

火山引擎配置入口参考：https://www.llingfei.com/695.html

1. 在火山引擎控制台开启 **大模型录音文件极速版识别** 能力，获取：
   - `app_key`（App ID）
   - `access_key`
2. 将 `channel` 保持为 `volcengine`，填写上述密钥信息即可。
3. 如果偏好环境变量，可在启动前设置：
   ```bash
   export LEXISHARP_APP_KEY=你的AppID
   export LEXISHARP_ACCESS_KEY=你的AccessKey
   ```
   环境变量优先级高于配置文件。

### Soniox（soniox）

1. 访问 [Soniox Console](https://console.soniox.com) 创建项目并生成 API Key。
2. 在配置文件中将 `channel` 改为 `soniox`，并填写 `soniox_api_key`；可按需调整模型和扩展参数：
   - `soniox_model`：默认使用官方推荐的 `stt-async-preview`。
   - `soniox_language_hints`：可选的语言提示列表（如 `["zh", "en"]`），有助于提升准确率。
   - `soniox_enable_speaker_diarization` / `soniox_enable_language_identification`：开启说话人区分或语言识别。
   - `soniox_context`：上下文提示文本，最长 10K 字符，用于辅助识别专有名词。
   - `soniox_poll_interval_s` / `soniox_poll_timeout_s`：控制轮询间隔与超时时间（秒）。
3. Soniox 也支持通过环境变量传参（优先级高于配置文件）：
   ```bash
   export SONIOX_API_KEY=你的SonioxAPIKey
  export SONIOX_MODEL=stt-async-preview
```
4. 当识别完成后，程序会自动清理已上传的文件与任务，可在日志中查看对应的 `client_reference_id`（与请求一致）。若需要更多参数示例，可参考官方文档：https://soniox.com/docs/stt/async/async-transcription

### 通义千问（qwen）

1. 登录 [阿里云百炼](https://bailian.console.aliyun.com/?tab=model#/api-key) 并创建 DashScope API Key。建议将密钥保存为环境变量：
   ```bash
   export DASHSCOPE_API_KEY=sk-xxxx
   ```
   若不使用环境变量，请在 `~/.lexisharp-linux/config.json` 的 `qwen_api_key` 字段填写完整密钥。
2. 安装通义千问官方 SDK：
   ```bash
   pip install dashscope
   ```
   应用会在检测到缺失时给出提示，未安装将无法调用该渠道。
3. 在设置中选择「通义千问（Qwen）」，按需配置：
   - `qwen_model`：默认使用 `qwen3-asr-flash`（生产环境推荐）。如需体验多语种 Beta 版本，可设为 `qwen-audio-asr`。
   - `qwen_context`：可选的上下文提示，用于增强专业词汇识别。
   - `qwen_language`：可选的语种提示（如 `zh`、`en`、`yue`），留空则由模型自动检测。
   - `qwen_enable_lid`：是否在返回结果中附带语种识别信息。
   - `qwen_enable_itn`：开启后通义千问会对数字、金额等文本做逆文本规范化（目前支持中英文）。
4. 通义千问要求音频格式为 16kHz 单声道，且单次调用不超过 10MB / 3 分钟。程序默认录音参数已满足要求，如遇超长录音可在界面中手动停止或拆分上传。
5. 更多参数说明与最佳实践，可参考官方文档《录音文件识别-通义千问》：https://help.aliyun.com/zh/model-studio/qwen-speech-recognition

## 使用步骤

### 当前项目初级极端更新频繁，暂不提供程序启动，请手动启动或者通过脚本启动程序。

#### 手动启动
```bash
source ~/.venvs/lexisharp/bin/activate
python lexisharp.py
```
####  使用一键脚本
```
chmod +x lexisharp.sh

```
arch可直接通过alt+空格键输入lexisharp.sh运行

1. 激活目标应用并让光标停留在文本输入框。
2. 回到 LexiSharp-linux，点击“开始”或使用默认热键 `Ctrl+Alt+A` 开始录音。
3. 再次点击（或 `Ctrl+Alt+S`）结束录音并等待识别。
4. 识别结果会自动复制到剪贴板，并尝试自动粘贴到目标窗口；若未成功，可手动使用 `Ctrl+V` 粘贴。
5. 可在界面勾选“显示浮动录音按钮”，获得置顶的悬浮录音键。

> 提示：`auto_paste` 默认开启。，程序会在复制成功后自动向目标窗口注入内容；若遇到兼容性问题，可随时关闭并手动粘贴。

## 常见问题
- **提示未找到 arecord**：确认已安装 `alsa-utils`，终端执行 `arecord -h` 验证。
- **提示未找到 xdotool**：安装 `xdotool` 后重新运行。
- **识别成功但未复制**：安装 `wl-clipboard`（Wayland 环境）或 `xclip/xsel`（X11 环境），并重启程序。
- **Wayland 自动粘贴无效**：确认 `wl-clipboard`、`python-evdev` 已安装，并确保当前用户对 `/dev/uinput` 具有写权限（将用户加入 `input` 组后重新登录）。
- **API 返回 403/401**：检查 App ID、Access Key 是否正确，确保服务已开通。
- **录音为空**：运行 `arecord -l` 查看设备列表，例如：
  ```
  **** List of CAPTURE Hardware Devices ****
  card 1: PCH [HDA Intel PCH], device 0: ALC3232 Analog [ALC3232 Analog]
    Subdevices: 1/1
    Subdevice #0: subdevice #0
  ```
  对应在配置中设置 `"arecord_device"` 为 `plughw:1,0`。
- **日志文件**：程序日志保存在.lexisharp-linux/目录下，遇到问题可以先检查log文件
## 即将实现：
- 支持更多第三方 ASR 服务或本地模型。
- 改进 UI 体验与配置流程。
- 提供更完善的自动粘贴兼容方案。

欢迎根据自身需求进行二次开发。LexiSharp-linux 希望成为可拓展的语音输入基础工具。***
