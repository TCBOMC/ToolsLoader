import os
import re
import json
import threading
import pyperclip
import time
import queue
from datetime import datetime
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QWidget, QLabel, QComboBox, QMainWindow, QLineEdit, QSplitter, QSizePolicy, QPushButton, QHBoxLayout, QVBoxLayout, QFileDialog, QMessageBox
from PyQt5.QtCore import Qt, QTimer, QEvent, QObject, QThread, pyqtSignal

DEFAULT_CONFIG = {
    "config": {
	    "selected_splicing_mode": "",
		"editing_splicing_mode": "",
        "splicing_mode": {
            "无序号换行": {
                "Regular_expression": "",
                "Replacement_expression": "",
                "Full_name": "{self}\\n"
            },
            "不换行": {
                "Regular_expression": "",
                "Replacement_expression": "",
                "Full_name": "{self}"
            },
            "普通数字序号换行": {
                "Regular_expression": "",
                "Replacement_expression": "",
                "Full_name": "{num1} {self}\\n"
            },
            "2位数字序号换行": {
                "Regular_expression": "",
                "Replacement_expression": "",
                "Full_name": "{num01} {self}\\n"
            },
            "3位数字序号换行": {
                "Regular_expression": "",
                "Replacement_expression": "",
                "Full_name": "{num001} {self}\\\n"
            },
            "中文数字序号换行": {
                "Regular_expression": "",
                "Replacement_expression": "",
                "Full_name": "{num一} {self}\\n"
            }
        }
    }
}


