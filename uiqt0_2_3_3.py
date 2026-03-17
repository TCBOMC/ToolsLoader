import os
import sys
import types
import ctypes
import signal
import inspect
import hashlib
import threading
import traceback
import subprocess
import faulthandler
import importlib.util
from PyQt5 import QtCore, QtGui, QtWidgets
from contextvars import ContextVar
from PyQt5.QtWidgets import QDialog, QTextEdit, QWidget, QLabel, QApplication, QPushButton, QScrollArea, QVBoxLayout, QHBoxLayout
from PyQt5.QtCore import Qt, pyqtBoundSignal, QThread, pyqtSignal, QTimer, QSize, QPropertyAnimation
from PyQt5.QtGui import QFont, QIcon, QPalette, QColor, QFontDatabase

# 打开文件保存崩溃信息
crash_log = open("crash.log", "a")

# 开启 faulthandler
faulthandler.enable(crash_log)

# 某些 Python 版本（>=3.9）支持 register
if hasattr(faulthandler, "register"):
    try:
        faulthandler.register(signal.SIGSEGV, crash_log)
        faulthandler.register(signal.SIGABRT, crash_log)
    except Exception as e:
        print("Warning: faulthandler.register failed:", e)

# 捕获 Python 层未处理异常
def excepthook(exc_type, exc_value, exc_tb):
    print("Uncaught exception:", exc_type.__name__)
    traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.stderr)
    traceback.print_exception(exc_type, exc_value, exc_tb, file=crash_log)
    crash_log.flush()

sys.excepthook = excepthook

# =======================
# DPI 设置（尽可能在 Windows 上启用 per-monitor DPI awareness）
# =======================
# 启用 Qt 高 DPI 模式（推荐）
QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)

# ==================================================
# 插件调用上下文
# ==================================================
_current_plugin_ctx = ContextVar("current_plugin_ctx", default=None)

def get_current_plugin_name():
    #print(f"Getting current plugin name...")
    return _current_plugin_ctx.get()

# 接管 threading.Thread

_original_thread_init = threading.Thread.__init__

def _context_aware_thread_init(self, *args, **kwargs):
    plugin_name = get_current_plugin_name()
    _original_thread_init(self, *args, **kwargs)

    if plugin_name:
        original_run = self.run

        def run_with_ctx():
            token = _current_plugin_ctx.set(plugin_name)
            try:
                return original_run()
            finally:
                _current_plugin_ctx.reset(token)

        self.run = run_with_ctx

threading.Thread.__init__ = _context_aware_thread_init

# 接管 Qt Signal

_original_signal_connect = pyqtBoundSignal.connect

def _context_aware_signal_connect(self, slot, *args, **kwargs):
    plugin_name = get_current_plugin_name()

    if not plugin_name:
        return _original_signal_connect(self, slot, *args, **kwargs)

    def wrapped(*a, **k):
        token = _current_plugin_ctx.set(plugin_name)
        try:
            try:
                # 先尝试完整参数调用
                return slot(*a, **k)
            except TypeError:
                # Qt 多给参数时自动降级
                return slot()
        finally:
            _current_plugin_ctx.reset(token)

    return _original_signal_connect(self, wrapped, *args, **kwargs)

pyqtBoundSignal.connect = _context_aware_signal_connect


# 接管 QTimer.singleShot

_original_single_shot = QtCore.QTimer.singleShot

def _context_aware_single_shot(*args):
    plugin_name = get_current_plugin_name()

    if not plugin_name:
        return _original_single_shot(*args)

    if len(args) == 2 and callable(args[1]):
        msec, func = args

        def wrapped():
            token = _current_plugin_ctx.set(plugin_name)
            try:
                func()
            finally:
                _current_plugin_ctx.reset(token)

        return _original_single_shot(msec, wrapped)

    if len(args) == 3:
        msec, receiver, slot = args

        def wrapped():
            token = _current_plugin_ctx.set(plugin_name)
            try:
                slot()
            finally:
                _current_plugin_ctx.reset(token)

        return _original_single_shot(msec, receiver, wrapped)

    raise TypeError("QTimer.singleShot 参数不合法")

QtCore.QTimer.singleShot = _context_aware_single_shot


# 接管 QTimer.timeout.connect （已删除）

"""_original_timeout_connect = QtCore.QTimer.timeout.fget

def _context_aware_timeout(self):
    signal = _original_timeout_connect(self)
    original_connect = signal.connect

    def connect(slot):
        ctx = copy_context()
        def wrapped(*a, **k):
            return ctx.run(slot, *a, **k)
        return original_connect(wrapped)

    signal.connect = connect
    return signal

QtCore.QTimer.timeout = property(_context_aware_timeout)"""

