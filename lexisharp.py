#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LexiSharp-linux 主程序：通过录音 + 多种云端 ASR（火山引擎、Soniox 等）实现一键语音输入。
"""

import audioop
import base64
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import wave
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

import tkinter as tk
from tkinter import messagebox, ttk

import pyperclip
import requests
from pynput import keyboard as pynput_keyboard

try:
    from evdev import UInput, ecodes
except ImportError:  # pragma: no cover - 处理运行时缺失
    UInput = None
    ecodes = None

# 配置文件路径
CONFIG_DIR = Path.home() / ".lexisharp-linux"
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "lexisharp.log"

# 默认配置模板
NEW_CONFIG_CREATED = False

CONFIG_TEMPLATE = {
    "api_url": "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",
    "app_key": "在此填写APP ID",
    "access_key": "在此填写Access Key",
    "resource_id": "volc.bigasr.auc_turbo",
    "model_name": "bigmodel",
    "channel": "volcengine",
    "soniox_api_base": "https://api.soniox.com",
    "soniox_api_key": "在此填写Soniox API Key",
    "soniox_model": "stt-async-preview",
    "soniox_language_hints": [],
    "soniox_enable_speaker_diarization": False,
    "soniox_enable_language_identification": False,
    "soniox_context": "",
    "soniox_poll_interval_s": 1.0,
    "soniox_poll_timeout_s": 120.0,
    "auto_paste": True,
    "paste_delay_ms": 200,
    "max_wait_s": 45,
    "log_level": "INFO",
    "arecord_device": "plughw:1,0",
    "start_hotkey": "ctrl+alt+a",
    "stop_hotkey": "ctrl+alt+s",
    "floating_button_enabled": False,
    "floating_button_size": 96,
    "type_delay_ms": 5
}


def ensure_config() -> dict:
    """
    确保配置文件存在并返回配置内容。
    """
    global NEW_CONFIG_CREATED
    if not CONFIG_PATH.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(CONFIG_TEMPLATE, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        NEW_CONFIG_CREATED = True
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)
    updated = False
    if "always_on_top" in config:
        if "floating_button_enabled" not in config:
            config["floating_button_enabled"] = bool(config.get("always_on_top"))
        config.pop("always_on_top", None)
        updated = True
    if config.get("start_hotkey", "").lower() == "ctrl+alt+r":
        config["start_hotkey"] = CONFIG_TEMPLATE["start_hotkey"]
        updated = True
    if config.get("stop_hotkey", "").lower() == "ctrl+alt+s":
        config["stop_hotkey"] = CONFIG_TEMPLATE["stop_hotkey"]
        updated = True
    for key, value in CONFIG_TEMPLATE.items():
        if key not in config:
            config[key] = value
            updated = True
    if updated:
        save_config(config)
    return config


def save_config(config: dict) -> None:
    """
    将配置写回磁盘。
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """
    初始化日志记录器，输出到配置目录下的日志文件。
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("lexisharp")
    logger.handlers.clear()
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.propagate = False

    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=5 * 1024 * 1024,
        backupCount=1,
        encoding="utf-8"
    )
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(threadName)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def current_active_window() -> Optional[str]:
    """
    读取当前激活窗口ID，依赖 xdotool。
    """
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow"],
            check=True,
            capture_output=True,
            text=True
        )
        window_id = result.stdout.strip()
        return window_id if window_id else None
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


class ClipboardHelper:
    """
    统一管理剪贴板操作，根据当前桌面环境自动选择工具。
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        parent_logger = logger or logging.getLogger("lexisharp")
        self.logger = parent_logger.getChild("clipboard")
        self._wl_copy_processes: List[subprocess.Popen] = []

    @staticmethod
    def _is_wayland() -> bool:
        return bool(os.environ.get("WAYLAND_DISPLAY"))

    @staticmethod
    def _which(command: str) -> Optional[str]:
        return shutil.which(command)

    def _cleanup_wl_copy_processes(self) -> None:
        """
        回收已结束的 wl-copy 进程，避免产生僵尸进程。
        """
        alive: List[subprocess.Popen] = []
        for proc in self._wl_copy_processes:
            if proc.poll() is None:
                alive.append(proc)
                continue
            try:
                proc.wait(timeout=0)
            except Exception:
                self.logger.debug("回收 wl-copy 子进程时出现异常", exc_info=True)
        self._wl_copy_processes = alive

    def copy(self, text: str | None) -> bool:
        """
        将文本写入剪贴板，优先使用 wl-copy，其次回退到 pyperclip。
        """
        payload = text if isinstance(text, str) else str(text or "")
        if self._is_wayland():
            wl_copy = self._which("wl-copy")
            if wl_copy:
                self._cleanup_wl_copy_processes()
                try:
                    self.logger.info("Wayland 环境检测到，调用 wl-copy 写入剪贴板，字符数=%d", len(payload))
                    process = subprocess.Popen(
                        [wl_copy, "--trim-newline"],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE
                    )
                except FileNotFoundError:
                    self.logger.error("未找到 wl-copy 命令，请确认 wl-clipboard 是否已安装。")
                except OSError as exc:
                    self.logger.error("启动 wl-copy 失败：%s", exc)
                except Exception:
                    self.logger.exception("wl-copy 写入剪贴板时发生异常")
                else:
                    try:
                        _, stderr_data = process.communicate(
                            payload.encode("utf-8"),
                            timeout=0.3
                        )
                    except subprocess.TimeoutExpired:
                        self.logger.info("wl-copy 写入剪贴板成功，进程将继续在后台托管内容。")
                        self._wl_copy_processes.append(process)
                        return True
                    except Exception:
                        process.kill()
                        process.communicate()
                        self.logger.exception("wl-copy 写入剪贴板时发生异常")
                    else:
                        stderr_text = (
                            stderr_data.decode("utf-8", errors="ignore") if stderr_data else ""
                        )
                        if process.returncode == 0:
                            self.logger.info("wl-copy 写入剪贴板成功")
                            return True
                        self.logger.error(
                            "wl-copy 执行失败（返回码=%s）：%s",
                            process.returncode,
                            stderr_text.strip()
                        )
            else:
                self.logger.warning("Wayland 环境下未检测到 wl-copy，可安装 wl-clipboard 获得最佳体验。")

        try:
            self.logger.info("回退至 pyperclip 写入剪贴板，字符数=%d", len(payload))
            pyperclip.copy(payload)
        except pyperclip.PyperclipException:
            self.logger.exception("pyperclip 写入剪贴板失败")
            return False
        except Exception:
            self.logger.exception("写入剪贴板时出现未知异常")
            return False
        else:
            self.logger.info("pyperclip 写入剪贴板成功")
            return True

    def paste(self) -> str:
        """
        从剪贴板读取文本，Wayland 优先使用 wl-paste。
        """
        if self._is_wayland():
            wl_paste = self._which("wl-paste")
            if wl_paste:
                try:
                    self.logger.info("Wayland 环境检测到，调用 wl-paste 读取剪贴板内容")
                    result = subprocess.run(
                        [wl_paste, "--no-newline"],
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    text = result.stdout
                except subprocess.CalledProcessError as exc:
                    stderr = exc.stderr.strip() if exc.stderr else ""
                    self.logger.error("wl-paste 执行失败（返回码=%s）：%s", exc.returncode, stderr)
                except FileNotFoundError:
                    self.logger.error("未找到 wl-paste 命令，请确认 wl-clipboard 是否已安装。")
                except Exception:
                    self.logger.exception("wl-paste 读取剪贴板时发生异常")
                else:
                    self.logger.info("wl-paste 读取剪贴板成功，字符数=%d", len(text))
                    return text
            else:
                self.logger.warning("Wayland 环境下未检测到 wl-paste，可安装 wl-clipboard 获得最佳体验。")

        try:
            self.logger.info("回退至 pyperclip 读取剪贴板内容")
            text = pyperclip.paste()
        except pyperclip.PyperclipException:
            self.logger.exception("pyperclip 读取剪贴板失败")
            return ""
        except Exception:
            self.logger.exception("读取剪贴板时发生未知异常")
            return ""
        else:
            if text is None:
                text = ""
            self.logger.info("pyperclip 读取剪贴板成功，字符数=%d", len(text))
            return text


class InputInjector:
    """
    在不同图形栈下注入键盘事件，Wayland 环境优先使用 uinput。
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        parent_logger = logger or logging.getLogger("lexisharp")
        self.logger = parent_logger.getChild("injector")
        self.is_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
        self.mode = "none"
        self._uinput: Optional["UInput"] = None
        if self.is_wayland:
            self._init_uinput()
        else:
            self.logger.debug("检测到非 Wayland 会话，保持默认 xdotool 注入模式。")

    def _init_uinput(self) -> None:
        """
        初始化 uinput 虚拟键盘。
        """
        if UInput is None or ecodes is None:
            self.logger.warning(
                "未安装 python-evdev，无需启用 uinput。请运行 `pip install evdev`。"
            )
            return
        uinput_path = Path("/dev/uinput")
        if not uinput_path.exists():
            self.logger.warning("未找到 /dev/uinput，无法启用虚拟键盘，请检查内核模块。")
            return
        if not os.access(uinput_path, os.W_OK):
            self.logger.warning(
                "/dev/uinput 无写权限，请将当前用户加入 input 组或调整 udev 规则。"
            )
            return
        capabilities = {
            # 仅声明需要的键位，其他事件类型由驱动自动处理，避免出现 Invalid argument。
            ecodes.EV_KEY: [
                ecodes.KEY_LEFTCTRL,
                ecodes.KEY_RIGHTCTRL,
                ecodes.KEY_V,
            ],
        }
        try:
            self._uinput = UInput(capabilities, name="LexiSharp Virtual Keyboard")
        except PermissionError as exc:
            self.logger.error("创建 uinput 虚拟键盘失败（权限不足）：%s", exc)
            self._uinput = None
        except OSError:
            self.logger.exception("创建 uinput 虚拟键盘失败")
            self._uinput = None
        else:
            self.mode = "uinput"
            self.logger.info("uinput 虚拟键盘初始化完成，自动粘贴将通过 Ctrl+V 注入。")

    def can_use_uinput(self) -> bool:
        """
        判断是否可使用 uinput 注入。
        """
        return self.mode == "uinput" and self._uinput is not None

    def inject_ctrl_v(self, wait_ms: int = 0) -> bool:
        """
        通过 uinput 发出 Ctrl+V 快捷键。
        """
        if not self.can_use_uinput():
            return False
        delay = max(0.0, wait_ms / 1000.0)
        if delay > 0:
            self.logger.debug("延时 %.3f 秒后发送 Ctrl+V。", delay)
            time.sleep(delay)
        try:
            self._emit_key(ecodes.KEY_LEFTCTRL, 1)
            time.sleep(0.01)
            self._emit_key(ecodes.KEY_V, 1)
            time.sleep(0.02)
            self._emit_key(ecodes.KEY_V, 0)
            time.sleep(0.01)
            self._emit_key(ecodes.KEY_LEFTCTRL, 0)
            time.sleep(0.01)
            self.logger.info("已通过 uinput 发送 Ctrl+V 快捷键。")
            return True
        except Exception:
            self.logger.exception("uinput 发送 Ctrl+V 时发生异常")
            return False

    def _emit_key(self, key_code: int, value: int) -> None:
        """
        写入单个按键事件。
        """
        if not self._uinput:
            raise RuntimeError("uinput 未初始化。")
        self._uinput.write(ecodes.EV_KEY, key_code, value)
        self._uinput.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
        self._uinput.syn()

    def close(self) -> None:
        """
        关闭并释放虚拟键盘。
        """
        if self._uinput:
            try:
                self._uinput.close()
                self.logger.info("已释放 uinput 虚拟键盘设备。")
            except Exception:
                self.logger.exception("释放 uinput 虚拟键盘失败")
            finally:
                self._uinput = None
                self.mode = "none"
class Recorder:
    """
    基于 arecord 的简易录音器。
    """

    def __init__(self, sample_rate: int = 16000, device: str | None = None, logger: logging.Logger | None = None):
        self.sample_rate = sample_rate
        self.device = device
        self.logger = logger or logging.getLogger("lexisharp.recorder")
        self._process: Optional[subprocess.Popen] = None
        self._file_path: Optional[str] = None
        self._wave_file: Optional[wave.Wave_write] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._current_level: float = 0.0

    def start(self) -> str:
        """
        启动录音并返回临时音频文件路径。
        """
        if self._process:
            raise RuntimeError("录音已在进行中。")

        self._stop_event.clear()
        self._current_level = 0.0

        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        tmp_file.close()
        self._file_path = tmp_file.name

        try:
            self._wave_file = wave.open(self._file_path, "wb")
            self._wave_file.setnchannels(1)
            self._wave_file.setsampwidth(2)
            self._wave_file.setframerate(self.sample_rate)
        except Exception:
            self._cleanup_files()
            raise

        try:
            self._process = subprocess.Popen(
                self._build_arecord_args(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name="RecorderReader",
                daemon=True
            )
            self._reader_thread.start()
        except FileNotFoundError as exc:
            self._cleanup_files()
            raise RuntimeError("未找到 arecord，请安装 alsa-utils。") from exc
        except Exception:
            self.stop()
            self._cleanup_files()
            raise
        return self._file_path

    def stop(self, timeout: float = 3.0) -> Optional[str]:
        """
        停止录音并返回音频文件路径。
        """
        if not self._process:
            return None
        try:
            self._stop_event.set()
            self._process.send_signal(signal.SIGINT)
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
        finally:
            if self._process and self._process.stdout:
                try:
                    self._process.stdout.close()
                except Exception:
                    pass
            self._process = None

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=timeout)
        self._reader_thread = None

        if self._wave_file:
            try:
                self._wave_file.close()
            finally:
                self._wave_file = None

        self._current_level = 0.0
        self._stop_event.clear()
        return self._file_path

    def is_running(self) -> bool:
        """
        判断录音是否正在进行。
        """
        return self._process is not None

    def current_level(self) -> float:
        """
        返回当前音量强度（0.0 ~ 1.0）。
        """
        return self._current_level

    def _reader_loop(self) -> None:
        """
        从 arecord 读取音频数据，写入文件并计算音量。
        """
        if not self._process or not self._process.stdout or not self._wave_file:
            return
        try:
            while not self._stop_event.is_set():
                chunk = self._process.stdout.read(4096)
                if not chunk:
                    break
                self._wave_file.writeframes(chunk)
                rms = audioop.rms(chunk, 2)
                self._current_level = min(rms / 32768.0, 1.0)
        except Exception:
            self._current_level = 0.0
        finally:
            self._current_level = 0.0

    def _cleanup_files(self) -> None:
        """
        录音初始化失败时清理临时文件。
        """
        if self._wave_file:
            try:
                self._wave_file.close()
            except Exception:
                pass
            self._wave_file = None
        if self._file_path and Path(self._file_path).exists():
            try:
                os.remove(self._file_path)
            except OSError:
                pass
        self._file_path = None

    def _build_arecord_args(self) -> list[str]:
        """
        构造 arecord 命令参数。
        """
        args = [
            "arecord",
            "-q",
            "-f",
            "S16_LE",
            "-r",
            str(self.sample_rate),
            "-c",
            "1",
        ]
        if self.device:
            args.extend(["-D", self.device])
            self.logger.info("使用指定录音设备：%s", self.device)
        args.extend(["-t", "raw", "-"])
        return args


class GlobalHotkeyManager:
    """
    管理全局快捷键监听。
    """

    def __init__(
        self,
        start_hotkey: str,
        stop_hotkey: str,
        start_callback,
        stop_callback,
        logger: logging.Logger
    ):
        self.logger = logger
        self.listener: Optional[pynput_keyboard.GlobalHotKeys] = None
        self._start_callback = start_callback
        self._stop_callback = stop_callback

        mapping = {}
        try:
            if start_hotkey:
                mapping[self._convert_hotkey(start_hotkey)] = self._wrap_callback(
                    start_callback,
                    "start"
                )
            if stop_hotkey:
                mapping[self._convert_hotkey(stop_hotkey)] = self._wrap_callback(
                    stop_callback,
                    "stop"
                )
        except ValueError as exc:
            raise RuntimeError(f"快捷键格式无效：{exc}") from exc

        if not mapping:
            logger.info("未配置全局快捷键，跳过注册。")
            return

        try:
            self.listener = pynput_keyboard.GlobalHotKeys(mapping)
            self.listener.start()
            logger.info("全局快捷键已注册：开始=%s，结束=%s", start_hotkey, stop_hotkey)
        except Exception as exc:
            raise RuntimeError(f"注册全局快捷键失败：{exc}") from exc

    def stop(self) -> None:
        """
        停止监听。
        """
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                self.logger.exception("停止快捷键监听失败")
            self.listener = None

    @staticmethod
    def _convert_hotkey(hotkey: str) -> str:
        """
        将配置中的快捷键描述转换为 pynput 识别的格式。
        """
        parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
        if not parts:
            raise ValueError("快捷键字符串为空。")

        formatted = []
        for part in parts:
            if len(part) == 1 and part.isprintable():
                formatted.append(part)
            else:
                formatted.append(f"<{part}>")
        return "+".join(formatted)

    def _wrap_callback(self, callback, tag: str):
        """
        包装回调，确保异常可追踪。
        """

        def _inner():
            try:
                callback()
            except Exception:  # pylint: disable=broad-except
                self.logger.exception("执行 %s 快捷键回调出错", tag)

        return _inner


class FloatingButton:
    """
    浮动录音按钮，支持拖拽与尺寸调节。
    """

    STATE_STYLE = {
        "idle": ("#4CAF50", "#388E3C", "开始录音"),
        "recording": ("#F44336", "#D32F2F", "录音中…"),
        "processing": ("#FF9800", "#F57C00", "识别中…"),
    }

    def __init__(self, app: "LexiSharpApp", size: int):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg="#1B1B1B")

        self.size = max(50, int(size))
        self._dragging = False
        self._press_offset = (0, 0)

        self.button = tk.Button(
            self.top,
            text="开始",
            bg="#4CAF50",
            fg="white",
            activebackground="#388E3C",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            font=("WenQuanYi Micro Hei", 10, "bold"),
            cursor="hand2"
        )
        self.button.pack(fill=tk.BOTH, expand=True)

        self.button.bind("<ButtonPress-1>", self._on_press)
        self.button.bind("<B1-Motion>", self._on_drag)
        self.button.bind("<ButtonRelease-1>", self._on_release)

        self.top.geometry(f"{self.size}x{self.size}+120+120")
        self.top.deiconify()

    def update_size(self, size: int) -> None:
        """
        调整按钮尺寸。
        """
        self.size = max(50, int(size))
        x = self.top.winfo_x() if self.top.winfo_ismapped() else 120
        y = self.top.winfo_y() if self.top.winfo_ismapped() else 120
        self.top.geometry(f"{self.size}x{self.size}+{x}+{y}")

    def set_state(self, state: str) -> None:
        """
        根据状态调整显示样式。
        """
        bg, active_bg, text = self.STATE_STYLE.get(state, self.STATE_STYLE["idle"])
        short_text = {
            "开始录音": "开始",
            "录音中…": "录音",
            "识别中…": "处理"
        }.get(text, text)
        self.button.configure(
            bg=bg,
            activebackground=active_bg,
            text=short_text
        )

    def destroy(self) -> None:
        """
        销毁浮动按钮。
        """
        if self.top:
            try:
                self.top.destroy()
            finally:
                self.top = None

    def _on_press(self, event) -> None:
        self._dragging = False
        self._press_offset = (event.x, event.y)
        self.app.prime_external_window()

    def _on_drag(self, event) -> None:
        self._dragging = True
        x = event.x_root - self._press_offset[0]
        y = event.y_root - self._press_offset[1]
        self.top.geometry(f"{self.size}x{self.size}+{x}+{y}")

    def _on_release(self, _event) -> None:
        if not self._dragging:
            self.app.root.after(0, self.app.toggle_recording)
        self._dragging = False


class LexiSharpApp:
    """
    Tkinter 图形界面应用，负责录音、调用ASR、剪贴板操作。
    """

    def __init__(self, root: tk.Tk, config: dict, logger: logging.Logger):
        self.root = root
        self.config = config
        self.logger = logger
        self.clipboard = ClipboardHelper(logger=self.logger)
        self.input_injector = InputInjector(logger=self.logger)
        self.start_hotkey = (self.config.get("start_hotkey") or "").strip()
        self.stop_hotkey = (self.config.get("stop_hotkey") or "").strip()
        self.hotkey_manager: Optional[GlobalHotkeyManager] = None
        self.floating_button: Optional[FloatingButton] = None
        self._floating_state = "idle"
        self.floating_enabled_var = tk.BooleanVar(
            master=self.root,
            value=bool(self.config.get("floating_button_enabled", False))
        )
        float_size = int(self.config.get("floating_button_size", 96) or 96)
        self.floating_size_var = tk.IntVar(
            master=self.root,
            value=max(50, min(200, float_size))
        )
        device = (self.config.get("arecord_device") or "").strip() or None
        if device:
            self.logger.info("配置中指定录音设备：%s", device)
        self.recorder = Recorder(device=device, logger=self.logger)

        self.root.update_idletasks()
        self.root_window_id = str(self.root.winfo_id())
        self._own_windows: set[str] = {self.root_window_id}
        self._last_external_window: Optional[str] = None
        self._register_window(self.root)

        self.target_window: Optional[str] = None
        self.audio_path: Optional[str] = None
        self.processing = False

        self.status_var = tk.StringVar(value="准备就绪，点击开始录音。")
        self.result_var = tk.StringVar(value="尚未识别内容。")
        self.button_text = tk.StringVar(value="开始录音")
        self.level_var = tk.DoubleVar(value=0.0)
        self.last_result_text: str = ""
        self.original_window_before_record: Optional[str] = None
        self.settings_dialog: Optional["SettingsDialog"] = None
        self.config_ready: bool = False
        self._config_prompt_shown: bool = False

        self._build_ui()
        self._update_floating_controls_state()
        self._schedule_level_update()
        self._validate_keys(initial=NEW_CONFIG_CREATED)
        self._init_hotkeys()
        if self.floating_enabled_var.get():
            self._create_floating_button()
            self._apply_floating_state(self._floating_state)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        """
        构建界面组件。
        """
        self.root.title("LexiSharp-linux")
        self.root.geometry("480x520")
        self.root.resizable(False, False)

        font_title = ("WenQuanYi Micro Hei", 16, "bold")
        font_body = ("WenQuanYi Micro Hei", 12)

        header_frame = tk.Frame(self.root)
        header_frame.pack(fill=tk.X, pady=(20, 10), padx=20)

        title_label = tk.Label(
            header_frame,
            text="LexiSharp-linux",
            font=font_title
        )
        title_label.pack(side=tk.LEFT)

        settings_button = tk.Button(
            header_frame,
            text="设置",
            command=self._open_settings,
            font=("WenQuanYi Micro Hei", 11),
            width=8
        )
        settings_button.pack(side=tk.RIGHT)

        hotkey_info = ""
        if self.start_hotkey and self.stop_hotkey:
            hotkey_info = (
                f"\n全局快捷键：开始录音 {self.start_hotkey.upper()}，"
                f"结束录音 {self.stop_hotkey.upper()}。"
            )

        instruction = (
            "操作说明：点击下方按钮开始录音，再次点击结束并识别。\n"
            "识别成功后文本会复制到剪贴板，并且自动粘贴到目标窗口。\n"
            "可在下方启用“显示浮动录音按钮”以使用可拖动的置顶录音键。"
            f"{hotkey_info}"
        )
        instruction_label = tk.Label(
            self.root,
            text=instruction,
            wraplength=360,
            justify=tk.LEFT,
            font=font_body
        )
        instruction_label.pack(pady=(0, 10), padx=20)

        self.record_button = tk.Button(
            self.root,
            textvariable=self.button_text,
            command=self.toggle_recording,
            font=("WenQuanYi Micro Hei", 14),
            width=16,
            height=2,
            bg="#4CAF50",
            fg="white",
            activebackground="#45A049"
        )
        self.record_button.pack(pady=10)

        floating_frame = tk.Frame(self.root)
        floating_frame.pack(pady=(4, 0))

        floating_toggle = tk.Checkbutton(
            floating_frame,
            text="显示浮动录音按钮",
            variable=self.floating_enabled_var,
            command=self._toggle_floating_button,
            font=font_body
        )
        floating_toggle.pack(side=tk.LEFT)

        size_label = tk.Label(
            floating_frame,
            text="按钮大小",
            font=font_body
        )
        size_label.pack(side=tk.LEFT, padx=(16, 4))

        self.floating_size_scale = tk.Scale(
            floating_frame,
            from_=60,
            to=160,
            orient=tk.HORIZONTAL,
            resolution=5,
            showvalue=True,
            variable=self.floating_size_var,
            command=self._on_floating_size_change,
            length=160
        )
        self.floating_size_scale.pack(side=tk.LEFT)

        level_label = tk.Label(
            self.root,
            text="实时音量检测",
            font=font_body
        )
        level_label.pack(pady=(0, 4))

        self.level_bar = ttk.Progressbar(
            self.root,
            orient=tk.HORIZONTAL,
            length=360,
            mode="determinate",
            maximum=100,
            variable=self.level_var
        )
        self.level_bar.pack(padx=20, fill=tk.X)

        status_label = tk.Label(
            self.root,
            textvariable=self.status_var,
            wraplength=360,
            justify=tk.LEFT,
            font=font_body,
            fg="#333333"
        )
        status_label.pack(pady=(10, 4))

        result_frame = tk.LabelFrame(self.root, text="最近识别结果", font=font_body)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(4, 16))

        result_display = tk.Label(
            result_frame,
            textvariable=self.result_var,
            wraplength=360,
            justify=tk.LEFT,
            font=font_body,
            fg="#000000"
        )
        result_display.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

    def _collect_missing_fields(self, config: dict) -> tuple[list[str], str]:
        """
        根据指定配置收集缺失的必填字段，并返回渠道标识。
        """
        channel = (config.get("channel") or "volcengine").strip().lower()
        missing_messages: list[str] = []

        if channel in {"volcengine", "volcano", "volc", "bytedance"}:
            app_key = os.environ.get("LEXISHARP_APP_KEY") or config.get("app_key", "")
            access_key = os.environ.get("LEXISHARP_ACCESS_KEY") or config.get("access_key", "")
            has_app_key = bool(str(app_key).strip()) and "在此填写" not in str(app_key)
            has_access_key = bool(str(access_key).strip()) and "在此填写" not in str(access_key)
            if not (has_app_key or has_access_key):
                missing_messages.append("至少填写火山引擎凭证（App ID 或 Access Key）。")
        elif channel == "soniox":
            api_key = os.environ.get("SONIOX_API_KEY") or config.get("soniox_api_key", "")
            if "在此填写" in api_key or not str(api_key).strip():
                missing_messages.append("Soniox API Key（soniox_api_key 或环境变量 SONIOX_API_KEY）")
        else:
            self.logger.info("检测到自定义渠道：%s，跳过配置校验。", channel)

        return missing_messages, channel

    def _validate_keys(self, initial: bool = False, trigger_prompt: bool = True) -> bool:
        """
        检查当前渠道所需配置是否完整。
        """
        self.logger.debug("开始校验配置。")
        missing_messages, channel = self._collect_missing_fields(self.config)

        if missing_messages:
            self.config_ready = False
            if hasattr(self, "record_button"):
                self.record_button.configure(state=tk.DISABLED)
            missing_text = "、".join(missing_messages)
            self.logger.warning("配置缺失：%s", missing_text)
            self.status_var.set("配置缺失，请点击右上角“设置”按钮完成必填项。")
            if trigger_prompt and (initial or not self._config_prompt_shown):
                messagebox.showwarning(
                    "配置提醒",
                    f"当前配置缺少以下内容：\n{missing_text}\n请在“设置”中完善后再使用。"
                )
                self._config_prompt_shown = True
            if trigger_prompt:
                self.root.after(0, self._open_settings)
            return False

        self.logger.info("配置校验通过，渠道：%s", channel)
        self.config_ready = True
        self._config_prompt_shown = False
        if hasattr(self, "record_button"):
            self.record_button.configure(state=tk.NORMAL)
        if initial or self.status_var.get().startswith("配置缺失"):
            self.status_var.set("准备就绪，点击开始录音。")
        return True

    def toggle_recording(self) -> None:
        """
        开始或停止录音。
        """
        if self.processing:
            return
        if not self.config_ready:
            messagebox.showwarning("配置提醒", "当前配置尚未完成，请先在设置中填写必需字段。")
            self._open_settings()
            return

        if not self.recorder.is_running():
            self.prime_external_window()
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self) -> None:
        """
        开始录音。
        """
        self.logger.info("开始录音。")
        try:
            self.audio_path = self.recorder.start()
        except RuntimeError as exc:
            self.logger.exception("启动录音失败")
            messagebox.showerror("录音失败", str(exc))
            self._schedule_floating_state("idle")
            return

        self.logger.info("录音文件已创建：%s", self.audio_path)
        self.button_text.set("停止录音")
        self.status_var.set("录音中…再次点击按钮即可结束。")
        self._schedule_floating_state("recording")

    def stop_recording(self) -> None:
        """
        停止录音并进入识别流程。
        """
        path = self.recorder.stop()
        self.audio_path = path
        self.logger.info("停止录音，文件路径：%s", path)
        if not path or not Path(path).exists():
            self.logger.warning("未捕获到音频文件，path=%s", path)
            self.button_text.set("开始录音")
            self.status_var.set("未捕获到音频文件，请重试。")
            self._schedule_floating_state("idle")
            return

        file_size = Path(path).stat().st_size
        self.logger.info("音频文件大小：%d 字节", file_size)
        self.button_text.set("开始录音")
        provider_name = self._channel_display_name()
        self.status_var.set(f"正在向 {provider_name} 发送识别请求…")
        self._schedule_floating_state("processing")
        self.processing = True
        threading.Thread(target=self._recognize_task, daemon=True).start()

    def _recognize_task(self) -> None:
        """
        后台线程：读取音频、调用ASR、处理结果。
        """
        threading.current_thread().name = "ASRWorker"
        try:
            self.logger.info("识别线程启动，音频文件：%s", self.audio_path)
            text = self._call_asr(self.audio_path)
            if text is None:
                self.logger.warning("ASR 未返回有效文本")
                self._update_status("未获得识别结果，请检查日志或稍后再试。")
                return
            self.logger.info("ASR 返回文本：%s", text)
            self._refresh_result(text)

            copy_success_message = "识别完成，内容已复制到剪贴板。"
            auto_paste_message = "识别完成，内容已复制到剪贴板，并自动粘贴到目标窗口。"
            copy_message = copy_success_message
            copied = self.clipboard.copy(text)
            if not copied:
                copy_message = (
                    "识别成功，但无法复制到剪贴板，请安装 wl-clipboard（Wayland）或 xclip/xsel（X11）。"
                )

            if copied:
                if self.config.get("auto_paste", False):
                    pasted = self._auto_paste_async()
                    if pasted:
                        self._update_status(auto_paste_message)
                    else:
                        self._update_status("识别完成，内容已复制到剪贴板，请手动粘贴。")
                else:
                    self._update_status("识别完成，内容已复制到剪贴板，请手动粘贴。")
            else:
                self._update_status(copy_message)
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.exception("识别流程发生异常")
            self._update_status(f"识别失败：{exc}")
        finally:
            if self.audio_path and Path(self.audio_path).exists():
                try:
                    self.logger.info("清理音频文件：%s", self.audio_path)
                    os.remove(self.audio_path)
                except OSError:
                    self.logger.exception("删除音频文件失败：%s", self.audio_path)
                    pass
            self.original_window_before_record = None
            self.audio_path = None
            self.processing = False
            self._schedule_floating_state("idle")

    def _channel_display_name(self) -> str:
        """
        返回识别渠道的可读名称。
        """
        channel = (self.config.get("channel") or "volcengine").strip().lower()
        if channel in {"volcengine", "volcano", "volc", "bytedance"}:
            return "火山引擎"
        if channel == "soniox":
            return "Soniox"
        return channel or "火山引擎"

    def _open_settings(self) -> None:
        """
        打开设置对话框。
        """
        try:
            if self.settings_dialog and self.settings_dialog.window.winfo_exists():
                self.settings_dialog.window.lift()
                self.settings_dialog.window.focus_force()
                return
        except Exception:
            self.settings_dialog = None
        try:
            self.settings_dialog = SettingsDialog(self)
        except Exception:
            self.logger.exception("打开设置窗口失败")
            messagebox.showerror("设置", "无法打开设置窗口，请查看日志。")

    def _on_settings_closed(self) -> None:
        """
        设置窗口关闭时回调。
        """
        self.settings_dialog = None

    def _call_asr(self, audio_file: str) -> Optional[str]:
        """
        根据配置选择识别渠道并返回文本结果。
        """
        channel = (self.config.get("channel") or "volcengine").strip().lower()
        self.logger.info("识别渠道：%s", channel or "volcengine")
        if channel in {"volcengine", "volcano", "volc", "bytedance"}:
            return self._call_volcengine(audio_file)
        if channel == "soniox":
            return self._call_soniox(audio_file)
        raise RuntimeError(f"未识别的识别渠道：{channel}")

    def _call_volcengine(self, audio_file: str) -> Optional[str]:
        """
        调用火山引擎极速版ASR接口。
        """
        self.logger.info("准备读取音频并发起火山引擎请求：%s", audio_file)
        with open(audio_file, "rb") as f:
            audio_data = base64.b64encode(f.read()).decode("utf-8")

        request_id = os.environ.get("LEXISHARP_REQUEST_ID") or f"lexisharp-{uuid4()}"
        self.logger.info("本次请求 RequestId：%s", request_id)

        payload = {
            "user": {
                "uid": os.environ.get("LEXISHARP_APP_KEY", self.config.get("app_key"))
            },
            "audio": {
                "data": audio_data
            },
            "request": {
                "model_name": self.config.get("model_name", "bigmodel")
            }
        }

        headers = {
            "X-Api-App-Key": os.environ.get("LEXISHARP_APP_KEY", self.config.get("app_key")),
            "X-Api-Access-Key": os.environ.get("LEXISHARP_ACCESS_KEY", self.config.get("access_key")),
            "X-Api-Resource-Id": self.config.get("resource_id", "volc.bigasr.auc_turbo"),
            "X-Api-Request-Id": request_id,
            "X-Api-Sequence": "-1"
        }

        try:
            response = requests.post(
                self.config.get("api_url", CONFIG_TEMPLATE["api_url"]),
                headers=headers,
                json=payload,
                timeout=int(self.config.get("max_wait_s", 45))
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            error_body = ""
            if exc.response is not None:
                error_body = exc.response.text
                self.logger.error(
                    "HTTP 请求异常，状态：%s，响应体：%s",
                    exc.response.status_code,
                    error_body[:500]
                )
            raise RuntimeError(f"网络请求失败：{exc}，响应内容：{error_body}") from exc
        except requests.RequestException as exc:
            self.logger.exception("火山引擎请求发送失败")
            raise RuntimeError(f"网络请求失败：{exc}") from exc

        status_code = response.headers.get("X-Api-Status-Code")
        self.logger.info("火山引擎返回状态码：%s，HTTP 状态：%s", status_code, response.status_code)
        if status_code != "20000000":
            message = response.headers.get("X-Api-Message", "未知错误")
            self.logger.error("火山引擎接口返回异常：%s - %s", status_code, message)
            raise RuntimeError(f"ASR接口返回异常：{status_code} - {message}")

        data = response.json()
        text = data.get("result", {}).get("text", "")
        self.logger.debug("火山引擎原始数据：%s", data)
        return text.strip() or None

    def _call_soniox(self, audio_file: str) -> Optional[str]:
        """
        调用 Soniox 异步识别接口。
        """
        api_key = os.environ.get("SONIOX_API_KEY") or self.config.get("soniox_api_key")
        if not api_key or not str(api_key).strip():
            raise RuntimeError("未配置 Soniox API Key，请在环境变量 SONIOX_API_KEY 或配置文件 soniox_api_key 中填写。")
        base_url = (self.config.get("soniox_api_base") or CONFIG_TEMPLATE["soniox_api_base"]).rstrip("/")
        model = (
            os.environ.get("SONIOX_MODEL")
            or self.config.get("soniox_model")
            or CONFIG_TEMPLATE["soniox_model"]
        )
        poll_interval = max(0.5, float(self.config.get("soniox_poll_interval_s", CONFIG_TEMPLATE["soniox_poll_interval_s"])))
        poll_timeout = max(poll_interval, float(self.config.get("soniox_poll_timeout_s", CONFIG_TEMPLATE["soniox_poll_timeout_s"])))
        timeout = int(self.config.get("max_wait_s", 45))

        request_id = os.environ.get("LEXISHARP_REQUEST_ID") or f"lexisharp-{uuid4()}"
        self.logger.info("Soniox 请求 RequestId：%s", request_id)

        session = requests.Session()
        session.headers["Authorization"] = f"Bearer {api_key}"
        session.headers["User-Agent"] = "LexiSharp-Soniox/1.0"

        file_id = None
        transcription_id = None
        upload_url = f"{base_url}/v1/files"
        transcription_url = f"{base_url}/v1/transcriptions"

        try:
            self.logger.info("开始上传音频至 Soniox：%s", audio_file)
            with open(audio_file, "rb") as audio_fp:
                response = session.post(upload_url, files={"file": audio_fp}, timeout=timeout)
            response.raise_for_status()
            file_id = response.json().get("id")
            if not file_id:
                raise RuntimeError("Soniox 文件上传响应缺少文件 ID。")
            self.logger.info("Soniox 文件 ID：%s", file_id)

            request_body = {
                "model": model,
                "file_id": file_id,
                "client_reference_id": request_id
            }
            hints = self.config.get("soniox_language_hints")
            if isinstance(hints, list):
                normalized = [str(item).strip() for item in hints if str(item).strip()]
                if normalized:
                    request_body["language_hints"] = normalized
            if bool(self.config.get("soniox_enable_speaker_diarization")):
                request_body["enable_speaker_diarization"] = True
            if bool(self.config.get("soniox_enable_language_identification")):
                request_body["enable_language_identification"] = True
            context_text = (self.config.get("soniox_context") or "").strip()
            if context_text:
                request_body["context"] = context_text

            self.logger.info("创建 Soniox 转写任务...")
            response = session.post(transcription_url, json=request_body, timeout=timeout)
            response.raise_for_status()
            transcription_id = response.json().get("id")
            if not transcription_id:
                raise RuntimeError("Soniox 转写创建响应缺少任务 ID。")
            self.logger.info("Soniox 转写 ID：%s", transcription_id)

            status_url = f"{transcription_url}/{transcription_id}"
            transcript_url = f"{status_url}/transcript"

            deadline = time.monotonic() + poll_timeout
            last_status = ""
            while True:
                if time.monotonic() > deadline:
                    raise RuntimeError("Soniox 识别超时，请检查音频或增大 soniox_poll_timeout_s。")
                response = session.get(status_url, timeout=timeout)
                response.raise_for_status()
                status_payload = response.json()
                status = (status_payload.get("status") or "").lower()
                if status != last_status:
                    self.logger.info("Soniox 任务状态：%s", status or "未知")
                    last_status = status
                if status == "completed":
                    break
                if status == "error":
                    message = status_payload.get("error_message") or status_payload.get("message") or "未知错误"
                    raise RuntimeError(f"Soniox 识别失败：{message}")
                time.sleep(poll_interval)

            self.logger.info("Soniox 转写完成，获取文本...")
            response = session.get(transcript_url, timeout=timeout)
            response.raise_for_status()
            transcript_payload = response.json()
            text = (transcript_payload.get("text") or "").strip()
            if not text:
                tokens = transcript_payload.get("tokens") or []
                text = self._render_soniox_tokens(tokens).strip() if tokens else ""
            if not text:
                raise RuntimeError("Soniox API 未返回可用文本内容。")
            return text
        except requests.HTTPError as exc:
            error_body = ""
            if exc.response is not None:
                error_body = exc.response.text
            self.logger.error("Soniox HTTP 请求异常：%s，响应：%s", exc, error_body[:500])
            raise RuntimeError(f"Soniox HTTP 请求失败：{exc}，响应内容：{error_body}") from exc
        except requests.RequestException as exc:
            self.logger.exception("Soniox 请求发送失败")
            raise RuntimeError(f"Soniox 网络请求失败：{exc}") from exc
        finally:
            cleanup_timeout = min(timeout, 30)
            if transcription_id:
                try:
                    session.delete(f"{transcription_url}/{transcription_id}", timeout=cleanup_timeout)
                except Exception:
                    self.logger.warning("Soniox 转写清理失败：%s", transcription_id)
            if file_id:
                try:
                    session.delete(f"{base_url}/v1/files/{file_id}", timeout=cleanup_timeout)
                except Exception:
                    self.logger.warning("Soniox 文件清理失败：%s", file_id)
            session.close()

    def _render_soniox_tokens(self, tokens: List[dict]) -> str:
        """
        将 Soniox token 序列拼装成可读文本。
        """
        parts: List[str] = []
        current_speaker: Optional[str] = None
        current_language: Optional[str] = None
        for token in tokens:
            text = str(token.get("text") or "")
            if not text:
                continue
            speaker = token.get("speaker")
            language = token.get("language")
            if speaker is not None and speaker != current_speaker:
                if parts:
                    parts.append("\n\n")
                current_speaker = speaker
                current_language = None
                parts.append(f"说话人 {speaker}: ")
            if language and language != current_language:
                current_language = language
                parts.append(f"[{language}] ")
            parts.append(text)
        return "".join(parts)

    def _auto_paste_async(self) -> bool:
        """
        自动激活目标窗口并触发粘贴。

        返回：
            bool: True 表示成功触发自动粘贴，False 表示失败或已回退。
        """
        try:
            text_source = "缓存结果" if self.last_result_text else "剪贴板"
            text = self.last_result_text or self.clipboard.paste()
            if not text:
                raise subprocess.CalledProcessError(returncode=1, cmd="xdotool type --window ...")

            self.logger.info("自动粘贴文本来源：%s，字符数=%d", text_source, len(text))
            paste_wait_ms = max(0, int(self.config.get("paste_delay_ms", 200)))
            if self.input_injector and self.input_injector.can_use_uinput():
                self.logger.info("尝试通过 uinput 注入 Ctrl+V，等待 %d ms。", paste_wait_ms)
                if self.input_injector.inject_ctrl_v(wait_ms=paste_wait_ms):
                    self.logger.info("uinput 自动粘贴流程完成。")
                    return True
                self.logger.warning("uinput 自动粘贴失败，将回退至 xdotool。")

            if not self.target_window:
                self.logger.warning("无法自动粘贴：缺少目标窗口")
                self._update_status("缺少目标窗口，已复制文本，请手动粘贴。")
                return False

            delay_ms = max(1, int(self.config.get("type_delay_ms", 5)))
            self.logger.info("准备向窗口 %s (%s) 模拟键入，字符数=%d",
                             self.target_window, self._window_name(self.target_window), len(text))
            result = subprocess.run(
                [
                    "xdotool",
                    "type",
                    "--window",
                    self.target_window,
                    "--clearmodifiers",
                    "--delay",
                    str(delay_ms),
                    text
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if result.returncode == 0:
                self.logger.info("已向窗口 %s 模拟键入文本", self.target_window)
                return True

            raise subprocess.CalledProcessError(returncode=result.returncode, cmd="xdotool type")
        except FileNotFoundError:
            self.logger.exception("自动粘贴失败：未找到 xdotool")
            self._update_status("未找到 xdotool，无法自动粘贴，请手动处理。")
            return False
        except subprocess.CalledProcessError as exc:
            self.logger.exception("自动粘贴执行失败")
            self._update_status(f"自动粘贴失败：{exc}")
            return False
        except Exception:
            self.logger.exception("自动粘贴流程出现未知异常")
            self._update_status("自动粘贴失败，已复制文本，请手动粘贴。")
            return False

    def _refresh_result(self, text: str) -> None:
        """
        更新识别结果展示。
        """
        self.last_result_text = text
        self.root.after(0, self.result_var.set, text)

    def _update_status(self, message: str) -> None:
        """
        在线程安全的前提下更新状态提示。
        """
        self.logger.info("状态更新：%s", message)
        self.root.after(0, self.status_var.set, message)

    def _update_floating_controls_state(self) -> None:
        """
        根据启用状态更新浮动按钮控制。
        """
        state = tk.NORMAL if self.floating_enabled_var.get() else tk.DISABLED
        if hasattr(self, "floating_size_scale"):
            self.floating_size_scale.configure(state=state)

    def _toggle_floating_button(self) -> None:
        """
        打开或关闭浮动录音按钮，并保存配置。
        """
        enabled = bool(self.floating_enabled_var.get())
        if enabled:
            self._create_floating_button()
        else:
            self._destroy_floating_button()
        self._update_floating_controls_state()
        self.config["floating_button_enabled"] = enabled
        self.config["floating_button_size"] = int(self.floating_size_var.get())
        try:
            save_config(self.config)
        except Exception:
            self.logger.exception("保存浮动按钮配置失败")

    def _on_floating_size_change(self, value: str) -> None:
        """
        调整浮动按钮大小。
        """
        try:
            size = int(float(value))
        except (TypeError, ValueError):
            return
        size = max(50, min(200, size))
        if self.floating_size_var.get() != size:
            self.floating_size_var.set(size)
        if self.floating_button:
            self.floating_button.update_size(size)
        self.config["floating_button_size"] = size
        try:
            save_config(self.config)
        except Exception:
            self.logger.exception("保存浮动按钮大小失败")

    def _create_floating_button(self) -> None:
        """
        创建浮动录音按钮。
        """
        size = int(self.floating_size_var.get())
        if self.floating_button:
            self.floating_button.update_size(size)
            self.floating_button.set_state(self._floating_state)
            return
        try:
            self.floating_button = FloatingButton(self, size)
            self.floating_button.top.update_idletasks()
            self._register_window(self.floating_button.top)
            self.floating_button.set_state(self._floating_state)
            self.logger.info("启用浮动录音按钮，尺寸：%d", size)
        except Exception as exc:
            self.logger.exception("创建浮动录音按钮失败")
            messagebox.showwarning(
                "浮动按钮不可用",
                f"创建浮动录音按钮失败：{exc}"
            )
            self.floating_enabled_var.set(False)
            self.floating_button = None
            self._update_floating_controls_state()

    def _destroy_floating_button(self) -> None:
        """
        销毁浮动录音按钮。
        """
        if self.floating_button:
            self._unregister_window(self.floating_button.top)
            self.floating_button.destroy()
            self.floating_button = None
            self.logger.info("已关闭浮动录音按钮。")

    def _schedule_floating_state(self, state: str) -> None:
        """
        异步更新浮动按钮状态。
        """
        self.root.after(0, self._apply_floating_state, state)

    def _apply_floating_state(self, state: str) -> None:
        """
        实际更新浮动按钮状态。
        """
        self._floating_state = state
        if self.floating_button:
            self.floating_button.set_state(state)
        self.logger.debug("浮动按钮状态更新为：%s", state)

    def _register_window(self, widget: tk.Widget) -> None:
        """
        将 Tk 组件对应的窗口加入内部窗口集合。
        """
        wid = self._widget_window_id(widget)
        if wid:
            self._own_windows.add(wid)
            self.logger.debug("注册内部窗口 ID：%s", wid)

    def _unregister_window(self, widget: tk.Widget) -> None:
        """
        从内部窗口集合移除指定窗口。
        """
        wid = self._widget_window_id(widget)
        if wid and wid in self._own_windows:
            self._own_windows.discard(wid)
            self.logger.debug("注销内部窗口 ID：%s", wid)

    @staticmethod
    def _widget_window_id(widget: tk.Widget) -> Optional[str]:
        """
        获取 Tk 组件的窗口 ID。
        """
        try:
            return str(widget.winfo_id())
        except Exception:
            return None

    @staticmethod
    def _window_name(window_id: Optional[str]) -> str:
        """
        获取窗口标题，便于调试。
        """
        if not window_id:
            return ""
        try:
            result = subprocess.run(
                ["xdotool", "getwindowname", window_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.5
            )
            name = result.stdout.strip()
            return name or ""
        except Exception:  # pragma: no cover
            return ""

    def prime_external_window(self) -> None:
        """
        在焦点切换前记录当前外部窗口 ID。
        """
        if self.input_injector and self.input_injector.can_use_uinput():
            # Wayland 下依赖 uinput，无需记录窗口 ID。
            self.logger.debug("已启用 uinput 自动粘贴，跳过窗口记录。")
            self.target_window = None
            return

        window_id = current_active_window()
        if not window_id:
            self.logger.debug("未能获取当前窗口，可能未安装 xdotool。")
            return
        if window_id in self._own_windows:
            self.logger.debug("当前窗口属于 LexiSharp，自行忽略：%s", window_id)
            return
        if window_id == self._last_external_window:
            self.logger.debug("外部窗口保持不变：%s", window_id)
        else:
            self.logger.info("记录外部窗口：%s (%s)", window_id, self._window_name(window_id))
        self.target_window = window_id
        self._last_external_window = window_id

    def _on_main_button_press(self, _event) -> None:
        """
        主窗口录音按钮按下时记录外部窗口。
        """
        self.prime_external_window()

    def _init_hotkeys(self) -> None:
        """
        初始化全局快捷键。
        """
        if not self.start_hotkey and not self.stop_hotkey:
            self.logger.info("未配置全局快捷键，跳过初始化。")
            return
        try:
            self.hotkey_manager = GlobalHotkeyManager(
                self.start_hotkey,
                self.stop_hotkey,
                self._on_hotkey_start,
                self._on_hotkey_stop,
                self.logger
            )
        except RuntimeError as exc:
            self.logger.exception("全局快捷键初始化失败")
            messagebox.showwarning(
                "全局快捷键不可用",
                f"全局快捷键初始化失败：{exc}"
            )
            self.hotkey_manager = None

    def _on_hotkey_start(self) -> None:
        """
        全局快捷键触发开始录音。
        """
        self.logger.info("收到开始录音快捷键信号。")
        self.root.after(0, self._start_from_hotkey)

    def _on_hotkey_stop(self) -> None:
        """
        全局快捷键触发结束录音。
        """
        self.logger.info("收到结束录音快捷键信号。")
        self.root.after(0, self._stop_from_hotkey)

    def _start_from_hotkey(self) -> None:
        """
        热键触发的开始录音逻辑。
        """
        if self.processing or self.recorder.is_running():
            self.logger.info(
                "忽略开始录音快捷键：processing=%s, is_running=%s",
                self.processing,
                self.recorder.is_running()
            )
            return
        self.prime_external_window()
        self.start_recording()

    def _stop_from_hotkey(self) -> None:
        """
        热键触发的结束录音逻辑。
        """
        if not self.recorder.is_running():
            self.logger.info("忽略结束录音快捷键：录音尚未开始。")
            return
        self.stop_recording()

    def _schedule_level_update(self) -> None:
        """
        定时刷新音量指示条。
        """
        level = 0.0
        try:
            level = max(0.0, min(self.recorder.current_level(), 1.0))
        except Exception:
            level = 0.0
        self.level_var.set(level * 100)
        self.root.after(100, self._schedule_level_update)

    def _on_close(self) -> None:
        """
        释放资源并关闭窗口。
        """
        self.logger.info("准备关闭应用。")
        if self.hotkey_manager:
            self.hotkey_manager.stop()
            self.hotkey_manager = None

        self._destroy_floating_button()

        if self.input_injector:
            self.input_injector.close()

        if self.recorder.is_running():
            path = self.recorder.stop()
            if path and Path(path).exists():
                try:
                    os.remove(path)
                    self.logger.info("关闭时清理音频文件：%s", path)
                except OSError:
                    self.logger.exception("关闭时删除音频文件失败：%s", path)

        self.root.destroy()


class SettingsDialog:
    """
    配置设置对话框，支持渠道切换与字段编辑。
    """

    CHANNEL_OPTIONS = [
        ("volcengine", "火山引擎（Volcengine）"),
        ("soniox", "Soniox"),
    ]

    CHANNEL_FIELDS: dict[str, list[dict[str, object]]] = {
        "volcengine": [
            {
                "key": "app_key",
                "label": "App ID",
                "type": "entry",
                "help": "必填：火山引擎控制台生成的 App ID。",
            },
            {
                "key": "access_key",
                "label": "Access Key",
                "type": "entry",
                "help": "必填：火山引擎控制台生成的 Access Key。",
            },
            {
                "key": "resource_id",
                "label": "Resource ID",
                "type": "entry",
                "help": "资源标识，若官方有更新可在此调整。",
            },
            {
                "key": "api_url",
                "label": "API 地址",
                "type": "entry",
                "help": "ASR 请求地址，通常保持默认即可。",
            },
            {
                "key": "model_name",
                "label": "模型名称",
                "type": "entry",
                "help": "模型标识，例如 bigmodel。",
            },
        ],
        "soniox": [
            {
                "key": "soniox_api_key",
                "label": "API Key",
                "type": "entry",
                "help": "必填：Soniox 控制台生成的 API Key。",
            },
            {
                "key": "soniox_api_base",
                "label": "API 基础地址",
                "type": "entry",
                "help": "默认 https://api.soniox.com，如需代理可在此调整。",
            },
            {
                "key": "soniox_model",
                "label": "模型（Model）",
                "type": "entry",
                "help": "例如 stt-async-preview，可参考官方文档。",
            },
            {
                "key": "soniox_language_hints",
                "label": "语言提示（逗号分隔）",
                "type": "entry",
                "list": True,
                "help": "可选：例如 zh,en，有助于提升识别准确度。",
            },
            {
                "key": "soniox_enable_speaker_diarization",
                "label": "启用说话人分离",
                "type": "boolean",
            },
            {
                "key": "soniox_enable_language_identification",
                "label": "启用语言识别",
                "type": "boolean",
            },
            {
                "key": "soniox_context",
                "label": "上下文提示",
                "type": "text",
                "height": 5,
                "help": "可选：输入行业词汇或短语，增强识别效果（≤10K 字符）。",
            },
            {
                "key": "soniox_poll_interval_s",
                "label": "轮询间隔（秒）",
                "type": "entry",
                "value_type": float,
                "default": CONFIG_TEMPLATE["soniox_poll_interval_s"],
            },
            {
                "key": "soniox_poll_timeout_s",
                "label": "超时时间（秒）",
                "type": "entry",
                "value_type": float,
                "default": CONFIG_TEMPLATE["soniox_poll_timeout_s"],
            },
        ],
    }

    def __init__(self, app: LexiSharpApp):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title("配置设置")
        self.window.geometry("520x560")
        self.window.minsize(520, 460)
        self.window.resizable(False, False)
        self.window.transient(app.root)
        self.window.grab_set()
        self.window.focus_force()
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.label_map = {key: label for key, label in self.CHANNEL_OPTIONS}
        self.value_map = {label: key for key, label in self.CHANNEL_OPTIONS}

        current_channel = (app.config.get("channel") or "volcengine").strip().lower()
        if current_channel not in self.label_map:
            current_channel = "volcengine"

        top_frame = tk.Frame(self.window, padx=18, pady=16)
        top_frame.pack(fill=tk.X)

        channel_label = tk.Label(
            top_frame,
            text="识别渠道",
            font=("WenQuanYi Micro Hei", 12, "bold")
        )
        channel_label.pack(anchor=tk.W)

        self.channel_var = tk.StringVar(value=self.label_map[current_channel])
        self.channel_combobox = ttk.Combobox(
            top_frame,
            textvariable=self.channel_var,
            state="readonly",
            values=[label for _, label in self.CHANNEL_OPTIONS],
            width=28
        )
        self.channel_combobox.pack(anchor=tk.W, pady=(4, 10))
        self.channel_combobox.bind("<<ComboboxSelected>>", self._on_channel_change)

        ttk.Separator(self.window, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=18)

        scroll_container = tk.Frame(self.window)
        scroll_container.pack(fill=tk.BOTH, expand=True, padx=18, pady=(12, 0))

        self.canvas = tk.Canvas(
            scroll_container,
            borderwidth=0,
            highlightthickness=0,
            width=0,
            height=0
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(scroll_container, orient=tk.VERTICAL, command=self.canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.channel_frame = tk.Frame(self.canvas)
        self._canvas_window = self.canvas.create_window((0, 0), window=self.channel_frame, anchor="nw")
        self.channel_frame.bind("<Configure>", lambda _event: self._update_scroll_region())
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)  # Linux scroll up
        self.canvas.bind("<Button-5>", self._on_mousewheel)  # Linux scroll down

        button_frame = tk.Frame(self.window, padx=18, pady=12)
        button_frame.pack(fill=tk.X, pady=(0, 12))

        save_button = tk.Button(
            button_frame,
            text="保存",
            width=10,
            command=self._save
        )
        save_button.pack(side=tk.RIGHT, padx=(6, 0))

        cancel_button = tk.Button(
            button_frame,
            text="取消",
            width=10,
            command=self.close
        )
        cancel_button.pack(side=tk.RIGHT)

        self.field_widgets: dict[str, dict[str, object]] = {}

        try:
            self.app._register_window(self.window)
        except Exception:
            self.app.logger.exception("注册设置窗口失败")

        self._render_channel_fields(current_channel)

    def _on_channel_change(self, _event=None) -> None:
        channel_key = self._get_selected_channel_key()
        self._render_channel_fields(channel_key)

    def _get_selected_channel_key(self) -> str:
        label = self.channel_var.get()
        return self.value_map.get(label, "volcengine")

    def _render_channel_fields(self, channel: str) -> None:
        for child in self.channel_frame.winfo_children():
            child.destroy()
        self.field_widgets.clear()

        fields = self.CHANNEL_FIELDS.get(channel, [])
        if not fields:
            empty_label = tk.Label(
                self.channel_frame,
                text="当前渠道暂无可配置项。",
                font=("WenQuanYi Micro Hei", 11),
                fg="#666666"
            )
            empty_label.pack(anchor=tk.W)
            return

        for field in fields:
            label = tk.Label(
                self.channel_frame,
                text=field["label"],
                font=("WenQuanYi Micro Hei", 12)
            )
            label.pack(anchor=tk.W, pady=(0, 2))

            key = field["key"]  # type: ignore[index]
            current_value = self._get_config_value(key, field)
            entry_type = field.get("type")

            if entry_type == "entry":
                entry = tk.Entry(self.channel_frame, width=38)
                if isinstance(current_value, list):
                    entry.insert(0, ", ".join(current_value))
                elif current_value not in (None, ""):
                    entry.insert(0, str(current_value))
                entry.pack(anchor=tk.W, pady=(0, 6))
                self.field_widgets[key] = {"widget": entry, "field": field}
            elif entry_type == "boolean":
                var = tk.BooleanVar(value=bool(current_value))
                checkbox = tk.Checkbutton(
                    self.channel_frame,
                    text="启用",
                    variable=var,
                    font=("WenQuanYi Micro Hei", 11)
                )
                checkbox.pack(anchor=tk.W, pady=(0, 6))
                self.field_widgets[key] = {"widget": checkbox, "variable": var, "field": field}
            elif entry_type == "text":
                height = field.get("height", 4)
                text_widget = tk.Text(self.channel_frame, width=42, height=int(height))
                text_widget.insert("1.0", str(current_value or ""))
                text_widget.pack(anchor=tk.W, pady=(0, 6))
                self.field_widgets[key] = {"widget": text_widget, "field": field}
            else:
                placeholder = tk.Label(
                    self.channel_frame,
                    text="暂不支持的字段类型",
                    fg="red"
                )
                placeholder.pack(anchor=tk.W, pady=(0, 6))
                continue

            help_text = field.get("help")
            if help_text:
                help_label = tk.Label(
                    self.channel_frame,
                    text=str(help_text),
                    font=("WenQuanYi Micro Hei", 10),
                    fg="#666666",
                    wraplength=360,
                    justify=tk.LEFT
                )
                help_label.pack(anchor=tk.W, pady=(0, 8))

        self._update_scroll_region()

    def _get_config_value(self, key: str, field: dict[str, object]):
        if key in self.app.config:
            value = self.app.config[key]
            if field.get("list"):
                if isinstance(value, list):
                    return value
                if isinstance(value, str):
                    return [item.strip() for item in value.split(",") if item.strip()]
            return value
        return field.get("default", CONFIG_TEMPLATE.get(key))

    def _save(self) -> None:
        channel_key = self._get_selected_channel_key()
        updates: dict[str, object] = {"channel": channel_key}
        fields = self.CHANNEL_FIELDS.get(channel_key, [])

        for field in fields:
            key = field["key"]  # type: ignore[index]
            stored = self.field_widgets.get(key)
            if not stored:
                continue
            widget = stored.get("widget")
            entry_type = field.get("type")

            if entry_type == "entry":
                value_str = widget.get().strip() if isinstance(widget, tk.Entry) else ""
                if field.get("list"):
                    value = [item.strip() for item in value_str.split(",") if item.strip()]
                elif field.get("value_type") is float:
                    if not value_str:
                        value_str = str(field.get("default", CONFIG_TEMPLATE.get(key, 0.0)))
                    try:
                        value = float(value_str)
                    except ValueError:
                        messagebox.showerror("设置", f"{field['label']} 需要填写数字。")  # type: ignore[index]
                        if isinstance(widget, tk.Entry):
                            widget.focus_set()
                        return
                else:
                    value = value_str
                updates[key] = value
            elif entry_type == "boolean":
                var = stored.get("variable")
                if isinstance(var, tk.BooleanVar):
                    updates[key] = bool(var.get())
            elif entry_type == "text":
                if isinstance(widget, tk.Text):
                    updates[key] = widget.get("1.0", tk.END).strip()

        candidate_config = dict(self.app.config)
        candidate_config.update(updates)
        missing_messages, _ = self.app._collect_missing_fields(candidate_config)
        if missing_messages:
            detail = "、".join(missing_messages)
            messagebox.showwarning("设置", f"保存失败：以下字段仍需填写：{detail}。")
            return

        try:
            self.app.config.update(updates)
            if not self.app._validate_keys(trigger_prompt=False):
                messagebox.showwarning("设置", "仍有必填项缺失，请继续完善。")
                return
            save_config(self.app.config)
            self.app.logger.info("配置已通过设置窗口更新：%s", updates.keys())
            provider_name = self.app._channel_display_name()
            self.app.status_var.set(f"配置已保存，当前识别渠道：{provider_name}。")
            messagebox.showinfo("设置", "配置已保存。")
            self.close()
        except Exception as exc:
            self.app.logger.exception("保存配置失败")
            messagebox.showerror("设置", f"保存失败：{exc}")

    def close(self) -> None:
        try:
            self.window.grab_release()
        except Exception:
            pass
        try:
            self.app._unregister_window(self.window)
        except Exception:
            pass
        if self.window.winfo_exists():
            self.window.destroy()
        self.app._on_settings_closed()

    def _on_canvas_configure(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        try:
            self.canvas.itemconfigure(self._canvas_window, width=event.width)
        except Exception:
            pass
        self._update_scroll_region()

    def _update_scroll_region(self) -> None:
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception:
            pass

    def _on_mousewheel(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        try:
            delta = 0
            if hasattr(event, "delta") and event.delta:
                delta = -1 if event.delta > 0 else 1
            else:
                if getattr(event, "num", None) == 4:
                    delta = -1
                elif getattr(event, "num", None) == 5:
                    delta = 1
            if delta:
                self.canvas.yview_scroll(delta, "units")
        except Exception:
            pass


def main() -> None:
    """
    应用入口。
    """
    config = ensure_config()
    logger = setup_logging(config.get("log_level", "INFO"))
    logger.info("LexiSharp-linux 启动，配置路径：%s", CONFIG_PATH)

    root = tk.Tk()
    app = LexiSharpApp(root, config, logger)
    root.mainloop()


if __name__ == "__main__":
    main()