class ClipboardApp:
    def __init__(self, root, main_frame, edit_frame, scale):
        # 这里初始化self.root和self.app的逻辑绝对不要改
        self.root = main_frame
        self.app = root
        self.scale = scale
        # 绝对不要改上面的内容

        self.running = False
        self.last_clipboard = ""
        self.items = {}
        self.dedupe_enabled = False
        self.temp_protected = set()
        self.header_checked = True
        self.config_path = "CopyConfig.json"
        self.new_config_window = None  # 保存单例窗口引用
        self.monitor_thread = None

        # 检查并加载配置文件
        self.config_data = self.load_or_create_config()

        # ---------- 顶部按钮行 ----------
        button_widget = QtWidgets.QWidget()
        button_widget.setFixedHeight(int(round(20 * self.scale)))  # 根据 scale 调整高度

        button_layout = QtWidgets.QHBoxLayout(button_widget)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(int(round(10 * self.scale)))
        #button_layout.setContentsMargins(10, 0, 10, 10)
        self.toggle_button = QPushButton("开始监听")
        self.toggle_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)  # 固定大小
        self.toggle_button.clicked.connect(self.toggle_monitor)
        button_layout.addWidget(self.toggle_button)

        self.dedupe_button = QPushButton("开启去重")
        self.dedupe_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)  # 固定大小
        self.dedupe_button.clicked.connect(self.toggle_dedupe)
        button_layout.addWidget(self.dedupe_button)

        # 下拉栏配置
        self.splicing_mode_combobox = QComboBox()
        button_layout.addWidget(self.splicing_mode_combobox)
        self.update_splicing_mode_list()
        self.splicing_mode_combobox.currentTextChanged.connect(self.on_splicing_mode_selected)

        """self.delete_button = QPushButton("删除")
        self.delete_button.clicked.connect(self.delete_selected)
        button_layout.addWidget(self.delete_button)

        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self.clear_all)
        button_layout.addWidget(self.clear_button)"""

        self.save_button = QPushButton("保存所选")
        self.save_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)  # 固定大小
        self.save_button.clicked.connect(self.save_to_file)
        button_layout.addWidget(self.save_button)

        self.copy_button = QPushButton("复制所选")
        self.copy_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)  # 固定大小
        self.copy_button.clicked.connect(self.copy_selected)
        button_layout.addWidget(self.copy_button)

        # ---------- 文件列表区域 ----------
        tree_frame = QWidget()
        main_layout = QVBoxLayout(self.root)
        main_layout.setContentsMargins(10, 0, 10, 10)
        main_layout.addWidget(button_widget)
        main_layout.addWidget(tree_frame)

        # 使用外部传入的 create_tree_view 函数生成 tree
        extra_cols = [
            (None, 400, True),
            ("日期", 120, False)
        ]
        tree_c, tree, tree_list = self.app.kit.ui.create_tree_view(tree_frame, tree_index=1,
                                                                  extra_columns=extra_cols,
                                                                  mode="表格")
        tree_c.show()
        self.tree = tree  # 保持引用
        self.tree_list = tree_list

        # 绑定点击事件和可编辑逻辑
        #self.tree.clicked.connect(self.on_tree_click)
        #self.enable_treeview_edit(self.tree)

        self.initialize_splicing_mode()

    def update_title(self):
        # 使用 self.tree_list 来统计总文件数和已选中文件数
        total = len(self.tree_list)
        selected = sum(1 for f in self.tree_list if f.get("checked", False))
        #print(selected, total)
        self.app.kit.ui.update_window_title(extra_text=f"- 剪贴板总数: {total}，已选中: {selected}")

    # ---------- 配置文件逻辑 ----------
    def load_or_create_config(self):
        """
        不再从文件读取，直接从主程序提供的 config 中加载插件配置。
        主程序保证在 kit 中已初始化 config，并能按插件名返回数据。
        """
        cfg = self.app.kit.config.load_plugin()   # 默认会根据 current_plugin_name 获取
        if cfg is None:
            # 如果该插件第一次使用，自动使用默认模板
            cfg = DEFAULT_CONFIG
            # 立即写入一次，确保进入配置系统
            self.app.kit.config.save_plugin(cfg)

        return cfg


    def save_config(self):
        """
        不再直接写文件，全部交给 config 系统保存（带异步写入队列）
        """
        try:
            self.app.kit.config.save_plugin(self.config_data)
        except Exception as e:
            self.app.kit.ui.show_message_box("错误", f"保存配置失败：{e}", "error")

    def initialize_splicing_mode(self):
        # 程序启动时根据配置文件自动选中上次使用的模式
        selected_mode = self.config_data["config"].get("selected_splicing_mode", "")
        splicing_modes = list(self.config_data["config"]["splicing_mode"].keys())
        splicing_modes.append("新建配置")

        # 如果配置中保存的模式仍存在，则选中它
        if selected_mode in splicing_modes:
            self.splicing_mode_combobox.setCurrentText(selected_mode)
        else:
            # 否则默认选第一个或“新建配置”
            default_mode = splicing_modes[0] if splicing_modes else "新建配置"
            self.splicing_mode_combobox.setCurrentText(default_mode)
            # 修正配置文件内容
            self.config_data["config"]["selected_splicing_mode"] = default_mode
            self.save_config()

    def update_splicing_mode_list(self):
        modes = list(self.config_data["config"]["splicing_mode"].keys())
        modes.append("新建配置")
        # QComboBox 用 addItems 或 clear + addItems
        self.splicing_mode_combobox.clear()
        self.splicing_mode_combobox.addItems(modes)

        # 优先使用配置中保存的 selected_splicing_mode
        selected_mode = self.config_data["config"]["selected_splicing_mode"]
        if selected_mode in modes:
            self.splicing_mode_combobox.setCurrentText(selected_mode)
        else:
            # 如果不存在，默认选第一个
            self.splicing_mode_combobox.setCurrentIndex(0)
            # 修正配置文件
            self.config_data["config"]["selected_splicing_mode"] = modes[0]
            self.save_config()

    def on_splicing_mode_selected(self, event=None):
        selected = self.splicing_mode_combobox.currentText()
        selected_mode = self.config_data["config"]["selected_splicing_mode"]
        if selected == "新建配置":
            self.open_new_config_window()
            self.splicing_mode_combobox.setCurrentText(selected_mode)
        else:
            self.config_data["config"]["selected_splicing_mode"] = selected

    def update_window_title(self):
        total = len(self.tree_list)
        selected = sum(1 for item in self.tree_list if item.get("checked"))
        self.app.kit.ui.update_window_title(f"剪贴板监听器 - 总共{total}行 已选{selected}行")

    # ---------- 监听 ----------
    def toggle_monitor(self):
        if not self.running:
            self.running = True
            self.toggle_button.setText("结束监听")
            self.last_clipboard = pyperclip.paste()

            # 如果线程不存在或已经停止，则创建新线程
            if not hasattr(self, "monitor_thread") or self.monitor_thread is None or not self.monitor_thread.is_alive():
                tid = self.app.start_plugin_thread(self.monitor_clipboard)
                # 保存线程对象
                self.monitor_thread = self.app.plugin_threads[self.app.current_plugin_name][tid]["thread"]

        else:
            self.running = False
            self.toggle_button.setText("开始监听")

    def monitor_clipboard_test(self, stop_event=None):
        """
        测试 stop_event 是否收到
        """
        print("monitor_clipboard 启动")
        while True:
            # 打印 stop_event 状态
            if stop_event is None:
                print("stop_event = None")
            else:
                print(f"stop_event.is_set() = {stop_event.is_set()}")

            # 模拟循环体耗时
            time.sleep(0.1)

    def exists_in_tree(self, content):
        """
        在 self.tree_list 中检测是否已存在相同 filename
        """
        for item in self.tree_list:
            if item.get("filename") == content:
                return True
        return False

    def monitor_clipboard(self, stop_event=None):
        """
        剪贴板监听线程
        stop_event: threading.Event，可选。通过 start_plugin_thread 启动时自动传入
        """
        check_interval = 0.5  # 剪贴板检测间隔
        stop_check_interval = 0.05  # stop_event 检查频率（快速响应关闭）

        while self.running and (stop_event is None or not stop_event.is_set()):
            start_time = time.time()

            # ---------- 剪贴板处理逻辑 ----------
            try:
                current = pyperclip.paste()
                if current != self.last_clipboard and current.strip():
                    self.last_clipboard = current
                    content = current.strip()

                    if content in self.temp_protected:
                        self.temp_protected.discard(content)
                        continue
                    if self.dedupe_enabled and self.exists_in_tree(content):
                        continue

                    # ---------- 检查 tree_list 是否为空 ----------
                    if isinstance(self.tree_list, list) and len(self.tree_list) == 1:
                        item = self.tree_list[0]
                        filename_empty = not item.get("filename")
                        extra_all_empty = all(
                            (v is None or str(v).strip() == "") for v in item.get("extra", {}).values()
                        )
                        if filename_empty and extra_all_empty:
                            self.tree_list.clear()

                    # ---------- 构造内容 ----------
                    new_item = {
                        "checked": True,
                        None: content,
                        "日期": ""
                    }
                    self.app.kit.ui.add_item(1, new_item, add_extra=True)

            except Exception as e:
                print("剪贴板读取错误:", e)

            # ---------- 分段睡眠以快速响应 stop_event ----------
            elapsed = time.time() - start_time
            remaining = check_interval - elapsed
            while remaining > 0 and self.running and (stop_event is None or not stop_event.is_set()):
                sleep_time = min(remaining, stop_check_interval)
                time.sleep(sleep_time)
                remaining -= sleep_time

        print("monitor_clipboard 已停止")

    def toggle_dedupe(self):
        if not self.dedupe_enabled:
            self.dedupe_enabled = True
            self.dedupe_button.setText("关闭去重")
            removed = self.remove_duplicates()
            if removed:  # 有变化才刷新 UI
                self.app.kit.ui.refresh_tree(1)

        else:
            self.dedupe_enabled = False
            self.dedupe_button.setText("开启去重")

    def remove_duplicates(self):
        seen = set()
        new_list = []
        removed = False

        for item in self.tree_list:
            name = item.get("filename")
            if name in seen:
                removed = True
                continue
            if name:
                seen.add(name)
            new_list.append(item)

        if removed:
            # 原地修改 self.tree_list 内容，而不是重新赋值
            self.tree_list.clear()
            self.tree_list.extend(new_list)
            self.update_window_title()
            print(f"new:{self.tree_list}")

        return removed

    # ---------- 保存 ----------
    def get_selected_items(self):
        """
        从 self.tree_list 中提取选中项的 filename。
        返回列表，只包含 checked=True 的项的 filename。
        """
        selected = [item["filename"] for item in self.tree_list if item.get("checked")]
        return selected

    def copy_selected(self):
        selected = self.get_selected_items()
        if selected:
            built_text = self.build_text_from_selection(selected)
            self.save_config()

            # ✅ 先加入保护
            if self.running:
                self.temp_protected.add(built_text)

            # ✅ 再复制
            pyperclip.copy(built_text)

            # ✅ 最后延迟更新 last_clipboard
            self.last_clipboard = built_text

    def save_to_file(self):
        selected = self.get_selected_items()
        if selected:
            built_text = self.build_text_from_selection(selected)

            # 更新 配置文件 的 selected_splicing_mode
            self.save_config()

            # PyQt5 文件保存对话框
            file_path, _ = QFileDialog.getSaveFileName(
                self.root,  # 父窗口
                "保存文件",
                "",
                "Text Files (*.txt);;All Files (*)",
                options=QFileDialog.Options()
            )
            if file_path:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(built_text)


    # ---------- 编辑 ----------
    def int_to_chinese(self, num):
        """将1-999的整数转换为对应的中文数字"""
        chinese_numerals = "零一二三四五六七八九"
        units = ["", "十", "百", "千"]
        digits = []
        # print(f"导入数：{num}")

        hundreds = num // 100  # 百位
        tens = (num % 100) // 10  # 十位
        ones = num % 10  # 个位

        # 处理百位
        if hundreds > 0:
            digits.append(chinese_numerals[hundreds] + units[2])

        # 处理十位
        if tens > 0:
            if hundreds == 0 and tens == 1:
                digits.append(units[1])  # 直接添加“十”
            else:
                digits.append(chinese_numerals[tens] + units[1])  # 添加十位数和“十”
        elif tens == 0 and hundreds > 0 and ones > 0:
            digits.append("零")  # 处理如“百零X”的情况

        # 处理个位
        if ones > 0:
            digits.append(chinese_numerals[ones])

        # print(f"输出数：{digits}")

        return ''.join(digits)

    def build_text_from_selection(self, selected):
        """根据当前拼接模式配置构建文本"""
        if not selected:
            return ""

        current_mode = self.splicing_mode_combobox.currentText().strip()
        splicing_modes = self.config_data["config"]["splicing_mode"]
        self.config_data["config"]["selected_splicing_mode"] = self.splicing_mode_combobox.currentText()
        self.save_config()

        if current_mode not in splicing_modes:
            self.app.kit.ui.show_message_box("提示", f"当前拼接模式不存在或未选择", "info")
            return "\n".join(selected)

        mode_cfg = splicing_modes[current_mode]
        full_name = mode_cfg.get("Full_name", "{self}\n")

        # 获取正则表达式配置
        regular_expr = mode_cfg.get("Regular_expression")
        replacement_expr = mode_cfg.get("Replacement_expression")

        formatted_lines = []
        for idx, text in enumerate(selected, start=1):
            # 先应用正则表达式替换
            if regular_expr and replacement_expr:
                # regular_expr = bytes(regular_expr, "utf-8").decode("unicode_escape")
                # replacement_expr = bytes(replacement_expr, "utf-8").decode("unicode_escape")
                text = self.apply_regex_replacement(text, regular_expr, replacement_expr)

            # 再格式化最终文本
            formatted_lines.append(self.format_line(idx, text, full_name))

        return "".join(formatted_lines)

    def apply_regex_replacement(self, text, pattern, replacement):
        """
        使用正则表达式处理文本，支持捕获组动态替换
        :param text: 原文本
        :param pattern: 正则表达式
        :param replacement: 替换表达式，可以使用 \1, \2 等捕获组
        :return: 更新后的文本
        """
        # print(f"pattern: {pattern}, replacement: {replacement}")
        try:
            # re.sub 支持 \1, \2 自动映射捕获组
            return re.sub(pattern, lambda m: m.expand(replacement), text)
        except re.error as e:
            self.app.kit.ui.show_message_box("正则表达式错误", f"正则表达式解析失败：{e}", "error")
            return text

    def format_line(self, num, text, full_name):
        """格式化单行文本"""

        # --- 工具函数 ------------------------------------------------------------
        def format_num(num, placeholder):
            zeros = placeholder.count("0")
            return str(num) if zeros == 0 else f"{num:0{zeros + 1}d}"

        def format_date(match):
            fmt = match.group(1)
            if fmt:
                fmt = fmt[1:]  # 去掉冒号
            else:
                fmt = "%Y-%m-%d"
            return datetime.now().strftime(fmt)

        def safe_literal_unescape(s: str) -> str:
            """
            智能逆转义字符串（支持 \\n, \\t, \\uXXXX 等），兼容字符串里同时包含单引号和双引号。
            优先用 json.loads 解析（包装为 JSON 字符串），失败时回退到 unicode_escape。
            """
            if not isinstance(s, str):
                return s

            # 1) 优先：用 json.loads 解析 — 将 s 当作 JSON 字符串，先转义反斜杠和双引号
            try:
                # 先把反斜杠和双引号转义，构造合法的 JSON 字符串
                esc = s.replace('\\', '\\\\').replace('"', '\\"')
                return json.loads('"' + esc + '"')
            except Exception:
                # 2) 回退：尝试 unicode_escape 解码（能处理 \uXXXX、\n、\t 等）
                try:
                    return bytes(s, "utf-8").decode("unicode_escape")
                except Exception:
                    # 3) 最后保底：原样返回（避免抛错）
                    return s

        def unescape_basic(s: str) -> str:
            escape_dict = {
                '\\\\': '\\',
                '\\n': '\n',
                '\\r': '\r',
                '\\t': '\t'
            }
            # 用 re.escape 确保 pattern 和字典 key 完全一致
            pattern = re.compile('|'.join(re.escape(k) for k in escape_dict.keys()))
            return pattern.sub(lambda m: escape_dict[m.group(0)], s)

        # -------------------------------------------------------------------------
        # result = full_name.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
        # result = ast.literal_eval(f"'{full_name}'")
        # result = safe_literal_unescape(full_name)
        result = unescape_basic(full_name)
        # result = full_name

        # {self} → 文本
        if "{self}" in result:
            result = result.replace("{self}", text)

        # {num一} → 中文数字
        if "{num一}" in result:
            result = result.replace("{num一}", self.int_to_chinese(num))

        # {num...} → 自动格式化数字
        num_pattern = r"\{num0*(1)?\}"
        result = re.sub(num_pattern, lambda m: format_num(num, m.group(0)), result)

        # {date[:格式]} → 当前日期/时间
        date_pattern = r"\{date(:[^}]*)?\}"
        result = re.sub(date_pattern, format_date, result)

        return result

    # ---------- 配置窗口 ----------
    def open_new_config_window(self):
        # 如果窗口已经存在并且没有被销毁，则只激活它
        if hasattr(self, "new_config_window") and self.new_config_window is not None:
            self.new_config_window.activateWindow()
            self.new_config_window.raise_()
            return

        # 创建新窗口
        win = QtWidgets.QWidget()
        self.new_config_window = win

        # >>> 修复：窗口关闭后清空 new_config_window 引用 <<<
        def on_close(event):
            self.new_config_window = None
            event.accept()

        win.closeEvent = on_close

        win.setWindowTitle("配置编辑")
        win.setMinimumSize(440, 220)

        # ---------- 顶部行 ----------
        top_layout = QHBoxLayout()

        label = QLabel("选择/编辑配置名称:")
        top_layout.addWidget(label)

        mode_combobox = QComboBox()
        modes = list(self.config_data["config"]["splicing_mode"].keys())
        modes.append("新建配置")
        mode_combobox.addItems(modes)
        mode_combobox.setCurrentText("新建配置")
        mode_combobox.setEditable(True)
        top_layout.addWidget(mode_combobox, stretch=1)

        save_btn = QPushButton("保存")
        del_btn = QPushButton("删除")
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(del_btn)
        top_layout.addLayout(btn_layout)

        # ---------- 下方编辑区 ----------
        reg_label = QLabel("Regular_expression:")
        reg_entry = QLineEdit()

        rep_label = QLabel("Replacement_expression:")
        rep_entry = QLineEdit()

        full_label = QLabel("Full_name:")
        full_entry = QLineEdit()

        main_layout = QVBoxLayout()
        main_layout.addLayout(top_layout)
        main_layout.addWidget(reg_label)
        main_layout.addWidget(reg_entry)
        main_layout.addWidget(rep_label)
        main_layout.addWidget(rep_entry)
        main_layout.addWidget(full_label)
        main_layout.addWidget(full_entry)

        win.setLayout(main_layout)

        # ---------- 初始化编辑状态 ----------
        self.config_data["config"]["editing_splicing_mode"] = "新建配置"

        def refresh_mode_combobox(selected_name=None):
            modes = list(self.config_data["config"]["splicing_mode"].keys())
            modes.append("新建配置")
            mode_combobox.clear()
            mode_combobox.addItems(modes)
            if selected_name in modes:
                mode_combobox.setCurrentText(selected_name)
            else:
                mode_combobox.setCurrentText("新建配置")
            load_selected_config()

        def load_selected_config():
            selected = mode_combobox.currentText()
            self.config_data["config"]["editing_splicing_mode"] = selected
            if selected == "新建配置":
                reg_entry.clear()
                rep_entry.clear()
                full_entry.clear()
            else:
                cfg = self.config_data["config"]["splicing_mode"].get(selected, {})
                reg_entry.setText(cfg.get("Regular_expression", ""))
                rep_entry.setText(cfg.get("Replacement_expression", ""))
                full_entry.setText(cfg.get("Full_name", ""))

        mode_combobox.currentTextChanged.connect(lambda _: load_selected_config())

        # ---------- 保存逻辑 ----------
        def save_config():
            new_name = mode_combobox.currentText().strip()
            editing_name = self.config_data["config"]["editing_splicing_mode"]

            if new_name == "":
                QMessageBox.warning(win, "警告", "配置名称不能为空！")
                return
            if new_name == "新建配置":
                QMessageBox.warning(win, "警告", "不能将配置命名为“新建配置”！")
                return

            new_data = {
                "Regular_expression": reg_entry.text(),
                "Replacement_expression": rep_entry.text(),
                "Full_name": full_entry.text()
            }

            if new_name != editing_name:
                if new_name in self.config_data["config"]["splicing_mode"]:
                    QMessageBox.warning(win, "警告", f"配置名称“{new_name}”已存在！")
                    return
                self.config_data["config"]["splicing_mode"][new_name] = new_data
                if editing_name != "新建配置" and editing_name in self.config_data["config"]["splicing_mode"]:
                    del self.config_data["config"]["splicing_mode"][editing_name]
            else:
                self.config_data["config"]["splicing_mode"][new_name] = new_data

            self.config_data["config"]["editing_splicing_mode"] = new_name
            self.save_config()
            QMessageBox.information(win, "成功", f"配置“{new_name}”已保存！")
            self.update_splicing_mode_list()
            refresh_mode_combobox(selected_name=new_name)

        # ---------- 删除逻辑 ----------
        def delete_config():
            editing_name = self.config_data["config"]["editing_splicing_mode"]
            if editing_name == "新建配置":
                QMessageBox.information(win, "提示", "未创建配置，无法删除。")
                return
            if editing_name not in self.config_data["config"]["splicing_mode"]:
                QMessageBox.warning(win, "警告", f"配置“{editing_name}”不存在。")
                return
            confirm = QMessageBox.question(win, "确认删除", f"确定要删除配置“{editing_name}”吗？",
                                           QMessageBox.Yes | QMessageBox.No)
            if confirm == QMessageBox.Yes:
                del self.config_data["config"]["splicing_mode"][editing_name]
                self.save_config()
                QMessageBox.information(win, "成功", f"配置“{editing_name}”已删除。")
                self.update_splicing_mode_list()
                refresh_mode_combobox(selected_name="新建配置")

        save_btn.clicked.connect(save_config)
        del_btn.clicked.connect(delete_config)

        # 初始化内容
        load_selected_config()
        win.show()