def resource_path(relative_path):
    """获取打包后资源路径（兼容 PyInstaller）"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# ------------------------
# 弹窗显示安装进度
# ------------------------
class InstallWindow(QDialog):
    append_text = pyqtSignal(str)
    update_dep = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("依赖安装")
        self.resize(250, 150)
        self.setWindowModality(Qt.ApplicationModal)  # 模态，阻塞主UI

        layout = QVBoxLayout()
        self.label = QLabel("准备安装依赖...")
        layout.addWidget(self.label)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        font = self.text.font()
        font.setPointSizeF(font.pointSizeF() * 0.75)  # 按比例缩小
        self.text.setFont(font)
        layout.addWidget(self.text)
        self.setLayout(layout)

        # 信号绑定槽
        self.append_text.connect(self.text.append)
        self.update_dep.connect(lambda name: self.label.setText(f"安装依赖中：{name}"))

    def set_dep(self, name):
        self.update_dep.emit(name)

    def append(self, text):
        self.append_text.emit(text)


# ------------------------
# 安装线程（实时输出pip日志）
# ------------------------
class InstallThread(QThread):
    log_signal = pyqtSignal(str)
    dep_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, packages, tools_libs, installed_deps):
        super().__init__()
        self.packages = packages
        self.tools_libs = tools_libs
        self._installed_deps = installed_deps

    def run(self):
        python_exe = sys.executable
        for package in self.packages:
            if package in self._installed_deps:
                self.log_signal.emit(f"{package} 已安装过，跳过")
                continue

            self.dep_signal.emit(package)
            self.log_signal.emit(f"开始安装依赖: {package}")

            try:
                # 使用 Popen 获取实时输出
                process = subprocess.Popen(
                    [python_exe, "-m", "pip", "install", package, "--target", self.tools_libs],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True
                )

                # 实时读取输出
                for line in iter(process.stdout.readline, ''):
                    if line:
                        self.log_signal.emit(line.rstrip())

                process.stdout.close()
                process.wait()

                if process.returncode == 0:
                    self._installed_deps.add(package)
                    self.log_signal.emit(f"依赖安装成功: {package}")
                else:
                    self.log_signal.emit(f"安装失败: {package}")

            except Exception as e:
                self.log_signal.emit(f"安装异常: {package} -> {e}")

        self.finished_signal.emit()


# 自动依赖安装相关
PIP_MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://pypi.org/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.mirrors.ustc.edu.cn/simple",
]

class LoadingDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("加载中...")
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        layout = QtWidgets.QVBoxLayout(self)
        self.label = QtWidgets.QLabel("正在加载...")
        layout.addWidget(self.label)
        self.resize(300, 100)

    def update_text(self, text):
        self.label.setText(text)
        QtWidgets.QApplication.processEvents()  # 刷新 UI

class CollapsibleSideBar(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.expanded_width = 192
        self.collapsed_width = 36
        self.active_button = None
        self.expanded = False
        self.font_family = self.load_fontawesome()
        self.resizing = False
        self.initial_expanded = None
        self.trigger_width = None
        self.plugin_buttons = {}  # plugin_name -> sidebar button

        # 初始化UI
        self.init_ui()
        self.init_side_bar_state()
        self.init_toolbar()
        self.update_toolbar_state()

        # 定时器用于检测拖动结束
        self.resize_timer = QTimer(self)
        self.resize_timer.setInterval(500)  # 500ms 不再 resize 就认为拖动结束
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.on_resize_finished)

    def init_ui(self):
        self.setWindowTitle("Win11 Task Manager Mockup")
        self.resize(800, 500)

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)  # 添加这一行，设置主布局的间距为0

        # 左侧列
        self.side_bar = QWidget()
        self.side_bar.setMaximumWidth(self.expanded_width)
        self.side_bar.setObjectName("side_bar")
        self.side_bar.setStyleSheet("""
        QWidget#side_bar{
            background-color: #e3e3e3;
        }
        """)

        self.side_layout = QVBoxLayout(self.side_bar)
        self.side_layout.setContentsMargins(0, 0, 0, 0)
        self.side_layout.setSpacing(0)

        # 收起/展开按钮
        self.toggle_btn = QPushButton("≡")
        self.toggle_btn.setFixedSize(30, 30)
        self.toggle_btn.setFont(QFont("Arial", 11))
        self.toggle_btn.setObjectName("toggle_btn")
        self.toggle_btn.setToolTip("收起")
        self.toggle_btn.setStyleSheet("""
            QPushButton#toggle_btn {
                text-align: center;    /* 改为居中 */
                padding-left: 0px;      /* 移除左边距，或设为0 */
                padding-bottom: 0px;
                border: 1px solid #cccccc;   /* 1px 描边 */
                margin-top: 6px;                 /* 6px 外边距 */
                margin-left: 6px;                 /* 6px 外边距 */
                border-radius: 6px;          /* 圆角 */
                background-color: transparent;
            }
            QPushButton#toggle_btn:hover {
                background-color: #cccccc;
            }
        """)

        self.side_layout.addWidget(self.toggle_btn, alignment=Qt.AlignTop | Qt.AlignLeft)

        self.toggle_btn.clicked.connect(self.toggle_side_bar)

        # 重载按钮
        self.reload_btn = QPushButton("↻")
        self.reload_btn.setFixedSize(30, 30)
        self.reload_btn.setFont(QFont("Arial", 11))
        self.reload_btn.setToolTip("重载")
        self.reload_btn.setObjectName("reload_btn")
        self.reload_btn.setStyleSheet("""
                    QPushButton#reload_btn {
                        text-align: center;    /* 改为居中 */
                        padding-left: 0px;      /* 移除左边距，或设为0 */
                        padding-bottom: 0px;
                        border: 1px solid #cccccc;   /* 1px 描边 */
                        margin-top: 6px;                 /* 6px 外边距 */
                        margin-left: 6px;                 /* 6px 外边距 */
                        border-radius: 6px;          /* 圆角 */
                        background-color: transparent;
                    }
                    QPushButton#reload_btn:hover {
                        background-color: #cccccc;
                    }
                """)

        self.side_layout.addWidget(self.reload_btn, alignment=Qt.AlignTop | Qt.AlignLeft)

        self.reload_btn.clicked.connect(self.main.on_reload)

        # 滚动画布
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setObjectName("side_scroll")
        self.scroll_area.setStyleSheet("""
            QScrollArea#side_scroll {
                border: none;  /* 去掉滚动区域的描边 */
                background-color: #e3e3e3;  /* 设置滚动区域背景色与左侧栏一致 */
            }
            QScrollBar:vertical { 
                width:6px;  /* 整体宽度保持6px，这样悬停时才有空间变宽 */
                background: #e3e3e3;  /* 滚动条轨道背景色设为 e3e3e3 */
                margin:0; 
            }
            QScrollBar::handle:vertical { 
                background: gray; 
                min-height: 20px;
                width:3px;  /* 手柄默认宽度为3px */
                border-radius:1.5px; 
                margin-left:1.5px;  /* 通过左边距让手柄居中 */
            }
            QScrollBar::handle:vertical:hover {
                background: #666666;  /* 悬停时颜色加深 */
                width:6px;  /* 悬停时手柄宽度变为6px */
                margin-left:0px;  /* 悬停时取消左边距 */
                border-radius:3px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { 
                height:0px; 
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: #e3e3e3;  /* 手柄上下区域的背景色设为 e3e3e3 */
            }
        """)
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("scroll_content")
        self.scroll_content.setStyleSheet("""
            QWidget#scroll_content {
                    background-color: transparent;  /* 设置滚动区域背景色与左侧栏一致 */
                }
        """)

        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(0)
        self.scroll_layout.setAlignment(Qt.AlignTop)
        self.scroll_area.setWidget(self.scroll_content)
        self.side_layout.addWidget(self.scroll_area)
        #self.side_layout.addStretch()

        # 初始化蓝色竖条，父控件改为 scroll_content
        self.indicator = QLabel(self.scroll_content)
        self.indicator.setFixedSize(3, 16)  # 宽3px，高16px
        self.indicator.setStyleSheet("""
            background-color: #0078d7; 
            border-radius: 1px;
            border: none;
        """)
        self.indicator.hide()  # 初始隐藏

        # 初始化完滚动区域之后
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.update_button_margins)
        self.scroll_area.verticalScrollBar().rangeChanged.connect(self.update_button_margins)

        # 右侧区域 - 修改部分
        # 创建外部容器，背景色为#e3e3e3，内外边距为0
        self.right_container = QWidget()
        self.right_container.setObjectName("right_container")
        #self.right_container.setStyleSheet("background-color: #e3e3e3;")
        self.right_container.setStyleSheet("""
            QWidget#right_container {
                background-color: #e3e3e3;
            }
        """)
        self.right_container.setContentsMargins(0, 0, 0, 0)

        # 容器布局，边距为0
        container_layout = QHBoxLayout(self.right_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # 内部右侧区域，设置左上角8px圆角
        self.right_area = QWidget()
        self.right_area.setObjectName("right_area")
        self.right_area.setStyleSheet("""
            QWidget#right_area {
                background-color: #f0f0f0;
                border-top-left-radius: 6px;
                border-top: 1px solid #cccccc;      /* 上方1px描边 */
                border-left: 1px solid #cccccc;      /* 左侧1px描边 */
            }
        """)

        # 内部布局
        self.right_layout = QVBoxLayout(self.right_area)
        self.right_layout.setContentsMargins(1, 1, 1, 1)
        self.right_layout.setSpacing(0)
        #self.right_layout.addWidget(QLabel("右侧内容区域，可放控件"))
        #self.create_right_area_controls(self.right_layout)

        # 将内部区域添加到容器布局中
        container_layout.addWidget(self.right_area)

        # 将容器添加到主布局
        self.main_layout.addWidget(self.side_bar)
        self.main_layout.addWidget(self.right_container)

        """# 添加测试按钮
        self.button_list = []
        for i in range(10):
            btn = self.create_button(f"测试按钮{i + 1}")
            self.scroll_layout.addWidget(btn)
            self.button_list.append(btn)

        if self.button_list:
            self.activate_button(self.button_list[0])  # 默认激活第一个按钮"""

    # 更新按钮右边距
    def update_button_margins(self):
        # 延迟一帧执行
        QTimer.singleShot(0, self._do_update_button_margins)

    def _do_update_button_margins(self):
        # 判断滚动条是否需要滚动（稳健方式）
        scrollbar_needed = self.scroll_area.verticalScrollBar().maximum() > 0
        right_margin = 0 if scrollbar_needed else 6

        for btn in self.plugin_buttons.values():
            if btn == self.active_button:
                # 激活按钮保持背景色，仅更新右边距
                btn.setStyleSheet(f"""
                    QPushButton {{
                        border: none;
                        border-radius: 6px;
                        background-color: #cccccc;
                        margin: 6px {right_margin}px 0px 6px;
                        padding: 0px;
                    }}
                """)
            else:
                # 普通按钮
                btn.setStyleSheet(f"""
                    QPushButton {{
                        border: none;
                        border-radius: 6px;
                        background-color: transparent;
                        margin: 6px {right_margin}px 0px 6px;
                        padding: 0px;
                    }}
                    QPushButton:hover {{
                        background-color: #cccccc;
                    }}
                """)

    def init_side_bar_state(self):
        """根据 self.expanded 初始化侧栏状态（无动画）"""

        if self.expanded:
            # 当前是展开逻辑（但你这里其实是“隐藏文本”的状态）
            self.side_bar.setMaximumWidth(self.expanded_width)
            self.side_bar.setMinimumWidth(self.expanded_width)
            self.toggle_btn.setToolTip("收起")
        else:
            # 折叠状态
            self.side_bar.setMaximumWidth(self.collapsed_width)
            self.side_bar.setMinimumWidth(self.collapsed_width)
            self.toggle_btn.setToolTip("展开")

        # 同步所有按钮文本显示状态
        for btn in self.plugin_buttons.values():
            btn.text_label.setVisible(not self.expanded)

    def init_toolbar(self):
        """初始化工具栏区域"""

        # ===== 工具栏容器（始终在scroll_area下面）=====
        self.toolbar_container = QWidget()
        self.toolbar_container.setContentsMargins(0, 0, 0, 0)

        self.toolbar_layout = QHBoxLayout(self.toolbar_container)
        self.toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.toolbar_layout.setSpacing(0)

        # ===== 工具集按钮（侧栏收起时显示）=====
        self.toolset_btn = QPushButton("🔧")
        self.toolset_btn.setFixedSize(30, 30)
        self.toolset_btn.setToolTip("工具栏")
        # self.toolset_btn.setPointSizeF(6)
        self.toolset_btn.setFont(QFont("", 11))
        self.toolset_btn.setObjectName("toolset_btn")
        # 使用和 toggle_btn 一样的样式，并增加 1px 描边
        self.toolset_btn.setStyleSheet("""
            QPushButton#toolset_btn {
                text-align: center;
                padding-top: 3px;
                padding-bottom: 3px;
                border: 1px solid #cccccc;
                margin-bottom: 6px;
                margin-left: 6px;
                border-radius: 6px;
                background-color: transparent;
            }
            QPushButton#toolset_btn:hover {
                background-color: #cccccc;
            }
        """)
        self.toolset_btn.clicked.connect(self.toggle_toolset_popup)

        self.toolbar_layout.addWidget(self.toolset_btn, alignment=Qt.AlignLeft)

        # ===== 工具集区域（真正的工具容器）=====
        self.toolset_area = QWidget(self.right_container)
        self.toolset_area.setFixedHeight(30)
        self.toolset_area.hide()

        # 创建水平滚动区域
        self.toolset_scroll = QScrollArea(self.toolset_area)
        self.toolset_scroll.setWidgetResizable(True)
        self.toolset_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.toolset_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.toolset_scroll.setStyleSheet("""
                QScrollArea {
                    border: none;
                    background: transparent;
                    padding-left: 6px;
                }
                QScrollArea > QWidget > QWidget {
                    background: transparent;
                }
                QScrollBar:horizontal {
                    height: 0px;
                }
            """)

        # 安装事件过滤器，将鼠标竖向滚动转换为横向滚动
        self.toolset_scroll.viewport().installEventFilter(self)
        self.toolset_scroll.installEventFilter(self)

        # 创建滚动内容容器
        self.toolset_content = QWidget()
        self.toolset_content.setFixedHeight(30)
        self.toolset_layout = QHBoxLayout(self.toolset_content)
        self.toolset_layout.setContentsMargins(0, 0, 0, 0)
        self.toolset_layout.setSpacing(0)
        self.toolset_layout.setAlignment(Qt.AlignLeft)

        """buttons = [
            ("\uf56f", "导入", lambda: None),
            ("\uf1f8", "删除", lambda: None),
            ("\uf51a", "清空", lambda: None),
            ("\uf0c5", "复制", lambda: None),
            ("\uf0c4", "剪切", lambda: None),
            ("\uf0ea", "粘贴", lambda: None),
        ]
        self.tool_button(buttons)"""

        self.toolset_scroll.setWidget(self.toolset_content)
        # 删除这里重复设置的样式表
        # self.toolset_scroll.setObjectName("toolset_scroll")
        # self.toolset_scroll.setStyleSheet("""
        #     QWidget#toolset_scroll {
        #             background-color: transparent;  /* 设置滚动区域背景色与左侧栏一致 */
        #         }
        # """)

        # 设置toolset_area的布局
        toolset_area_layout = QHBoxLayout(self.toolset_area)
        toolset_area_layout.setContentsMargins(0, 0, 0, 0)
        toolset_area_layout.setSpacing(0)
        toolset_area_layout.addWidget(self.toolset_scroll)

        # 默认尺寸
        self.toolset_area.setMinimumWidth(30)
        self.toolset_area.setMaximumWidth(186)

        # 插入到 scroll_area 下方
        self.side_layout.addWidget(self.toolbar_container, alignment=Qt.AlignBottom)

    def tool_button(self, buttons):
        button_n = 0
        for icon_unicode, name, action in buttons:
            btn = QPushButton(icon_unicode)
            btn.setToolTip(name)
            btn.setFixedSize(30, 30)  # 可根据需求调整大小
            btn.setFont(QFont(self.font_family, 11))  # 调整大小
            btn.setObjectName(f"tool_btn{button_n}")
            btn.setStyleSheet(f"""
                            QPushButton#tool_btn{button_n} {{
                                text-align: center;
                                padding-left: 0px;
                                padding-bottom: 0px;
                                border: 0px solid #9a9a9a;
                                margin-bottom: 6px;
                                margin-left: 0px;
                                border-radius: 6px;
                                background-color: transparent;
                            }}
                            QPushButton#tool_btn{button_n}:hover {{
                                background-color: #cccccc;
                            }}
                        """)
            btn.clicked.connect(action)
            self.toolset_layout.addWidget(btn)
            button_n += 1

    # 添加事件过滤器方法
    def eventFilter(self, obj, event):
        """事件过滤器，处理鼠标滚轮事件转换为横向滚动"""
        if obj in (self.toolset_scroll, self.toolset_scroll.viewport()):
            if event.type() == event.Type.Wheel:
                # 获取滚轮事件的垂直滚动角度
                delta = event.angleDelta().y()

                # 如果有垂直滚动，转换为水平滚动
                if delta != 0:
                    # 获取当前水平滚动条位置
                    h_scrollbar = self.toolset_scroll.horizontalScrollBar()
                    current_pos = h_scrollbar.value()

                    # 计算新的位置（向上滚动向左，向下滚动向右）
                    step = 40  # 每次滚动的步长，可以根据需要调整
                    if delta > 0:
                        new_pos = current_pos - step
                    else:
                        new_pos = current_pos + step

                    # 设置新的水平滚动条位置
                    h_scrollbar.setValue(new_pos)

                    # 事件已处理
                    return True

        # 其他事件交给父类处理
        return super().eventFilter(obj, event)

    # 假设 self 是你的主窗口
    def toggle_toolset_popup(self):
        """点击工具按钮弹出/关闭工具集"""
        if self.toolset_area.isVisible():
            self.toolset_area.hide()
            return

        # 弹出窗口设置
        self.toolset_area.setParent(self)
        self.toolset_area.setWindowFlags(Qt.FramelessWindowHint)  # 无边框
        self.toolset_area.setAttribute(Qt.WA_ShowWithoutActivating)
        self.toolset_area.setObjectName("toolset_area")
        self.toolset_area.setStyleSheet("""
            QWidget#toolset_area {
                border: 1px solid #cccccc;
                background-color: #e3e3e3;
                border-radius: 6px;
                margin-bottom: 6px;
                margin-left: 6px;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:horizontal {
                height: 0px;
            }
        """)

        # ===== 计算按钮总宽度 =====
        buttons = self.toolset_content.findChildren(QPushButton)
        total_width = sum(btn.width() for btn in buttons) + 8  # +边距
        min_width = 30
        max_width = 186
        popup_width = max(min_width, min(total_width, max_width))
        self.toolset_area.setFixedWidth(popup_width)  # 设置弹出窗口宽度

        # 设置位置
        global_pos = self.toolset_btn.mapToGlobal(self.toolset_btn.rect().topRight())
        local_pos = self.mapFromGlobal(global_pos)
        local_pos.setX(local_pos.x() + 8)
        self.toolset_area.move(local_pos)
        self.toolset_area.show()

        # 刷新按钮状态
        self.toolset_btn.setDown(False)
        self.toolset_btn.repaint()

    def update_toolbar_state(self):
        """更新工具栏状态（展开/收起）"""

        if self.expanded:
            # 收起按钮隐藏，工具集放入 toolbar_container 布局
            self.toolset_btn.hide()

            # 移除浮动属性
            self.toolset_area.setParent(self.toolbar_container)
            self.toolset_area.setWindowFlags(Qt.Widget)  # 普通控件
            # 清空toolset_area样式，让它透明
            self.toolset_area.setStyleSheet("")

            # 设置滚动区域在展开状态下也为透明
            self.toolset_scroll.setStyleSheet("""
                QScrollArea {
                    border: none;
                    background: transparent;
                    padding-left: 6px;
                }
                QScrollArea > QWidget > QWidget {
                    background: transparent;
                }
                QScrollBar:horizontal {
                    height: 0px;
                }
            """)

            self.toolbar_layout.addWidget(self.toolset_area)

            # 使用固定宽度展开
            self.toolset_area.setMinimumWidth(self.expanded_width)
            self.toolset_area.setMaximumWidth(self.expanded_width)
            self.toolset_area.show()

        else:
            # 展开按钮显示，工具集变为浮动
            self.toolset_btn.show()

            self.toolset_area.setParent(self.right_container)
            self.toolset_area.setWindowFlags(Qt.Popup)
            self.toolset_area.setStyleSheet("""
                QWidget#toolset_area {
                    border: 1px solid #cccccc;
                    background-color: #e3e3e3;
                    border-radius: 6px;
                }
                QScrollArea {
                    border: none;
                    background: transparent;
                }
                QScrollArea > QWidget > QWidget {
                    background: transparent;
                }
                QScrollBar:horizontal {
                    height: 0px;
                }
            """)
            self.toolset_area.setMinimumWidth(30)
            self.toolset_area.setMaximumWidth(400)
            self.toolset_area.hide()

    def update_toolset_position(self):
        """更新弹出工具栏的位置"""
        if self.toolset_area.isVisible():
            global_pos = self.toolset_btn.mapToGlobal(self.toolset_btn.rect().topRight())
            local_pos = self.mapFromGlobal(global_pos)

            # 增加间距
            local_pos.setX(local_pos.x() + 8)

            self.toolset_area.move(local_pos)

    # 主窗口移动事件时，让浮动工具集跟随
    def moveEvent(self, event):
        super().moveEvent(event)
        self.update_toolset_position()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_toolset_position()
        self.on_resize_event()

    def on_resize_event(self):
        """处理侧栏自动展开/收起逻辑"""
        # 每次 resize 都重启定时器
        self.resize_timer.start()

        if not self.resizing:
            # 第一次 resize 时，认为开始调整
            self.resizing = True
            self.initial_expanded = self.expanded  # 记录调整前状态

        if not self.initial_expanded:
            return  # 调整前侧栏收起，不处理

        window_width = self.width()
        side_width = self.side_bar.width()
        ratio = side_width / window_width

        if self.trigger_width is None:
            if self.expanded and ratio >= 0.3:
                self.toggle_side_bar()       # 收起
                self.trigger_width = window_width
        else:
            if window_width > self.trigger_width:
                if not self.expanded:
                    self.toggle_side_bar()   # 展开
            elif window_width < self.trigger_width:
                if self.expanded:
                    self.toggle_side_bar()   # 收起

    def on_resize_finished(self):
        """拖动结束，清空状态"""
        self.resizing = False
        self.trigger_width = None
        self.initial_expanded = None

    def create_button(self, text, icon=None):
        btn = QPushButton()
        btn.setCheckable(False)
        btn.setFixedHeight(30)  # 固定高度

        # 按钮样式
        btn.setStyleSheet("""
            QPushButton {
                border: none;
                border-radius: 6px;
                background-color: transparent;
                margin: 6px 6px 0px 6px;  /* 上 右 下 左 */
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #cccccc;
            }
        """)

        layout = QHBoxLayout(btn)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # ===== 图标 =====
        icon_widget = QLabel()
        icon_widget.setAlignment(Qt.AlignCenter)
        icon_widget.setFixedSize(36, 36)
        icon_widget.setStyleSheet("""
            QLabel {
                background-color: transparent;
                border: 1px solid #cccccc;   /* 1px 描边 */
                border-radius: 6px;
                margin: 6px 6px 6px 6px;  /* 上 右 下 左 */
                padding: 0px;
            }
        """)
        icon_widget.setAttribute(Qt.WA_TransparentForMouseEvents)

        if icon:
            # 文件图标
            if "file" in icon:
                file_icon = QIcon(icon["file"])
                icon_widget.setPixmap(file_icon.pixmap(QSize(24, 24)))
            # 文本图标
            elif "text" in icon:
                chars = icon["text"]
                num_chars = len(chars)

                if num_chars == 1:
                    icon_widget.setFont(QFont("", 11))
                    icon_widget.setText(chars)
                elif num_chars == 2:
                    icon_widget.setFont(QFont("", 9))
                    icon_widget.setText(chars)
                else:
                    # 三字符及以上使用两行两列的排列（类似原来的逻辑）
                    display_chars = chars[:4].ljust(4)
                    icon_widget.setFont(QFont("", 6))
                    icon_widget.setText(f"{display_chars[:2]}\n{display_chars[2:]}")

            elif "text_ico" in icon:
                chars = icon["text_ico"]
                num_chars = len(chars)

                if num_chars == 1:
                    icon_widget.setFont(QFont(self.font_family, 11))
                    icon_widget.setText(chars)
                elif num_chars == 2:
                    icon_widget.setFont(QFont(self.font_family, 9))
                    icon_widget.setText(chars)
                else:
                    # 三字符及以上使用两行两列的排列（类似原来的逻辑）
                    display_chars = chars[:4].ljust(4)
                    icon_widget.setFont(QFont(self.font_family, 6))
                    icon_widget.setText(f"{display_chars[:2]}\n{display_chars[2:]}")
        else:
            # 默认文本生成图标
            chars = text[:4].ljust(4)
            icon_widget.setFont(QFont("Arial", 6))
            icon_widget.setText(f"{chars[:2]}\n{chars[2:]}")

        # ===== 文本 =====
        text_label = QLabel(text)
        text_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        text_label.setFont(QFont("Arial", 9))
        text_label.setStyleSheet("""
            background: transparent;
            padding: 0px;
        """)
        text_label.setFixedHeight(36)
        text_label.setMinimumWidth(0)
        text_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        layout.addWidget(icon_widget)
        layout.addWidget(text_label)
        layout.addStretch()  # 让文本扩展

        if self.expanded:
            text_label.show()

        else:
            text_label.hide()

        btn.icon_widget = icon_widget
        btn.text_label = text_label
        btn.setToolTip(text)

        btn.clicked.connect(lambda checked, b=btn: self.activate_button(b))

        return btn

    def toggle_side_bar(self):
        start = self.side_bar.width()
        end = self.collapsed_width if self.expanded else self.expanded_width

        self.anim = QPropertyAnimation(self.side_bar, b"maximumWidth")
        self.anim.setDuration(100)
        self.anim.setStartValue(start)
        self.anim.setEndValue(end)
        self.anim.start()

        if self.expanded:
            self.toggle_btn.setToolTip("展开")
            self.side_bar.setMinimumWidth(self.collapsed_width)
        else:
            self.toggle_btn.setToolTip("收起")
            self.side_bar.setMinimumWidth(self.expanded_width)

        # 修改按钮的显示/隐藏逻辑
        for btn in self.plugin_buttons.values():
            if self.expanded:
                btn.text_label.hide()
            else:
                btn.text_label.show()

            # ⭐ 保留激活按钮样式
            if btn == self.active_button:
                btn.setStyleSheet("""
                    QPushButton {
                        border: none;
                        border-radius: 6px;
                        background-color: #cccccc;
                        margin: 6px 6px 0px 6px;
                        padding: 0px;
                    }
                """)
            else:
                # 非激活按钮，恢复默认样式
                btn.setStyleSheet("""
                    QPushButton {
                        border: none;
                        border-radius: 6px;
                        background-color: transparent;
                        margin: 6px 6px 0px 6px;
                        padding: 0px;
                    }
                    QPushButton:hover {
                        background-color: #cccccc;
                    }
                """)

        self.expanded = not self.expanded
        self.update_toolbar_state()

    def activate_button(self, btn):
        if self.active_button == btn:
            return

        prev_button = self.active_button

        # 还原上一个激活按钮样式
        if prev_button:
            prev_button.setStyleSheet("""
                QPushButton {
                    border: none;
                    border-radius: 6px;
                    background-color: transparent;
                    margin: 6px 6px 0px 6px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: #cccccc;
                }
            """)

        # 设置当前按钮为激活样式
        btn.setStyleSheet("""
            QPushButton {
                border: none;
                border-radius: 6px;
                background-color: #cccccc;
                margin: 6px 6px 0px 6px;
                padding: 0px;
            }
        """)

        # 计算竖条在 scroll_content 内的纵向位置
        target_y = btn.pos().y() + (btn.height() - self.indicator.height()) // 2 + 3
        if not self.indicator.isVisible():
            # 第一次显示
            self.indicator.move(6, target_y)
            self.indicator.show()
            self.indicator.raise_()  # 确保在按钮上方
        else:
            self.indicator.raise_()  # 确保在按钮上方
            anim = QPropertyAnimation(self.indicator, b"pos")
            anim.setDuration(50)
            anim.setStartValue(self.indicator.pos())
            anim.setEndValue(QtCore.QPoint(6, target_y))
            anim.start()
            # 避免动画被回收
            self.anim = anim

        self.active_button = btn
        self.update_button_margins()

    # 获取字体路径
    def get_font_path(self):
        # MEIPASS 是 PyInstaller 打包后的临时目录
        if getattr(sys, "_MEIPASS", False):
            base_path = sys._MEIPASS
        else:
            base_path = os.getcwd()  # IDE / 开发环境
        return os.path.join(base_path, "fonts", "fa-solid-900.ttf")

    # 加载 FontAwesome
    def load_fontawesome(self):
        font_path = self.get_font_path()
        font_id = QFontDatabase.addApplicationFont(font_path)
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            return families[0]
        return None


