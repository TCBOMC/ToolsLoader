import re
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QComboBox, QLineEdit, QPushButton, QCheckBox,
                             QFrame, QSpacerItem, QSizePolicy, QTimeEdit)
from PyQt5.QtCore import Qt, QTime
from datetime import timedelta
import os


class OffsetTimelineApp:
    def __init__(self, app, main_frame, edit_frame, scale):
        self.app = app
        self.main_frame = main_frame
        self.edit_frame = edit_frame
        self.scale = scale

        # 存储文件列表的引用
        self.tree_files = None

        self.history = []
        self.operation_index = -1

        # 创建主布局
        self.setup_ui()

    def setup_ui(self):
        """设置用户界面"""
        main_layout = QVBoxLayout(self.main_frame)
        main_layout.setContentsMargins(10, 0, 10, 0)
        #main_layout.setSpacing(15)

        # 创建上方的控制面板
        self.create_control_panel(main_layout)

        # 创建文件列表区域
        tree_frame = QWidget()
        tree_layout = QVBoxLayout(tree_frame)
        tree_layout.setContentsMargins(0, 0, 0, 10)

        # 使用外部传入的 create_tree_view 函数生成 tree
        extra_cols = [
            (None, 400, True),
            #("test", 120, False)
        ]

        # 创建文件列表
        tree_container, tree, self.tree_files = self.app.kit.ui.create_tree_view(
            tree_frame, tree_index=1, extra_columns=extra_cols
        )
        tree_layout.addWidget(tree_container)

        # 将文件列表区域添加到主布局
        main_layout.addWidget(tree_frame)

    def create_control_panel(self, parent_layout):
        """创建控制面板"""
        # 主水平布局
        self.control_frame = QWidget()
        control_frame_height = int(round(20 * self.scale))
        self.control_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.control_frame.setFixedHeight(control_frame_height)

        control_layout = QHBoxLayout(self.control_frame)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(int(10 * self.scale))

        # 字幕类型选择
        type_label = QLabel("字幕类型:")
        self.type_combo = QComboBox()
        self.type_combo.addItems(["ass", "ssa", "srt"])

        # 偏移时间输入 - 使用 QTimeEdit
        time_label = QLabel("偏移时间:")

        # 正负号切换按钮
        self.time_sign_btn = QPushButton("+")
        #self.time_sign_btn.setMaximumWidth(25)
        #self.time_sign_btn.setMaximumHeight(25)
        self.time_sign_btn.setCheckable(True)  # 设置为可切换状态
        self.time_sign_btn.setChecked(False)  # 默认为正号
        self.time_sign_btn.setStyleSheet("""
                    QPushButton {
                        border: 1px solid #ccc;
                        border-radius: 3px;
                        background-color: #f0f0f0;
                        padding-left: 6px;    /* 左侧内边距 */
                        padding-right: 6px;   /* 右侧内边距 */
                        padding-top: 3px;     /* 顶部内边距 */
                        padding-bottom: 3px;  /* 底部内边距 */
                    }
                    QPushButton:checked {
                        background-color: #e0e0e0;
                        border: 1px solid #999;
                    }
                """)
        self.time_sign_btn.clicked.connect(self.on_sign_btn_clicked)

        # 时间输入控件
        self.time_input = QTimeEdit()
        self.time_input.setDisplayFormat("HH:mm:ss.zzz")
        self.time_input.setTime(QTime(0, 0, 0, 0))  # 设置默认值
        self.time_input.setFixedWidth(int(90 * self.scale))

        # 水平布局组合
        time_layout = QHBoxLayout()
        time_layout.setSpacing(0)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.addWidget(self.time_sign_btn)
        time_layout.addWidget(self.time_input)

        # 添加到左侧布局
        control_layout.addWidget(type_label)
        control_layout.addWidget(self.type_combo)
        #control_layout.addSpacing(20)
        control_layout.addWidget(time_label)
        control_layout.addLayout(time_layout)

        # 添加弹性空间
        #control_layout.addStretch()

        # ASS/SSA 特定选项容器
        self.ass_options_container = QWidget()
        ass_options_layout = QHBoxLayout(self.ass_options_container)
        ass_options_layout.setContentsMargins(0, 0, 0, 0)
        #ass_options_layout.setSpacing(15)

        # 样式输入框
        style_label = QLabel("样式:")
        self.style_input = QLineEdit()
        self.style_input.setPlaceholderText("为空时偏移所有样式")
        self.style_input.setMinimumWidth(int(45 * self.scale))

        # "包含Comment"复选框
        self.include_comment_check = QCheckBox("包含Comment")

        ass_options_layout.addWidget(style_label)
        ass_options_layout.addWidget(self.style_input)
        ass_options_layout.addWidget(self.include_comment_check)

        # 添加到控制布局
        control_layout.addWidget(self.ass_options_container)

        # 执行按钮（右对齐）
        self.execute_btn = QPushButton("执行偏移")
        self.execute_btn.setFixedWidth(int(round(60 * self.scale)))
        #self.execute_btn.setFixedHeight(int(round(20 * self.scale)))
        control_layout.addWidget(self.execute_btn)

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

        control_layout.addWidget(undo_redo_widget)

        undo_redo_layout.addWidget(self.undo_btn)
        undo_redo_layout.addWidget(self.redo_btn)

        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn.clicked.connect(self.redo)

        # 将控制面板添加到父布局
        #parent_layout.addLayout(control_layout)
        parent_layout.addWidget(self.control_frame)

        # 连接信号
        self.type_combo.currentTextChanged.connect(self.on_type_changed)
        self.execute_btn.clicked.connect(self.execute_offset)

        # 初始化ASS/SSA选项的可见性
        self.on_type_changed(self.type_combo.currentText())

    def on_type_changed(self, text):
        """字幕类型改变时的处理"""
        # ASS/SSA类型显示额外选项，SRT类型隐藏
        if text in ["ass", "ssa"]:
            self.ass_options_container.show()
            # 移除可能存在的stretch
            control_layout = self.control_frame.layout()
            # 查找并移除stretch
            for i in range(control_layout.count()):
                item = control_layout.itemAt(i)
                if isinstance(item, QSpacerItem):
                    control_layout.removeItem(item)
                    break
        else:
            self.ass_options_container.hide()
            # 添加stretch替代隐藏的widget
            control_layout = self.control_frame.layout()
            # 查找ass_options_container的位置并插入stretch
            for i in range(control_layout.count()):
                widget = control_layout.itemAt(i).widget()
                if widget == self.ass_options_container:
                    # 使用insertStretch而不是addStretch，确保位置正确
                    control_layout.insertStretch(i, 0)
                    break

    def on_sign_btn_clicked(self):
        """正负号按钮点击事件"""
        if self.time_sign_btn.isChecked():
            self.time_sign_btn.setText("-")
            self.time_sign_btn.setToolTip("负偏移（时间提前）")
        else:
            self.time_sign_btn.setText("+")
            self.time_sign_btn.setToolTip("正偏移（时间延后）")

    def parse_time_string(self, time_str):
        """解析时间字符串为毫秒数"""
        # 使用 QTimeEdit 的解析方式
        qtime = self.time_input.time()
        total_ms = (qtime.hour() * 3600 + qtime.minute() * 60 + qtime.second()) * 1000 + qtime.msec()

        # 应用正负号（按钮被选中表示负号）
        if self.time_sign_btn.isChecked():
            total_ms = -total_ms

        return total_ms

    def format_timedelta(self, td):
        """将timedelta格式化为字符串"""
        total_seconds = td.total_seconds()
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        milliseconds = int((total_seconds - int(total_seconds)) * 1000)

        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

    def process_ass_file(self, file_path, offset_ms, include_comment, target_styles):
        """处理ASS/SSA文件"""
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()

            lines = content.split('\n')
            in_events_section = False
            modified_lines = []

            for line in lines:
                # 检查是否进入[Events]部分
                if line.strip().lower() == '[events]':
                    in_events_section = True
                    modified_lines.append(line)
                    continue

                # 检查是否离开[Events]部分
                if in_events_section and line.strip().startswith('['):
                    in_events_section = False

                # 处理[Events]部分中的时间轴
                if in_events_section and (line.strip().startswith('Dialogue:') or line.strip().startswith('Comment:')):
                    parts = line.split(',', maxsplit=9)

                    if len(parts) >= 10:
                        event_type = parts[0].strip()

                        # 检查是否为Comment类型且是否包含Comment
                        if event_type.startswith('Comment:') and not include_comment:
                            modified_lines.append(line)
                            continue

                        # 检查样式是否在目标样式中（如果指定了目标样式）
                        style = parts[3].strip()
                        if target_styles and style not in target_styles:
                            modified_lines.append(line)
                            continue

                        # 解析和修改时间
                        start_time = parts[1].strip()
                        end_time = parts[2].strip()

                        # 解析ASS/SSA时间（秒精确到2位，格式：H:MM:SS.cc）
                        def parse_ass_time(t):
                            hh, mm, ss_ms = t.split(':')
                            # 处理秒和小数秒部分
                            if '.' in ss_ms:
                                ss, cs = ss_ms.split('.')  # cs是百分秒
                                # 如果小数部分有3位（来自offset_ms），只取前2位
                                if len(cs) > 2:
                                    cs = cs[:2]
                                elif len(cs) == 1:
                                    cs = cs + '0'
                                milliseconds = int(cs) * 10  # 百分秒转毫秒
                            else:
                                ss = ss_ms
                                milliseconds = 0

                            return timedelta(hours=int(hh), minutes=int(mm),
                                             seconds=int(ss), milliseconds=milliseconds)

                        # 格式化时间为ASS/SSA格式（秒精确到2位）
                        def format_ass_time(td):
                            total_seconds = td.total_seconds()
                            hours = int(total_seconds // 3600)
                            minutes = int((total_seconds % 3600) // 60)
                            seconds = int(total_seconds % 60)
                            centiseconds = int((td.microseconds // 10000) % 100)  # 百分秒

                            # 格式化：H:MM:SS.cc
                            return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"

                        try:
                            start_td = parse_ass_time(start_time)
                            end_td = parse_ass_time(end_time)

                            # 应用偏移
                            offset_td = timedelta(milliseconds=offset_ms)
                            new_start_td = start_td + offset_td
                            new_end_td = end_td + offset_td

                            # 确保时间不为负
                            if new_start_td.total_seconds() < 0:
                                new_start_td = timedelta(0)
                            if new_end_td.total_seconds() < 0:
                                new_end_td = timedelta(milliseconds=100)

                            parts[1] = format_ass_time(new_start_td)
                            parts[2] = format_ass_time(new_end_td)

                            modified_line = ','.join(parts)
                            modified_lines.append(modified_line)
                        except Exception as e:
                            print(f"时间解析错误: {e}, 原始行: {line}")
                            modified_lines.append(line)
                    else:
                        modified_lines.append(line)
                else:
                    modified_lines.append(line)

            # 写回文件
            with open(file_path, 'w', encoding='utf-8-sig') as f:
                f.write('\n'.join(modified_lines))

            return True, f"成功处理: {os.path.basename(file_path)}"

        except Exception as e:
            return False, f"处理失败 {os.path.basename(file_path)}: {str(e)}"

    def process_srt_file(self, file_path, offset_ms):
        """处理SRT文件"""
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()

            lines = content.split('\n')
            modified_lines = []
            i = 0

            while i < len(lines):
                line = lines[i].strip()

                # 检查是否为时间轴行
                if '-->' in line:
                    try:
                        # 解析时间轴
                        start_str, end_str = line.split(' --> ')

                        # 解析SRT时间格式
                        def parse_srt_time(t):
                            hh, mm, ss_ms = t.split(':')
                            ss, ms = ss_ms.split(',')
                            return timedelta(hours=int(hh), minutes=int(mm),
                                             seconds=int(ss), milliseconds=int(ms))

                        def format_srt_time(td):
                            hours = td.seconds // 3600 + td.days * 24
                            minutes = (td.seconds % 3600) // 60
                            seconds = td.seconds % 60
                            milliseconds = td.microseconds // 1000
                            return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

                        start_td = parse_srt_time(start_str.strip())
                        end_td = parse_srt_time(end_str.strip())

                        # 应用偏移
                        offset_td = timedelta(milliseconds=offset_ms)
                        new_start_td = start_td + offset_td
                        new_end_td = end_td + offset_td

                        # 确保时间不为负
                        if new_start_td.total_seconds() < 0:
                            new_start_td = timedelta(0)
                        if new_end_td.total_seconds() < 0:
                            new_end_td = timedelta(milliseconds=100)

                        new_line = f"{format_srt_time(new_start_td)} --> {format_srt_time(new_end_td)}"
                        modified_lines.append(new_line)
                    except Exception as e:
                        modified_lines.append(line)
                else:
                    modified_lines.append(lines[i])

                i += 1

            # 写回文件
            with open(file_path, 'w', encoding='utf-8-sig') as f:
                f.write('\n'.join(modified_lines))

            return True, f"成功处理: {os.path.basename(file_path)}"

        except Exception as e:
            return False, f"处理失败 {os.path.basename(file_path)}: {str(e)}"

    def execute_offset(self):
        """执行偏移操作"""
        # 获取输入值
        subtitle_type = self.type_combo.currentText()
        time_str = self.time_input.text().strip()

        # 验证时间输入
        if not time_str:
            self.show_message("错误", "请输入偏移时间")
            return

        try:
            offset_ms = self.parse_time_string(time_str)
        except ValueError as e:
            self.show_message("错误", f"时间格式错误: {str(e)}")
            return

        # 获取要处理的文件
        if not self.tree_files:
            self.show_message("错误", "没有找到要处理的文件")
            return

        # 获取选中的文件（根据checked字段）
        selected_files = []
        for file_info in self.tree_files:
            if file_info.get('checked', False):
                file_path = file_info.get('fullpath', '') or file_info.get('path', '')
                if file_path and os.path.exists(file_path):
                    selected_files.append(file_path)

        if not selected_files:
            self.show_message("错误", "没有选中要处理的文件")
            return

        # 处理每个文件
        success_count = 0
        fail_count = 0
        messages = []

        # 撤销记录数据
        operation_data = []

        for file_path in selected_files:
            if not os.path.exists(file_path):
                messages.append(f"文件不存在: {os.path.basename(file_path)}")
                fail_count += 1
                continue

            # 根据文件后缀名和选择的类型确定处理方式
            file_ext = os.path.splitext(file_path)[1].lower().lstrip('.')

            # 如果文件扩展名与选择的类型不匹配，跳过或按文件扩展名处理
            actual_type = file_ext if file_ext in ['ass', 'ssa', 'srt'] else subtitle_type

            try:
                # 读取原始内容
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    old_content = f.read()

                # 根据文件类型调用相应的处理函数
                if actual_type in ['ass', 'ssa']:
                    include_comment = self.include_comment_check.isChecked()
                    style_filter = self.style_input.text().strip()
                    target_styles = [s.strip() for s in style_filter.split(',')] if style_filter else []

                    success, message = self.process_ass_file(file_path, offset_ms,
                                                             include_comment, target_styles)
                else:  # srt
                    success, message = self.process_srt_file(file_path, offset_ms)

                if success:
                    # 读取新内容
                    with open(file_path, "r", encoding="utf-8-sig") as f:
                        new_content = f.read()

                    # 记录撤销信息
                    operation_data.append({
                        "fullpath": file_path,
                        "old_content": old_content,
                        "new_content": new_content
                    })

                    success_count += 1
                else:
                    fail_count += 1

                messages.append(message)

            except Exception as e:
                error_msg = f"处理失败 {os.path.basename(file_path)}: {str(e)}"
                messages.append(error_msg)
                fail_count += 1
                print(f"处理异常: {error_msg}")  # 调试输出

        # ===== 注册历史记录（只注册一次）=====
        if operation_data:
            self.register_operation(
                "offset",
                operation_data,
                new_transaction=True
            )

        # 显示结果
        result_msg = f"处理完成!\n成功: {success_count} 个文件\n失败: {fail_count} 个文件"

        if messages:
            # 显示最后几条消息（避免消息过长）
            display_count = min(10, len(messages))
            result_msg += f"\n\n最后{display_count}条处理信息:\n" + "\n".join(messages[-display_count:])

        self.show_message("结果", result_msg)

        # 调试输出，帮助定位问题
        print(f"选中文件数: {len(selected_files)}")
        print(f"成功: {success_count}, 失败: {fail_count}")
        for msg in messages:
            print(f"  - {msg}")

    def show_message(self, title, message):
        """显示消息对话框"""
        # 这里可以根据您的应用环境使用相应的消息框
        # 例如: QMessageBox.information(self.main_frame, title, message)
        print(f"[{title}] {message}")  # 临时使用print，您可以根据需要修改
        self.app.kit.ui.show_message_box(f"{title}", f"{message}", "info")

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

        # 步骤逆序
        for action in reversed(actions):

            if action["action"] == "rename":
                continue
                #self.undo_rename(action["list"])

            elif action["action"] == "scrub":
                continue
                #self.undo_scrub(action["list"])

            elif action["action"] == "offset":
                self.undo_offset(action["list"])

        self.operation_index -= 1
        self.app.kit.ui.show_tree_message(1, "撤销操作")

    def redo(self):
        if self.operation_index >= len(self.history) - 1:
            self.app.kit.ui.show_tree_message(1, "无可重做内容")
            return

        self.operation_index += 1
        actions = self.history[self.operation_index]

        for action in actions:

            if action["action"] == "rename":
                continue
                #self.redo_rename(action["list"])

            elif action["action"] == "scrub":
                continue
                #self.redo_scrub(action["list"])

            elif action["action"] == "offset":
                self.redo_offset(action["list"])

        self.app.kit.ui.show_tree_message(1, "重做操作")

    def undo_offset(self, items):

        for entry in reversed(items):

            fullpath = entry["fullpath"]
            old_content = entry["old_content"]

            if os.path.exists(fullpath):
                with open(fullpath, "w", encoding="utf-8-sig") as f:
                    f.write(old_content)

        self.app.kit.ui.refresh_tree(1)

    def redo_offset(self, items):

        for entry in items:

            fullpath = entry["fullpath"]
            new_content = entry["new_content"]

            if os.path.exists(fullpath):
                with open(fullpath, "w", encoding="utf-8-sig") as f:
                    f.write(new_content)

        self.app.kit.ui.refresh_tree(1)

def create_ui(app, main_frame, edit_frame, scale):
    global offsetTimeline
    #root = TkinterDnD.Tk()
    offsetTimeline = OffsetTimelineApp(app, main_frame, edit_frame, scale)

def get_info():
    return {"icon":{"text_ico":"\uf1de"}, "display_name":"字幕调轴器"}