def create_ui(app, main_frame, edit_frame, scale):
    global clipboard
    #root = TkinterDnD.Tk()
    clipboard = ClipboardApp(app, main_frame, edit_frame, scale)

def on_add(tree_id, fullpath, filename):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 格式化到秒
    dict = {
        "日期": now
    }
    #print(f"dddddd{dict}")
    #clipboard.update_title()
    return dict  # 返回

def on_add_end(tree_id):
    clipboard.update_title()

def on_reload():
    """
    重载前调用，用于安全终止后台线程
    """
    print("插件 on_reload 被调用，尝试停止线程...")
    clipboard.running = False  # 停止 monitor_clipboard 循环
    if hasattr(clipboard, "monitor_thread") and clipboard.monitor_thread:
        if clipboard.monitor_thread.is_alive():
            clipboard.monitor_thread.join(timeout=0.1)  # 等待线程结束，避免阻塞太久
    print("后台线程已停止")
    clipboard.update_title()

def on_copy_selected(tree_id, content):
    clipboard.temp_protected.add(content)

def on_cut_selected(tree_id, content):
    clipboard.temp_protected.add(content)
    clipboard.update_title()

def on_check(tree_id):
    clipboard.update_title()

def on_paste_items(tree_id, content):
    clipboard.update_title()

def on_toggle_all_selection(tree_id):
    clipboard.update_title()

def on_clear_all(tree_id):
    clipboard.update_title()

def on_delete_selected(tree_id, content):
    clipboard.update_title()

def get_info():
    return {"icon":{"text":"📋"}, "display_name":"剪贴板工具"}