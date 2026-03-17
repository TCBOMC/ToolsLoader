import os
import sys
import types
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
from PyQt5.QtWidgets import QDialog, QTextEdit, QWidget, QLabel, QApplication, QVBoxLayout
from PyQt5.QtCore import Qt, pyqtBoundSignal, QThread, pyqtSignal

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
        self.plugin_info = {}
        self.plugin_files = {}
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

        # 顶部容器
        top_widget = QtWidgets.QWidget()
        top_layout = QtWidgets.QGridLayout(top_widget)
        top_layout.setContentsMargins(self.padx, self.pady, self.padx, 0)
        top_layout.setHorizontalSpacing(self.padx)
        top_layout.setVerticalSpacing(self.pady)
        layout.addWidget(top_widget)

        # 左侧 plugin_frame
        plugin_frame_widget = QtWidgets.QWidget()
        plugin_frame_layout = QtWidgets.QHBoxLayout(plugin_frame_widget)
        plugin_frame_layout.setContentsMargins(0, 0, 0, 0)
        plugin_frame_layout.setSpacing(self.padx)
        top_layout.addWidget(plugin_frame_widget, 0, 0, 1, 1)

        # 右侧 tree_edit_frame
        tree_edit_widget = QtWidgets.QWidget()
        self.tree_edit_layout = QtWidgets.QHBoxLayout(tree_edit_widget)
        self.tree_edit_layout.setContentsMargins(0, 0, 0, 0)
        self.tree_edit_layout.setSpacing(self.padx)
        top_layout.addWidget(tree_edit_widget, 0, 1, 1, 1)

        # 第二行：top_edit_frame
        self.top_edit_frame_container = QtWidgets.QWidget()
        top_edit_layout = QtWidgets.QHBoxLayout(self.top_edit_frame_container)
        top_edit_layout.setContentsMargins(self.padx, self.pady // 2, self.padx, self.pady // 2)
        layout.addWidget(self.top_edit_frame_container)

        # reload 按钮 + plugin 下拉框
        self.reload_btn = QtWidgets.QPushButton("↻")
        self.reload_btn.setFixedWidth(int(20 * self.scale))
        self.reload_btn.setFixedHeight(int(20 * self.scale))
        self.reload_btn.clicked.connect(self.on_reload)
        plugin_frame_layout.addWidget(self.reload_btn)
        reload_font = self.reload_btn.font()
        reload_font.setPointSizeF(self.font_size * self.scale)
        self.reload_btn.setFont(reload_font)

        self.plugin_combo = QtWidgets.QComboBox()
        self.plugin_combo.setMinimumWidth(int(100 * self.scale))
        self.plugin_combo.setFixedHeight(int(20 * self.scale))
        self.plugin_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.plugin_combo.currentIndexChanged.connect(self.run_selected_plugin)
        plugin_frame_layout.addWidget(self.plugin_combo)
        plugin_combo_font = self.plugin_combo.font()
        plugin_combo_font.setPointSizeF(self.font_size * self.scale)
        self.plugin_combo.setFont(plugin_combo_font)

        self.create_edit_widget()

        # 中央插件显示区
        self.ui_stack = QtWidgets.QStackedWidget()
        self.ui_stack.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.ui_stack, 1)

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
            ("导入", "import_files"),
            ("删除", "delete_selected"),
            ("清空", "clear_all"),
            ("复制", "copy_selected"),
            ("剪切", "cut_selected"),
            ("粘贴", "paste_items"),
        ]

        created_buttons = []

        for text, method_name in buttons_info:
            if hasattr(self.kit.ui, method_name):
                method = getattr(self.kit.ui, method_name)
                try:
                    btn = self.kit.ui.create_button(
                        text,
                        lambda _, m=method: m(self.active_tree_id)  # 加 _ 来接收 clicked 的参数
                    )
                    created_buttons.append(btn)
                except Exception as e:
                    print(f"按钮 '{text}' 创建失败: {e}")
            else:
                print(f"按钮 '{text}' 被跳过，方法 '{method_name}' 不存在")

        # 至少有一个按钮成功创建才添加标签
        if created_buttons:
            label = QtWidgets.QLabel("列表操作:")
            self.tree_edit_layout.addWidget(label)
            for btn in created_buttons:
                self.tree_edit_layout.addWidget(btn)

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
        current_plugin_name = self.plugin_combo.currentText()
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
        self.plugin_combo.blockSignals(True)  # 🚫 暂时禁止触发信号
        self.plugin_combo.clear()
        self.plugin_combo.addItems(list(self.plugins.keys()))
        self.plugin_combo.blockSignals(False)  # ✅ 恢复信号

        # 恢复选择
        if current_plugin_name in self.plugins:
            idx = self.plugin_combo.findText(current_plugin_name)
            if idx >= 0:
                self.plugin_combo.setCurrentIndex(idx)
                # 手动调用一次即可，不会重复
                self.run_selected_plugin()
        elif self.plugins:
            first = list(self.plugins.keys())[0]
            idx = self.plugin_combo.findText(first)
            if idx >= 0:
                self.plugin_combo.setCurrentIndex(idx)
                self.run_selected_plugin()

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

        # 更新下拉列表
        self.plugin_combo.blockSignals(True)
        self.plugin_combo.clear()
        self.plugin_combo.addItems(list(self.plugins.keys()))
        self.plugin_combo.blockSignals(False)
        if self.plugins:
            first = list(self.plugins.keys())[0]
            idx = self.plugin_combo.findText(first)
            if idx >= 0:
                self.plugin_combo.setCurrentIndex(idx)
                self.run_selected_plugin()

    def run_selected_plugin(self):
        plugin_name = self.plugin_combo.currentText()
        if not plugin_name:
            return

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

                old_plugin_name = self.current_plugin_name
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