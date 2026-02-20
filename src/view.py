import logging
import os
import subprocess
import tkinter as tk
from queue import Queue
from tkinter import END, BooleanVar, StringVar, messagebox

import customtkinter as ctk
import windnd

from src import meta, utils
from src.controller import Controller
from src.model import message
from src.service.config import ConfigService
from src.service.message import MessageService
from src.service.video import VideoService

# NOTE: Preset 技术参数到用户友好滑块值的映射表
# 索引 0 = 最慢最优画质，索引 8 = 最快
PRESET_LIST = [
    "veryslow",
    "slower",
    "slow",
    "medium",
    "fast",
    "faster",
    "veryfast",
    "superfast",
    "ultrafast",
]

# NOTE: 速度滑块上的用户友好标签
SPEED_LABELS = {
    0: "极慢(最佳)",
    2: "慢",
    4: "快",
    6: "很快",
    8: "极快",
}


def _detect_gpu_acceleration() -> bool:
    """
    自动检测系统是否支持 GPU 硬件加速

    通过查询 FFmpeg 的 hwaccel 列表，判断是否有可用的 GPU 加速方案。
    支持 CUDA(NVIDIA)、QSV(Intel)、D3D11VA/DXVA2(通用 Windows) 等。

    Returns:
        bool: 如果检测到可用的 GPU 加速则返回 True
    """
    try:
        ffmpeg_path = meta.FFMPEG_PATH
        result = subprocess.run(
            [ffmpeg_path, "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = result.stdout.lower()
        # NOTE: 检测常见 GPU 加速方案，任一可用即返回 True
        gpu_methods = ["cuda", "qsv", "d3d11va", "dxva2", "opencl", "vulkan"]
        has_gpu = any(method in output for method in gpu_methods)
        logging.info(f"GPU 加速检测: {'可用' if has_gpu else '不可用'} (hwaccels: {output.strip()})")
        return has_gpu
    except Exception as e:
        logging.warning(f"GPU 加速检测失败: {e}")
        return False



class View:
    """
    VideoSlim 应用程序的主视图类（customtkinter 现代 UI 版本）

    使用 customtkinter 框架构建深色主题界面，提供画质/速度滑块、
    预设配置选择、拖拽文件、进度显示等功能。
    """

    def __init__(self, root: tk.Tk, controller: Controller):
        """
        初始化 VideoSlim 应用程序视图

        Args:
            root: Tkinter 根窗口对象
            controller: 控制器对象，用于处理业务逻辑
        """
        self.root = root
        self.controller = controller
        self.queue = Queue()
        self.configs_name_list: list[str] = []
        # NOTE: 标记用户是否手动调节了滑块，防止预设选择时的无限递归
        self._slider_updating = False
        # NOTE: GPU 检测结果缓存，预设切换时不应覆盖此值
        self._gpu_available = _detect_gpu_acceleration()
        # NOTE: 窗口拖拽用绝对坐标缓存，避免每帧查询 winfo 导致闪烁
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._window_start_x = 0
        self._window_start_y = 0

        self._setup_ui()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # NOTE: 消息队列轮询间隔 50ms，保持 UI 响应性
        self.root.after(50, self._check_message_queue)

    def _setup_ui(self):
        """
        设置应用程序的用户界面

        创建深色主题的现代 UI，包含拖拽区域、配置面板、进度条、操作按钮等。
        使用 grid 布局实现响应式界面。
        """
        # 全局主题设置
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root.title(f"VideoSlim 视频压缩 {meta.VERSION}")
        # NOTE: 去掉系统默认窗口边框，使用自定义圆角窗口
        self.root.overrideredirect(True)

        # 设置图标
        icon_path = utils.get_path("./tools/icon.ico")
        if os.path.exists(icon_path):
            self.root.iconbitmap(icon_path)

        # 窗口居中
        window_width, window_height = 640, 710
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        position_x = (screen_width - window_width) // 2
        position_y = (screen_height - window_height) // 2
        self.root.geometry(
            f"{window_width}x{window_height}+{position_x}+{position_y}"
        )

        # NOTE: 圆角窗口核心技术：
        # 1. 设置窗口背景为特殊透明色
        # 2. 使该颜色完全透明（看到桌面）
        # 3. 在其上放置带 corner_radius 的 CTkFrame
        # 4. CTkFrame 圆角外的区域就是透明色，视觉上就是圆角窗口
        TRANSPARENT_COLOR = "#000001"
        self.root.configure(bg=TRANSPARENT_COLOR)
        self.root.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
        # NOTE: alpha=0.99 启用合成器双缓冲，减少拖拽闪烁
        self.root.wm_attributes("-alpha", 0.99)

        # ═══ 圆角外壳（整个窗口的可见区域） ═══
        outer_frame = ctk.CTkFrame(
            self.root,
            fg_color="#0f0f23",
            corner_radius=16,
            border_width=1,
            border_color="#2a2a4a",
        )
        outer_frame.pack(fill="both", expand=True, padx=2, pady=2)

        # ═══ 自定义标题栏（在圆角外壳内部顶部） ═══
        titlebar = ctk.CTkFrame(
            outer_frame, fg_color="transparent", height=42
        )
        titlebar.pack(fill="x", padx=4, pady=(4, 0))
        titlebar.pack_propagate(False)

        # 标题栏图标和文字
        ctk.CTkLabel(
            titlebar,
            text=f"  🎬 VideoSlim {meta.VERSION}",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#8899bb",
        ).pack(side="left", padx=(8, 0))

        # 关闭按钮
        close_btn = ctk.CTkButton(
            titlebar,
            text="✕",
            width=36,
            height=28,
            font=ctk.CTkFont(size=13),
            fg_color="transparent",
            hover_color="#e74c3c",
            text_color="#8899bb",
            corner_radius=6,
            command=self._on_close,
        )
        close_btn.pack(side="right", padx=(0, 4))

        # 最小化按钮
        min_btn = ctk.CTkButton(
            titlebar,
            text="─",
            width=36,
            height=28,
            font=ctk.CTkFont(size=13),
            fg_color="transparent",
            hover_color="#2d2d4a",
            text_color="#8899bb",
            corner_radius=6,
            command=self._minimize_window,
        )
        min_btn.pack(side="right", padx=(0, 2))

        # NOTE: 拖拽事件绑定到标题栏
        titlebar.bind("<Button-1>", self._on_titlebar_press)
        titlebar.bind("<B1-Motion>", self._on_titlebar_drag)

        # ═══ 内容区域（在圆角外壳内部，带圆角底部） ═══
        main_frame = ctk.CTkFrame(
            outer_frame, fg_color="#1a1a2e", corner_radius=12
        )
        main_frame.pack(fill="both", expand=True, padx=4, pady=(4, 4))

        # ═══ 拖拽区域（文件列表） ═══
        drop_frame = ctk.CTkFrame(
            main_frame, fg_color="#16213e", corner_radius=12, border_width=2,
            border_color="#0f3460"
        )
        drop_frame.pack(fill="x", pady=(0, 10))

        self.title_var = StringVar()
        self.title_var.set("📂 将视频文件或文件夹拖拽到下方区域")
        self.title_label = ctk.CTkLabel(
            drop_frame,
            textvariable=self.title_var,
            font=ctk.CTkFont(size=13),
            text_color="#8899bb",
            anchor="w",
        )
        self.title_label.pack(fill="x", padx=12, pady=(10, 4))

        self.text_box = ctk.CTkTextbox(
            drop_frame,
            height=140,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color="#0d1b2a",
            text_color="#c8d6e5",
            border_width=0,
            corner_radius=8,
        )
        self.text_box.pack(fill="x", padx=12, pady=(0, 10))

        # 拖拽功能
        windnd.hook_dropfiles(self.root, func=self._on_drop_files)

        # ═══ 压缩配置区域 ═══
        config_frame = ctk.CTkFrame(
            main_frame, fg_color="#16213e", corner_radius=12, border_width=1,
            border_color="#0f3460"
        )
        config_frame.pack(fill="x", pady=(0, 10))

        config_title = ctk.CTkLabel(
            config_frame,
            text="⚙️ 压缩配置",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#e0e0ff",
            anchor="w",
        )
        config_title.pack(fill="x", padx=14, pady=(10, 6))

        # 配置方案选择行
        preset_row = ctk.CTkFrame(config_frame, fg_color="transparent")
        preset_row.pack(fill="x", padx=14, pady=(0, 8))

        ctk.CTkLabel(
            preset_row,
            text="配置方案",
            font=ctk.CTkFont(size=13),
            text_color="#8899bb",
            width=70,
        ).pack(side="left")

        self.select_config_name = StringVar(self.root, value="常规默认")
        self.config_combobox = ctk.CTkComboBox(
            preset_row,
            values=[],
            variable=self.select_config_name,
            width=200,
            height=32,
            font=ctk.CTkFont(size=13),
            dropdown_font=ctk.CTkFont(size=13),
            fg_color="#0d1b2a",
            border_color="#0f3460",
            button_color="#0f3460",
            button_hover_color="#1a5276",
            dropdown_fg_color="#16213e",
            dropdown_hover_color="#1a5276",
            state="readonly",
            command=self._on_preset_changed,
        )
        self.config_combobox.pack(side="left", padx=(8, 0))

        # ── 画质滑块 (CRF) ──
        quality_frame = ctk.CTkFrame(config_frame, fg_color="transparent")
        quality_frame.pack(fill="x", padx=14, pady=(0, 4))

        ctk.CTkLabel(
            quality_frame,
            text="画质",
            font=ctk.CTkFont(size=13),
            text_color="#8899bb",
            width=70,
        ).pack(side="left")

        # NOTE: CRF 范围 0-51，滑块反向：左边（低 CRF）= 高画质
        self.quality_slider = ctk.CTkSlider(
            quality_frame,
            from_=1,
            to=51,
            number_of_steps=50,
            width=300,
            height=18,
            progress_color="#4361ee",
            button_color="#7b83eb",
            button_hover_color="#9ba1f5",
            fg_color="#2d2d4a",
            command=self._on_quality_slider_changed,
        )
        self.quality_slider.set(23.5)
        self.quality_slider.pack(side="left", padx=(8, 8))

        self.quality_value_label = ctk.CTkLabel(
            quality_frame,
            text="CRF 23.5",
            font=ctk.CTkFont(size=12),
            text_color="#7b83eb",
            width=75,
        )
        self.quality_value_label.pack(side="left")

        # 画质说明
        quality_hint_frame = ctk.CTkFrame(config_frame, fg_color="transparent")
        quality_hint_frame.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkLabel(
            quality_hint_frame,
            text="← 更高画质（体积更大）",
            font=ctk.CTkFont(size=10),
            text_color="#556688",
        ).pack(side="left", padx=(78, 0))
        ctk.CTkLabel(
            quality_hint_frame,
            text="更小体积（画质降低）→",
            font=ctk.CTkFont(size=10),
            text_color="#556688",
        ).pack(side="right", padx=(0, 50))

        # ── 速度滑块 (Preset) ──
        speed_frame = ctk.CTkFrame(config_frame, fg_color="transparent")
        speed_frame.pack(fill="x", padx=14, pady=(0, 4))

        ctk.CTkLabel(
            speed_frame,
            text="速度",
            font=ctk.CTkFont(size=13),
            text_color="#8899bb",
            width=70,
        ).pack(side="left")

        self.speed_slider = ctk.CTkSlider(
            speed_frame,
            from_=0,
            to=8,
            number_of_steps=8,
            width=300,
            height=18,
            progress_color="#06d6a0",
            button_color="#4ecdc4",
            button_hover_color="#7eddd6",
            fg_color="#2d2d4a",
            command=self._on_speed_slider_changed,
        )
        self.speed_slider.set(0)  # veryslow
        self.speed_slider.pack(side="left", padx=(8, 8))

        self.speed_value_label = ctk.CTkLabel(
            speed_frame,
            text="veryslow",
            font=ctk.CTkFont(size=12),
            text_color="#4ecdc4",
            width=75,
        )
        self.speed_value_label.pack(side="left")

        # 速度说明
        speed_hint_frame = ctk.CTkFrame(config_frame, fg_color="transparent")
        speed_hint_frame.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(
            speed_hint_frame,
            text="← 更慢（压缩效果更好）",
            font=ctk.CTkFont(size=10),
            text_color="#556688",
        ).pack(side="left", padx=(78, 0))
        ctk.CTkLabel(
            speed_hint_frame,
            text="更快（压缩效果稍差）→",
            font=ctk.CTkFont(size=10),
            text_color="#556688",
        ).pack(side="right", padx=(0, 50))

        # ═══ 高级选项 ═══
        options_frame = ctk.CTkFrame(
            main_frame, fg_color="#16213e", corner_radius=12, border_width=1,
            border_color="#0f3460"
        )
        options_frame.pack(fill="x", pady=(0, 10))

        options_title = ctk.CTkLabel(
            options_frame,
            text="🔧 高级选项",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#e0e0ff",
            anchor="w",
        )
        options_title.pack(fill="x", padx=14, pady=(10, 6))

        # NOTE: 所有 4 个选项放在同一行
        opts_row = ctk.CTkFrame(options_frame, fg_color="transparent")
        opts_row.pack(fill="x", padx=14, pady=(0, 10))

        self.recurse_var = BooleanVar(value=True)
        ctk.CTkCheckBox(
            opts_row,
            text="递归子文件夹",
            variable=self.recurse_var,
            font=ctk.CTkFont(size=12),
            text_color="#c8d6e5",
            fg_color="#4361ee",
            hover_color="#3251de",
            border_color="#4a4a6a",
            checkmark_color="#ffffff",
        ).pack(side="left", padx=(0, 12))

        self.delete_source_var = BooleanVar(value=True)
        ctk.CTkCheckBox(
            opts_row,
            text="删除源文件",
            variable=self.delete_source_var,
            font=ctk.CTkFont(size=12),
            text_color="#c8d6e5",
            fg_color="#4361ee",
            hover_color="#3251de",
            border_color="#4a4a6a",
            checkmark_color="#ffffff",
        ).pack(side="left", padx=(0, 12))

        self.delete_audio_var = BooleanVar(value=False)
        ctk.CTkCheckBox(
            opts_row,
            text="删除音频",
            variable=self.delete_audio_var,
            font=ctk.CTkFont(size=12),
            text_color="#c8d6e5",
            fg_color="#4361ee",
            hover_color="#3251de",
            border_color="#4a4a6a",
            checkmark_color="#ffffff",
        ).pack(side="left", padx=(0, 12))

        # NOTE: GPU 加速使用 __init__ 中缓存的检测结果，可用时默认开启
        self.gpu_var = BooleanVar(value=self._gpu_available)
        self.gpu_checkbox = ctk.CTkCheckBox(
            opts_row,
            text="GPU 加速" + (" ✓" if self._gpu_available else " (不可用)"),
            variable=self.gpu_var,
            font=ctk.CTkFont(size=12),
            text_color="#c8d6e5" if self._gpu_available else "#666688",
            fg_color="#06d6a0",
            hover_color="#05c090",
            border_color="#4a4a6a",
            checkmark_color="#ffffff",
        )
        self.gpu_checkbox.pack(side="left", padx=(0, 0))

        # ═══ 进度区域 ═══
        progress_frame = ctk.CTkFrame(
            main_frame, fg_color="#16213e", corner_radius=12, border_width=1,
            border_color="#0f3460"
        )
        progress_frame.pack(fill="x", pady=(0, 10))

        progress_title = ctk.CTkLabel(
            progress_frame,
            text="📊 进度",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#e0e0ff",
            anchor="w",
        )
        progress_title.pack(fill="x", padx=14, pady=(12, 8))

        # 当前文件进度
        cur_row = ctk.CTkFrame(progress_frame, fg_color="transparent")
        cur_row.pack(fill="x", padx=14, pady=(0, 6))

        ctk.CTkLabel(
            cur_row,
            text="当前文件",
            font=ctk.CTkFont(size=12),
            text_color="#8899bb",
            width=65,
        ).pack(side="left")

        self.cur_bar = ctk.CTkProgressBar(
            cur_row,
            width=400,
            height=14,
            progress_color="#4361ee",
            fg_color="#2d2d4a",
            corner_radius=7,
        )
        self.cur_bar.set(0)
        self.cur_bar.pack(side="left", padx=(8, 8))

        self.cur_percent_label = ctk.CTkLabel(
            cur_row,
            text="0%",
            font=ctk.CTkFont(size=12),
            text_color="#7b83eb",
            width=45,
        )
        self.cur_percent_label.pack(side="left")

        # 总进度
        total_row = ctk.CTkFrame(progress_frame, fg_color="transparent")
        total_row.pack(fill="x", padx=14, pady=(4, 14))

        ctk.CTkLabel(
            total_row,
            text="总 进 度",
            font=ctk.CTkFont(size=12),
            text_color="#8899bb",
            width=65,
        ).pack(side="left")

        self.total_bar = ctk.CTkProgressBar(
            total_row,
            width=400,
            height=14,
            progress_color="#06d6a0",
            fg_color="#2d2d4a",
            corner_radius=7,
        )
        self.total_bar.set(0)
        self.total_bar.pack(side="left", padx=(8, 8))

        self.total_percent_label = ctk.CTkLabel(
            total_row,
            text="0%",
            font=ctk.CTkFont(size=12),
            text_color="#4ecdc4",
            width=45,
        )
        self.total_percent_label.pack(side="left")

        # ═══ 底部按钮区 ═══
        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(0, 0))

        clear_btn = ctk.CTkButton(
            btn_frame,
            text="🗑️ 清空文件",
            width=140,
            height=42,
            font=ctk.CTkFont(size=14),
            fg_color="#2d2d4a",
            hover_color="#3d3d5a",
            border_width=1,
            border_color="#4a4a6a",
            command=self._clear_file_list,
        )
        clear_btn.pack(side="left", padx=(0, 20))

        self.compress_btn = ctk.CTkButton(
            btn_frame,
            text="🚀 开始压缩",
            width=200,
            height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#4361ee",
            hover_color="#3251de",
            command=self._start_compression,
        )
        self.compress_btn.pack(side="right")

    # ═══════════════════════════════════════
    # 滑块和预设联动逻辑
    # ═══════════════════════════════════════

    def _on_preset_changed(self, preset_name: str):
        """
        预设配置切换回调

        当用户从下拉框选择预设时，自动更新画质/速度滑块位置和 GPU 加速选项。
        使用 _slider_updating 标志防止滑块回调触发无限递归。
        """
        config = ConfigService.get_instance().get_config(preset_name)
        if config is None:
            return

        # NOTE: 设置标志位，阻止滑块 changed 回调中的预设切换逻辑
        self._slider_updating = True
        try:
            # 更新画质滑块
            self.quality_slider.set(config.x264.crf)
            self.quality_value_label.configure(
                text=f"CRF {config.x264.crf:.1f}"
            )

            # 更新速度滑块
            preset_value = config.x264.preset
            if preset_value in PRESET_LIST:
                idx = PRESET_LIST.index(preset_value)
                self.speed_slider.set(idx)
                self.speed_value_label.configure(text=preset_value)

            # NOTE: GPU 加速不跟随预设切换，保持自动检测值
            # 预设中的 opencl_acceleration 仅在压缩时生效
        finally:
            self._slider_updating = False

    def _on_quality_slider_changed(self, value: float):
        """
        画质滑块变化回调

        更新 CRF 值显示。如果不是由预设切换触发，则将配置方案标记为"自定义"。
        """
        crf = round(value, 1)
        self.quality_value_label.configure(text=f"CRF {crf:.1f}")

    def _on_speed_slider_changed(self, value: float):
        """
        速度滑块变化回调

        将滑块数值映射到 x264 preset 字符串并更新显示。
        """
        idx = int(round(value))
        if 0 <= idx < len(PRESET_LIST):
            preset_name = PRESET_LIST[idx]
            self.speed_value_label.configure(text=preset_name)

    # ═══════════════════════════════════════
    # 文件和生命周期事件
    # ═══════════════════════════════════════

    def _on_drop_files(self, file_paths):
        """
        处理拖拽到应用程序中的文件

        Args:
            file_paths: 拖拽的文件路径列表（bytes 类型，GBK 编码）
        """
        files = "\n".join(item.decode("gbk") for item in file_paths)
        self.text_box.insert(END, files + "\n")

    def _on_close(self):
        """
        处理应用程序关闭事件

        如果有正在处理的任务，弹出确认对话框。
        """
        if VideoService.get_instance().is_processing():
            response = messagebox.askyesno(
                "确认", "当前有正在处理的任务，是否关闭程序？"
            )
            if not response:
                return

        self.controller.close()

    def _minimize_window(self):
        """
        最小化无边框窗口

        NOTE: overrideredirect 窗口不能直接 iconify，
        需要先临时恢复边框再最小化，然后在恢复时重新去掉边框。
        """
        self.root.overrideredirect(False)
        self.root.iconify()
        # NOTE: 监听恢复事件，恢复时重新去掉系统边框
        self.root.bind("<Map>", self._on_window_restore)

    def _on_window_restore(self, event):
        """
        窗口从最小化恢复时的回调

        重新应用无边框模式。
        """
        self.root.unbind("<Map>")
        self.root.overrideredirect(True)

    def _on_titlebar_press(self, event):
        """
        记录拖拽起始位置

        NOTE: 使用 x_root/y_root 绝对屏幕坐标 + 窗口起始位置缓存，
        避免拖拽过程中每帧调用 winfo_x/winfo_y 导致的闪烁。
        """
        self._drag_start_x = event.x_root
        self._drag_start_y = event.y_root
        self._window_start_x = self.root.winfo_x()
        self._window_start_y = self.root.winfo_y()

    def _on_titlebar_drag(self, event):
        """根据鼠标移动拖拽窗口位置（使用绝对坐标差值，无闪烁）"""
        dx = event.x_root - self._drag_start_x
        dy = event.y_root - self._drag_start_y
        self.root.geometry(
            f"+{self._window_start_x + dx}+{self._window_start_y + dy}"
        )

    def _clear_file_list(self):
        """清空文件列表文本框"""
        self.text_box.delete("1.0", END)

    # ═══════════════════════════════════════
    # 消息队列处理
    # ═══════════════════════════════════════

    def _check_message_queue(self):
        """
        检查消息队列并处理接收到的消息

        定期检查消息队列，根据消息类型更新 UI 状态，
        包括警告、进度、完成等消息的处理。
        """
        while True:
            msg = MessageService.get_instance().try_receive_message()

            match msg:
                case None:
                    break
                case message.WarningMessage(title=t, message=m):
                    messagebox.showwarning(t, m)
                case message.UpdateMessage():
                    messagebox.showinfo("更新提示", "有新版本可用，请前往官网更新")
                case message.ErrorMessage(title=t, message=m):
                    messagebox.showerror(t, m)
                case message.ExitMessage():
                    self.root.destroy()
                case message.ConfigLoadMessage(config_names=config_names):
                    # 配置加载完成：更新下拉框选项并选中第一个
                    self.config_combobox.configure(values=config_names)
                    self.select_config_name.set(config_names[0])
                    # NOTE: 主动触发一次预设切换，使滑块同步到第一个配置的值
                    self._on_preset_changed(config_names[0])
                case message.CompressionStartMessage():
                    self.compress_btn.configure(state="disabled")
                    self.cur_bar.set(0)
                    self.cur_percent_label.configure(text="0%")
                    self.total_bar.set(0)
                    self.total_percent_label.configure(text="0%")
                case message.CompressionCurrentProgressMessage(
                    file_name=_, current=current, total=total
                ):
                    if total > 0:
                        progress = current / total
                        self.cur_bar.set(progress)
                        self.cur_percent_label.configure(
                            text=f"{progress * 100:.0f}%"
                        )
                case message.CompressionTotalProgressMessage(
                    current=current, total=total, file_name=file_name
                ):
                    if total > 0:
                        progress = current / total
                        self.total_bar.set(progress)
                        self.total_percent_label.configure(
                            text=f"{progress * 100:.0f}%"
                        )
                    # 更新标题栏显示当前处理状态
                    short_name = os.path.basename(file_name)
                    self.title_var.set(
                        f"⏳ [{current}/{total}] 正在处理: {short_name}"
                    )
                case message.CompressionErrorMessage(title=t, message=m):
                    messagebox.showerror(t, m)
                    self.compress_btn.configure(state="normal")
                case message.CompressionFinishedMessage(total=total):
                    messagebox.showinfo("完成", f"✅ 压缩完成！共处理 {total} 个文件")
                    self.title_var.set(
                        f"✅ 处理完成！已处理 {total} 个文件"
                    )
                    self.compress_btn.configure(state="normal")
                    self.cur_bar.set(0)
                    self.cur_percent_label.configure(text="0%")
                    self.total_bar.set(1.0)
                    self.total_percent_label.configure(text="100%")
                case _:
                    continue

        # NOTE: 1 秒间隔轮询消息队列，避免过高 CPU 占用
        self.root.after(1000, self._check_message_queue)

    # ═══════════════════════════════════════
    # 压缩启动
    # ═══════════════════════════════════════

    def _start_compression(self):
        """
        启动视频压缩过程

        从 UI 收集用户设置（预设或自定义滑块值），验证文件列表，
        然后调用控制器开始视频压缩任务。

        NOTE: 当用户调节了滑块时，需要基于滑块值动态创建配置；
        当使用预设时直接传递预设名称。
        """
        config_name = self.select_config_name.get()
        delete_source = self.delete_source_var.get()
        delete_audio = self.delete_audio_var.get()
        recurse = self.recurse_var.get()

        # NOTE: 将 GPU 加速的 UI 选项同步到当前选中配置
        gpu_enabled = self.gpu_var.get()
        config = ConfigService.get_instance().get_config(config_name)
        if config is not None:
            config.x264.opencl_acceleration = gpu_enabled
            # 同步滑块值到配置（允许用户在预设基础上微调）
            config.x264.crf = round(self.quality_slider.get(), 1)
            speed_idx = int(round(self.speed_slider.get()))
            if 0 <= speed_idx < len(PRESET_LIST):
                config.x264.preset = PRESET_LIST[speed_idx]

        # 获取文件列表
        text_content = self.text_box.get("1.0", END)
        lines = [line for line in text_content.splitlines() if line.strip()]

        if not lines:
            messagebox.showwarning("提示", "请先拖拽视频文件到窗口")
            return

        # 禁用按钮防止重复点击
        self.compress_btn.configure(state="disabled")

        self.controller.compression(
            config_name, delete_audio, delete_source, lines, recurse
        )
