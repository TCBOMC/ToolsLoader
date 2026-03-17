import os
import shutil
import threading
from PyQt5.QtWidgets import QWidget, QSplitter, QSizePolicy, QPushButton, QHBoxLayout, QVBoxLayout, QFileDialog, QMessageBox
from PyQt5.QtCore import Qt, QTimer, QEvent, QObject, QThread, pyqtSignal

class ResizeWatcher(QObject):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize:
            self.callback()
        return False

class FileManagerApp(QObject):
    color_signal = pyqtSignal(str, str)
    restore_buttons_signal = pyqtSignal()

    def __init__(self, root, main_frame, edit_frame, scale):
        super().__init__()  # 你在代码中缺失了
        self.color_signal.connect(lambda fp, st: self.app.update_tree_item_color(2, fp, st))
        self.restore_buttons_signal.connect(self.restore_buttons_state)
        self.root = main_frame
        self.app = root
        self.scale = scale
        self.left_items = {}
        self.right_items = {}
        self.left_selected_item = None
        self.right_selected_item = None
        self.setup_ui()
        self._original_states = {}
        # 安装事件过滤器来监听 resize 事件
        self.resize_watcher = ResizeWatcher(self.update_exec_button_position)
        self.root.installEventFilter(self.resize_watcher)

    def setup_ui(self):
        # 顶层主布局
        self.main_layout = QVBoxLayout(self.root)
        self.main_layout.setContentsMargins(10, 0, 10, 10)
        self.parameter_width = int(50 * self.scale)

        # ---------- 按钮栏（高度固定，不会占满窗口） ----------
        self.button_frame = QWidget()
        # 固定高度策略，避免被拉伸
        btn_frame_height = int(round(20 * self.scale))  # 根据缩放设置高度（可调整）
        self.button_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.button_frame.setFixedHeight(btn_frame_height)

        self.button_layout = QHBoxLayout(self.button_frame)
        self.button_layout.setContentsMargins(0, 0, 0, 0)
        self.button_layout.setSpacing(int(round(10 * self.scale)))

        # 注意：我们不把按钮放在 button_frame 的 layout 中进行最终定位（为了能随 sash 移动）
        # 仍在此处创建以便风格继承，但会把父控件改为 self.root（方便绝对移动）
        self.exec_btn = QPushButton("提取文件")
        self.exec_btn.clicked.connect(self.execute_copy)
        # 设置一个建议高度（保证与 button_frame 高度匹配）
        self.exec_btn.setFixedWidth(int(round(60 * self.scale)))
        self.exec_btn.setFixedHeight(int(round(20 * self.scale)))
        exec_font = self.exec_btn.font()
        exec_font.setPointSizeF(self.app.font_size * self.scale)
        self.exec_btn.setFont(exec_font)
        # 先在 layout 放一个占位（可以让 UI 在无移动前看到按钮位于中间）
        # 但我们将按钮 parent 改为 root 并在后面移动它到正确位置
        placeholder = QWidget()
        placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.button_layout.addWidget(placeholder)

        self.main_layout.addWidget(self.button_frame)

        # ---------- 分割面板（你的 create_split_frame 返回 QSplitter） ----------
        # 这里调用你已有的 create_split_frame（PyQt 版本）
        self.paned, main_frames = self.app.kit.ui.create_split_frame(self.root, orient="horizontal", n=2, sashwidth=5, sashrelief="line")
        left_frame, right_frame = main_frames
        self.left_frame, self.right_frame = left_frame, right_frame
        self.main_layout.addWidget(self.paned)

        # 把 exec_btn 的父控件改为 self.root（主窗口容器），这样可以 absolute move
        # 注意：如果 self.root 是 QMainWindow 的 centralWidget，确保坐标系正确（这里假设 root 是 QWidget）
        self.exec_btn.setParent(self.root)
        self.exec_btn.show()
        self.exec_btn.raise_()

        # 连接 splitterMoved 信号，使按钮跟随 handle 水平移动
        # splitterMoved(pos, index) -> pos 是 handle 的像素位置（相对于左边缘）
        try:
            self.paned.splitterMoved.connect(self.on_splitter_moved)
        except Exception:
            # 防御：若 create_split_frame 返回的对象行为不同，可改为 QTimer 定时更新
            pass

        # 初始化位置（等布局稳定后调用）
        QTimer.singleShot(100, self.update_exec_button_position)

        # 左右 TreeView
        self.setup_treeview(left_frame, "left")
        self.setup_treeview(right_frame, "right")

    def eventFilter(self, obj, event):
        # 捕获 root 的 resize 事件
        if obj is self.root and event.type() == QEvent.Resize:
            # 当窗口尺寸变化时，重新调整按钮位置
            self.update_exec_button_position()
        return False

    def on_splitter_moved(self, pos, index):
        """
        Qt 信号回调：splitter 被拖动时调用
        pos: handle 相对于 splitter 左侧的像素位置
        index: handle 索引
        """
        # 直接使用 pos（pos 是 handle 的 x 坐标）
        self.update_exec_button_position(handle_x=pos)

    def update_exec_button_position(self, handle_x=None):
        """
        将按钮移动到与 splitter handle 中心对齐的位置。
        handle_x: 如果为空，会尝试用 sizes() 计算左面板宽度来估算位置。
        """
        btn_h = self.exec_btn.height()
        frame_h = self.button_frame.height()
        frame_y = self.button_frame.y()

        splitter_x = self.paned.x()

        # 计算水平位置（相对于 root）
        if handle_x is None:
            try:
                sizes = self.paned.sizes()  # [left_width, right_width]
                left_width = sizes[0] if sizes else 0
                x = splitter_x + left_width
            except Exception:
                x = int(self.root.width() / 2)
        else:
            x = splitter_x + handle_x

        btn_w = self.exec_btn.width()
        target_x = int(round(x - btn_w / 2))
        target_y = int(round(frame_y + (frame_h - btn_h) / 2))

        max_x = max(0, self.root.width() - btn_w)
        target_x = max(0, min(target_x, max_x))

        self.exec_btn.move(target_x, target_y)
        self.exec_btn.update()

    def setup_treeview(self, frame, side):
        extra_cols = [
            (None, 400, True),
            ("大小", self.parameter_width, False)
        ]
        idx = 1 if side == "left" else 2
        container, tree, files = self.app.kit.ui.create_tree_view(frame, tree_index=idx, extra_columns=extra_cols)

        #container.setParent(frame)  # 对应原 pack
        container.show()

        if side == "left":
            self.left_tree = tree
            self.left_files = files
        else:
            self.right_tree = tree
            self.right_files = files

    def set_buttons_state(self, state: str):
        """统一设置所有按钮的状态（Qt 版）"""
        enabled = (state != 'disabled')

        self.exec_btn.setEnabled(enabled)

    def save_and_disable_buttons(self):
        """保存控件原始状态，并将它们全部禁用（Qt 版）"""
        self._original_states = {
            'exec_btn': self.exec_btn.isEnabled()
        }
        self.set_buttons_state('disabled')

    def restore_buttons_state(self):
        """将控件状态还原为保存的原始状态（Qt 版）"""
        #print(f"恢复按钮状态 exec_btn:{self._original_states.get('exec_btn')}")
        if not hasattr(self, "_original_states") or not self._original_states:
            print("没有原状态")
            return  # 防止未保存直接还原

        self.exec_btn.setEnabled(self._original_states.get('exec_btn', True))

    def update_tree_files(self, tree_id):
        self.app.kit.ui.refresh_tree(tree_id)

    def update_right_from_left(self):
        """
        根据左侧选中的文件或目录生成右侧文件列表。
        - checked=False 的条目直接跳过
        - 文件夹会遍历其中所有文件后添加
        - 文件直接添加
        """
        print(f"左侧文件列表：{self.left_files}")
        self.right_files.clear()
        for f in self.left_files:
            # 跳过未勾选的条目
            if not f.get("checked", False):
                continue

            path = f["fullpath"]
            if os.path.isdir(path):
                # 遍历文件夹所有文件
                for root_dir, _, files in os.walk(path):
                    for file_name in files:
                        full_path = os.path.join(root_dir, file_name)
                        if not any(rf["fullpath"] == full_path for rf in self.right_files):
                            size_str = get_human_readable_size(os.path.getsize(full_path))
                            self.right_files.append({
                                "fullpath": full_path,
                                "filename": file_name,
                                "checked": True,
                                "extra": {"大小": size_str}
                            })
            elif os.path.isfile(path):
                # 直接添加文件
                if not any(rf["fullpath"] == path for rf in self.right_files):
                    size_str = get_human_readable_size(os.path.getsize(path))
                    self.right_files.append({
                        "fullpath": path,
                        "filename": os.path.basename(path),
                        "checked": True,
                        "extra": {"大小": size_str}
                    })

        # 刷新右侧显示和标题
        self.update_title()
        print(f"右侧文件列表：{self.right_files}")

    def get_checked_paths(self, side):
        """
        获取左侧或右侧已选中的文件路径
        """
        files_list = self.left_files if side == "left" else self.right_files
        return [f["fullpath"] for f in files_list if f.get("checked", False)]

    def execute_copy(self):
        """
        将右侧已选中文件复制到目标文件夹，并在 TreeView 中染色显示状态
        """
        #print("主线程?", QThread.currentThread() == self.thread())
        self.save_and_disable_buttons()

        # ---- 1. 文件选择必须在主线程 ----
        target_dir = QFileDialog.getExistingDirectory(None, "选择保存位置")
        if not target_dir:
            self.restore_buttons_state()
            return

        # ---- 2. 获取已选文件 ----
        files_to_copy = [f for f in self.right_files if f.get("checked", False)]
        if not files_to_copy:
            self.restore_buttons_state()
            return

        # ---- 4. 任务开始前打印 load_all ----
        #initial_config = self.app.kit.config.load_all()
        #print("任务开始前配置:", initial_config)
        #fmc = self.app.kit.config.load_plugin("File_Extracter_qt2")
        #print(f"插件配置:{fmc}")
        #self.app.kit.config.save_plugin({"test111": "test"}, "File_Extracter_qt2")

        # ---- 3. 清空所有行颜色 ----
        self.app.kit.ui.clear_tree_item_color(2)

        # ---- 5. 初始化文件状态字典 ----
        files_status = {}

        # ---- 6. 后台复制任务 ----
        def task():
            #fmc = self.app.kit.config.load_plugin("File_Extracter_qt2")
            #print(f"插件配置:{fmc}")
            success_count = 0
            for f in files_to_copy:
                fullpath = f["fullpath"]

                # 标记处理中的灰色
                self.app.kit.ui.update_tree_item_color(2, fullpath, "processing")
                my_name = self.app.get_current_plugin_name()
                #print(f"当前插件：{my_name}")

                try:
                    dest = os.path.join(target_dir, os.path.basename(fullpath))
                    shutil.copy2(fullpath, dest)
                    success_count += 1
                    files_status[os.path.basename(fullpath)] = "success"
                    self.app.kit.ui.update_tree_item_color(2, fullpath, "success")
                except Exception as e:
                    files_status[os.path.basename(fullpath)] = "fail"
                    self.app.kit.ui.update_tree_item_color(2, fullpath, "fail")

                # 每完成一个文件就保存一次
                #self.app.kit.config.save_plugin(files_status, "File_Extracter_qt2")

            # 完成提示
            self.app.kit.ui.show_message_box("完成", f"已复制 {success_count} 个文件到\n{target_dir}", "info")

            # ---- 任务结束后再次打印 load_all ----
            #final_config = self.app.kit.config.load_all()
            #print("任务结束后配置:", final_config)

            #QTimer.singleShot(0, self.restore_buttons_state)
            self.restore_buttons_signal.emit()

        # ---- 7. 启动后台线程 ----
        threading.Thread(target=task, daemon=True).start()

    def update_title(self):
        # 使用 self.right_files 来统计总文件数和已选中文件数
        total = len(self.right_files)
        selected = sum(1 for f in self.right_files if f.get("checked", False))
        #print(selected, total)
        self.app.kit.ui.update_window_title(extra_text=f"- 文件总数: {total}，已选中: {selected}")


