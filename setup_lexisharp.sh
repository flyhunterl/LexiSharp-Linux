#!/bin/bash

# --- 配置项 ---
# 虚拟环境的路径，与你常用的 ~/.venvs/lexisharp 一致
VENV_PATH="${HOME}/.venvs/lexisharp"
# Python 依赖清单文件，请确保它与脚本在同一目录下
REQUIREMENTS_FILE="requirements.txt"

# --- 脚本开始 ---
echo "✨ 欢迎使用 LexiSharp 环境准备一键脚本！✨"
echo "本脚本将帮助您自动完成系统依赖安装、Python 虚拟环境设置以及 Python 依赖安装。"
echo "--------------------------------------------------------"

# --- 1. 检测操作系统并安装系统依赖 ---
echo "⚙️  正在检测您的操作系统并准备安装系统依赖..."

OS_TYPE=""
# 尝试使用 /etc/os-release 来识别操作系统
if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [[ "$ID" == "ubuntu" || "$ID" == "debian" || "$ID_LIKE" == "debian" ]]; then
        OS_TYPE="debian"
    elif [[ "$ID" == "fedora" || "$ID" == "rhel" || "$ID_LIKE" == "fedora" || "$ID_LIKE" == "rhel" ]]; then
        OS_TYPE="fedora"
    elif [[ "$ID" == "arch" || "$ID" == "manjaro" || "$ID_LIKE" == "arch" ]]; then
        OS_TYPE="arch"
    fi
else
    # 兼容没有 /etc/os-release 的老系统（或特殊发行版），通过包管理器判断
    if command -v apt &> /dev/null; then
        OS_TYPE="debian"
    elif command -v dnf &> /dev/null; then
        OS_TYPE="fedora"
    elif command -v pacman &> /dev/null; then
        OS_TYPE="arch"
    fi
fi

if [ -z "$OS_TYPE" ]; then
    echo "⚠️  抱歉，未能识别您的操作系统类型。请您手动安装系统依赖。 "
    echo "    - Arch/Manjaro: sudo pacman -S python python-pip alsa-utils xdotool wl-clipboard xclip tk"
    echo "    - Debian/Ubuntu: sudo apt update && sudo apt install python3 python3-venv python3-pip alsa-utils xdotool wl-clipboard xclip"
    echo "    - Fedora/RHEL: sudo dnf install python3 python3-pip python3-virtualenv alsa-utils xdotool wl-clipboard xclip python3-tkinter"
    echo "脚本已终止。"
    exit 1
fi

echo "✅ 您的操作系统是：${OS_TYPE}。"
echo "    准备安装系统依赖，这可能需要您的 sudo 密码..."

case "$OS_TYPE" in
    debian)
        sudo apt update && sudo apt install -y python3 python3-venv python3-pip alsa-utils xdotool wl-clipboard xclip python3-tk
        ;;
    fedora)
        sudo dnf install -y python3 python3-pip python3-virtualenv alsa-utils xdotool wl-clipboard xclip python3-tkinter
        ;;
    arch)
        sudo pacman -S --noconfirm python python-pip alsa-utils xdotool wl-clipboard xclip tk
        ;;
    *)
        echo "⚠️  未知操作系统类型，请手动安装系统依赖。脚本已终止。"
        exit 1
        ;;
esac

if [ $? -ne 0 ]; then
    echo "❌ 系统依赖安装失败。请检查错误信息并手动解决。脚本已终止。"
    exit 1
fi
echo "✅ 系统依赖安装完成！"
echo "ℹ️  若在 Wayland 下启用自动粘贴，请确保当前用户已加入 input 组（sudo usermod -aG input \$USER，并重新登录）。"

# --- 2. 创建并设置虚拟环境 ---
echo "--------------------------------------------------------"
echo "🐍 正在创建或检查 Python 虚拟环境 (${VENV_PATH})..."

if [ ! -d "$VENV_PATH" ]; then
    echo "    虚拟环境不存在，正在创建..."
    # 确保使用 python3 来创建虚拟环境
    python3 -m venv "$VENV_PATH"
    if [ $? -ne 0 ]; then
        echo "❌ 虚拟环境创建失败。请检查 python3 和 venv 模块是否正确安装。脚本已终止。"
        exit 1
    fi
    echo "✅ 虚拟环境创建成功！"
else
    echo "✅ 虚拟环境已存在。"
fi

# 存储虚拟环境中 pip 和 python 的路径，以便后续直接使用
VENV_PIP="${VENV_PATH}/bin/pip"
VENV_PYTHON="${VENV_PATH}/bin/python"

if [ ! -f "$VENV_PIP" ]; then
    echo "❌ 无法找到虚拟环境中的 pip 可执行文件 (${VENV_PIP})。请检查虚拟环境是否正确创建。脚本已终止。"
    exit 1
fi

# --- 3. 安装 Python 依赖 ---
echo "--------------------------------------------------------"
echo "📦 正在使用虚拟环境安装 Python 依赖..."

if [ ! -f "$REQUIREMENTS_FILE" ]; then
    echo "⚠️  警告：未找到 ${REQUIREMENTS_FILE} 文件。请确保该文件在当前目录下，否则将无法安装 Python 依赖。"
    echo "    如果您确认不需要安装 Python 依赖，可以忽略此警告。"
else
    echo "    正在安装 ${REQUIREMENTS_FILE} 中列出的依赖..."
    # 先升级 pip 自身，确保安装过程顺利
    "$VENV_PIP" install --upgrade pip
    "$VENV_PIP" install -r "$REQUIREMENTS_FILE"
    if [ $? -ne 0 ]; then
        echo "❌ Python 依赖安装失败。请检查 ${REQUIREMENTS_FILE} 文件内容或您的网络连接。脚本已终止。"
        exit 1
    fi
    echo "✅ Python 依赖安装完成！"
fi

echo "--------------------------------------------------------"
echo "🎉 环境准备和依赖安装已全部完成！"
echo "🚀 要激活虚拟环境，请运行：source ${VENV_PATH}/bin/activate"
echo "    激活后，您就可以在独立的环境中运行 LexiSharp 了。"
echo "--------------------------------------------------------"