class ToolsLoader(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.window_title_prefix = "插件化 UI 系统"
        self.setWindowTitle(self.window_title_prefix)

        self.scale = 1.0  # ✅ 固定为1，完全交给Qt自动DPI
        print(f"Qt自动DPI已启用，逻辑缩放 self.scale = {self.scale}")

        base_width, base_height = 680, 420
        self.resize(base_width, base_height)
        self.setMinimumSize(550, 200)

        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))
        else:
            print(f"⚠ 找不到图标: {icon_path}")

        self.set_titlebar_color("#e3e3e3")  # 设置标题栏颜色

        # UI尺寸常量
        self.padx = 10
        self.pady = 10
        self.ipady = 1
        self.scrollbar_width = 16
        self.line_height = 20
        self.parameter_width = 50
        self.font_size = 8.0  # 基础字号，可调整

        # 状态与数据结构（尽量保留原名称）
        self.bg_color = QtGui.QColor("#f0f0f0")
        self.plugins = {}  # 存放已加载的模块对象
        self.plugin_hashes = {}
        self.current_plugin = None
        self.current_plugin_name = ""
        self.active_tree_id = 0
        self.plugin_active_tree_ids = {}
        self.plugins_folder = "plugins_qt2"
        self.kits_folder = "kits_qt"

        # 插件相关容器（保持名称一致）
        self.plugin_files = {}
        self.plugin_info = {}
        self.plugin_frames = {}
        self.plugin_edit_frames = {}
        self.plugin_trees = {}
        self.plugin_tree_map = {}
        self.plugin_tree_frames = {}
        self.plugin_tree_container = {}
        self.plugin_title_extras = {}
        self.plugin_trees_widget = {}
        self.plugin_headers = {}
        self.plugin_threads = {}  # { plugin_name: { thread_id: {"thread":..., "stop":Event()} } }
        self._thread_counter = 0

        # -------------------------
        # 插件依赖目录
        # -------------------------
        self._installed_deps = set()

        self.tools_libs = os.path.abspath("tools_libs")

        if not os.path.exists(self.tools_libs):
            os.makedirs(self.tools_libs)

        # 插件依赖优先
        if self.tools_libs not in sys.path:
            sys.path.insert(0, self.tools_libs)

        # ----------------------------
        # 创建加载窗口
        # ----------------------------
        self.loading_dialog = LoadingDialog(self)
        self.loading_dialog.show()
        QtWidgets.QApplication.processEvents()  # 确保窗口立即显示

        # ----------------------------
        # 分步加载 kit
        # ----------------------------
        self.kit_gen = self.load_kits_generator()
        QtCore.QTimer.singleShot(0, self._load_next_kit_step)

    def set_titlebar_color(self, hex_color):
        if sys.platform != "win32":
            return
        hex_color = hex_color.lstrip("#")
        r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        color = b | (g << 8) | (r << 16)
        DWMWA_CAPTION_COLOR = 35
        hwnd = self.winId().__int__()
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(DWMWA_CAPTION_COLOR),
            ctypes.byref(ctypes.c_int(color)),
            ctypes.sizeof(ctypes.c_int)
        )

    def _load_next_kit_step(self):
        try:
            next(self.kit_gen)
            QtCore.QTimer.singleShot(0, self._load_next_kit_step)
        except StopIteration:
            # kit 加载完毕，可以初始化 UI 或部分依赖 kit 的内容
            self.init_ui_after_kits()
            # 然后开始加载 plugins
            self.plugin_gen = self.load_plugins_generator()
            QtCore.QTimer.singleShot(0, self._load_next_plugin_step)

    def _load_next_plugin_step(self):
        try:
            next(self.plugin_gen)
            QtCore.QTimer.singleShot(0, self._load_next_plugin_step)
        except StopIteration:
            # 全部加载完成
            self.loading_dialog.close()
            self.fix_all_font()

    def init_ui_after_kits(self):
        # 构建 UI：顶部工具栏 + 顶部编辑区 + 中央插件显示区
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 直接实例化 CollapsibleSideBar
        self.sidebar_ui = CollapsibleSideBar(self)
        layout.addWidget(self.sidebar_ui)

        # =========================
        # 在 right_area 中创建容器
        # =========================

        # 1️⃣ 可选编辑行
        self.top_edit_frame_container = QtWidgets.QWidget()
        top_edit_layout = QtWidgets.QHBoxLayout(self.top_edit_frame_container)
        top_edit_layout.setContentsMargins(self.padx, self.pady // 2, self.padx, self.pady // 2)

        # 2️⃣ 主区域
        self.ui_stack = QtWidgets.QStackedWidget()
        self.ui_stack.setContentsMargins(0, 0, 0, 0)

        self.sidebar_ui.right_layout.addWidget(self.top_edit_frame_container)
        self.sidebar_ui.right_layout.addWidget(self.ui_stack, 1)

        self.tree_edit_layout = self.sidebar_ui.toolset_layout

        self.create_edit_widget()

    def get_current_plugin_name(self):
        return get_current_plugin_name()

    def run_in_context(self, ctx, func, *args, **kwargs):
        """
        在给定的 context 中执行函数
        """
        if ctx is None:
            return func(*args, **kwargs)
        return ctx.run(func, *args, **kwargs)

    def call_plugin_func(self, func, *args, plugin_name=None, **kwargs):
        plugin_name = plugin_name or self.get_current_plugin_name() or self.current_plugin_name
        if not callable(func):
            return None
        token = None
        if plugin_name:
            token = _current_plugin_ctx.set(plugin_name)
        try:
            return func(*args, **kwargs)
        finally:
            if token is not None:
                _current_plugin_ctx.reset(token)

    def plugin_call_back(self, func_name, *args, plugin_name=None, **kwargs):
        pname = (
                plugin_name
                or self.get_current_plugin_name()
                or self.current_plugin_name
        )

        plugin_obj = self.plugins.get(pname)
        if not plugin_obj:
            return None

        func = getattr(plugin_obj, func_name, None)
        return self.call_plugin_func(
            func,
            *args,
            plugin_name=pname,
            **kwargs
        )

    def closeEvent(self, event):
        # 先保存 config 数据
        cfg = getattr(self, 'cfg', None) or getattr(getattr(self, 'kit', None), 'config', None)
        if cfg is not None:
            print("💾 程序关闭前保存 config 数据...")
            cfg.shutdown()

        # 再调用父类关闭事件
        super().closeEvent(event)

    def fix_all_font(self):
        """遍历 self 下所有子控件，统一设置字体大小"""
        base_size = self.font_size * self.scale

        def apply_font(w):
            f = w.font()
            f.setPointSizeF(base_size)
            w.setFont(f)

        # 先设置 self 自己（如果它本身是 QWidget）
        if isinstance(self, QWidget):
            apply_font(self)

        # 遍历所有子控件
        for child in self.findChildren(QWidget):
            apply_font(child)

    # -------------------------
    # 加载工具集
    # -------------------------
    def create_edit_widget(self):
        buttons_info = [
            ("\uf56f", "导入", "import_files"),
            ("\uf1f8", "删除", "delete_selected"),
            ("\uf51a", "清空", "clear_all"),
            ("\uf0c5", "复制", "copy_selected"),
            ("\uf0c4", "剪切", "cut_selected"),
            ("\uf0ea", "粘贴", "paste_items"),
        ]

        # 生成新的按钮信息列表，保持三元组结构 (icon, text, callback)
        processed_buttons = []
        for ico, text, method_name in buttons_info:
            if hasattr(self.kit.ui, method_name):
                method = getattr(self.kit.ui, method_name)
                processed_buttons.append((ico, text, lambda _, m=method: m(self.active_tree_id)))
            else:
                print(f"按钮 '{text}' 被跳过，方法 '{method_name}' 不存在")

        # 一次性创建所有按钮
        if processed_buttons:
            self.sidebar_ui.tool_button(processed_buttons)  # 假设 tool_button 支持列表

    # -------------------------
    # 自动安装依赖
    # -------------------------
    def install_with_window(self, packages):
        if not packages:
            return

        dlg = InstallWindow()
        thread = InstallThread(packages, self.tools_libs, self._installed_deps)

        # 信号连接
        thread.log_signal.connect(dlg.append)
        thread.dep_signal.connect(dlg.set_dep)
        thread.finished_signal.connect(dlg.accept)

        thread.start()
        dlg.exec()  # 模态阻塞主UI，焦点只能在弹窗上
        thread.wait()  # 等待线程结束

    # 自动安装单个依赖
    def install_package(self, package):
        self.install_with_window([package])

    # 解析requirements
    def parse_requirements(self, script_path):
        requirements = []
        in_block = False
        try:
            with open(script_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped in ["# requirements", "# requirements start", "# deps"]:
                        in_block = True
                        continue
                    if stripped in ["# requirements_end", "# requirements end", "# end"]:
                        break
                    if not in_block:
                        continue
                    if stripped == "":
                        break
                    if not stripped.startswith("#"):
                        break
                    dep = stripped[1:].strip()
                    if dep:
                        requirements.append(dep)
        except Exception as e:
            print(f"读取插件依赖声明失败: {e}")
        return requirements

    # 安装脚本声明依赖
    def install_declared_requirements(self, script_name, script_path):
        deps = self.parse_requirements(script_path)
        if deps:
            print(f"脚本 {script_name} 声明依赖: {deps}")
            self.install_with_window(deps)

    # 加载脚本并自动安装缺失依赖
    def load_script_with_deps(self, script_name, script_path):
        # 先安装声明依赖
        self.install_declared_requirements(script_name, script_path)

        spec = importlib.util.spec_from_file_location(script_name, script_path)
        module = importlib.util.module_from_spec(spec)

        last_missing = None
        while True:
            try:
                spec.loader.exec_module(module)
                return module
            except ModuleNotFoundError as e:
                missing = e.name
                print(f"脚本 {script_name} 缺少依赖: {missing}")
                if missing == last_missing:
                    raise RuntimeError(f"依赖 {missing} 安装失败，重复出现")
                self.install_package(missing)
                last_missing = missing
            except Exception:
                traceback.print_exc()
                raise

    # -------------------------
    # 加载工具集
    # -------------------------
    def on_reload(self):
        """重载 config 脚本前先保存旧数据"""
        cfg = getattr(self.kit, 'config', None)
        if cfg is not None:
            print("💾 重载前保存 config 数据...")
            cfg.shutdown()  # 保存并关闭写线程

        self.reload_kits()
        self.reload_current_plugin()

    def clear_tree_edit_layout(self):
        """清空 tree_edit_layout 内所有控件"""
        while self.tree_edit_layout.count():
            item = self.tree_edit_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def reload_kits(self):
        """检测 kits 文件夹下变化的 .py 文件并重新加载"""
        folder = getattr(self, "kits_folder", "kits")
        folder_path = os.path.abspath(folder)

        if not os.path.exists(folder_path):
            print(f"⚠️ kits 文件夹不存在，正在创建: {folder_path}")
            os.makedirs(folder_path)
            return

        if not hasattr(self, "kit_hashes"):
            self.kit_hashes = {}

        # 检测变化
        kits_to_reload = set()
        current_files = set()

        for filename in os.listdir(folder_path):
            if not filename.endswith(".py") or filename == "__init__.py":
                continue
            module_name = filename[:-3]
            module_path = os.path.join(folder_path, filename)
            current_files.add(module_name)
            current_hash = self.calculate_file_hash(module_path)
            if current_hash is None:
                continue
            if (module_name not in self.kit_hashes or
                    self.kit_hashes[module_name] != current_hash):
                kits_to_reload.add(module_name)
                self.kit_hashes[module_name] = current_hash

        # 检测被删除的 kit
        existing_kits = set(getattr(self, "kits", {}).keys())
        removed = existing_kits - current_files
        for name in removed:
            self._cleanup_single_kit(name)
            print(f"🗑️ 已移除 kit: {name}")

        # 重载变化的 kit
        for kit_name in kits_to_reload:
            self._reload_single_kit(kit_name)

        # 清空 tree_edit_layout 并重新创建按钮
        self.clear_tree_edit_layout()
        self.create_edit_widget()

    def _reload_single_kit(self, kit_name):
        """重载单个 kit 文件"""
        folder = getattr(self, "kits_folder", "kits")
        kit_path = os.path.abspath(os.path.join(folder, f"{kit_name}.py"))
        if not os.path.exists(kit_path):
            self._cleanup_single_kit(kit_name)
            return

        try:
            # 清理旧模块
            if kit_name in sys.modules:
                del sys.modules[kit_name]

            # 使用自动依赖加载
            module = self.load_script_with_deps(kit_name, kit_path)
            sys.modules[kit_name] = module
            self.kits[kit_name] = module

            # 清除旧引用
            if hasattr(self.kit, kit_name):
                delattr(self.kit, kit_name)

            # 重新挂载类到 self.kit
            if hasattr(module, kit_name):
                cls = getattr(module, kit_name)
                if isinstance(cls, type):
                    kit_obj = types.SimpleNamespace()

                    # 尝试实例化，优先注入主程序 self
                    instance = None
                    try:
                        instance = cls(self)
                    except TypeError:
                        try:
                            instance = cls()
                        except Exception as e:
                            traceback.print_exc()
                            print(f"⚠️ Kit {kit_name} 无法实例化：{e}")

                    # 优先使用实例（确保方法绑定）
                    if instance is not None:
                        for attr_name in dir(instance):
                            if attr_name.startswith("__"):
                                continue
                            setattr(kit_obj, attr_name, getattr(instance, attr_name))
                    else:
                        for attr_name in dir(cls):
                            if attr_name.startswith("__"):
                                continue
                            setattr(kit_obj, attr_name, getattr(cls, attr_name))

                    setattr(self.kit, kit_name, kit_obj)
                    print(f"🔄 已重载 kit: {kit_name}")
                else:
                    print(f"⚠️ 模块 {kit_name} 中的 {kit_name} 不是类。")
            else:
                print(f"⚠️ 模块 {kit_name} 中未找到同名类。")

        except Exception as e:
            print(f"❌ 重载 kit {kit_name} 失败：{e}")
            traceback.print_exc()

    def _cleanup_single_kit(self, kit_name):
        """删除 kit 对象及其模块"""
        print(f"清理：{kit_name}")
        if hasattr(self.kit, kit_name):
            delattr(self.kit, kit_name)
        if hasattr(self, "kits") and kit_name in self.kits:
            del self.kits[kit_name]
        if hasattr(self, "kit_hashes") and kit_name in self.kit_hashes:
            del self.kit_hashes[kit_name]

    def load_kits_generator(self):
        self.kits = {}
        self.kit = types.SimpleNamespace()
        self.kit_hashes = getattr(self, "kit_hashes", {})

        folder_path = os.path.abspath(self.kits_folder)
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            print(f"已创建 kits 文件夹：{folder_path}")
            return

        for filename in os.listdir(folder_path):
            if not filename.endswith(".py") or filename == "__init__.py":
                continue
            module_name = filename[:-3]
            module_path = os.path.join(folder_path, filename)

            self.loading_dialog.update_text(f"加载 kit: {module_name}")
            QtWidgets.QApplication.processEvents()

            try:
                module = self.load_script_with_deps(module_name, module_path)
                self.kits[module_name] = module

                if hasattr(module, module_name):
                    cls = getattr(module, module_name)
                    if isinstance(cls, type):
                        kit_obj = types.SimpleNamespace()
                        instance = None
                        try:
                            instance = cls(self)
                        except TypeError:
                            try:
                                instance = cls()
                            except Exception as e:
                                print(f"⚠️ Kit {module_name} 无法实例化：{e}")

                        if instance:
                            for attr_name in dir(instance):
                                if attr_name.startswith("__"):
                                    continue
                                setattr(kit_obj, attr_name, getattr(instance, attr_name))
                        else:
                            for attr_name in dir(cls):
                                if attr_name.startswith("__"):
                                    continue
                                setattr(kit_obj, attr_name, getattr(cls, attr_name))

                        setattr(self.kit, module_name, kit_obj)
                        print(f"✅ 已加载 kit: {module_name}")

                h = self.calculate_file_hash(module_path)
                if h:
                    self.kit_hashes[module_name] = h

            except Exception as e:
                print(f"❌ 加载 kit {module_name} 失败：{e}")
                traceback.print_exc()
            yield  # 每加载完一个 kit 让 UI 刷新

    # -------------------------
    # 计算文件 hash
    # -------------------------
    def calculate_file_hash(self, filepath):
        try:
            with open(filepath, "rb") as f:
                md5 = hashlib.md5()
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    md5.update(chunk)
                return md5.hexdigest()
        except Exception as e:
            print(f"计算文件hash失败 {filepath}: {e}")
            return None

    # -------------------------
    # 插件加载/重载逻辑
    # -------------------------
    def reload_current_plugin(self):
        current_plugin_name = self.current_plugin_name
        if not current_plugin_name:
            return
        print(f"开始重载插件，当前插件: {current_plugin_name}")
        self.current_plugin_name = current_plugin_name
        # 调用当前插件的刷新回调（如果有实现）
        self.plugin_call_back("on_reload", plugin_name=current_plugin_name)

        self.kit.ui.update_window_title("", current_plugin_name)

        folder = self.plugins_folder
        folder_path = os.path.abspath(folder)
        plugins_to_reload = set()

        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            return

        for filename in os.listdir(folder_path):
            if filename.endswith(".py") and filename != "__init__.py":
                module_name = filename[:-3]
                module_path = os.path.join(folder_path, filename)
                current_hash = self.calculate_file_hash(module_path)
                if current_hash is None:
                    continue
                if (module_name not in self.plugin_hashes or self.plugin_hashes[module_name] != current_hash):
                    plugins_to_reload.add(module_name)
                    self.plugin_hashes[module_name] = current_hash
                    print(f"检测到插件变化: {module_name}")

        # 确保当前插件被重载（即使没有变化）
        if current_plugin_name and current_plugin_name not in plugins_to_reload:
            plugins_to_reload.add(current_plugin_name)
            print(f"强制重载当前插件: {current_plugin_name}")

        # 重载需要更新的插件
        for plugin_name in plugins_to_reload:
            self._reload_single_plugin(plugin_name)

        # 更新 combo 列表
        self._sync_sidebar_plugins()

        # 恢复当前插件
        if current_plugin_name in self.plugins:
            self.run_selected_plugin(current_plugin_name)
        elif self.plugins:
            first = list(self.plugins.keys())[0]
            self.run_selected_plugin(first)

    # 重设插件按钮列表
    def _sync_sidebar_plugins(self):
        # ===== 删除不存在的插件按钮 =====
        for plugin_name in list(self.sidebar_ui.plugin_buttons.keys()):
            if plugin_name not in self.plugins:
                btn = self.sidebar_ui.plugin_buttons.pop(plugin_name)
                btn.setParent(None)
                btn.deleteLater()

        # ===== 新增插件按钮 =====
        for plugin_name, plugin_module in self.plugins.items():
            if plugin_name in self.sidebar_ui.plugin_buttons:
                continue

            # 尝试获取 get_info 信息
            info = {}
            try:
                if hasattr(plugin_module, "get_info"):
                    result = plugin_module.get_info()
                    if isinstance(result, dict):
                        info = result
            except Exception as e:
                print(f"⚠️ 插件 {plugin_name} get_info() 执行失败: {e}")
                traceback.print_exc()

            # 保存到 self.plugin_info
            self.plugin_info[plugin_name] = info

            # 获取 icon（如果有）
            icon = info.get("icon")
            display_name = info.get("display_name",f"{plugin_name}")

            # 创建按钮，传入 icon
            btn = self.sidebar_ui.create_button(display_name, icon=icon)

            # 绑定插件点击
            btn.clicked.connect(
                lambda checked=False, name=plugin_name: self.run_selected_plugin(name)
            )

            # 添加到 sidebar
            self.sidebar_ui.scroll_layout.addWidget(btn)
            self.sidebar_ui.plugin_buttons[plugin_name] = btn

    def _reload_single_plugin(self, plugin_name):
        plugin_path = os.path.abspath(f"{self.plugins_folder}/{plugin_name}.py")
        if not os.path.exists(plugin_path):
            # 插件不存在 -> 清理资源
            self._cleanup_plugin_resources(plugin_name)
            if plugin_name in self.plugins:
                del self.plugins[plugin_name]
            if plugin_name in self.plugin_hashes:
                del self.plugin_hashes[plugin_name]
            print(f"插件文件不存在，已清理: {plugin_name}")
            return

        try:
            # 尝试停止插件内线程
            self.stop_plugin_threads(plugin_name)
            # 清理旧资源
            self._cleanup_plugin_resources(plugin_name)

            # 从 sys.modules 删除旧模块（如果存在）
            if plugin_name in sys.modules:
                try:
                    del sys.modules[plugin_name]
                except Exception:
                    pass

            # 使用 importlib 从文件加载模块
            module = self.load_script_with_deps(plugin_name, plugin_path)
            sys.modules[plugin_name] = module
            self.plugins[plugin_name] = module
            print(f"成功加载/重载插件: {plugin_name}")
        except Exception as e:
            print(f"重载插件 {plugin_name} 失败：{e}")
            traceback.print_exc()
            # 加载失败时清理资源
            self._cleanup_plugin_resources(plugin_name)
            if plugin_name in self.plugins:
                del self.plugins[plugin_name]

    def _cleanup_plugin_resources(self, plugin_name):
        # 从 ui_stack 中移除并销毁对应的 widget
        if plugin_name in self.plugin_frames:
            try:
                widget = self.plugin_frames[plugin_name]
                index = self.ui_stack.indexOf(widget)
                if index != -1:
                    self.ui_stack.removeWidget(widget)
                widget.deleteLater()
            except Exception:
                pass
            del self.plugin_frames[plugin_name]

        if plugin_name in self.plugin_edit_frames:
            try:
                w = self.plugin_edit_frames[plugin_name]
                w.deleteLater()
            except Exception:
                pass
            del self.plugin_edit_frames[plugin_name]

        # 清理树和文件列表
        self.plugin_trees.pop(plugin_name, None)
        self.plugin_tree_frames.pop(plugin_name, None)
        self.plugin_files.pop(plugin_name, None)
        self.plugin_tree_container.pop(plugin_name, None)
        self.plugin_trees_widget.pop(plugin_name, None)
        self.plugin_headers.pop(plugin_name, None)

    def load_plugins_generator(self):
        folder_path = os.path.abspath(self.plugins_folder)
        self.plugins.clear()
        self.plugin_info = {}
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            return

        for filename in os.listdir(folder_path):
            if filename.endswith(".py") and filename != "__init__.py":
                module_name = filename[:-3]
                module_path = os.path.join(folder_path, filename)

                self.loading_dialog.update_text(f"加载 plugin: {module_name}")
                QtWidgets.QApplication.processEvents()

                try:
                    module = self.load_script_with_deps(module_name, module_path)
                    self.plugins[module_name] = module
                    h = self.calculate_file_hash(module_path)
                    if h:
                        self.plugin_hashes[module_name] = h
                except Exception as e:
                    print(f"❌ 加载 plugin {module_name} 失败：{e}")
                    traceback.print_exc()
                yield  # 每加载完一个 plugin 让 UI 刷新

        # ===== 创建 sidebar 插件按钮 =====
        for plugin_name, plugin_module in self.plugins.items():

            if plugin_name in self.sidebar_ui.plugin_buttons:
                continue

            # 尝试获取 icon
            icon = None
            info = {}
            try:
                if hasattr(plugin_module, "get_info"):
                    info = plugin_module.get_info()
                    if isinstance(info, dict):
                        icon = info.get("icon")
            except Exception as e:
                print(f"⚠️ 插件 {plugin_name} get_info() 执行失败: {e}")
                traceback.print_exc()

            # 保存到 self.plugin_info
            self.plugin_info[plugin_name] = info
            display_name = info.get("display_name", f"{plugin_name}")

            # 创建按钮，传入 icon（如果有）
            btn = self.sidebar_ui.create_button(display_name, icon=icon)

            # 绑定插件点击
            btn.clicked.connect(
                lambda checked=False, name=plugin_name: self.run_selected_plugin(name)
            )

            # 添加到 sidebar
            self.sidebar_ui.scroll_layout.addWidget(btn)

            self.sidebar_ui.plugin_buttons[plugin_name] = btn

        # 默认打开第一个插件
        if self.plugins:
            first = list(self.plugins.keys())[0]
            self.run_selected_plugin(first)

    def run_selected_plugin(self, plugin_name=None):
        if plugin_name is None:
            plugin_name = self.current_plugin_name
        if not plugin_name:
            return

        # sidebar按钮激活
        btn = self.sidebar_ui.plugin_buttons.get(plugin_name)
        if btn:
            self.sidebar_ui.activate_button(btn)

        plugin = self.plugins.get(plugin_name)
        if not plugin:
            return

        self.current_plugin_name = plugin_name
        self.kit.ui.update_plugin_title(plugin_name)

        extra_text = self.plugin_title_extras.get(plugin_name, "")
        self.kit.ui.update_window_title(extra_text)

        # ========= 绑定插件上下文 =========
        token = _current_plugin_ctx.set(plugin_name)

        target_id = -100

        try:
            if plugin_name not in self.plugin_frames:
                old_plugin_name = self.current_plugin_name

                plugin_widget = QtWidgets.QWidget()
                plugin_widget.setAutoFillBackground(True)
                pal = plugin_widget.palette()
                pal.setColor(plugin_widget.backgroundRole(), self.bg_color)
                plugin_widget.setPalette(pal)

                self.ui_stack.addWidget(plugin_widget)
                self.plugin_frames[plugin_name] = plugin_widget

                plugin_edit_widget = QtWidgets.QWidget()
                plugin_edit_layout = QtWidgets.QHBoxLayout(plugin_edit_widget)
                plugin_edit_layout.setContentsMargins(0, 0, 0, 0)
                self.plugin_edit_frames[plugin_name] = plugin_edit_widget

                if not hasattr(self, "_edit_stack"):
                    self._edit_stack = QtWidgets.QStackedWidget()
                    for i in reversed(range(self.top_edit_frame_container.layout().count())):
                        item = self.top_edit_frame_container.layout().takeAt(i)
                        w = item.widget()
                        if w:
                            w.setParent(None)
                    self.top_edit_frame_container.layout().addWidget(self._edit_stack)

                self._edit_stack.addWidget(plugin_edit_widget)

                self.plugin_files.setdefault(plugin_name, {})
                self.plugin_trees.setdefault(plugin_name, {})
                self.plugin_tree_frames.setdefault(plugin_name, {})
                self.plugin_tree_container.setdefault(plugin_name, {})
                self.plugin_trees_widget.setdefault(plugin_name, {})
                self.plugin_headers.setdefault(plugin_name, {})
                self.current_plugin = plugin

                if hasattr(plugin, "create_ui"):
                    plugin.create_ui(
                        self,
                        plugin_widget,
                        plugin_edit_widget,
                        self.scale
                    )

                    trees = self.plugin_trees.get(plugin_name, {})
                    if trees:
                        target_id = self.plugin_active_tree_ids.get(plugin_name)
                        if target_id is None:
                            target_id = min(trees.keys())
                        self.kit.ui.activate_tree(target_id, plugin_name)
                    else:
                        self.active_tree_id = 0

            else:
                # 插件 UI 已存在，只同步 active_tree_id
                self.active_tree_id = self.plugin_active_tree_ids.get(plugin_name, 0)

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "插件执行错误", f"插件 {plugin_name} 执行失败:\n{e}"
            )
            traceback.print_exc()
            self._cleanup_plugin_resources(plugin_name)
            self.current_plugin = old_plugin_name
            return

        finally:
            # ========= 恢复上下文 =========
            _current_plugin_ctx.reset(token)

        widget = self.plugin_frames.get(plugin_name)
        if widget:
            self.ui_stack.setCurrentWidget(widget)

        edit_widget = self.plugin_edit_frames.get(plugin_name)
        if edit_widget and hasattr(self, "_edit_stack"):
            self._edit_stack.setCurrentWidget(edit_widget)

        #print(f"{plugin_name}激活tree: {self.active_tree_id}:{target_id}")

    # ============================================================
    # 线程管理：为每个插件维护线程、支持强制停止
    # ============================================================
    def start_plugin_thread(self, target, *args, plugin_name=None, **kwargs):
        """启动可被强制停止的线程"""
        if plugin_name is None:
            plugin_name = self.current_plugin_name

        stop_event = threading.Event()
        thread_id = self._thread_counter
        self._thread_counter += 1

        # 包装线程函数，保证 stop_event 对线程可见
        def thread_wrapper():
            try:
                # 如果 target 接受 stop_event 参数
                sig = inspect.signature(target)
                if "stop_event" in sig.parameters:
                    target(stop_event, *args, **kwargs)
                else:
                    # target 不接受 stop_event，也把 stop_event 传进去给线程可用
                    # 将 stop_event 暴露给 target 线程内使用（如果 target 内用 self.app.plugin_threads 获取的话）
                    # 这里我们让 target 直接执行
                    target(*args, **kwargs)
            except Exception as e:
                print(f"[线程异常] plugin={plugin_name} tid={thread_id}: {e}")
                traceback.print_exc()

        t = threading.Thread(
            target=thread_wrapper,
            daemon=True,
            name=f"{plugin_name}-thread-{thread_id}"
        )

        self.plugin_threads.setdefault(plugin_name, {})[thread_id] = {
            "thread": t,
            "stop": stop_event
        }

        t.start()
        print(f"[线程启动] plugin={plugin_name} tid={thread_id}")
        return thread_id

    def stop_plugin_threads(self, plugin_name=None):
        """强制停止某插件所有线程（用于 reload 时）"""
        if plugin_name is None:
            plugin_name = self.current_plugin_name

        if plugin_name not in self.plugin_threads:
            return

        threads = self.plugin_threads[plugin_name]
        print(f"[线程停止] 开始终止插件 {plugin_name} 的 {len(threads)} 个线程")

        # 通知所有线程停止
        for tid, item in threads.items():
            item["stop"].set()

        # 保证插件内部 self.running 也置 False
        plugin = self.plugins.get(plugin_name)
        if plugin and hasattr(plugin, "running"):
            plugin.running = False

        # 等待线程结束
        for tid, item in list(threads.items()):
            t = item["thread"]
            if t.is_alive():
                t.join(timeout=1.0)
            print(f"[线程结束] plugin={plugin_name} tid={tid}")

        del self.plugin_threads[plugin_name]


# -------------------------
# 启动
# -------------------------
def main():
    app = QtWidgets.QApplication(sys.argv)
    window = ToolsLoader()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()

# 打包指令：pyinstaller --noconfirm --onefile --windowed -i icon.ico --add-data "icon.ico;." uiqt0_2_2_8.py