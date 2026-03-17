import os
import sys
import json
import time
import ctypes
import hashlib
import types
import traceback
import importlib.util
import traceback
from functools import wraps
import faulthandler
import signal
import contextvars
from inspect import signature, Parameter
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QWidget, QSizePolicy, QLabel, QComboBox, QApplication, QPushButton, QShortcut, QFileDialog, QMessageBox, QSplitter, QSplitterHandle, QFrame, QBoxLayout, QGridLayout, QHBoxLayout, QVBoxLayout, QTreeWidget, QAbstractItemView, QTreeWidgetItem, QHeaderView, QStyledItemDelegate, QLineEdit, QCheckBox
from PyQt5.QtCore import Qt, QEventLoop, QPropertyAnimation, pyqtSlot, QPoint, QMetaObject, QSize, QEvent, QObject, QThread, pyqtSignal, QTimer, Q_ARG
from PyQt5.QtGui import QCursor, QPainter, QKeySequence, QColor, QBrush


class MainThreadExecutor(QObject):
    """
    独立的主线程执行器，用于在子线程中安全地执行主线程函数

    特性：
    - 装饰器方式：@executor.run_in_main_thread（需要返回值） 或 @executor.run_in_main_thread_async（不需要返回值）
    - 直接调用方式：call_in_main_thread 和 call_in_main_thread_async
    - 支持返回值：同步执行时可以获取返回值
    - 保持上下文：使用 contextvars 保持上下文
    """

    # 通用执行信号
    _execute_function_signal = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent = parent

        # 连接信号到主线程执行槽
        self._execute_function_signal.connect(
            self._execute_function_main,
            Qt.QueuedConnection
        )

        # 存储正在执行的任务信息（用于调试）
        self._active_tasks = {}

    # ==================================================
    # 公共API：装饰器
    # ==================================================

    def run_in_main_thread(self, func):
        """
        装饰器：将函数包装为可在主线程执行的版本（同步，有返回值）

        用法：
        @executor.run_in_main_thread
        def my_function(arg1, arg2):
            return arg1 + arg2

        # 在任何线程中调用
        result = my_function(1, 2)  # 会阻塞直到主线程执行完毕
        """

        @wraps(func)
        def wrapper(*args, **kwargs):
            return self.call_in_main_thread(func, *args, **kwargs)

        return wrapper

    def run_in_main_thread_async(self, func):
        """
        装饰器：将函数包装为可在主线程异步执行的版本（无返回值，不阻塞）

        用法：
        @executor.run_in_main_thread_async
        def update_ui(text):
            self.label.setText(text)

        # 在任何线程中调用，立即返回，不阻塞
        update_ui("Hello")
        """

        @wraps(func)
        def wrapper(*args, **kwargs):
            self.call_in_main_thread_async(func, *args, **kwargs)

        return wrapper

    # ==================================================
    # 公共API：直接调用
    # ==================================================

    def call_in_main_thread(self, func, *args, **kwargs):
        """
        直接调用函数在主线程执行（同步，有返回值）

        参数：
            func: 要执行的函数
            *args, **kwargs: 函数的参数

        返回：
            函数的返回值

        用法：
            result = executor.call_in_main_thread(my_function, arg1, arg2)
        """
        # 如果已经在主线程，直接执行
        if QThread.currentThread() == QApplication.instance().thread():
            return func(*args, **kwargs)

        # 在子线程中，需要通过信号发送到主线程并等待结果
        ctx = contextvars.copy_context()
        loop = QEventLoop()
        result_container = []
        error_container = []

        # 生成任务ID（用于调试）
        task_id = id(func)

        payload = {
            "task_id": task_id,
            "ctx": ctx,
            "func": func,
            "args": args,
            "kwargs": kwargs,
            "loop": loop,
            "result_container": result_container,
            "error_container": error_container
        }

        # 记录任务开始
        self._active_tasks[task_id] = {
            "func": func.__name__,
            "start_time": QApplication.instance().applicationDisplayName()  # 简化，实际可用time.time()
        }

        try:
            # 发射信号
            self._execute_function_signal.emit(payload)

            # 等待执行完成
            loop.exec()

            # 如果有错误，抛出异常
            if error_container:
                raise error_container[0]

            # 返回结果
            return result_container[0] if result_container else None

        finally:
            # 清理任务记录
            self._active_tasks.pop(task_id, None)

    def call_in_main_thread_async(self, func, *args, **kwargs):
        """
        直接调用函数在主线程执行（异步，无返回值，不阻塞）

        参数：
            func: 要执行的函数
            *args, **kwargs: 函数的参数

        用法：
            executor.call_in_main_thread_async(my_function, arg1, arg2)
        """
        # 如果已经在主线程，直接执行
        if QThread.currentThread() == QApplication.instance().thread():
            return func(*args, **kwargs)

        # 在子线程中，通过信号发送到主线程（不等待结果）
        ctx = contextvars.copy_context()

        payload = {
            "task_id": id(func),
            "ctx": ctx,
            "func": func,
            "args": args,
            "kwargs": kwargs,
            "loop": None,
            "result_container": None,
            "error_container": None
        }

        self._execute_function_signal.emit(payload)

    # ==================================================
    # 内部方法：在主线程执行函数
    # ==================================================

    @pyqtSlot(object)
    def _execute_function_main(self, payload):
        """
        在主线程中执行函数（内部使用）
        """
        ctx = payload["ctx"]
        func = payload["func"]
        args = payload["args"]
        kwargs = payload["kwargs"]
        loop = payload.get("loop")
        result_container = payload.get("result_container")
        error_container = payload.get("error_container")

        def execute():
            try:
                # 执行函数
                result = func(*args, **kwargs)

                # 存储结果
                if result_container is not None:
                    result_container.append(result)

            except Exception as e:
                # 存储错误
                if error_container is not None:
                    error_container.append(e)
                else:
                    # 如果没有错误容器，打印错误但不抛出
                    import traceback
                    traceback.print_exc()
            finally:
                # 如果有事件循环，退出
                if loop:
                    loop.quit()

        # 在指定的上下文中执行
        ctx.run(execute)

    # ==================================================
    # 工具方法
    # ==================================================

    def is_main_thread(self):
        """检查当前是否在主线程"""
        return QThread.currentThread() == QApplication.instance().thread()

    def get_active_tasks(self):
        """获取当前正在执行的任务信息（用于调试）"""
        return dict(self._active_tasks)

    def wait_for_all_tasks(self, timeout_ms=5000):
        """
        等待所有异步任务完成（谨慎使用）

        参数：
            timeout_ms: 超时时间（毫秒）
        """
        if not self._active_tasks:
            return True

        # 创建一个事件循环等待所有任务完成
        loop = QEventLoop()
        timer = QApplication.instance().startTimer(timeout_ms)

        # 这里需要更复杂的实现来跟踪所有任务
        # 简化版本：只是检查任务列表是否为空
        import time
        start = time.time()
        while self._active_tasks and (time.time() - start) * 1000 < timeout_ms:
            QApplication.processEvents()
            time.sleep(0.01)

        return len(self._active_tasks) == 0

# 创建全局 executor
executor = MainThreadExecutor()

