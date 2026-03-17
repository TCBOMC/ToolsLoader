import os
import re
import time
import copy
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QLabel, QFileDialog, QMessageBox
)
from PyQt5.QtCore import QTimer

class ReplaceApp:
    def __init__(self, app, main_frame, edit_frame, scale):
        self.app = app
        self.main_frame = main_frame
        self.edit_frame = edit_frame
        self.scale = scale

        self.file_backup = {}
        self.files = []

        self.create_widgets()

        self.history = []  # 操作历史
        self.operation_index = -1  # 当前操作指针

    # ---------------- UI ----------------
    def create_widgets(self):
        layout = QVBoxLayout(self.main_frame)
        layout.setContentsMargins(10, 0, 10, 10)
        layout.setSpacing(6)

        # ===== 顶部容器（固定高度，随 scale 缩放）=====
        top_container = QWidget(self.main_frame)
        top_container.setFixedHeight(int(round(20 * self.scale)))

        top_layout = QHBoxLayout(top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(int(round(10 * self.scale)))

        # 添加普通模式/正则模式切换按钮
        self.mode_btn = QPushButton("普通模式")
        self.mode_btn.setCheckable(True)
        self.mode_btn.setFixedWidth(int(round(60 * self.scale)))
        self.mode_btn.clicked.connect(self.toggle_mode)
        top_layout.addWidget(self.mode_btn)

        self.text_entry = QLineEdit()
        self.text_entry.setPlaceholderText("输入要查找的文本")
        self.replace_entry = QLineEdit()
        self.replace_entry.setPlaceholderText("替换为(可选)")
        self.btn_process = QPushButton("处理字幕")
        # ===== 撤销+重做按钮 =====
        undo_redo_widget = QtWidgets.QWidget()
        undo_redo_layout = QtWidgets.QHBoxLayout(undo_redo_widget)
        undo_redo_layout.setContentsMargins(0, 0, 0, 0)
        undo_redo_layout.setSpacing(0)

        self.undo_btn = QtWidgets.QPushButton("撤销")
        self.redo_btn = QtWidgets.QPushButton("重做")

        btn_width = int(round(30 * self.scale))  # 每个按钮宽度
        self.undo_btn.setFixedWidth(btn_width)
        self.redo_btn.setFixedWidth(btn_width)

        undo_redo_layout.addWidget(self.undo_btn)
        undo_redo_layout.addWidget(self.redo_btn)
        self.btn_process.setFixedWidth(int(round(60 * self.scale)))
        #self.btn_restore.setFixedWidth(int(round(60 * self.scale)))

        top_layout.addWidget(self.text_entry, 1)
        top_layout.addWidget(QLabel("替换为:"))
        top_layout.addWidget(self.replace_entry, 1)
        top_layout.addWidget(self.btn_process)
        top_layout.addWidget(undo_redo_widget)

        layout.addWidget(top_container)

        # ===== Tree 区域 =====
        tree_frame = QWidget()
        tree_layout = QVBoxLayout(tree_frame)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(tree_frame, 1)

        extra_cols = [
            ("文件名", 400, True),
            ("目标数", 60, False)
        ]
        self.tree_c, self.tree, self.files = self.app.kit.ui.create_tree_view(
            tree_frame, tree_index=1, extra_columns=extra_cols
        )
        self.tree_c.show()

        # ===== 信号 =====
        self.btn_process.clicked.connect(self.process_files)
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn.clicked.connect(self.redo)

        self.timer = QTimer()
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.update_count)
        self.text_entry.textChanged.connect(self.on_text_change)

    def toggle_mode(self):
        """切换普通模式/正则模式"""
        if self.mode_btn.text() == "普通模式":
            self.mode_btn.setText("正则模式")
        else:
            self.mode_btn.setText("普通模式")

        # 切换模式后重新计数
        self.update_count()

    # ---------------- 工具逻辑 ----------------
    def replace_outside_tags(self, text, target, replacement, tag_open="<", tag_close=">"):
        result = []
        i = 0
        n = len(text)

        is_regex = self.mode_btn.text() == "正则模式"
        if is_regex:
            try:
                pattern = re.compile(target)
            except re.error:
                # 正则表达式无效时回退到普通模式
                is_regex = False

        while i < n:
            if text[i] == tag_open:
                j = i + 1
                while j < n and text[j] != tag_close:
                    j += 1

                if j < n:  # 找到 >
                    result.append(text[i:j + 1])
                    i = j + 1
                else:  # 没有闭合，当普通文本处理
                    if is_regex:
                        match = pattern.match(text, i)
                        if match:
                            result.append(replacement or "")
                            i = match.end()
                        else:
                            result.append(text[i])
                            i += 1
                    else:
                        if text[i:i + len(target)] == target:
                            result.append(replacement or "")
                            i += len(target)
                        else:
                            result.append(text[i])
                            i += 1
            else:
                if is_regex:
                    match = pattern.match(text, i)
                    if match:
                        result.append(replacement or "")
                        i = match.end()
                    else:
                        result.append(text[i])
                        i += 1
                else:
                    if text[i:i + len(target)] == target:
                        result.append(replacement or "")
                        i += len(target)
                    else:
                        result.append(text[i])
                        i += 1

        return ''.join(result)

    def count_outside_tags(self, text, target, tag_open="<", tag_close=">"):
        count = 0
        i = 0
        n = len(text)

        is_regex = self.mode_btn.text() == "正则模式"
        if is_regex:
            try:
                pattern = re.compile(target)
            except re.error:
                # 正则表达式无效时回退到普通模式
                is_regex = False

        while i < n:
            if text[i] == tag_open:
                j = i + 1
                while j < n and text[j] != tag_close:
                    j += 1

                if j < n:  # 找到 >
                    i = j + 1
                else:  # 没闭合，当普通文本处理
                    if is_regex:
                        match = pattern.match(text, i)
                        if match:
                            count += 1
                            i = match.end()
                        else:
                            i += 1
                    else:
                        if text[i:i + len(target)] == target:
                            count += 1
                            i += len(target)
                        else:
                            i += 1
            else:
                if is_regex:
                    match = pattern.match(text, i)
                    if match:
                        count += 1
                        i = match.end()
                    else:
                        i += 1
                else:
                    if text[i:i + len(target)] == target:
                        count += 1
                        i += len(target)
                    else:
                        i += 1

        return count

    def process_srt(self, lines, target_text, replacement_text):
        modified_lines = []
        is_processing_subtitle = False
        current_subtitle = []
        time_axis_pattern = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$')

        for line in lines:
            stripped_line = line.strip()
            if time_axis_pattern.match(stripped_line):
                if current_subtitle:
                    modified_lines.extend(current_subtitle)
                    modified_lines.append("\n")
                modified_lines.append(line)
                current_subtitle = []
                is_processing_subtitle = True
            elif is_processing_subtitle and stripped_line:
                replaced = self.replace_outside_tags(line, target_text, replacement_text, tag_open="<", tag_close=">")
                current_subtitle.append(replaced)
            else:
                is_processing_subtitle = False
                modified_lines.extend(current_subtitle)
                modified_lines.append(line)
                current_subtitle = []

        # 确保最后一段字幕也被写入
        if current_subtitle:
            modified_lines.extend(current_subtitle)
            modified_lines.append("\n")

        return modified_lines

    def process_ass(self, lines, target_text, replacement_text):
        modified_lines = []

        for line in lines:
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) > 9:
                    text = parts[9]
                    parts[9] = self.replace_outside_tags(
                        text,
                        target_text,
                        replacement_text,
                        tag_open="{",
                        tag_close="}"
                    )
                modified_lines.append(",".join(parts))
            else:
                modified_lines.append(line)

        return modified_lines

    def process_vtt(self, lines, target_text, replacement_text):
        modified_lines = []
        is_processing_subtitle = False
        found_first_time_axis = False
        current_subtitle = []

        # 匹配 VTT 时间轴行：如 00:00:05.000 --> 00:00:10.000，可带定位信息
        time_axis_pattern = re.compile(r'^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}')

        for line in lines:
            stripped = line.strip()

            if not found_first_time_axis:
                if time_axis_pattern.match(stripped):
                    found_first_time_axis = True
                    modified_lines.append(line)  # 只添加一次
                    is_processing_subtitle = True
                else:
                    modified_lines.append(line)
                continue

            if time_axis_pattern.match(stripped):
                if current_subtitle:
                    modified_lines.extend(current_subtitle)
                    modified_lines.append("\n")
                modified_lines.append(line)
                current_subtitle = []
                is_processing_subtitle = True
            elif is_processing_subtitle and stripped:
                replaced = self.replace_outside_tags(line, target_text, replacement_text, tag_open="<", tag_close=">")
                current_subtitle.append(replaced)
            else:
                is_processing_subtitle = False
                modified_lines.extend(current_subtitle)
                modified_lines.append(line)
                current_subtitle = []

        if current_subtitle:
            modified_lines.extend(current_subtitle)
            modified_lines.append("\n")

        return modified_lines

    def process_sub(self, lines, target_text, replacement_text):
        modified_lines = []
        is_processing_subtitle = False
        current_subtitle = []
        time_axis_pattern = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$')

        for line in lines:
            stripped_line = line.strip()
            if time_axis_pattern.match(stripped_line):
                if current_subtitle:
                    modified_lines.extend(current_subtitle)
                    modified_lines.append("\n")
                modified_lines.append(line)
                current_subtitle = []
                is_processing_subtitle = True
            elif is_processing_subtitle and stripped_line:
                replaced = self.replace_outside_tags(line, target_text, replacement_text, tag_open="{", tag_close="}")
                current_subtitle.append(replaced)
            else:
                is_processing_subtitle = False
                modified_lines.extend(current_subtitle)
                modified_lines.append(line)
                current_subtitle = []

        # 确保最后一段字幕也被写入
        if current_subtitle:
            modified_lines.extend(current_subtitle)
            modified_lines.append("\n")

        return modified_lines

    def process_lrc(self, lines, target_text, replacement_text):
        modified_lines = []
        time_tag_pattern = re.compile(r"(\[[0-9.:]+\])+")
        header_tag_pattern = re.compile(r"^\[(ti|ar|al|by|re|ve|offset):", re.IGNORECASE)

        for line in lines:
            # 保留头部标签（如 [ti:], [ar:] 等）
            if header_tag_pattern.match(line):
                modified_lines.append(line)
                continue

            # 识别时间标签
            match = time_tag_pattern.match(line)
            if match:
                time_tags = match.group(0)
                lyric_text = line[len(time_tags):]
                replaced = self.replace_outside_tags(lyric_text, target_text, replacement_text, tag_open="<", tag_close=">")
                modified_lines.append(f"{time_tags}{replaced}")
            else:
                # 没有匹配时间标签，保留原样
                modified_lines.append(line)
        return modified_lines

    def process_ssa(self, lines, target_text, replacement_text):
        return self.process_ass(lines, target_text, replacement_text)

    def count_target_text_in_srt(self, lines, target_text):
        count = 0
        is_processing_subtitle = False
        current_subtitle = []
        time_axis_pattern = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$')

        for line in lines:
            stripped_line = line.strip()
            if time_axis_pattern.match(stripped_line):
                if current_subtitle:
                    count += sum(
                        self.count_outside_tags(l, target_text, tag_open="<", tag_close=">") for l in current_subtitle)
                current_subtitle = []
                is_processing_subtitle = True
            elif is_processing_subtitle and stripped_line:
                current_subtitle.append(line)
            else:
                is_processing_subtitle = False
                count += sum(self.count_outside_tags(l, target_text, tag_open="<", tag_close=">") for l in current_subtitle)
                current_subtitle = []

            # 不遗漏最后一段字幕
        if current_subtitle:
            count += sum(self.count_outside_tags(l, target_text, tag_open="<", tag_close=">") for l in current_subtitle)

        return count

    def count_target_text_in_ass(self, lines, target_text):
        count = 0

        for line in lines:
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) > 9:
                    count += self.count_outside_tags(
                        parts[9],
                        target_text,
                        tag_open="{",
                        tag_close="}"
                    )

        return count

    def count_target_text_in_vtt(self, lines, target_text):
        count = 0
        is_processing_subtitle = False
        found_first_time_axis = False
        current_subtitle = []

        # VTT 时间轴格式：00:00:05.000 --> 00:00:10.000（可附带位置信息）
        time_axis_pattern = re.compile(r'^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}')

        for line in lines:
            stripped = line.strip()

            if not found_first_time_axis:
                if time_axis_pattern.match(stripped):
                    found_first_time_axis = True
                    is_processing_subtitle = True
                else:
                    continue  # 忽略时轴前内容

            if time_axis_pattern.match(stripped):
                if current_subtitle:
                    count += sum(
                        self.count_outside_tags(l, target_text, tag_open="<", tag_close=">") for l in current_subtitle)
                current_subtitle = []
                is_processing_subtitle = True
            elif is_processing_subtitle and stripped:
                current_subtitle.append(line)
            else:
                is_processing_subtitle = False
                count += sum(self.count_outside_tags(l, target_text, tag_open="<", tag_close=">") for l in current_subtitle)
                current_subtitle = []

        if current_subtitle:
            count += sum(self.count_outside_tags(l, target_text, tag_open="<", tag_close=">") for l in current_subtitle)

        return count

    def count_target_text_in_sub(self, lines, target_text):
        count = 0
        is_processing_subtitle = False
        current_subtitle = []
        time_axis_pattern = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$')

        for line in lines:
            stripped_line = line.strip()
            if time_axis_pattern.match(stripped_line):
                if current_subtitle:
                    count += sum(
                        self.count_outside_tags(l, target_text, tag_open="{", tag_close="}") for l in current_subtitle)
                current_subtitle = []
                is_processing_subtitle = True
            elif is_processing_subtitle and stripped_line:
                current_subtitle.append(line)
            else:
                is_processing_subtitle = False
                count += sum(self.count_outside_tags(l, target_text, tag_open="{", tag_close="}") for l in current_subtitle)
                current_subtitle = []

            # 不遗漏最后一段字幕
        if current_subtitle:
            count += sum(self.count_outside_tags(l, target_text, tag_open="{", tag_close="}") for l in current_subtitle)

        return count

    def count_target_text_in_lrc(self, lines, target_text):
        total_count = 0
        time_tag_pattern = re.compile(r"(\[[0-9.:]+\])+")
        header_tag_pattern = re.compile(r"^\[(ti|ar|al|by|re|ve|offset):", re.IGNORECASE)

        for line in lines:
            if header_tag_pattern.match(line):
                continue  # 忽略头部标签

            match = time_tag_pattern.match(line)
            if match:
                time_tags = match.group(0)
                lyric_text = line[len(time_tags):]
                total_count += self.count_outside_tags(lyric_text, target_text, tag_open="<", tag_close=">")
        return total_count

    def count_target_text_in_ssa(self, lines, target_text):
        return self.count_target_text_in_ass(lines, target_text)

    # ---------------- 事件 ----------------
    def on_text_change(self):
        self.timer.stop()
        self.timer.start()

    def update_count(self):
        self.timer.stop()
        target_text = self.text_entry.text()
        if not target_text:
            for item in self.files:
                item["extra"]["目标数"] = 0
            self.app.kit.ui.refresh_tree(1)
            return

        #print(f"开始刷新：“{target_text}”")

        for item in self.files:
            f = item["fullpath"]
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    lines = fp.readlines()

                ext = os.path.splitext(f)[1].lower()
                if ext == ".srt":
                    count = self.count_target_text_in_srt(lines, target_text)
                elif ext == ".ass":
                    count = self.count_target_text_in_ass(lines, target_text)
                elif ext == ".vtt":
                    count = self.count_target_text_in_vtt(lines, target_text)
                elif ext == ".sub":
                    count = self.count_target_text_in_sub(lines, target_text)
                elif ext == ".lrc":
                    count = self.count_target_text_in_lrc(lines, target_text)
                elif ext == ".ssa":
                    count = self.count_target_text_in_ssa(lines, target_text)
                else:
                    count = sum(l.count(target_text) for l in lines)

                item["extra"]["目标数"] = count

            except Exception as e:
                QMessageBox.critical(self.main_frame, "错误", f"{f}\n{e}")

        self.app.kit.ui.refresh_tree(1)

    def process_files(self):
        target_text = self.text_entry.text()
        replacement_text = self.replace_entry.text()

        if not target_text:
            QMessageBox.warning(self.main_frame, "提示", "请输入要替换的内容")
            return

        self.file_backup.clear()
        step_list = []

        for item in self.files:
            if not item.get("checked", False):
                continue

            f = item["fullpath"]
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    lines = fp.readlines()

                #self.file_backup[f] = lines

                ext = os.path.splitext(f)[1].lower()
                if ext == ".srt":
                    new_lines = self.process_srt(lines, target_text, replacement_text)
                elif ext == ".ass":
                    new_lines = self.process_ass(lines, target_text, replacement_text)
                elif ext == ".vtt":
                    new_lines = self.process_vtt(lines, target_text, replacement_text)
                elif ext == ".sub":
                    new_lines = self.process_sub(lines, target_text, replacement_text)
                elif ext == ".lrc":
                    new_lines = self.process_lrc(lines, target_text, replacement_text)
                elif ext == ".ssa":
                    new_lines = self.process_ssa(lines, target_text, replacement_text)
                else:
                    new_lines = [l.replace(target_text, replacement_text) for l in lines]

                with open(f, "w", encoding="utf-8") as fp:
                    fp.writelines(new_lines)

                step_list.append({"fullpath": f, "old_lines": lines, "new_lines": new_lines})

            except Exception as e:
                QMessageBox.critical(self.main_frame, "错误", f"{f}\n{e}")

        # 整体注册一次操作
        self.register_operation("replace", step_list, new_transaction=True)

        self.update_count()

        QMessageBox.information(self.main_frame, "完成", "处理完成")

    # ================= 撤销/重做 =================
    def register_operation(self, action, data_list, new_transaction=True):
        """
        action: str
        data_list: list
        new_transaction: 是否新建事务
        """

        step = {
            "action": action,
            "list": data_list
        }

        # 如果在 undo 状态下执行新操作，需要丢弃未来历史
        if self.operation_index < len(self.history) - 1:
            self.history = self.history[:self.operation_index + 1]

        if new_transaction or self.operation_index == -1:
            # 新建事务
            self.history.append([step])
            self.operation_index += 1
        else:
            # 添加到当前事务
            self.history[self.operation_index].append(step)

    def undo(self):
        if self.operation_index < 0:
            self.app.kit.ui.show_tree_message(1, "无可撤销内容")
            return

        actions = self.history[self.operation_index]
        for action in reversed(actions):
            if action["action"] == "replace":
                for entry in action["list"]:
                    with open(entry["fullpath"], "w", encoding="utf-8") as f:
                        f.writelines(entry["old_lines"])

                    # 立即更新 tree_file 中的数据（如果你用它显示文件内容）
                    file_data = self._find_file_data(entry["fullpath"])
                    if file_data:
                        file_data["extra"] = copy.deepcopy(file_data.get("extra", {}))

        self.operation_index -= 1

        self.update_count()

        self.app.kit.ui.show_tree_message(1, "撤销操作")

    def redo(self):
        if self.operation_index >= len(self.history) - 1:
            self.app.kit.ui.show_tree_message(1, "无可重做内容")
            return

        self.operation_index += 1
        actions = self.history[self.operation_index]

        for action in actions:
            if action["action"] == "replace":
                for entry in action["list"]:
                    # 写入新内容
                    with open(entry["fullpath"], "w", encoding="utf-8") as f:
                        f.writelines(entry["new_lines"])

                    # 更新 tree_file 中的数据（保证 UI 能显示最新状态）
                    file_data = self._find_file_data(entry["fullpath"])
                    if file_data:
                        file_data["extra"] = copy.deepcopy(file_data.get("extra", {}))

        self.update_count()

        self.app.kit.ui.show_tree_message(1, "重做操作")

    def _find_file_data(self, fullpath):
        for f in self.files:
            if f["fullpath"] == fullpath or f["fullpath"].replace("\\", "/") == fullpath.replace("\\", "/"):
                return f
        return None

    def restore_files(self):
        for f, lines in self.file_backup.items():
            try:
                with open(f, "w", encoding="utf-8") as fp:
                    fp.writelines(lines)
            except Exception as e:
                QMessageBox.critical(self.main_frame, "错误", f"{f}\n{e}")

        QMessageBox.information(self.main_frame, "完成", "已恢复所有文件")


# ================== 入口 ==================
def create_ui(app, main_frame, edit_frame, scale):
    global replace_app
    replace_app = ReplaceApp(app, main_frame, edit_frame, scale)

"""def on_add_end(tree_id, pulugin_name=None):
    print("on_add_end")
    replace_app.update_count()"""

def on_add_files_end(tree_id, pulugin_name=None):
    #print("on_add_files_end")
    replace_app.update_count()

def get_info():
    return {"icon":{"text":"📝"}, "display_name":"替换字幕文本"}