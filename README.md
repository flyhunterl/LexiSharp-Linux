# LexiSharp-linux

LexiSharp-linux 是一款运行于 Linux 桌面的轻量级语音输入工具，围绕“录音 → 识别 → 粘贴”这一流程展开，实现最小成本的语音转文字体验。
项目灵感来源于安卓版LexiSharp。安卓版版本已经是一个非常完善且功能强大的版本！地址：https://github.com/BryceWG/LexiSharp-Keyboard


### 目前项目处于初级开发阶段，暂不提供执行程序.可以通过一键脚本或手动启动方式使用

<img width="479" height="547" alt="image" src="https://github.com/user-attachments/assets/26d7d875-26da-4004-8270-305192cf16f3" />


## 功能概览
- **一键录音**：主界面仅保留“开始/停止录音”按钮，同时可选启用悬浮按钮（置顶、可拖拽）。
- **极速转写**：集成火山引擎大模型录音文件极速版识别 API。
- **结果自动复制/粘贴**：识别文本会自动写入剪贴板；启用自动粘贴后会模拟 `Ctrl+V` 将内容送入目标窗口（默认关闭，Wayland 下需要安装 `wl-clipboard`）。
- **配置简明**：首次启动生成 `~/.lexisharp-linux/config.json` 模板，填入密钥即可使用。

## 环境准备
### 使用一键脚本

添加权限
```bash
chmod +x setup_lexisharp.sh
```

运行脚本
```bash
./setup_lexisharp.sh
```

### 手动安装
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

> 若在 Wayland 环境启用 `auto_paste`，请确保已安装 `wl-clipboard`（Arch使用 `paru -S wl-clipboard`安装），并将当前用户加入 `input` 组（或放开 `/dev/uinput` 权限）。程序会通过 `python-evdev`（已在 `requirements.txt` 中）创建虚拟键盘自动发送 `Ctrl+V`。 


## 火山引擎配置

火山引擎配置入口参考：https://www.llingfei.com/695.html

1. 在火山引擎控制台开启 **大模型录音文件极速版识别** 能力，获取：
   - `app_key`（App ID）
   - `access_key`
2. 首次运行 `python lexisharp.py` 会在 `~/.lexisharp-linux/config.json` 生成配置模板，字段说明：
   ```json
   {
     "api_url": "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",
     "app_key": "你的AppID",
     "access_key": "你的AccessKey",
     "resource_id": "volc.bigasr.auc_turbo",
     "model_name": "bigmodel",
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
3. 如果偏好环境变量，可在启动前设置：
   ```bash
   export LEXISHARP_APP_KEY=你的AppID
   export LEXISHARP_ACCESS_KEY=你的AccessKey
   ```
   环境变量优先级高于配置文件。

## 使用步骤
### 手动启动
```bash
source ~/.venvs/lexisharp/bin/activate
python lexisharp.py
```
### 使用一键脚本
```
chmod +x lexisharp.sh

```
arch可直接通过alt+空格键输入lexisharp.sh运行

1. 激活目标应用并让光标停留在文本输入框。
2. 回到 LexiSharp-linux，点击“开始”或使用默认热键 `Ctrl+Alt+A` 开始录音。
3. 再次点击（或 `Ctrl+Alt+S`）结束录音并等待识别。
4. 识别结果会自动复制到剪贴板，并尝试自动粘贴到目标窗口；若未成功，可手动使用 `Ctrl+V` 粘贴。
5. 可在界面勾选“显示浮动录音按钮”，获得置顶的悬浮录音键。

> 提示：`auto_paste` 默认关闭。满足依赖条件后可将其设为 `true`，程序会在复制成功后自动向目标窗口注入内容；若遇到兼容性问题，可随时关闭并手动粘贴。

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

## 即将实现：
- 支持更多第三方 ASR 服务或本地模型。
- 改进 UI 体验与配置流程。
- 提供更完善的自动粘贴兼容方案。

欢迎根据自身需求进行二次开发。LexiSharp-linux 希望成为可拓展的语音输入基础工具。***