class SignalManager(QObject):
    """
    统一管理各种信号：消息框、树染色、树刷新、文件处理等
    """
    # ------------------------
    # signals（payload = dict）
    # ------------------------
    show_message_signal = pyqtSignal(object)
    color_signal = pyqtSignal(object)
    refresh_signal = pyqtSignal(object)
    one_file_done_signal = pyqtSignal(object)
    clear_color_signal = pyqtSignal(object)
    update_window_title_signal = pyqtSignal(object)
    add_item_signal = pyqtSignal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.parent_ref = parent
        self._last_message_result = None

        # 主线程 slot
        self.show_message_signal.connect(self._show_message_box_main, Qt.QueuedConnection)
        self.color_signal.connect(self._apply_color_main, Qt.QueuedConnection)
        self.refresh_signal.connect(self._refresh_tree_main, Qt.QueuedConnection)
        self.one_file_done_signal.connect(self._handle_one_file_done_main, Qt.QueuedConnection)
        self.clear_color_signal.connect(self._clear_tree_item_color_main, Qt.QueuedConnection)
        self.update_window_title_signal.connect(self._update_window_title_main, Qt.QueuedConnection)
        self.add_item_signal.connect(self._add_item_main, Qt.QueuedConnection)

        # 保存正在运行的线程，防止被 GC
        self._threads = []

    # ==================================================
    # 内部工具：统一的 Context 切换执行器
    # ==================================================
    def _run_in_main_context(self, payload):
        """
        payload:
        {
            "ctx": contextvars.Context,
            "func": callable,
            "args": tuple
        }
        """
        ctx = payload["ctx"]
        func = payload["func"]
        args = payload["args"]

        main = self.parent_ref.main
        return main.run_in_context(ctx, func, *args)

    def _emit_with_context(self, signal, func, *args):
        ctx = contextvars.copy_context()
        signal.emit({
            "ctx": ctx,
            "func": func,
            "args": args,
        })

    # ------------------------
    # 新增：线程安全 add_item
    # ------------------------
    @pyqtSlot(object)
    def _add_item_main(self, payload):
        self._run_in_main_context(payload)

    def add_item(self, content, tree_id, plugin_name, row=None, replace=False, add_extra=False):
        if QThread.currentThread() == QApplication.instance().thread():
            self.parent_ref.add_item_main(content, tree_id, plugin_name, row, replace, add_extra)
            return

        self._emit_with_context(
            self.add_item_signal,
            self.parent_ref.add_item_main,
            content, tree_id, plugin_name, row, replace, add_extra
        )

    # ------------------------
    # 消息框
    # ------------------------
    @pyqtSlot(object)
    def _show_message_box_main(self, payload):
        """在主线程中弹窗并设置结果"""
        title, text, icon, loop = payload

        if icon == "info":
            QMessageBox.information(None, title, text)
            self._last_message_result = None
        elif icon == "warning":
            QMessageBox.warning(None, title, text)
            self._last_message_result = None
        elif icon == "error":
            QMessageBox.critical(None, title, text)
            self._last_message_result = None
        elif icon == "2question":
            reply = QMessageBox.question(None, title, text,
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            self._last_message_result = "yes" if reply == QMessageBox.StandardButton.Yes else "no"
        elif icon == "3question":
            msg_box = QMessageBox(QMessageBox.Icon.Question, title, text)
            msg_box.setStandardButtons(QMessageBox.StandardButton.Yes |
                                       QMessageBox.StandardButton.No |
                                       QMessageBox.StandardButton.Cancel)
            msg_box.setDefaultButton(QMessageBox.StandardButton.No)
            reply = msg_box.exec()
            if reply == QMessageBox.StandardButton.Yes:
                self._last_message_result = "yes"
            elif reply == QMessageBox.StandardButton.No:
                self._last_message_result = "no"
            else:
                self._last_message_result = "cancel"
        else:
            QMessageBox.information(None, title, text)
            self._last_message_result = None

        # 如果有阻塞的事件循环，退出
        if loop:
            loop.quit()

    def show_message(self, title, text, icon="info"):
        """线程安全消息框，支持子线程阻塞等待结果"""
        if QThread.currentThread() == QApplication.instance().thread():
            # 主线程直接调用
            self._last_message_result = None
            self._show_message_box_main((title, text, icon, None))
            return self._last_message_result

        # 子线程：使用 QEventLoop 阻塞等待主线程弹窗
        loop = QEventLoop()
        self.show_message_signal.emit((title, text, icon, loop))
        loop.exec()  # 阻塞，直到 _show_message_box_main 调用 loop.quit()
        return self._last_message_result

    # ------------------------
    # 树控件染色
    # ------------------------
    @pyqtSlot(object)
    def _clear_tree_item_color_main(self, payload):
        self._run_in_main_context(payload)

    def clear_tree_item_color(self, tree_id, plugin_name):
        self._emit_with_context(
            self.clear_color_signal,
            self.parent_ref.clear_tree_item_color_main,
            tree_id, plugin_name
        )

    @pyqtSlot(object)
    def _apply_color_main(self, payload):
        self._run_in_main_context(payload)

    def apply_color(self, tree_id, fullpath, state, plugin_name):
        self._emit_with_context(
            self.color_signal,
            self.parent_ref.apply_color_main,
            tree_id, fullpath, state, plugin_name
        )

    # ------------------------
    # 树刷新
    # ------------------------
    @pyqtSlot(object)
    def _refresh_tree_main(self, payload):
        self._run_in_main_context(payload)

    def refresh_tree(self, tree_id, plugin_name):
        if QThread.currentThread() == QApplication.instance().thread():
            self.parent_ref.refresh_tree_main(tree_id, plugin_name)
            return

        self._emit_with_context(
            self.refresh_signal,
            self.parent_ref.refresh_tree_main,
            tree_id, plugin_name
        )

    # ------------------------
    # 文件处理
    # ------------------------
    @pyqtSlot(object)
    def _handle_one_file_done_main(self, payload):
        self._run_in_main_context(payload)

    def one_file_done(self, plugin_name, tree_id, file_info):
        self._emit_with_context(
            self.one_file_done_signal,
            self._handle_one_file_done,
            plugin_name, tree_id, file_info
        )

    def _handle_one_file_done(self, plugin_name, tree_id, file_info):
        parent = self.parent_ref

        existing_files = parent.plugin_files.get(plugin_name, {}).get(tree_id, [])
        existing_paths = {f["fullpath"] for f in existing_files}

        if file_info["fullpath"] in existing_paths:
            return  # 已存在 → 跳过

        if plugin_name not in parent.plugin_files:
            parent.plugin_files[plugin_name] = {}
        if tree_id not in parent.plugin_files[plugin_name]:
            parent.plugin_files[plugin_name][tree_id] = []

        parent.plugin_files[plugin_name][tree_id].append(file_info)
        parent.plugin_call_back(
                "on_add_end",
                tree_id,
                plugin_name=plugin_name
            )
        parent.refresh_tree(tree_id)

    # ------------------------
    # 窗口标题更新
    # ------------------------
    @pyqtSlot(object)
    def _update_window_title_main(self, payload):
        self._run_in_main_context(payload)

    def update_window_title(self, title, plugin_name):
        if QThread.currentThread() == QApplication.instance().thread():
            self.parent_ref.update_window_title_main(title, plugin_name)
            return

        self._emit_with_context(
            self.update_window_title_signal,
            self.parent_ref.update_window_title_main,
            title, plugin_name
        )


class ToastPopup(QWidget):
    """淡入淡出 Toast 弹窗"""
    active_toast = None

    def __init__(self, parent=None, text="完成了！", duration=2000, align="c", pos=(0, 0)):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus)

        self.label = QLabel(text, self)
        self.label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 165);"
            "color: white;"
            "padding: 5px 5px;"
            "border-radius: 4px;"
        )
        self.label.adjustSize()
        self.resize(self.label.size())

        self.align = self._validate_align(align)
        self.pos_offset = QPoint(*pos)

        self.animation = QPropertyAnimation(self, b"windowOpacity")
        self.animation.setDuration(300)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)

        QTimer.singleShot(duration, self.fade_out)

    def _validate_align(self, align: str) -> str:
        """检查 align 是否有效，无效或冲突返回 'c'"""
        valid_chars = set("nswe")  # 支持的方向字母
        align = align.lower()

        # 非法字母直接返回 'c'
        if any(c not in valid_chars for c in align):
            return "c"

        # 冲突检查
        if "n" in align and "s" in align:
            return "c"
        if "w" in align and "e" in align:
            return "c"
        if len(align) > 2:  # 出现 3 个及以上字母视为 'c'
            return "c"

        # 空字符串默认中心
        if len(align) == 0:
            return "c"

        return align

    def show(self):
        if ToastPopup.active_toast:
            ToastPopup.active_toast.close()
        ToastPopup.active_toast = self

        if self.parent():
            self.move(self.calculate_position())
        super().show()
        self.animation.setDirection(QPropertyAnimation.Forward)
        self.animation.start()

    def calculate_position(self):
        """根据align和pos_offset计算弹窗在屏幕上的位置（以parent为基准）"""
        parent = self.parent()
        pw = parent.width()
        ph = parent.height()
        sw = self.width()
        sh = self.height()

        # 默认中心位置
        align = self.align.lower()
        # 父窗口左上角在屏幕坐标
        px, py = parent.mapToGlobal(parent.rect().topLeft()).x(), parent.mapToGlobal(parent.rect().topLeft()).y()

        # 默认中心
        x = px + (pw - sw) // 2
        y = py + (ph - sh) // 2

        # 上下对齐
        if "n" in align:
            y = py
        if "s" in align:
            y = py + ph - sh

        # 左右对齐
        if "w" in align:
            x = px
        if "e" in align:
            x = px + pw - sw

        # 返回加上偏移量的位置
        return QPoint(x + self.pos_offset.x(), y + self.pos_offset.y())

    def fade_out(self):
        self.animation.setDirection(QPropertyAnimation.Backward)
        self.animation.start()
        self.animation.finished.connect(self.close_toast)

    def close_toast(self):
        ToastPopup.active_toast = None
        self.close()


class LineSplitterHandle(QSplitterHandle):
    """自定义 handle，实现 line 分隔线"""
    def __init__(self, orientation, parent, line_color="#d0d0d0"):
        super().__init__(orientation, parent)
        self.line_color = line_color
        self.setMinimumSize(1, 1)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, Qt.transparent)
        painter.setPen(QColor(self.line_color))
        if self.orientation() == Qt.Horizontal:
            x = rect.width() // 2
            painter.drawLine(x, 0, x, rect.height())
        else:
            y = rect.height() // 2
            painter.drawLine(0, y, rect.width(), y)
        painter.end()


class LineSplitter(QSplitter):
    """自定义 splitter，支持 line handle"""
    def __init__(self, orientation=Qt.Horizontal, parent=None, line_color="#d0d0d0"):
        super().__init__(orientation, parent)
        self.line_color = line_color

    def createHandle(self):
        handle = LineSplitterHandle(self.orientation(), self, self.line_color)
        return handle


class TreeEditDelegate(QStyledItemDelegate):
    def __init__(self, ui_instance, tree_id):
        super().__init__()
        self.ui = ui_instance  # 只保存 ui 实例
        self.tree_id = tree_id  # 保存 tree_id

    def createEditor(self, parent, option, index):
        # 延迟获取 tree 和 mode
        tree, mode = self.ui.get_tree(self.tree_id, get_mode=True)
        col = index.column()

        # 禁止编辑 checkbox / filename / 添加按钮列
        if col == 0 or (mode == "表格" and col == tree.columnCount() - 1):
            return None

        editor = QLineEdit(parent)
        editor.original_value = index.model().data(index)
        return editor

    def setEditorData(self, editor, index):
        text = index.model().data(index)
        editor.setText(text)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text())
        tree, _ = self.ui.get_tree(self.tree_id, get_mode=True)
        item = tree.itemFromIndex(index)
        if item:
            self.ui.on_item_edited(item, index.column(), self.tree_id)

    def eventFilter(self, editor, event):
        # 回车保存
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.commitData.emit(editor)
            self.closeEditor.emit(editor)
            return True

        # Esc 取消恢复原值
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            editor.setText(editor.original_value)
            self.closeEditor.emit(editor)
            return True

        # 失焦保存
        if event.type() == QEvent.FocusOut:
            self.commitData.emit(editor)
            self.closeEditor.emit(editor)

        return super().eventFilter(editor, event)

