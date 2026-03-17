import json
import os
import sys
from pathlib import Path
import subprocess
from PyQt5.QtWidgets import (
    QLabel, QPushButton, QLineEdit, QTextEdit, QListWidget,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QSlider, QComboBox, QCheckBox, QRadioButton,
    QCalendarWidget, QTabWidget, QWidget, QVBoxLayout, QGroupBox
)
from PyQt5.QtCore import Qt

class SubtitleExtractorApp:
    def __init__(self):
        # 初始化总表格
        self.font_name_registry = {}  # { "字体文件名": {nameID: {platformID: string, ...}, ... } }
        self.files = []  # 存储(全路径, 文件名)
        self.original_files = []  # 用来存储原始文件名，便于还原
        self.renamed_files = []  # 用来保存重命名后的文件与原始文件的映射

        # 文件列表字段顺序
        self.file_fields = ["fullpath", "filename", "checked", "width", "height", "fps", "probe_info"]

        # 定义源 codec 与目标字幕文件格式对应关系
        self.codec_to_subfmt = {
            'ass': 'ass',  # Advanced SubStation Alpha
            'ssa': 'ass',  # SubStation Alpha (同 ASS)
            'subrip': 'srt',  # SubRip
            'webvtt': 'vtt',  # WebVTT
            'dvd_subtitle': 'sub',  # VOBSUB / DVD 字幕
            'microdvd': 'sub',  # MicroDVD 字幕
            'hdmv_pgs_subtitle': 'sup',  # Blu-ray PGS
            'mov_text': 'srt'  # MP4 内嵌字幕，导出为 SRT
        }

        # ==============================
        # 兼容 PyCharm + 打包后两种情况
        # ==============================
        if getattr(sys, "frozen", False):
            # 打包后的 exe 运行环境
            self.base_dir = Path(sys.executable).parent
        else:
            # 源码运行时（PyCharm、命令行）
            self.base_dir = Path(__file__).parent
    def run_silently(self, cmd, **kwargs):
        """静默运行命令行指令并在失败时抛出异常"""
        if os.name == 'nt':  # Windows系统
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            kwargs['startupinfo'] = startupinfo

        kwargs.setdefault('stdout', subprocess.PIPE)
        kwargs.setdefault('stderr', subprocess.PIPE)
        kwargs.setdefault('stdin', subprocess.DEVNULL)

        result = subprocess.run(cmd, **kwargs)
        if result.returncode != 0:
            stderr = result.stderr.decode(errors='ignore').strip()
            raise RuntimeError(f"ffmpeg 执行失败 (code={result.returncode}):\n{stderr}")
        return result

    # ✅ 自动定位 ffmpeg.exe 或系统 ffmpeg
    def get_ffmpeg_exe(self):
        ffmpeg_exe = os.path.join(self.base_dir, "ffmpeg", "ffmpeg.exe")
        return ffmpeg_exe if os.path.exists(ffmpeg_exe) else "ffmpeg"

    # ✅ 自动定位 ffprobe.exe 或系统 ffprobe
    def get_ffprobe_exe(self):
        ffprobe_exe = os.path.join(self.base_dir, "ffmpeg", "ffprobe.exe")
        return ffprobe_exe if os.path.exists(ffprobe_exe) else "ffprobe"

    # ✅ 替代 ffmpeg.run()
    def silent_ffmpeg_run(self, args, **kwargs):
        """
        使用 subprocess 直接调用 ffmpeg，静默执行。
        参数 args 为 ffmpeg 参数列表（不含可执行路径）。
        """
        ffmpeg_exe = self.get_ffmpeg_exe()
        cmd = [ffmpeg_exe] + args
        result = self.run_silently(cmd, **kwargs)

        if result.returncode != 0:
            raise RuntimeError(
                f"❌ ffmpeg 执行失败 (code={result.returncode})\n{result.stderr.decode(errors='ignore')}"
            )
        return result

    # ✅ 替代 ffmpeg.probe()
    def silent_ffmpeg_probe(self, filename, **kwargs):
        """
        使用 subprocess 调用 ffprobe 获取视频信息。
        """
        ffprobe_exe = self.get_ffprobe_exe()
        cmd = [
            ffprobe_exe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            filename
        ]

        result = self.run_silently(cmd, **kwargs)
        if result.returncode != 0:
            raise RuntimeError(
                f"❌ ffprobe 执行失败 (code={result.returncode})\n{result.stderr.decode(errors='ignore')}"
            )

        return json.loads(result.stdout.decode('utf-8', errors='ignore'))

