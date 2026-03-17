import os
import zlib
import re
import copy
import queue
import random
import struct
import threading
from PyQt5 import QtWidgets, QtCore
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QLabel, QFileDialog, QMessageBox
)

def calculate_crc32(file_path):
    """计算文件 CRC32"""
    buf_size = 65536
    crc32 = 0
    with open(file_path, 'rb') as f:
        while chunk := f.read(buf_size):
            crc32 = zlib.crc32(chunk, crc32)
    return f"{crc32 & 0xFFFFFFFF:08X}"


def get_preview_name(file_name, crc32_value):
    """生成预览文件名"""
    match = re.search(r"\[([A-F0-9]{8})\]\.\w+$", file_name, re.IGNORECASE)
    if match:
        return re.sub(
            r"\[([A-F0-9]{8})\](\.\w+)$",
            f"[{crc32_value}]\\2",
            file_name,
            flags=re.IGNORECASE
        )
    else:
        name, ext = os.path.splitext(file_name)
        return f"{name} [{crc32_value}]{ext}"


class RenameApp(QtCore.QObject):
    rename_request = QtCore.pyqtSignal()

    def __init__(self, app, main_frame, edit_frame, scale):
        super().__init__()
        self.app = app
        self.main_frame = main_frame
        self.edit_frame = edit_frame
        self.scale = scale

        self.renamed_files = []

        self.create_widgets()

        # 校验队列（只存 fullpath）
        self.verify_queue = queue.Queue()

        # 已经校验过的文件（避免重复）
        #self.verified_set = set()

        # 当前排队中的文件（避免重复入队）
        self.in_queue_set = set()

        # 历史
        self.history = []
        self.operation_index = -1

        # 校验线程
        self.verify_thread = None

        # 线程锁
        self.lock = threading.Lock()

        self._PATCH_INV_MATRIX = self._build_patch_matrix()

        self.rename_request.connect(self.on_rename_files)

    # ================= UI =================
    def create_widgets(self):
        layout = QtWidgets.QVBoxLayout(self.main_frame)
        layout.setContentsMargins(10, 0, 10, 10)

        # ===== 顶部按钮 =====
        # 用 QWidget 包裹按钮布局，方便设置固定高度
        button_widget = QtWidgets.QWidget()
        button_widget.setFixedHeight(int(round(20 * self.scale)))  # 根据 scale 调整高度
        button_layout = QtWidgets.QHBoxLayout(button_widget)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(int(round(10 * self.scale)))

        self.ogname_hash_btn = QtWidgets.QPushButton("原名洗码")
        self.rename_hash_btn = QtWidgets.QPushButton("改名洗码")
        self.text_entry = QLineEdit()
        self.text_entry.setPlaceholderText("目标hash(8位可选)")
        self.rename_btn = QtWidgets.QPushButton("确认改名")
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

        self.ogname_hash_btn.setFixedWidth(int(round(60 * self.scale)))
        self.rename_hash_btn.setFixedWidth(int(round(60 * self.scale)))
        #self.text_entry.setFixedWidth(int(round(90 * self.scale)))
        self.rename_btn.setFixedWidth(int(round(60 * self.scale)))
        #self.restore_btn.setFixedWidth(int(round(60 * self.scale)))

        """# 可选：按钮字体大小也跟随 scale 缩放
        for btn in [self.ogname_hash_btn, self.rename_hash_btn, self.rename_btn, self.undo_btn, self.redo_btn]:
            font = btn.font()
            font.setPointSizeF(8 * self.scale)
            btn.setFont(font)"""

        button_layout.addWidget(self.ogname_hash_btn)
        button_layout.addWidget(self.rename_hash_btn)
        button_layout.addWidget(self.text_entry, 1)
        button_layout.addStretch()
        button_layout.addWidget(self.rename_btn)
        button_layout.addWidget(undo_redo_widget)

        layout.addWidget(button_widget)

        # ===== 使用主程序封装的 TreeView =====
        extra_cols = [
            ("当前文件名", 400, True),
            ("校验", 60, False, 0),
            ("预览文件名", 400, True)
        ]

        tree_container, self.tree, self.tree_file = \
            self.app.kit.ui.create_tree_view(
                tree_index=1,
                extra_columns=extra_cols,
                #show_checkbox=False
            )

        layout.addWidget(tree_container)

        # ===== 信号绑定 =====
        self.ogname_hash_btn.clicked.connect(self.wash_crc_mode1)
        self.rename_hash_btn.clicked.connect(self.wash_crc_mode3)
        self.rename_btn.clicked.connect(self.on_rename_files_only)
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn.clicked.connect(self.redo)

    # ================= 后台计算 =================

    def process_files_in_background(self):

        for file_data in self.tree_file:

            file_path = file_data["fullpath"]
            file_name = file_data["filename"]

            crc32_value = calculate_crc32(file_path)
            new_name = get_preview_name(file_name, crc32_value)

            # ===== 校验状态 =====
            if file_name == new_name:
                check_result = "通过"
            else:
                match = re.search(r"\[([A-F0-9]{8})\]\.\w+$", file_name, re.IGNORECASE)
                if match:
                    check_result = "不通过"
                else:
                    check_result = "无法校验"

            # ===== 写入 extra =====
            file_data["extra"]["校验"] = check_result
            file_data["extra"]["预览文件名"] = new_name

            self.app.kit.ui.refresh_tree(1)

    def verify_worker(self):

        while True:
            try:
                fullpath = self.verify_queue.get(timeout=1)
            except queue.Empty:
                continue

            # ===== 标记为处理中 =====
            self.app.kit.ui.update_tree_item_color(
                1, fullpath, "processing"
            )

            with self.lock:
                self.in_queue_set.discard(fullpath)

            # 找到对应数据
            file_data = None
            for item in self.tree_file:
                if item["fullpath"] == fullpath:
                    file_data = item
                    break

            if not file_data:
                self.verify_queue.task_done()
                continue

            filename = file_data["filename"]

            try:
                crc32_value = calculate_crc32(fullpath)
                new_name = get_preview_name(filename, crc32_value)

                # 存入 extra
                file_data["extra"]["calculated_hash"] = crc32_value
                # 假设原始 CRC 可以从文件名里解析
                match = re.search(r"\[([A-F0-9]{8})\]\.\w+$", filename, re.IGNORECASE)
                file_data["extra"]["original_hash"] = match.group(1) if match else None

                if filename == new_name:
                    check_result = "通过"
                    color_state = "success"

                else:
                    match = re.search(
                        r"\[([A-F0-9]{8})\]\.\w+$",
                        filename,
                        re.IGNORECASE
                    )

                    if match:
                        check_result = "不通过"
                        color_state = "fail"
                    else:
                        check_result = "无法校验"
                        color_state = "partial"

            except Exception:
                check_result = "校验失败"
                new_name = ""
                color_state = "fail"
                file_data["extra"]["calculated_hash"] = None
                file_data["extra"]["original_hash"] = None

            # ===== 更新数据 =====
            with self.lock:
                file_data["extra"]["校验"] = check_result
                file_data["extra"]["预览文件名"] = new_name
                #self.verified_set.add(fullpath)

            # 刷新显示
            self.app.kit.ui.refresh_tree(1)

            # ===== 染色（子线程直接调用即可）=====
            self.app.kit.ui.update_tree_item_color(
                1, fullpath, color_state
            )

            self.verify_queue.task_done()

    # ================= 更新显示 =================

    def update_tree(self):

        self.tree.clear()

        for file_data in self.tree_file:

            check = file_data.get("check_result", "")
            current = file_data.get("current_name", "")
            new = file_data.get("new_name", "")

            item = QtWidgets.QTreeWidgetItem(
                [current, check, new]
            )

            if check == "通过":
                item.setBackground(1, QtCore.Qt.green)
            elif check == "不通过":
                item.setBackground(1, QtCore.Qt.red)
            elif check == "无法校验":
                item.setBackground(1, QtCore.Qt.yellow)

            self.tree.addTopLevelItem(item)

    # ================= 重命名 =================
    def on_rename_files_only(self):
        renamed_files = self.rename_files()
        if renamed_files:
            self.register_operation(
                action="rename",
                data_list=renamed_files,
                new_transaction=True
            )

        QtWidgets.QMessageBox.information(
            self.main_frame,
            "完成",
            "文件重命名完成！"
        )

    def on_rename_files(self):
        renamed_files = self.rename_files()
        if renamed_files:
            self.register_operation(
                action="rename",
                data_list=renamed_files,
                new_transaction=False
            )

        QtWidgets.QMessageBox.information(
            self.main_frame,
            "完成",
            "改名洗码完成！"
        )

    def rename_files(self):
        """
        重命名文件：
        - 仅处理 tree_file 中 checked==True 的文件
        - 使用 extra['预览文件名'] 作为新文件名
        - 记录 renamed_files
        """
        renamed_files = []
        for file_data in self.tree_file:
            if not file_data.get("checked", False):
                continue  # 只处理勾选的文件

            file_path = file_data["fullpath"]
            new_name = file_data["extra"].get("预览文件名", None)
            if not new_name:
                continue  # 没有预览名就跳过

            original_name = file_data.get("filename", os.path.basename(file_path))
            old_extra = copy.deepcopy(file_data["extra"])

            directory = os.path.dirname(file_path)
            new_path = os.path.join(directory, new_name)

            if file_path != new_path:
                try:
                    os.rename(file_path, new_path)

                    # 更新数据结构
                    file_data["fullpath"] = new_path
                    file_data["filename"] = new_name
                    new_extra = copy.deepcopy(file_data["extra"])

                    renamed_files.append({
                        "fullpath": new_path,
                        "original_name": original_name,
                        "old_extra": old_extra,
                        "new_extra": new_extra
                    })

                except Exception as e:
                    QtWidgets.QMessageBox.critical(
                        self.main_frame,
                        "错误",
                        f"无法重命名文件：\n{e}"
                    )
                    return

        # 刷新树控件
        self.app.kit.ui.refresh_tree(1)
        return renamed_files

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
                self.undo_rename(action["list"])

            elif action["action"] == "scrub":
                self.undo_scrub(action["list"])

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
                self.redo_rename(action["list"])

            elif action["action"] == "scrub":
                self.redo_scrub(action["list"])

        self.app.kit.ui.show_tree_message(1, "重做操作")

    def undo_scrub(self, items):
        for entry in reversed(items):
            fullpath = entry["fullpath"]
            added = entry["added_byte"]

            size = os.path.getsize(fullpath)
            with open(fullpath, "rb+") as f:
                f.truncate(size - len(added))

            # 恢复原始 CRC
            file_data = self._find_file_data(fullpath)
            if file_data:
                file_data["extra"] = copy.deepcopy(entry["old_extra"])
        self.app.kit.ui.refresh_tree(1)

    def redo_scrub(self, items):
        for entry in items:
            fullpath = entry["fullpath"]
            added = entry["added_byte"]

            with open(fullpath, "ab") as f:
                f.write(added)

            # 更新 CRC 为新值
            file_data = self._find_file_data(fullpath)
            if file_data:
                file_data["extra"] = copy.deepcopy(entry["new_extra"])
        self.app.kit.ui.refresh_tree(1)

    def undo_rename(self, items):
        for entry in reversed(items):
            fullpath = entry["fullpath"]
            original_name = entry["original_name"]
            dirpath = os.path.dirname(fullpath)
            original_fullpath = os.path.join(dirpath, original_name)
            if os.path.exists(fullpath):
                os.rename(fullpath, original_fullpath)

            # 更新 tree_file 中 filename 和 fullpath
            file_data = self._find_file_data(fullpath)
            if file_data:
                file_data["filename"] = original_name
                file_data["fullpath"] = original_fullpath
                file_data["extra"] = copy.deepcopy(entry["old_extra"])
        self.app.kit.ui.refresh_tree(1)

    def redo_rename(self, items):
        for entry in items:
            fullpath = entry["fullpath"]
            original_name = entry["original_name"]
            dirpath = os.path.dirname(fullpath)
            original_fullpath = os.path.join(dirpath, original_name)
            if os.path.exists(original_fullpath):
                os.rename(original_fullpath, fullpath)

            # 更新 tree_file 中 filename 和 fullpath
            file_data = self._find_file_data(original_fullpath)
            if file_data:
                file_data["filename"] = os.path.basename(fullpath)
                file_data["fullpath"] = fullpath
                file_data["extra"] = copy.deepcopy(entry["new_extra"])
        self.app.kit.ui.refresh_tree(1)

    def _find_file_data(self, fullpath):
        for f in self.tree_file:
            if f["fullpath"] == fullpath or f["fullpath"].replace("\\", "/") == fullpath.replace("\\", "/"):
                return f
        return None

    # ================= 洗码模式 =================
    def wash_crc_mode1(self):
        """
        模式1（高效版）：
        - 只处理 tree_file 中 checked==True 的文件
        - 使用 extra['calculated_hash'] 作为当前 CRC
        - 在文件末尾追加 4 字节补丁，但保持 CRC 不变
        """

        def worker():
            ops = []  # 记录补丁操作
            for file_data in self.tree_file:
                if not file_data.get("checked", False):
                    continue

                fullpath = file_data["fullpath"]
                calculated_crc_hex = file_data["extra"].get("calculated_hash", None)
                if not calculated_crc_hex:
                    print(f"跳过未计算 CRC 的文件: {fullpath}")
                    continue

                try:
                    current_crc = int(calculated_crc_hex, 16)
                except ValueError:
                    print(f"无效 CRC32 值: {calculated_crc_hex} -> {fullpath}")
                    continue

                old_extra = copy.deepcopy(file_data["extra"])

                try:
                    # 模式1补丁：保持 CRC 不变
                    patch = self._compute_patch_bytes(current_crc, current_crc)

                    # 追加到文件末尾
                    with open(fullpath, "ab") as f:
                        f.write(patch)

                    # 更新 calculated_hash
                    file_data["extra"]["calculated_hash"] = f"{current_crc:08X}"
                    new_extra = copy.deepcopy(file_data["extra"])

                    # 记录补丁
                    ops.append({
                        "fullpath": fullpath,
                        "added_byte": patch,
                        "original_crc": current_crc,
                        "new_crc": current_crc,
                        "old_extra": old_extra,
                        "new_extra": new_extra
                    })

                    print(f"已处理文件（CRC保持不变）: {fullpath}")
                except Exception as e:
                    print(f"处理失败: {fullpath}, {e}")

            # 注册为一个新的事务
            if ops:
                self.register_operation(action="scrub", data_list=ops, new_transaction=True)

            self.app.kit.ui.show_message_box("完成", "原名洗码完成！")



        # 启动后台线程
        threading.Thread(target=worker, daemon=True).start()

    def wash_crc_mode3(self):
        """
        模式3：
        - 从 self.text_entry 获取目标 CRC
        - 空值时随机生成
        - 对 checked 文件追加 4 字节补丁，使 CRC = 目标值
        - 更新预览文件名并进行重命名
        """
        target_crc_hex = self.text_entry.text().strip()
        ops = []  # 记录补丁操作

        # 处理目标 CRC
        if not target_crc_hex:
            # 随机生成 32 位 CRC
            target_crc = random.randint(0, 0xFFFFFFFF)
            target_crc_hex = f"{target_crc:08X}"
        else:
            try:
                target_crc = int(target_crc_hex, 16) & 0xFFFFFFFF
            except ValueError:
                print(f"输入的目标 CRC 不合法: {target_crc_hex}")
                QtWidgets.QMessageBox.critical(
                    self.main_frame,
                    "错误",
                    f"输入的目标 CRC 不合法: {target_crc_hex}"
                )
                return

        def worker():
            for file_data in self.tree_file:
                if not file_data.get("checked", False):
                    continue

                fullpath = file_data["fullpath"]
                filename = file_data["filename"]
                calculated_crc_hex = file_data["extra"].get("calculated_hash", None)
                if not calculated_crc_hex:
                    print(f"跳过未计算 CRC 的文件: {fullpath}")
                    continue

                try:
                    current_crc = int(calculated_crc_hex, 16)
                except ValueError:
                    print(f"无效 CRC32 值: {calculated_crc_hex} -> {fullpath}")
                    continue

                old_extra = copy.deepcopy(file_data["extra"])

                try:
                    # 生成补丁，使 CRC = 目标值
                    patch = self._compute_patch_bytes(current_crc, target_crc)
                    with open(fullpath, "ab") as f:
                        f.write(patch)

                    # 更新预览文件名
                    new_preview_name = get_preview_name(filename, f"{target_crc:08X}")
                    file_data["extra"]["预览文件名"] = new_preview_name
                    new_extra = copy.deepcopy(file_data["extra"])

                    ops.append({
                        "fullpath": fullpath,
                        "added_byte": patch,
                        "original_crc": current_crc,
                        "new_crc": current_crc,
                        "old_extra": old_extra,
                        "new_extra": new_extra
                    })

                except Exception as e:
                    print(f"处理失败: {fullpath}, {e}")
                    continue

            if ops:
                self.register_operation(action="scrub", data_list=ops, new_transaction=True)
            # 文件补丁处理完毕，调用 rename_files 进行重命名
            self.rename_request.emit()

        threading.Thread(target=worker, daemon=True).start()

    # ================= 洗码工具 =================

    def _build_patch_matrix(self):
        """生成 32×32 的差分矩阵（一次即可，后续复用）"""
        base_rows = []
        zero_crc = zlib.crc32(b"\x00" * 4) & 0xFFFFFFFF  # baseline for 4×0x00
        for j in range(32):
            unit = bytearray(4)
            unit[j // 8] = 1 << (j % 8)
            row_crc = zlib.crc32(unit) & 0xFFFFFFFF
            base_rows.append(row_crc ^ zero_crc)
        # 高 32 位附加单位矩阵，用于 Gauss 消元求逆
        rows = [(r) | ((1 << i) << 32) for i, r in enumerate(base_rows)]
        for col in range(32):
            # 选主元
            pivot = None
            for r in range(col, 32):
                if (rows[r] >> col) & 1:
                    pivot = r
                    break
            if pivot is None:
                raise RuntimeError("补丁矩阵不可逆——理论上不应发生！")
            # 行交换
            rows[col], rows[pivot] = rows[pivot], rows[col]
            pivot_row = rows[col]
            # 消元
            for r in range(32):
                if r != col and ((rows[r] >> col) & 1):
                    rows[r] ^= pivot_row
        # 拆出逆矩阵（低 32 位已化为单位矩阵）
        return [row >> 32 for row in rows]

    def fix_file_crc32(self, file_path: str, target_crc_hex: str,
                       current_crc: int | None = None) -> bool:
        """
        使文件实际 CRC32 与文件名中的目标 CRC32 一致。
        - 如果文件已匹配，直接返回 False；
        - 如果写入了补丁，返回 True，并在 self.scrubbed_files 里
          记录 {file_path: patch_bytes}，同时把 self.last_action 设为 'scrub'。
        """
        # ------------ 计算当前/目标 CRC32 ------------
        target_crc = int(target_crc_hex, 16) & 0xFFFFFFFF
        if current_crc is None:
            with open(file_path, "rb") as f:
                current_crc = zlib.crc32(f.read()) & 0xFFFFFFFF
        if current_crc == target_crc:
            return False  # 已匹配，无需补丁

        # ------------ 生成补丁并写入 ------------
        patch = self._compute_patch_bytes(current_crc, target_crc)
        with open(file_path, "ab") as f:
            f.write(patch)

        # ------------ 记录洗码信息 ------------
        if not hasattr(self, "scrubbed_files"):
            self.scrubbed_files = {}  # 首次使用时创建
        self.scrubbed_files[file_path] = patch  # 记下补丁

        return True

    def _compute_patch_bytes(self, current_crc: int, target_crc: int) -> bytes:
        """给定当前 CRC32 和目标 CRC32，返回 4 字节补丁。"""
        # baseline：在 current_crc 基础上追加 4×0x00 的 CRC 结果
        baseline_crc = zlib.crc32(b"\x00\x00\x00\x00", current_crc) & 0xFFFFFFFF
        diff = target_crc ^ baseline_crc  # 我们需要用补丁贡献出 diff

        # 将 diff 表达为 32 位向量
        bits_vector = 0
        for bit in range(32):
            if (diff >> bit) & 1:
                bits_vector ^= self._PATCH_INV_MATRIX[bit]

        # bits_vector 的 32 位即为补丁字节的比特表示
        patch = bytearray(4)
        for bit in range(32):
            if (bits_vector >> bit) & 1:
                patch[bit // 8] ^= 1 << (bit % 8)
        # 验证（调试期保留，发布可注释）
        # final_crc = zlib.crc32(patch, current_crc) & 0xFFFFFFFF
        # assert final_crc == target_crc, "补丁计算失败！"  # 理论上一定成立
        return bytes(patch)

    # ================= 回调 =================
    def on_add_files_end(self, tree_id=None, plugin_name=None):
        """
        主程序添加文件完成后调用
        扫描 tree_file，把未校验文件加入校验队列
        """

        with self.lock:
            for file_data in self.tree_file:

                fullpath = file_data["fullpath"]

                # ===== 确保 extra 存在 =====
                extra = file_data.setdefault("extra", {})

                # ===== 如果已经校验过（存在 calculated_hash），跳过 =====
                if extra.get("calculated_hash") is not None:
                    continue

                # 如果已经在队列中，跳过
                if fullpath in self.in_queue_set:
                    continue

                # 初始化 extra 字段（避免KeyError）
                if "extra" not in file_data:
                    file_data["extra"] = {}

                if "校验" not in file_data["extra"]:
                    file_data["extra"]["校验"] = ""
                if "预览文件名" not in file_data["extra"]:
                    file_data["extra"]["预览文件名"] = ""

                # 加入队列
                self.verify_queue.put(fullpath)
                self.in_queue_set.add(fullpath)

        # 启动线程（如果未启动）
        self.start_verify_thread()

    def start_verify_thread(self):

        if self.verify_thread and self.verify_thread.is_alive():
            return  # 已有线程在运行

        self.verify_thread = threading.Thread(
            target=self.verify_worker,
            daemon=True
        )
        self.verify_thread.start()


# ================= 外部入口 =================

def create_ui(app, main_frame, edit_frame, scale):
    global rename_app
    rename_app = RenameApp(app, main_frame, edit_frame, scale)

def on_add_files_end(tree_id, plugin_name=None):
    rename_app.on_add_files_end(tree_id, plugin_name)

def get_info():
    return {"icon":{"text":"🔢"}, "display_name":"哈希校验器"}