# UI工具集
class ui(QObject):
    def __init__(self, main):
        super().__init__()
        self.main = main

        # 创建执行器
        self.executor = MainThreadExecutor(self)
        self.signal_manager = SignalManager(parent=self)
        #print("载入ui工具集")

    def __getattr__(self, name):
        """
        当访问 ui.xxx 且 ui 本身没有这个属性时，
        自动返回 self.main.xxx 的值。
        """
        if hasattr(self.main, name):
            return getattr(self.main, name)
        raise AttributeError(f"'ui' object has no attribute '{name}'")

    def __setattr__(self, name, value):
        if name != "main" and hasattr(self, "main") and hasattr(self.main, name):
            setattr(self.main, name, value)
        else:
            super().__setattr__(name, value)

    def create_button(self, text, handler):
        btn = QtWidgets.QPushButton(text)

        # 设置字体（使用 pointSizeF 自动适配 DPI）
        font = btn.font()
        font.setPointSizeF(self.font_size * self.scale)
        btn.setFont(font)

        # 设置按钮大小（跟随 self.scale 缩放）
        btn.setFixedWidth(int(40 * self.scale))
        btn.setFixedHeight(int(20 * self.scale))

        btn.clicked.connect(handler)
        return btn

    def update_plugin_title(self, plugin_name):
        #print("update_plugin_title:", plugin_name)
        # === 修改：仅更新前缀并直接设置窗口标题为前缀 ===
        # 这样后续插件调用 update_window_title(extra_text) 时会把 extra_text 追加到这个前缀后面
        info = self.main.plugin_info.get(plugin_name, {})
        display_name = info.get('display_name', plugin_name)
        self.main.window_title_prefix = f"插件化 UI 系统 插件：{display_name}"
        # 立即把窗口标题设置为当前前缀（无额外文本）
        self.main.setWindowTitle(self.main.window_title_prefix)

    # -------------------------
    # 提供给插件的通用函数（保留接口）
    # -------------------------
    def fix_single_font(self, widget):
        """只调整单个控件字体，使用 self.font_size 和 self.scale"""
        font = widget.font()
        font.setPointSizeF(self.font_size * self.scale)
        widget.setFont(font)

    def update_window_title(self, extra_text="", plugin_name=None):
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        self.signal_manager.update_window_title(extra_text, plugin_name)

    def update_window_title_main(self, extra_text="", plugin_name=None):
        current_plugin = plugin_name or self.main.get_current_plugin_name()
        fallback_plugin = self.current_plugin_name
        plugin_name = current_plugin or fallback_plugin
        #print(f"尝试更新插件：{current_plugin}, 当前显示插件：{fallback_plugin}\n标题: {extra_text}")

        # 始终记录 extra_text（即使不是当前显示插件）
        if plugin_name:
            self.plugin_title_extras[plugin_name] = extra_text

        # 如果当前主窗口显示的插件 ≠ 本对象对应的插件
        # 则不进行 UI 标题的实际更新
        if current_plugin != self.current_plugin_name:
            return

        base_title = getattr(self.main, "window_title_prefix", "插件化 UI 系统")
        if extra_text:
            new_title = f"{base_title} {extra_text}"
        else:
            new_title = base_title

        self.main.setWindowTitle(new_title)
        #self.signal_manager.update_window_title(new_title)

    def show_message_box(self, title, text, icon="info"):
        # 主类接口，内部调用 MessageBoxHelper
        return self.signal_manager.show_message(title, text, icon)

    def create_split_frame(self, parent=None, orient="horizontal", n=None,
                           sashwidth=5, handle_color="#d0d0d0", bg="#f0f0f0",
                           handlesize=8, opaqueresize=True, bd=0,
                           sashrelief="raised", width=None, height=None,
                           ratios=None):
        """
        PyQt splitter 创建函数
        支持浮雕和 line handle
        ratios: 可选参数 [(ratio, expandable), ...]
        """
        orient_map = {"horizontal": Qt.Horizontal, "vertical": Qt.Vertical}
        if orient not in orient_map:
            raise ValueError("orient 参数必须是 'horizontal' 或 'vertical'")

        # ===== 自动处理 n =====
        if ratios is not None and n is None:
            n = len(ratios)
        elif n is None:
            n = 2  # 默认值

        if sashrelief == "line":
            splitter = LineSplitter(orient_map[orient], parent, line_color=handle_color)
        else:
            splitter = QSplitter(orient_map[orient], parent)
            relief_styles = {
                "flat": f"border: 0px solid {bg};",
                "raised": f"border: 2px solid white; border-right-color: gray; border-bottom-color: gray;",
                "sunken": f"border: 2px solid gray; border-right-color: white; border-bottom-color: white;",
                "ridge": f"border: 2px ridge {bg};",
                "groove": f"border: 2px groove {bg};",
                "solid": f"border: 2px solid {bg};"
            }
            handle_style = relief_styles.get(sashrelief, relief_styles["flat"])
            splitter.setStyleSheet(f"""
                QSplitter::handle {{
                    {handle_style}
                }}
                QSplitter::handle:hover {{
                    background: lightgray;
                }}
            """)

        splitter.setHandleWidth(sashwidth)
        splitter.setOpaqueResize(opaqueresize)
        if width:
            splitter.setFixedWidth(width)
        if height:
            splitter.setFixedHeight(height)

        # 处理 ratios，不直接使用引用
        local_ratios = []
        if ratios is None:
            local_ratios = [(1, True)] * n
        else:
            for i in range(n):
                if i < len(ratios):
                    # 拷贝元组，确保不引用原对象
                    r, e = ratios[i]
                    local_ratios.append((r, e))
                else:
                    local_ratios.append((1, True))

        subframes = []
        for i in range(n):
            sub = QFrame(splitter)
            sub.setObjectName(f"{parent.objectName() if parent else 'frame'}_sub{i}")
            if bd == 0:
                sub.setFrameShape(QFrame.NoFrame)
                sub.setStyleSheet(f"QFrame#{sub.objectName()} {{ background-color: {bg}; }}")
            else:
                sub.setFrameShape(QFrame.StyledPanel)
                sub.setStyleSheet(
                    f"QFrame#{sub.objectName()} {{ background-color: {bg}; border: {bd}px solid transparent; }}")
            splitter.addWidget(sub)
            subframes.append(sub)

            # 设置伸缩性
            ratio, expandable = local_ratios[i]
            splitter.setStretchFactor(i, ratio)
            if not expandable:
                sub.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # ===== 循环结束后，统一设置初始大小比例 =====
        total_ratio = sum(r for r, _ in local_ratios)
        initial_sizes = [r / total_ratio for r, _ in local_ratios]
        splitter.setSizes([int(size * 1000) for size in initial_sizes])

        splitter.update()

        if parent is not None:
            if isinstance(parent.layout(), (QBoxLayout, QGridLayout)):
                parent.layout().addWidget(splitter)
            else:
                lay = QVBoxLayout(parent)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.addWidget(splitter)

        return splitter, subframes

    def create_tree_view(self, parent=None, tree_index=0, extra_columns=None, container_style=None, mode="文件", plugin_name=None, show_checkbox=True):
        #print(f"处理前extra_columns:{extra_columns}")
        if extra_columns is None:
            extra_columns = [(None, 200, True, None, None, [])]  # 6元组，添加position参数
        else:
            new_cols = []
            for col in extra_columns:
                col_len = len(col)

                if col_len == 3:
                    # (name, width, stretch)
                    name, width, stretch = col
                    new_cols.append((name, width, stretch, None, None, []))

                elif col_len == 4:
                    # 新格式: (name, width, stretch, position)
                    name, width, stretch, position = col
                    if position is not None and not isinstance(position, int):
                        raise ValueError("position 参数必须是整数或 None")
                    new_cols.append((name, width, stretch, position, None, []))

                elif col_len == 5:
                    # 新格式: (name, width, stretch, position, widget_spec)
                    name, width, stretch, position, widget_spec = col
                    if position is not None and not isinstance(position, int):
                        raise ValueError("position 参数必须是整数或 None")
                    new_cols.append((name, width, stretch, position, widget_spec, []))

                elif col_len == 6:
                    # 新格式: (name, width, stretch, position, widget_spec, callbacks)
                    name, width, stretch, position, widget_spec, callbacks = col
                    if position is not None and not isinstance(position, int):
                        raise ValueError("position 参数必须是整数或 None")
                    if callbacks is None:
                        callbacks = []
                    new_cols.append((name, width, stretch, position, widget_spec, callbacks))

                else:
                    raise ValueError("extra_columns 必须是 3、4、5 或 6 元组")

            extra_columns = new_cols

        if container_style is None:
            container_style = f"""
                QFrame#tree_container_{tree_index} {{
                    border: 1px solid #cccccc;
                    background-color: transparent;
                }}
                """

        # ✅ TreeView 外部容器：2px 边框，默认背景色
        container = QFrame(parent)
        container.setObjectName(f"tree_container_{tree_index}")  # 精准 CSS 作用域
        container.setStyleSheet(container_style)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        tree = QTreeWidget(container)

        # ❗ 禁掉 QTree 自己的边框，否则叠加变粗
        tree.setFrameShape(QFrame.NoFrame)

        # ======================
        # 处理列位置排序
        # ======================
        # 分离指定了位置和未指定位置的列
        positioned_cols = []  # (position, original_index, col_data)
        unpositioned_cols = []  # (original_index, col_data)

        for idx, col in enumerate(extra_columns):
            name, width, stretch, position, widget_spec, callbacks = col

            if position is not None:
                positioned_cols.append((position, idx, col))
            else:
                unpositioned_cols.append((idx, col))

        # 按位置排序（允许负值）
        positioned_cols.sort(key=lambda x: x[0])

        # 创建最终显示顺序的列表
        display_columns = []
        used_positions = set()

        # 第一步：按排序后的位置放置指定列
        for pos, orig_idx, col in positioned_cols:
            used_positions.add(pos)
            display_columns.append((pos, orig_idx, col))

        # 第二步：为未指定位置的列寻找合适的位置
        next_pos = 0
        for orig_idx, col in unpositioned_cols:
            # 寻找最小的未被占用的位置
            while next_pos in used_positions:
                next_pos += 1
            used_positions.add(next_pos)
            display_columns.append((next_pos, orig_idx, col))
            next_pos += 1

        # 按位置排序最终列表
        display_columns.sort(key=lambda x: x[0])

        # 提取用于显示的列数据（保持原始数据不变）
        display_cols_data = [col for _, _, col in display_columns]

        # 创建原始索引到显示索引的映射
        orig_to_display = {}
        for display_idx, (_, orig_idx, _) in enumerate(display_columns):
            orig_to_display[orig_idx] = display_idx

        # 表头模式 - 使用display_cols_data创建显示
        if mode == "表格":
            # 多出 1 列：用作添加按钮
            tree.setColumnCount(len(display_cols_data) + 2)

            labels = ["☐"] + [
                f"列表{tree_index}" if col[0] is None else col[0] for col in display_cols_data
            ] + [""]

            tree.setHeaderLabels(labels)

        else:
            # 原文件模式
            tree.setColumnCount(len(display_cols_data) + 1)
            tree.setHeaderLabels(["☐"] + [
                f"列表{tree_index}" if col[0] is None else col[0] for col in display_cols_data
            ])

        # ✅ 允许 Ctrl/Shift 多选
        tree.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # ✅ 取消表头双击（防止吞掉单击）
        #header = HeaderClickableNoDbl(Qt.Horizontal, tree)
        header = QHeaderView(Qt.Horizontal, tree)
        tree.setHeader(header)
        header.setSectionsClickable(True)

        # ✅ 第一列固定
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        tree.setColumnWidth(0, 25)

        # ✅ 控制复选框列显示/隐藏
        tree.setColumnHidden(0, not show_checkbox)

        # ✅ 横向滚动条
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        tree.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)

        # ✅ 文件名列可拖
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        tree.setColumnWidth(1, 240)

        # ✅ 最小列宽
        header.setMinimumSectionSize(8)

        # ✅ 其他列 - 使用display_cols_data设置显示
        for i, col in enumerate(display_cols_data):
            # col 是完整元组: (name, width, stretch, position, widget_spec, callbacks)
            width = col[1]  # 宽度在第2个位置
            stretch = col[2]  # stretch在第3个位置

            # 计算实际列索引：+1 因为第0列是复选框
            col_idx = i + 1

            # 设置列宽和调整模式
            tree.setColumnWidth(col_idx, width)
            header.setSectionResizeMode(col_idx, QHeaderView.Interactive)

        header.setStretchLastSection(False)

        # ✅ Stretch 列记录 - 使用display_cols_data
        stretch_columns = [i + 1 for i, col in enumerate(display_cols_data) if col[2]]

        # ✅ 表格模式：添加列固定宽度 30px
        if mode == "表格":
            add_col_idx = tree.columnCount() - 1
            tree.setColumnWidth(add_col_idx, 18)
            header.setSectionResizeMode(add_col_idx, QHeaderView.Fixed)

        # 保存上一宽度
        tree._last_viewport_width = tree.viewport().width()

        def resize_tree_event(event):
            super(QTreeWidget, tree).resizeEvent(event)

            tree_width = tree.viewport().width()
            delta = tree_width - getattr(tree, "_last_viewport_width", tree_width)
            tree._last_viewport_width = tree_width

            if stretch_columns and delta != 0:  # 仅在窗口大小变化时调整
                total_width = sum(tree.columnWidth(i) for i in range(tree.columnCount()))
                extra = tree_width - total_width
                if extra != 0:
                    # 按比例分配 extra
                    for col in stretch_columns:
                        tree.setColumnWidth(col, tree.columnWidth(col) + extra // len(stretch_columns))

        tree.resizeEvent = resize_tree_event

        # ✅ Tree 基础属性
        tree.setAcceptDrops(True)
        tree.setDragEnabled(False)
        tree.setDropIndicatorShown(True)
        tree.setDragDropMode(QTreeWidget.DragDropMode.DropOnly)
        tree.setRootIsDecorated(False)
        tree.setItemsExpandable(False)
        tree.setIndentation(0)
        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)  # 先禁用默认编辑
        tree.setItemDelegate(TreeEditDelegate(self, tree_index))

        header.setSectionsMovable(True)
        header.setDragEnabled(True)

        files = []
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name

        # 初始化容器
        if plugin_name not in self.plugin_trees:
            self.plugin_trees[plugin_name] = {}

        if plugin_name not in self.plugin_files:
            self.plugin_files[plugin_name] = {}

        if plugin_name not in self.plugin_headers:
            self.plugin_headers[plugin_name] = {}

        # 保存 tree / files
        self.plugin_trees[plugin_name][tree_index] = {"tree": tree, "mode": mode}
        self.plugin_files[plugin_name][tree_index] = files
        self.plugin_headers[plugin_name][tree_index] = {}
        self.plugin_tree_frames.setdefault(plugin_name, {})[tree_index] = container
        self.plugin_tree_container.setdefault(plugin_name, {})[tree_index] = container_style  # 保存原始样式
        # 保存表头控件类型（widget_spec + callbacks）
        #print(f"处理后extra_columns:{extra_columns}")
        # 按显示顺序保存表头信息
        for display_idx, (pos, orig_idx, col) in enumerate(display_columns):
            name, width, stretch, position, widget_spec, callbacks = col

            # 处理列名
            if name is None:
                name = f"列表{tree_index}"

            # 保存表头信息，使用与原来完全相同的结构
            # 注意：这里存储的是原始列的信息，只是按显示顺序排列
            self.plugin_headers[plugin_name][tree_index][display_idx] = {
                "name": name,
                "widget_spec": widget_spec,
                "callbacks": callbacks,
                # 可以额外添加一些辅助信息，但不要改变原有结构
                "_original_index": orig_idx,  # 添加下划线前缀表示内部使用，不影响外部调用
                "_position": position,
            }

        # 保存列映射关系（用于内部函数定位）
        if not hasattr(self, '_plugin_column_maps'):
            self._plugin_column_maps = {}
        if plugin_name not in self._plugin_column_maps:
            self._plugin_column_maps[plugin_name] = {}

        self._plugin_column_maps[plugin_name][tree_index] = {
            "orig_to_display": orig_to_display,
            "display_to_orig": {v: k for k, v in orig_to_display.items()},
        }

        # ======================
        # 追加 “+” 插入按钮列
        # ======================
        if mode == "表格":
            # col_idx = len(extra_columns)
            plus_col_idx = len(self.plugin_headers[plugin_name][tree_index])

            # 回调：插入一行（由 add_empty_row 完成）
            def plus_button_callback(row=None, item=None, tree=None, **_):
                # 触发实际的添加
                self.add_empty_row(tree_index, after_item=item)

            # 追加到 plugin_headers
            self.plugin_headers[plugin_name][tree_index][plus_col_idx] = {
                "name": "",  # 表头名为空
                "widget_spec": "button(+)",  # 显示按钮
                "callbacks": [plus_button_callback],  # 回调由 add_widgets_to_row 自动包装
            }

        #print(f"plugin_headers:{self.plugin_headers[plugin_name][tree_index]}")

        # ✅ 焦点事件：tree 互斥高亮
        old_focus_in = tree.focusInEvent

        def focus_in(event, tid=tree_index):
            self.activate_tree(tid, plugin_name)
            return old_focus_in(event)

        tree.focusInEvent = focus_in
        tree._clickable_enabled = True  # 默认可交互

        # ✅ 行点击：控制复选框
        def handle_item_click(item, column):
            if not getattr(tree, "_clickable_enabled", True):
                return  # 禁用时直接返回

            if column == 0:
                # 切换新的 check 状态
                new_checked = item.text(0) == "☐"
                item.setText(0, "☑" if new_checked else "☐")

                # ✅ 从 plugin_tree_map 获取 fullpath
                p_name = plugin_name
                plugin_tree_map = self.plugin_tree_map.get(p_name, {}).get(tree_index, {})
                item_to_path = plugin_tree_map.get("item_to_path", {})
                full = item_to_path.get(id(item))

                if full:
                    file_list = self.plugin_files.get(p_name, {}).get(tree_index, [])
                    for f in file_list:
                        if f["fullpath"] == full:
                            f["checked"] = new_checked
                            break

                    # 回写结构（file_list 已经是引用，通常无需重复赋值）
                    self.plugin_files[p_name][tree_index] = file_list

                # 更新表头复选框显示
                self.update_header_checkbox(tree_index)

                # 调用插件回调（如果有实现）
                self.plugin_call_back(
                    "on_check",
                    tree_index,
                    plugin_name=plugin_name
                )

        tree.itemClicked.connect(handle_item_click)

        def handle_header_click(index):
            if not getattr(tree, "_clickable_enabled", True):
                return  # 禁用时直接返回

            if index != 0:
                return

            p_name = plugin_name

            # 确保 files 容器存在
            if p_name not in self.plugin_files:
                self.plugin_files[p_name] = {}
            if tree_index not in self.plugin_files[p_name]:
                self.plugin_files[p_name][tree_index] = []
            file_list = self.plugin_files[p_name][tree_index]

            # 若没有文件，直接设置 header 为空
            if not file_list:
                tree.headerItem().setText(0, "☐")
                return

            # 决定新状态：如果存在未选中 -> 切为全选；否则全不选
            any_unchecked = any(not f.get("checked") for f in file_list)
            new_state = True if any_unchecked else False

            # 应用到数据与 UI
            for i, f in enumerate(file_list):
                f["checked"] = new_state
                # 更新对应行 UI（如果行已存在）
                if i < tree.topLevelItemCount():
                    tree.topLevelItem(i).setText(0, "☑" if new_state else "☐")

            # 回写
            self.plugin_files[p_name][tree_index] = file_list

            # 更新表头文本
            self.update_header_checkbox(tree_index)
            # 调用当前插件的刷新回调（如果有实现）
            self.plugin_call_back(
                "on_check",
                tree_index,
                plugin_name=plugin_name
            )

        tree.header().sectionClicked.connect(handle_header_click)

        # ✅ 拖拽添加文件

        # ✅ 拖拽进入和移动时：仅在拖拽文件时激活 tree
        def drag_enter_event(e, tid=tree_index):
            if e.mimeData().hasUrls():
                e.accept()
                # 鼠标拖拽悬停时自动高亮该 tree
                self.activate_tree(tid, plugin_name)
            else:
                e.ignore()

        def drag_move_event(e, tid=tree_index):
            if e.mimeData().hasUrls():
                e.accept()
                # 在拖拽过程中持续移动也可触发激活（避免进入一次后错过）
                self.activate_tree(tid, plugin_name)
            else:
                e.ignore()

        tree.dragEnterEvent = drag_enter_event
        tree.dragMoveEvent = drag_move_event
        tree.dropEvent = lambda event, tid=tree_index: self.drop_event(event, tid)

        # ✅ 点击复选框不选中行
        old_mousePress = tree.mousePressEvent

        def mouse_press(event):
            index = tree.indexAt(event.pos())
            if not index.isValid():
                return old_mousePress(event)

            # 点击复选框列：仍按原逻辑处理
            if index.column() == 0:
                item = tree.topLevelItem(index.row())
                handle_item_click(item, 0)
                return  # 不选中整行

            # ✅ 行已被选中并单击非复选框列 → 进入编辑模式
            if tree.selectionModel().isSelected(index):
                tree.edit(index)
                return

            # 默认行为：选中行
            old_mousePress(event)

        tree.mousePressEvent = mouse_press

        # ✅ 双击不触发行选择（当两次单击处理）
        def no_double_click(event):
            index = tree.indexAt(event.pos())
            if index.isValid():
                item = tree.topLevelItem(index.row())
                handle_item_click(item, index.column())
            return

        def header_no_double_click(event):
            # 获取点击的逻辑列
            header = tree.header()
            index = header.logicalIndexAt(event.pos())

            # 仅处理第一列复选框
            if index == 0:
                # 手动触发 handle_header_click
                handle_header_click(0)

            # 阻止 Qt 默认双击行为
            event.accept()

        tree.mouseDoubleClickEvent = no_double_click
        header.mouseDoubleClickEvent = header_no_double_click

        layout.addWidget(tree)

        # 保存编辑的tree内容
        #tree.itemChanged.connect(lambda item, col, tid=tree_index: self.on_item_edited(item, col, tid))

        # ==== parent != None 直接放入父，以便立即可见 ====
        if parent is not None:
            if isinstance(parent.layout(), QBoxLayout) or isinstance(parent.layout(), QGridLayout):
                parent.layout().addWidget(container)
            else:
                # 防御式容错
                lay = QVBoxLayout(parent)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.addWidget(container)

        # ================================
        # 表格模式：默认添加一行 + 添加按钮
        # ================================
        if mode == "表格":
            self.add_empty_row(tree_index)

        # ====== 新增控件 ======
        row = tree.topLevelItemCount() - 1
        self.add_widgets_to_row(tree_index, row, plugin_name)

        # -------------------------------
        # 快捷键绑定
        # -------------------------------
        def bind_tree_shortcuts(tree, tree_id):
            tree.setFocusPolicy(Qt.StrongFocus)

            class KeyFilter(QObject):
                def __init__(self, parent_window, tree_id):
                    super().__init__(parent_window)
                    self.parent_window = parent_window
                    self.tree_id = tree_id

                def eventFilter(self, obj, event):
                    if event.type() == QEvent.KeyPress:
                        key = event.key()
                        modifiers = event.modifiers()

                        if key == Qt.Key_A and modifiers & Qt.ControlModifier:
                            obj.selectAll()
                            return True

                        if key == Qt.Key_C and modifiers & Qt.ControlModifier:
                            self.parent_window.copy_selected(self.tree_id)
                            return True

                        if key == Qt.Key_X and modifiers & Qt.ControlModifier:
                            self.parent_window.cut_selected(self.tree_id)
                            return True

                        if key == Qt.Key_V and modifiers & Qt.ControlModifier:
                            self.parent_window.paste_items(self.tree_id)
                            return True

                        if key == Qt.Key_Delete:
                            self.parent_window.delete_selected(self.tree_id)
                            return True

                    return False

            key_filter = KeyFilter(self, tree_id)  # 注意传入窗口实例
            tree.installEventFilter(key_filter)
            tree._key_filter = key_filter  # 防止被 GC

        # 调用
        bind_tree_shortcuts(tree, tree_index)
        self.main.fix_all_font()

        return container, tree, files

    # -------------------------
    # 占位的 操作函数（与原脚本同名，插件可能会调用）
    # 你可以根据需要在这里实现具体逻辑
    # -------------------------
    def get_tree(self, tree_id, get_mode=False, plugin_name=None):
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        #print(f"获取tree：{plugin_name}")

        # 确保 plugin_name 层级存在
        if plugin_name not in self.plugin_trees:
            self.plugin_trees[plugin_name] = {}

        # 确保 tree_id 层级存在
        if tree_id not in self.plugin_trees[plugin_name]:
            self.plugin_trees[plugin_name][tree_id] = {"tree": None, "mode": "文件"}

        tree_info = self.plugin_trees[plugin_name][tree_id]
        tree = tree_info.get("tree")
        mode = tree_info.get("mode", "文件")

        if get_mode:
            return tree, mode
        return tree

    def get_file_list(self, tree_id, plugin_name=None):
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        #print(f"获取列表{plugin_name}")
        # 确保 plugin_name 层级存在
        if plugin_name not in self.plugin_files:
            self.plugin_files[plugin_name] = {}
        # 确保 tree_id 层级存在
        if tree_id not in self.plugin_files[plugin_name]:
            # 没有对应 Tree 时可以选择创建一个空引用占位（这里 None）
            self.plugin_files[plugin_name][tree_id] = []
        return self.plugin_files[plugin_name][tree_id]

    def get_trees_widgets(self, tree_index, row, name, plugin_name=None):
        """
        获取指定 tree_index、row 的控件。
        - 如果 name 为 int，则按列索引获取
        - 如果 name 为 str，则按表头名称获取
        """
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name

        tree_widgets = self.plugin_trees_widget.get(plugin_name, {}) \
            .get(tree_index, {}) \
            .get(row, {})

        if tree_widgets is None:
            return None

        # 如果是整数，直接按列索引获取
        if isinstance(name, int):
            return tree_widgets.get(name)

        # 如果是字符串，则先找对应列索引
        elif isinstance(name, str):
            headers_info = self.plugin_headers.get(plugin_name, {}).get(tree_index, {})
            for col_idx, info in headers_info.items():
                if info.get("name") == name:
                    return tree_widgets.get(col_idx)

        return None

    def bind_global_shortcuts(self):
        # Ctrl+C
        shortcut_copy = QShortcut(QKeySequence("Ctrl+C"), self)
        shortcut_copy.activated.connect(lambda: self.copy_selected(self.active_tree_id))

        # Ctrl+V
        shortcut_paste = QShortcut(QKeySequence("Ctrl+V"), self)
        shortcut_paste.activated.connect(lambda: self.paste_items(self.active_tree_id))

        # Ctrl+X
        shortcut_cut = QShortcut(QKeySequence("Ctrl+X"), self)
        shortcut_cut.activated.connect(lambda: self.cut_selected(self.active_tree_id))

        # Ctrl+A
        shortcut_select_all = QShortcut(QKeySequence("Ctrl+A"), self)
        shortcut_select_all.activated.connect(lambda: self.get_tree(self.active_tree_id).selectAll())

        # Delete
        shortcut_delete = QShortcut(QKeySequence(Qt.Key_Delete), self)
        shortcut_delete.activated.connect(lambda: self.delete_selected(self.active_tree_id))

    @executor.run_in_main_thread_async
    def set_tree_checkbox_visible(self, tree_index, visible, plugin_name=None):
        """
        设置指定树的复选框列是否可见

        Args:
            tree_index: 树索引
            visible: True=显示，False=隐藏
            plugin_name: 插件名称（可选，默认自动获取）
        """
        # 获取插件名称
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name

        # 获取树对象
        if plugin_name not in self.plugin_trees:
            print(f"警告: 插件 {plugin_name} 不存在")
            return False

        if tree_index not in self.plugin_trees[plugin_name]:
            print(f"警告: 树索引 {tree_index} 不存在")
            return False

        tree_info = self.plugin_trees[plugin_name][tree_index]
        tree = tree_info["tree"]

        # 设置复选框列（第0列）的隐藏状态
        tree.setColumnHidden(0, not visible)

        return True

    @executor.run_in_main_thread
    def toggle_tree_checkbox(self, tree_index, plugin_name=None):
        """
        切换指定树复选框列的显示状态

        Args:
            tree_index: 树索引
            plugin_name: 插件名称（可选，默认自动获取）

        Returns:
            bool: 切换后的状态 (True=显示, False=隐藏)
        """
        # 获取插件名称
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name

        if plugin_name not in self.plugin_trees:
            print(f"警告: 插件 {plugin_name} 不存在")
            return False

        if tree_index not in self.plugin_trees[plugin_name]:
            print(f"警告: 树索引 {tree_index} 不存在")
            return False

        tree_info = self.plugin_trees[plugin_name][tree_index]
        tree = tree_info["tree"]

        # 切换隐藏状态
        current_hidden = tree.isColumnHidden(0)
        new_visible = current_hidden  # 如果当前隐藏，则设为显示
        tree.setColumnHidden(0, not new_visible)

        return new_visible

    @executor.run_in_main_thread
    def get_tree_checkbox_visible(self, tree_index, plugin_name=None):
        """
        获取指定树复选框列的显示状态

        Args:
            tree_index: 树索引
            plugin_name: 插件名称（可选，默认自动获取）

        Returns:
            bool: True=显示, False=隐藏, None=树不存在
        """
        # 获取插件名称
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name

        if plugin_name not in self.plugin_trees:
            return None

        if tree_index not in self.plugin_trees[plugin_name]:
            return None

        tree_info = self.plugin_trees[plugin_name][tree_index]
        tree = tree_info["tree"]

        return not tree.isColumnHidden(0)

    def _wrap_callback(self, cb, tree, item, col, widget):
        """
        根据回调函数实际可接受的参数，智能传递 row/col/widget 以及信号参数
        """
        sig = signature(cb)
        param_names = list(sig.parameters.keys())

        def wrapped(*args, **kwargs):
            call_args = []
            call_kwargs = {}

            # 将 row/col/widget 只传给函数声明接受的参数
            row = tree.indexOfTopLevelItem(item)
            extra = {'row': row, 'col': col, 'widget': widget, 'item': item, 'tree': tree}

            for name in param_names:
                if name in extra:
                    call_kwargs[name] = extra[name]

            # 如果回调接受位置参数，把 PyQt 信号传来的参数放进去
            for i, value in enumerate(args):
                if i < len(param_names):
                    pname = param_names[i]
                    if pname not in call_kwargs:
                        call_args.append(value)

            try:
                return cb(*call_args, **call_kwargs)
            except TypeError:
                # fallback：只传 *args，尽量不报错
                try:
                    return cb(*args)
                except TypeError:
                    # 如果还是报错，就直接调用不传参数
                    return cb()

        return wrapped

    def add_widgets_to_row(self, tree_index, row, plugin_name=None):
        """
        在指定 row 中根据 plugin_headers 添加控件。
        不保存控件，返回 {col_idx: widget} 字典，由 refresh_tree 负责保存。
        """
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        #print(f"控件：{plugin_name}")
        tree = self.plugin_trees[plugin_name][tree_index]["tree"]

        item = tree.topLevelItem(row)
        if not item:
            return {}

        headers_info = self.plugin_headers.get(plugin_name, {}).get(tree_index, {})

        widgets = {}  # 返回值

        for col_idx, info in headers_info.items():
            tree_col_idx = col_idx + 1  # 跳过复选框
            widget_spec = info.get("widget_spec")
            callbacks = info.get("callbacks", [])

            if not widget_spec:
                continue

            widget = None

            # 按钮
            if widget_spec.startswith("button("):
                text = widget_spec[7:-1]
                widget = QPushButton(text)
                for cb in callbacks:
                    if callable(cb):
                        widget.clicked.connect(
                            self._wrap_callback(cb, tree, item, col_idx, widget)
                        )

            # 下拉框
            elif widget_spec.startswith("combobox("):
                options = widget_spec[9:-1].split("|")
                combo = QComboBox()
                combo.addItems(options)
                widget = combo
                for cb in callbacks:
                    if callable(cb):
                        combo.currentIndexChanged.connect(
                            self._wrap_callback(cb, tree, item, col_idx, combo)
                        )

            # 复选框
            elif widget_spec.startswith("checkbutton("):
                text = widget_spec[12:-1]
                widget = QCheckBox(text)
                for cb in callbacks:
                    if callable(cb):
                        widget.stateChanged.connect(
                            self._wrap_callback(cb, tree, item, col_idx, widget)
                        )

            # 标签
            elif widget_spec.startswith("label("):
                text = widget_spec[6:-1]
                widget = QLabel(text)

            if widget:
                widget.setFixedHeight(18)  # 控件高度固定为 18px
                tree.setItemWidget(item, tree_col_idx, widget)
                self.fix_single_font(widget)
                widgets[col_idx] = widget  # 返回给 refresh_tree

        return widgets

    def add_empty_row(self, tree_id, after_item=None, plugin_name=None):
        """
        只修改 self.plugin_files，不直接操作 tree。
        插入后调用 refresh_tree 重建整个 tree。
        """
        tree, mode = self.get_tree(tree_id, get_mode=True, plugin_name=plugin_name)
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name

        headers = [tree.headerItem().text(i) for i in range(tree.columnCount())]
        extra_headers = headers[2:-1]

        # 唯一 ID
        uid = str(int(time.time() * 1000))

        row_data = {
            "fullpath": uid,
            "filename": "",
            "checked": True,
            "extra": {h: "" for h in extra_headers}
        }

        files = self.plugin_files.setdefault(plugin_name, {}).setdefault(tree_id, [])

        if after_item is None:
            files.append(row_data)
        else:
            row_index = tree.indexOfTopLevelItem(after_item)
            if row_index == -1:
                files.append(row_data)
            else:
                files.insert(row_index + 1, row_data)

        # 统一刷新（唯一添加行的入口）
        self.refresh_tree_main(tree_id)

    def activate_tree(self, tree_id, plugin_name=None):
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        #print(f"{plugin_name}: Activating tree {tree_id}")

        prev_id = self.plugin_active_tree_ids.get(plugin_name, None)

        # 即使 tree_id 相同，也要重新绘制样式，
        # 因为可能是另一个插件刚切过来。
        if prev_id == tree_id:
            # 强制刷新样式
            cur_cont = self.plugin_tree_frames.get(plugin_name, {}).get(tree_id)
            if cur_cont:
                cur_cont.setStyleSheet(f"""
                    QFrame#tree_container_{tree_id} {{
                        border: 1px solid #0078d7;
                    }}
                """)

            # 关键：同步当前 active_tree_id
            self.active_tree_id = tree_id
            return

        # 恢复上一个 tree 样式
        if prev_id is not None:
            prev_cont = self.plugin_tree_frames.get(plugin_name, {}).get(prev_id)
            if prev_cont:
                prev_style = self.plugin_tree_container.get(plugin_name, {}).get(prev_id, "")
                prev_cont.setStyleSheet(prev_style)

        # 设置当前高亮
        cur_cont = self.plugin_tree_frames.get(plugin_name, {}).get(tree_id)
        if cur_cont:
            cur_cont.setStyleSheet(f"""
                QFrame#tree_container_{tree_id} {{
                    border: 1px solid #0078d7;
                }}
            """)
            self.plugin_active_tree_ids[plugin_name] = tree_id
            self.active_tree_id = tree_id  # 可保留旧字段用于兼容

        # 把焦点交给 QTreeWidget
        cur_tree = self.get_tree(tree_id, plugin_name=plugin_name)
        if cur_tree:
            cur_tree.setFocus()

    def set_treeview_clickable(self, tree_id: int, enabled: bool):
        """
        控制 QTreeWidget 是否允许点击交互。
        关闭点击时，会屏蔽鼠标、键盘、滚轮事件；
        并禁止行选择与编辑；
        恢复时自动还原。
        """
        tree = self.get_tree(tree_id)
        if not tree:
            print(f"[WARN] set_treeview_clickable: 未找到 tree_id={tree_id} 对应的控件。")
            return

        if not hasattr(self, "_tree_click_filters"):
            self._tree_click_filters = {}

        if not enabled:
            # 已禁用则不重复添加
            if tree_id in self._tree_click_filters:
                return
            tree._clickable_enabled = False

            class ClickBlocker(QObject):
                """拦截 Tree 所有交互事件。"""

                def eventFilter(self, obj, event):
                    if event.type() in (
                            QEvent.MouseButtonPress,
                            QEvent.MouseButtonRelease,
                            QEvent.MouseButtonDblClick,
                            QEvent.MouseMove,
                            QEvent.Wheel,
                            QEvent.KeyPress,
                            QEvent.KeyRelease,
                            QEvent.ContextMenu,
                            QEvent.FocusIn,
                    ):
                        return True  # 阻止事件
                    return False

            blocker = ClickBlocker(tree)
            tree.installEventFilter(blocker)
            self._tree_click_filters[tree_id] = blocker

            # ⚙️ 禁止交互行为
            tree.setFocusPolicy(Qt.NoFocus)
            tree.setSelectionMode(QAbstractItemView.NoSelection)
            tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tree.header().setSectionsClickable(False)

            # ⚙️ 外观提示禁用
            tree.setStyleSheet("QTreeWidget { color: gray; }")
            tree.setCursor(Qt.ForbiddenCursor)

        else:
            blocker = self._tree_click_filters.pop(tree_id, None)
            if blocker:
                tree.removeEventFilter(blocker)

            tree._clickable_enabled = True

            # ⚙️ 恢复正常交互
            tree.setFocusPolicy(Qt.StrongFocus)
            tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
            tree.setEditTriggers(QAbstractItemView.EditKeyPressed | QAbstractItemView.SelectedClicked)
            tree.header().setSectionsClickable(True)

            tree.setStyleSheet("")  # 恢复样式
            tree.setCursor(Qt.ArrowCursor)

    def on_item_edited(self, item, column, tree_id, plugin_name=None):
        if column < 1:  # 复选框列不处理
            return

        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        files = self.plugin_files.get(plugin_name, {}).get(tree_id, [])

        tree, mode = self.get_tree(tree_id, get_mode=True, plugin_name=plugin_name)
        plugin_tree_map = self.plugin_tree_map.get(plugin_name, {}).get(tree_id, {})
        item_to_path = plugin_tree_map.get("item_to_path", {})
        fullpath = item_to_path.get(id(item))
        if not fullpath:
            return

        headers = [tree.headerItem().text(i) for i in range(tree.columnCount())]

        # filename 列
        if column == 1:
            for f in files:
                if f["fullpath"] == fullpath:
                    f["filename"] = item.text(column)
                    break
        # extra 列
        elif 2 <= column < tree.columnCount() - 1:
            key = headers[column]
            for f in files:
                if f["fullpath"] == fullpath:
                    f.setdefault("extra", {})[key] = item.text(column)
                    break

    def update_header_checkbox(self, tree_id, plugin_name=None):
        """
        根据 self.plugin_files[plugin_name][tree_id] 的 checked 字段来更新表头复选框状态：
        - 全选 -> "☑"
        - 全不选 -> "☐"
        - 部分选中 -> "☐"（你之前用文本表示半选；若要显示半选可改为 ▣）
        这个函数是线程安全的（UI 调用时应在主线程）。
        """
        tree = self.get_tree(tree_id, plugin_name=plugin_name)
        if tree is None:
            return

        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name

        files = self.plugin_files.get(plugin_name, {}).get(tree_id, None)

        # 优先以 data 为准（如果 files 存在）
        if files is not None:
            total = len(files)
            if total == 0:
                tree.headerItem().setText(0, "☐")
                return
            checked_count = sum(1 for f in files if f.get("checked"))
        else:
            # 回退到检查 UI（不推荐长期依赖）
            total = tree.topLevelItemCount()
            if total == 0:
                tree.headerItem().setText(0, "☐")
                return
            checked_count = sum(1 for i in range(total) if tree.topLevelItem(i).text(0) == "☑")

        if checked_count == 0:
            tree.headerItem().setText(0, "☐")
        elif checked_count == total:
            tree.headerItem().setText(0, "☑")
        else:
            # 你现在使用文本复选框，显示部分选中用 "☐"（也可以改成 "▣"）
            tree.headerItem().setText(0, "☐")

    def add_item(self, tree_id, content, plugin_name=None, row=None, replace=False, add_extra=False):
        #print(f"save导入：{self.main.get_current_plugin_name()}")
        plugin = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        self.signal_manager.add_item(content, tree_id, plugin, row, replace, add_extra)

    def add_item_main(self, content, tree_id, plugin_name=None, row=None, replace=False, add_extra=False):
        """
        向 self.plugin_files[self.current_plugin_name][tree_id] 添加或替换一条记录。

        content: dict，例如 {0: "123", 3: "abc"} 或 {"文件名":"123","宽度":"abc"} 或 {"fullpath": "c:/.."}
        row: None -> 末尾追加；int -> 指定行插入或替换
        replace: True -> 替换指定行；False -> 在指定行插入

        特别逻辑：
        - mode == "文件"：必须提供 fullpath，filename 若未填充则用 fullpath 生成
        - mode == "表格"：如果未提供 fullpath，则自动生成时间戳作为唯一 uid
        """
        # print(f"content:{content}")

        plugin = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        # print(f"导入：{plugin}； 当前插件：{self.main.get_current_plugin_name()}")

        # 确保 plugin_files 结构存在
        if plugin not in self.plugin_files:
            self.plugin_files[plugin] = {}
        if tree_id not in self.plugin_files[plugin]:
            self.plugin_files[plugin][tree_id] = []
        files = self.plugin_files[plugin][tree_id]

        # 获取 tree 与 mode
        tree, mode = self.get_tree(tree_id, get_mode=True)

        # header_info
        header_info = self.plugin_headers.get(plugin, {}).get(tree_id, {})

        # ===== 新增：建立列名到显示索引和原始索引的映射 =====
        name_to_display_idx = {}  # 列名 -> 显示列索引（0-based，不包括复选框）
        name_to_orig_idx = {}  # 列名 -> 原始列索引
        display_idx_to_name = {}  # 显示索引 -> 列名

        for display_idx, info in header_info.items():
            col_name = info.get("name")
            if col_name:
                name_to_display_idx[col_name] = display_idx
                name_to_orig_idx[col_name] = info.get("_original_index", display_idx)
                display_idx_to_name[display_idx] = col_name

        # ===== 新增：找到文件名列（_original_index=0）的显示位置 =====
        filename_display_idx = 0  # 默认值
        for display_idx, info in header_info.items():
            if info.get("_original_index") == 0:
                filename_display_idx = display_idx
                break

        # 提取 fullpath
        provided_fullpath = None
        if isinstance(content, dict) and "fullpath" in content:
            provided_fullpath = content.get("fullpath")
        else:
            for k, v in content.items():
                if isinstance(k, str) and k == "fullpath":
                    provided_fullpath = v
                    break
                if isinstance(k, str):
                    # 通过列名查找对应的原始索引是否为0（文件名列）
                    if k in name_to_orig_idx and name_to_orig_idx[k] == 0:
                        provided_fullpath = v
                        break
                if isinstance(k, int):
                    # 如果传入的是显示索引，需要转换为原始索引
                    if k == 0:  # 复选框列
                        continue
                    col_info = header_info.get(k - 1, {})  # k-1 因为传入的可能包含复选框
                    if col_info.get("_original_index") == 0:
                        provided_fullpath = v
                        break

        # --- 计算 filename（仅用于传给 on_add） ---
        filename_for_callback = ""
        # 先尝试从 content 找 filename
        for key, val in content.items():
            if key is None:
                filename_for_callback = str(val)
                break
            if isinstance(key, str):
                if key in name_to_orig_idx and name_to_orig_idx[key] == 0:
                    filename_for_callback = str(val)
                    break
            if isinstance(key, int):
                if key == 1:  # 兼容旧代码
                    filename_for_callback = str(val)
                    break
                col_info = header_info.get(key - 1, {})
                if col_info.get("_original_index") == 0:
                    filename_for_callback = str(val)
                    break

        # 文件模式且 filename 为空，用 fullpath 提取
        if not filename_for_callback and mode == "文件" and provided_fullpath:
            filename_for_callback = os.path.basename(provided_fullpath)

        # fullpath 处理
        if mode == "文件":
            if not provided_fullpath:
                print(
                    f"出问题的插件：{plugin}/{plugin_name}/{self.main.get_current_plugin_name()}/{self.current_plugin_name}\nid:{tree_id}\n内容：{content}")
                raise ValueError(f"出问题的插件{plugin}：mode == '文件' 时，content 中必须提供 'fullpath'")
            uid = str(provided_fullpath)
        else:
            uid = str(provided_fullpath) if provided_fullpath else str(int(time.time() * 1000))

        # --- add_extra ---
        if add_extra and uid:
            extra_dict = self.plugin_call_back(
                "on_add",
                tree_id,
                uid,
                filename_for_callback,
                plugin_name=plugin
            )
            if isinstance(extra_dict, dict):
                content = {**content, **extra_dict}

        # print(f"content:{content}")

        # 构造 new_item
        new_item = {
            "fullpath": uid,
            "filename": "",
            "checked": True,
            "extra": {},
        }

        # ===== 关键修改：映射 content 到新结构 =====
        for key, val in content.items():
            if key == "fullpath":
                continue

            col_display_idx = None  # 显示索引（0-based，不包括复选框）
            col_name = None

            # 1. 按显示索引解析（content中可能直接使用显示索引）
            if isinstance(key, int):
                if key == 0:  # 复选框列
                    new_item["checked"] = (str(val) not in ("0", "False", "false", "", "☐"))
                    continue
                else:
                    # key 可能是包含复选框的列索引，需要转换为显示索引
                    # 注意：key=1 是第一个数据列，对应显示索引0
                    col_display_idx = key - 1

            # 2. 按列名称解析
            elif isinstance(key, str):
                if key in name_to_display_idx:
                    col_display_idx = name_to_display_idx[key]
                    col_name = key
                elif key.lower() in ("checked",):
                    new_item["checked"] = (str(val) not in ("0", "False", "false", "", "☐"))
                    continue
                else:
                    # 未知的key，当作extra
                    new_item["extra"][key] = val
                    continue

            # 3. key 是 None 的特殊处理：更新 filename
            elif key is None:
                new_item["filename"] = str(val)
                continue

            # 4. 如果无法定位列索引，则当作 extra
            if col_display_idx is None:
                new_item["extra"][str(key)] = val
                continue

            # 5. 根据显示索引赋值
            if col_display_idx == filename_display_idx:
                # 这是文件名列
                new_item["filename"] = str(val)
            else:
                # 其他列，放入extra中，使用列名作为key
                if not col_name:
                    col_name = display_idx_to_name.get(col_display_idx, str(col_display_idx + 1))
                new_item["extra"][col_name] = val

        # 文件模式下 filename 为空时，用 fullpath 提取文件名
        if mode == "文件" and not new_item["filename"]:
            new_item["filename"] = os.path.basename(uid)

        # 插入或替换
        if row is None:
            files.append(new_item)
        else:
            try:
                row = int(row)
            except Exception:
                files.append(new_item)
            else:
                if replace:
                    if 0 <= row < len(files):
                        files[row] = new_item
                    else:
                        files.append(new_item)
                else:
                    if 0 <= row <= len(files):
                        files.insert(row, new_item)
                    else:
                        files.append(new_item)

        # 回调
        self.plugin_call_back(
            "on_add_end",
            tree_id,
            plugin_name=plugin_name
        )

        # 刷新 tree
        # print(f"new_item:{new_item}")
        self.refresh_tree_main(tree_id, plugin_name=plugin)

    def refresh_tree(self, tree_id, plugin_name=None):
        """
        线程安全的刷新入口，任何线程都可以调用
        """
        plugin = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        self.signal_manager.refresh_tree(tree_id, plugin)

    def refresh_tree_main(self, tree_id, plugin_name=None):
        """
        唯一添加、删除 tree 行的函数。
        根据 self.plugin_files 重建整个 tree。
        """
        tree, mode = self.get_tree(tree_id, get_mode=True, plugin_name=plugin_name)
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        files = self.plugin_files.get(plugin_name, {}).get(tree_id, [])

        if mode == "表格" and len(files) == 0:
            # 会自动写入 plugin_files，然后 refresh_tree 会再次被调用
            self.add_empty_row(tree_id)
            return

        # 保存颜色
        color_map = {}
        for row in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(row)
            fullpath = self.plugin_tree_map[plugin_name][tree_id]["item_to_path"].get(id(item))
            if fullpath:
                # 保存整个行的背景颜色（也可以只保存某一列）
                color_map[fullpath] = []
                for col in range(item.columnCount()):
                    brush = item.background(col)
                    color_map[fullpath].append(brush)

        # 清空 tree
        tree.clear()

        # 清空映射
        if plugin_name not in self.plugin_tree_map:
            self.plugin_tree_map[plugin_name] = {}
        self.plugin_tree_map[plugin_name][tree_id] = {
            "path_to_item": {},
            "item_to_path": {}
        }

        path_to_item = self.plugin_tree_map[plugin_name][tree_id]["path_to_item"]
        item_to_path = self.plugin_tree_map[plugin_name][tree_id]["item_to_path"]

        # ===== 关键修改：获取列位置映射 =====
        plugin_headers = self.plugin_headers.get(plugin_name, {}).get(tree_id, {})

        # 找到文件名列（original_index=0）的显示位置
        filename_col_idx = 1  # 默认值，以防找不到
        for display_idx, header_info in plugin_headers.items():
            if header_info.get("_original_index") == 0:
                filename_col_idx = display_idx + 1  # +1 因为第0列是复选框
                break

        # 创建列名到显示索引的映射（用于extra列）
        col_name_to_display_idx = {}
        for display_idx, header_info in plugin_headers.items():
            col_name = header_info.get("name")
            if col_name:
                col_name_to_display_idx[col_name] = display_idx + 1  # +1 因为第0列是复选框

        #print(f"映射：{col_name_to_display_idx}")

        # 清理控件缓存
        if plugin_name not in self.plugin_trees_widget:
            self.plugin_trees_widget[plugin_name] = {}
        self.plugin_trees_widget[plugin_name][tree_id] = {}

        for f in files:
            # === 创建行 ===
            item = QTreeWidgetItem(tree)
            flags = item.flags()
            flags |= Qt.ItemIsEditable
            item.setFlags(flags)

            # 复选框列（始终在第0列）
            item.setText(0, "☑" if f.get("checked", True) else "☐")

            # ===== 关键修改：文件名列使用找到的实际位置 =====
            item.setText(filename_col_idx, f.get("filename", ""))

            # ===== 关键修改：extra 列根据列名匹配位置 =====
            extra = f.get("extra", {})
            for col_name, col_value in extra.items():
                if col_name in col_name_to_display_idx:
                    display_col = col_name_to_display_idx[col_name]
                    item.setText(display_col, str(col_value))

            # 映射表
            uid = f.get("fullpath")
            if uid:
                path_to_item[uid] = item
                item_to_path[id(item)] = uid

                # ===== 重新应用颜色 =====
                if uid in color_map:
                    for col, brush in enumerate(color_map[uid]):
                        item.setBackground(col, brush)

            # === 给该行添加控件 ===
            row = tree.indexOfTopLevelItem(item)
            widgets = self.add_widgets_to_row(tree_id, row, plugin_name)

            # 保存控件结构
            self.plugin_trees_widget[plugin_name][tree_id][row] = widgets

        self.update_header_checkbox(tree_id)
        self.main.fix_all_font()

    def clear_tree_item_color(self, tree_id, plugin_name=None):
        """
        线程安全：可在任何线程直接调用（通过 SignalManager 信号分发）
        """
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        self.signal_manager.clear_tree_item_color(tree_id, plugin_name)



    def clear_tree_item_color_main(self, tree_id, plugin_name):
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        if not plugin_name:
            return

        tree = self.get_tree(tree_id, plugin_name=plugin_name)
        if not tree:
            return

        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            for col in range(tree.columnCount()):
                item.setBackground(col, QBrush(Qt.white))

    def update_tree_item_color(self, tree_id, fullpath: str, state="success", plugin_name=None):
        """
        线程安全：可以在任何线程直接调用
        """
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        self.signal_manager.apply_color(tree_id, fullpath, state, plugin_name)

    def apply_color_main(self, tree_id, fullpath, state, plugin_name=None):
        COLOR_MAP = {
            "success": QColor("#c8e6c9"),
            "partial": QColor("#fff9c4"),
            "fail": QColor("#ffcdd2"),
            "processing": QColor("#e0e0e0"),
        }
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        #print(f"来自 {self.main.get_current_plugin_name()} 的请求，颜色为 {state}")
        if not plugin_name:
            return

        item_map = self.plugin_tree_map.get(plugin_name, {}).get(tree_id, {}).get("path_to_item", {})
        item = item_map.get(fullpath)
        if not item:
            return

        color = COLOR_MAP.get(state, QColor("#c8e6c9"))
        tree = item.treeWidget()
        if not tree:
            return
        for col in range(tree.columnCount()):
            item.setBackground(col, QBrush(color))

    def drop_event(self, event, tree_id):
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        self.add_files_thread(paths, tree_id)
        event.acceptProposedAction()

    def import_files_OLD(self, tree_id):
        paths, _ = QFileDialog.getOpenFileNames(self.main, "选择文件")
        if paths:
            self.add_files_thread(paths, tree_id)

    def import_files(self, tree_id, plugin_name=None):
        tree, mode = self.get_tree(tree_id, True, plugin_name=plugin_name)
        if mode == "文件":
            paths, _ = QFileDialog.getOpenFileNames(self.main, "选择文件")
            if paths:
                self.add_files_thread(paths, tree_id)

        elif mode == "表格":
            self.add_empty_row(tree_id)

        else:
            self.add_empty_row(tree_id)

    def add_files_thread(self, paths, tree_id, plugin_name=None):
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        #print(f"导入文件：{plugin_name}")
        plugin = self.plugins.get(plugin_name)
        tree, mode = self.get_tree(tree_id, True, plugin_name=plugin_name)

        if not paths or not plugin or not tree:
            return

        # 插件回调：开始
        self.plugin_call_back(
            "on_add_files_start",
            tree_id,
            plugin_name=plugin_name
        )

        # --- 在线程中执行文件处理 ---
        worker = QThread()

        def process_files():
            for p in paths:
                extra_dict = {}
                # ---- 插件处理单文件 ----
                result = self.plugin_call_back(
                    "on_add",
                    tree_id,
                    p,
                    os.path.basename(p),
                    plugin_name=plugin_name
                )

                if isinstance(result, dict):
                    extra_dict = result
                else:
                    extra_dict = {}

                item = {
                    "fullpath": p,
                    "filename": os.path.basename(p),
                    "checked": True,
                    "extra": extra_dict
                }
                # 发射信号 — 改为使用 SignalManager
                self.signal_manager.one_file_done(plugin_name, tree_id, item)

        # --- 将函数移到线程执行 ---
        worker.run = process_files
        self.signal_manager._threads.append(worker)  # 防止被GC
        tree._process_thread = worker

        # --- 插件可选回调：结束 ---
        def call_add_end():
            self.plugin_call_back(
                "on_add_files_end",
                tree_id,
                plugin_name=plugin_name
            )

        worker.finished.connect(
            lambda: QTimer.singleShot(
                0,
                lambda: call_add_end()
            )
        )

        worker.start()

    def delete_selected(self, tree_id, plugin_name=None):
        tree, mode = self.get_tree(tree_id, get_mode=True, plugin_name=plugin_name)
        if not tree:
            return

        selected = tree.selectedItems()
        #print(f"选中数量：{len(selected)}\nmap：{self.plugin_tree_map[self.current_plugin_name][tree_id]}")
        if not selected:
            QMessageBox.information(self.main, "提示", "请先选中要删除的文件行")
            return

        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        tree_map = self.plugin_tree_map.get(plugin_name, {}).get(tree_id, {})

        item_to_path = tree_map.get("item_to_path", {})

        selected_paths = {item_to_path[id(item)] for item in selected if id(item) in item_to_path}

        if not selected_paths:
            QMessageBox.warning(self.main, "提示", "未能获取选中项的路径")
            return

        files = self.plugin_files[plugin_name][tree_id]

        deleted_files = [f for f in files if f["fullpath"] in selected_paths]
        if not deleted_files:
            return

        files[:] = [f for f in files if f["fullpath"] not in selected_paths]

        self.refresh_tree(tree_id)

        content = json.dumps(deleted_files, ensure_ascii=False)
        # 回调
        self.plugin_call_back(
            "on_delete_selected",
            tree_id,
            content,
            plugin_name=plugin_name
        )

    def clear_all(self, tree_id, plugin_name=None):
        tree, mode = self.get_tree(tree_id, get_mode=True, plugin_name=plugin_name)
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        files = self.plugin_files[plugin_name][tree_id]

        files.clear()

        self.refresh_tree(tree_id)

        # 插件回调
        self.plugin_call_back(
            "on_clear_all",
            tree_id,
            plugin_name=plugin_name
        )

    def copy_selected(self, tree_id, plugin_name=None):
        tree = self.get_tree(tree_id, plugin_name=plugin_name)
        if not tree:
            return

        selected = tree.selectedItems()
        if not selected:
            QMessageBox.information(self.main, "提示", "请先选中要复制的文件行")
            return

        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        plugin_tree_map = self.plugin_tree_map.get(plugin_name, {}).get(tree_id, {})
        item_to_path = plugin_tree_map.get("item_to_path", {})

        paths = {item_to_path[id(item)] for item in selected if id(item) in item_to_path}

        if not paths:
            QMessageBox.warning(self.main, "提示", "未能获取选中项的路径")
            return

        files = self.get_file_list(tree_id)
        selected_files = [f for f in files if f["fullpath"] in paths]
        if not selected_files:
            QMessageBox.information(self.main, "提示", "未找到选中的文件信息")
            return

        try:
            content = json.dumps(selected_files, ensure_ascii=False)
            # ✅ 调用插件回调（可选）
            self.plugin_call_back(
                "on_copy_selected",
                tree_id,
                content,
                plugin_name=plugin_name
            )

            QApplication.clipboard().setText(content)
            popup = ToastPopup(tree, text="文件信息已复制到剪贴板", duration=2000, align="c")
            popup.show()
        except Exception as e:
            QMessageBox.critical(self.main, "错误", f"复制失败：{e}")

    def cut_selected(self, tree_id, plugin_name=None):
        tree = self.get_tree(tree_id, plugin_name=plugin_name)
        if not tree:
            return

        selected = tree.selectedItems()
        if not selected:
            QMessageBox.information(self.main, "提示", "请先选中要复制的文件行")
            return

        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name
        plugin_tree_map = self.plugin_tree_map.get(plugin_name, {}).get(tree_id, {})
        item_to_path = plugin_tree_map.get("item_to_path", {})

        paths = {item_to_path[id(item)] for item in selected if id(item) in item_to_path}

        if not paths:
            QMessageBox.warning(self.main, "提示", "未能获取选中项的路径")
            return

        files = self.get_file_list(tree_id)
        selected_files = [f for f in files if f["fullpath"] in paths]
        if not selected_files:
            return

        try:
            content = json.dumps(selected_files, ensure_ascii=False)
            # ✅ 调用剪切回调（可选）并传入复制内容
            self.plugin_call_back(
                "on_cut_selected",
                tree_id,
                content,
                plugin_name=plugin_name
            )

            QApplication.clipboard().setText(content)

        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self.main, "错误", f"剪切失败：{e}")
            return

        # 删除对应文件记录
        files_list = self.plugin_files[plugin_name][tree_id]
        files_list[:] = [f for f in files_list if f["fullpath"] not in paths]

        # 刷新树
        self.refresh_tree(tree_id)

    def paste_items(self, tree_id, plugin_name=None):
        tree, mode = self.get_tree(tree_id, True, plugin_name=plugin_name)
        if not tree:
            return

        # -----------------------------
        # 读取剪贴板 JSON
        # -----------------------------
        try:
            data = QApplication.clipboard().text()
            copied_files = json.loads(data)
            if not isinstance(copied_files, list):
                raise ValueError("剪贴板数据格式错误")
        except Exception as e:
            QMessageBox.critical(self.main, "错误", f"读取剪贴板失败：{e}")
            return

        files = self.get_file_list(tree_id)
        plugin_name = plugin_name or self.main.get_current_plugin_name() or self.current_plugin_name

        existing = {f["fullpath"] for f in files}

        # -----------------------------
        # 解析 tree 多列名 (跳过 checkbox / filename)
        # -----------------------------
        extra_cols = [tree.headerItem().text(i) for i in range(2, tree.columnCount())]

        def has_all_extra(f):
            extra = f.get("extra", {})
            if not isinstance(extra, dict):
                return False
            lower = {k.lower() for k in extra.keys()}
            return all(col.lower() in lower for col in extra_cols)

        full_info = [f for f in copied_files if has_all_extra(f)]
        partial = [f["fullpath"] for f in copied_files if not has_all_extra(f)]

        # -----------------------------
        # 冲突判断
        # -----------------------------
        conflict = [f for f in full_info if f["fullpath"] in existing]
        if conflict:
            txt = "\n".join(f["fullpath"] for f in conflict)
            res = QMessageBox.question(
                self.main, "冲突提示",
                f"以下文件已存在：\n{txt}\n\n是否覆盖？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if res == QMessageBox.Cancel:
                return
            if res == QMessageBox.Yes:
                files[:] = [f for f in files if f["fullpath"] not in {c["fullpath"] for c in conflict}]
            else:
                full_info = [f for f in full_info if f["fullpath"] not in {c["fullpath"] for c in conflict}]

        # -----------------------------
        # 根据 mode 区分行为
        # -----------------------------
        pasted_files = []
        if mode == "表格":
            pasted_files = full_info + [f for f in copied_files if f["fullpath"] in partial]
            # 直接覆盖添加列表
            files.extend(full_info + [f for f in copied_files if f["fullpath"] in partial])
            self.plugin_files[plugin_name][tree_id] = files
            self.refresh_tree(tree_id)
        else:
            # 原来的“文件模式”
            if full_info:
                files.extend(full_info)
                pasted_files.extend(full_info)
                self.plugin_files[plugin_name][tree_id] = files
                self.refresh_tree(tree_id)
            if partial:
                pasted_files.extend([f for f in copied_files if f["fullpath"] in partial])
                self.add_files_thread(partial, tree_id)

        # -----------------------------
        # 回调 + 提示
        # -----------------------------
        content = json.dumps(pasted_files, ensure_ascii=False)
        if full_info or partial:
            # 回调
            self.plugin_call_back(
                "on_paste_items",
                tree_id,
                content,
                plugin_name=plugin_name
            )

            #QMessageBox.information(self, "提示", "粘贴完成")
            popup = ToastPopup(tree, text="粘贴完成", duration=2000, align="c")
            popup.show()

    @executor.run_in_main_thread_async
    def show_tree_message(self, tree_id, text, duration=1000, align="c", plugin_name=None):
        tree, mode = self.get_tree(tree_id, True, plugin_name=plugin_name)
        popup = ToastPopup(tree, text=text, duration=duration, align=align)
        popup.show()