def create_ui(app, main_frame, edit_frame, scale):
    global file_manager
    #root = TkinterDnD.Tk()
    file_manager = FileManagerApp(app, main_frame, edit_frame, scale)

def get_human_readable_size(size_bytes):
    """将字节大小转换为带单位的可读形式"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}PB"

def on_add(tree_id, file_path, name):
    """
    处理单个文件或文件夹：
    - 如果是文件，直接添加到 self.right_tree 的 files 列表
    - 如果是文件夹，遍历所有文件并添加
    返回一个字典 {"大小": size}，size 为 file_path 或文件夹总大小（带单位）
    """
    total_size = 0  # 用于累加文件夹总大小

    def add_file_to_files_list(file_fullpath):
        nonlocal total_size
        size_bytes = os.path.getsize(file_fullpath)
        total_size += size_bytes
        size_str = get_human_readable_size(size_bytes)
        extra_dict = {"大小": size_str}

        file_manager.right_files.append({
            "fullpath": file_fullpath,
            "filename": os.path.basename(file_fullpath),
            "checked": True,
            "extra": extra_dict
        })

    if os.path.isfile(file_path) and (tree_id == 1):
        total_size += os.path.getsize(file_path)
    elif os.path.isdir(file_path) and (tree_id == 1):
        for root_dir, _, files in os.walk(file_path):
            for f in files:
                full_path = os.path.join(root_dir, f)
                total_size += os.path.getsize(full_path)
    elif os.path.isfile(file_path) and (tree_id == 2):
        size_bytes = os.path.getsize(file_path)
        total_size += size_bytes
    elif os.path.isdir(file_path) and (tree_id == 2):
        for root, _, files in os.walk(file_path):
            for f in files:
                full_path = os.path.join(root, f)
                size_bytes = os.path.getsize(full_path)
                total_size += size_bytes

    file_manager.update_title()
    return {"大小": get_human_readable_size(total_size)}

def on_add_files_end(tree_id):
    #print("end")
    if tree_id == 1:
        file_manager.update_right_from_left()
        # 调用刷新方法刷新 tree view
        file_manager.update_tree_files(2)

    file_manager.restore_buttons_state()

def on_check(tree_id):
    if tree_id == 1:
        print("click")
        file_manager.update_right_from_left()
    elif tree_id == 2:
        file_manager.update_title()
    file_manager.update_tree_files(2)

def on_toggle_all_selection(tree_id):
    if tree_id == 1:
        file_manager.update_right_from_left()
    elif tree_id == 2:
        file_manager.update_title()
    file_manager.update_tree_files(2)

def on_clear_all(tree_id):
    print("clear")
    if tree_id == 1:
        file_manager.update_right_from_left()
    elif tree_id == 2:
        file_manager.update_title()
    file_manager.update_tree_files(2)

def on_delete_selected(tree_id, content):
    print("delete")
    if tree_id == 1:
        file_manager.update_right_from_left()
    elif tree_id == 2:
        file_manager.update_title()
    file_manager.update_tree_files(2)

def on_cut_selected(tree_id, content=None):
    if tree_id == 1:
        file_manager.update_right_from_left()
    elif tree_id == 2:
        file_manager.update_title()
    file_manager.update_tree_files(2)

def on_paste_items(tree_id, content):
    if tree_id == 1:
        file_manager.update_right_from_left()
    elif tree_id == 2:
        file_manager.update_title()
    file_manager.update_tree_files(2)

def on_add_files_start(tree_id):
    #print("start")
    file_manager.save_and_disable_buttons()

def get_info():
    return {"icon":{"text":"📂"}, "display_name":"文件提取器"}

"""if __name__ == "__main__":
    root = TkinterDnD.Tk()
    root.geometry("600x400")
    app = FileManagerApp(root, root, None, 1.5)
    root.mainloop()"""

# 打包指令：pyinstaller -F -w --collect-data=tkinterdnd2 .\.venv\Scripts\file0.0.8.py