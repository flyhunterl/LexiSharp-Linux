#!/bin/bash

# ----------------------------------------------------
# LexiSharp-Linux 项目一键启动脚本
# ----------------------------------------------------

# 1. 确保脚本是从项目根目录运行
# 如果脚本不在项目根目录，或者需要从任何位置调用，这行很重要
# 获取脚本所在的目录，并切换到该目录
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
cd "$SCRIPT_DIR" || { echo "错误：无法切换到项目目录！" >&2; exit 1; }

echo "--- 正在启动 LexiSharp-Linux ---"

# 2. 激活虚拟环境
# 请确保 ~/.venvs/lexisharp 是您虚拟环境的正确路径
VENV_PATH="$HOME/.venvs/lexisharp/bin/activate"

if [ -f "$VENV_PATH" ]; then
    echo "正在激活虚拟环境: $VENV_PATH"
    source "$VENV_PATH"
    # 检查虚拟环境是否成功激活
    if [ "$?" -ne 0 ]; then
        echo "错误：虚拟环境激活失败！请检查路径和权限。" >&2
        exit 1
    fi
else
    echo "错误：找不到虚拟环境激活脚本 '$VENV_PATH'。" >&2
    echo "请检查虚拟环境路径是否正确，或是否已创建该虚拟环境。" >&2
    exit 1
fi

# 3. 运行您的 Python 项目
echo "正在运行 lexisharp.py..."
python lexisharp.py

# 4. 脚本执行完毕
echo "--- LexiSharp-Linux 运行结束 ---"

# 5. 可选：运行完毕后是否自动退出虚拟环境
# 如果您希望在 lexisharp.py 运行结束后，当前终端依然保持在虚拟环境内，
# 那么请注释掉下面的 'deactivate' 命令。
# 如果您希望运行结束后自动退出虚拟环境，就保持下面的行。
# deactivate