def create_ui(main, canvas_frame, button_frame, scale):
    """
    Plugin UI entry point called by host program.

    :param main: the main window instance (has update_window_title, etc.)
    :param canvas_frame: widget for main plugin area (top)
    :param button_frame: widget for plugin assistant / edit area (bottom)
    :param scale: UI scale (float)
    """
    # 更新窗口标题
    main.kit.ui.update_window_title("插件：PyQt组件测试 / Plugin UI test")

    # -------------------------
    # 上方按钮栏内容
    # -------------------------
    btn_layout = QVBoxLayout()
    btn_layout.addWidget(QPushButton("按钮 1"))
    btn_layout.addWidget(QPushButton("按钮 2"))
    button_frame.setLayout(btn_layout)

    # -------------------------
    # 在 canvas_frame 中创建横向三列区域
    # -------------------------
    splitter, subframes = main.kit.ui.create_split_frame(
        orient="horizontal", n=5, sashwidth=3, handle_color="#d0d0d0", bg="#f0f0f0", sashrelief="line"
    )

    # 手动将 splitter 放入 canvas_frame，并占满
    canvas_layout = QVBoxLayout()
    canvas_layout.setContentsMargins(0, 0, 0, 0)  # 去掉边距
    canvas_layout.addWidget(splitter)
    canvas_frame.setLayout(canvas_layout)

    left_frame, center_frame, right_framem, tree_frame, tree_frame2= subframes

    # -------------------------
    # Left Column 内容
    # -------------------------
    left_layout = QVBoxLayout()
    left_layout.addWidget(QLabel("⬇ 输入区域 / Input area:"))
    left_layout.addWidget(QLineEdit("输入框 / QLineEdit"))
    left_layout.addWidget(QTextEdit("多行文本框 / QTextEdit"))

    left_layout.addWidget(QLabel("📋 列表 / QListWidget"))
    list_widget = QListWidget()
    list_widget.addItems(["条目 A", "Item B", "項目 C"])
    left_layout.addWidget(list_widget)

    left_layout.addWidget(QLabel("🌳 树控件 / TreeWidget"))
    tree = QTreeWidget()
    tree.setHeaderLabels(["Name", "Value"])
    root = QTreeWidgetItem(["Root", "1"])
    root.addChild(QTreeWidgetItem(["Child", "2"]))
    tree.addTopLevelItem(root)
    left_layout.addWidget(tree)

    left_frame.setLayout(left_layout)

    extra_cols = [
        (None, 200, True),
        ("宽度", 50, False),
        ("高度", 50, False),
        ("帧率", 50, False)
    ]

    tree_container, tree, tree_file = main.kit.ui.create_tree_view(tree_index=1, extra_columns=extra_cols)

    tree_layout = QVBoxLayout()
    tree_layout.setContentsMargins(5, 5, 5, 5)  # 去掉边距
    tree_layout.addWidget(tree_container)
    tree_frame.setLayout(tree_layout)

    extra_cols2 = [
        (None, 200, True),
        ("宽度", 50, False),
        ("高度", 50, False),
        ("帧率", 50, False)
    ]

    tree_container2, tree2, tree_file2 = main.kit.ui.create_tree_view(tree_index=2, extra_columns=extra_cols2)

    tree_layout2 = QVBoxLayout()
    tree_layout2.setContentsMargins(5, 5, 5, 5)  # 去掉边距
    tree_layout2.addWidget(tree_container2)
    tree_frame2.setLayout(tree_layout2)

    # -------------------------
    # Center Column 内容
    # -------------------------
    center_layout = QVBoxLayout()
    center_layout.addWidget(QLabel("📑 中间区域 / Center Area"))
    center_layout.addWidget(QTextEdit("这里可以放其他控件"))
    center_frame.setLayout(center_layout)

    # -------------------------
    # Right Column 内容
    # -------------------------
    right_layout = QVBoxLayout()

    group = QGroupBox("⚙️ 控件测试 / Controls")
    group_layout = QVBoxLayout()
    group_layout.addWidget(QCheckBox("开启功能"))
    group_layout.addWidget(QRadioButton("模式 A"))
    group_layout.addWidget(QRadioButton("模式 B"))
    combo = QComboBox()
    combo.addItems(["Option 1", "Option 2", "Option 3"])
    group_layout.addWidget(combo)
    group.setLayout(group_layout)
    right_layout.addWidget(group)

    right_layout.addWidget(QLabel("📆 Calendar"))
    right_layout.addWidget(QCalendarWidget())

    right_layout.addWidget(QLabel("📊 进度 / Progress"))
    progress = QProgressBar()
    progress.setValue(50)
    right_layout.addWidget(progress)

    slider = QSlider(Qt.Horizontal)
    slider.setValue(50)
    slider.valueChanged.connect(progress.setValue)
    right_layout.addWidget(slider)

    right_layout.addWidget(QLabel("📑 表格 / Table"))
    table = QTableWidget(2, 2)
    table.setItem(0, 0, QTableWidgetItem("A1"))
    table.setItem(1, 1, QTableWidgetItem("B2"))
    right_layout.addWidget(table)

    btn = QPushButton("点击→修改标题")
    btn.clicked.connect(lambda: main.kit.ui.update_window_title("你点了按钮！"))
    right_layout.addWidget(btn)

    tabs = QTabWidget()
    tab1 = QWidget(); tab1.setLayout(QVBoxLayout())
    tab1.layout().addWidget(QLabel("Tab 内容 1"))
    tab2 = QWidget(); tab2.setLayout(QVBoxLayout())
    tab2.layout().addWidget(QLabel("Tab 内容 2"))
    tabs.addTab(tab1, "Tab A")
    tabs.addTab(tab2, "Tab B")
    right_layout.addWidget(tabs)

    right_framem.setLayout(right_layout)

# sub_extractor
sub_extractor = SubtitleExtractorApp()

def on_add(tree_id, file_path, name):
    """
        支持返回字典或列表
        """
    width = ""
    height = ""
    fps = ""
    probe_info = None

    try:
        info = sub_extractor.silent_ffmpeg_probe(file_path)
        probe_info = info  # 保存完整视频信息
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                width = stream.get("width", "")
                height = stream.get("height", "")
                r_frame_rate = stream.get("r_frame_rate", "")
                if r_frame_rate and r_frame_rate != "0/0":
                    num, den = map(int, r_frame_rate.split("/"))
                    if den != 0:
                        fps = round(num / den, 3)
                break
    except Exception:
        pass

    # 用统一字段顺序创建 tuple
    file_tuple = {
        "宽度": width,  # width
        "高度": height,  # height
        "帧率": fps,  # fps
        "probe_info": probe_info  # full probe 信息
    }
    #print(file_tuple)

    # 示例2：返回字典，自动匹配列名
    return file_tuple



    print("[demo_plugin] UI loaded successfully")
