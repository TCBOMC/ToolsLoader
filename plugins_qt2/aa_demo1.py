import random
import string
import threading
import time
from PyQt5.QtWidgets import QApplication, QMainWindow, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from PyQt5.QtCore import Qt, QTimer

main = None  # 全局变量
_timer_running = False  # 定时器运行标志
_thread = None  # 子线程对象


def on_reload():
    """插件重载时的清理函数，主程序会自动调用"""
    global main, _timer_running, _thread
    print("=" * 50)
    print("插件正在重载，清理资源...")

    # 停止定时器和线程
    _timer_running = False

    # 等待线程结束
    if _thread and _thread.is_alive():
        _thread.join(timeout=1.0)

    # 清除全局引用
    main = None
    _thread = None

    print("资源清理完成")
    print("=" * 50)


def create_ui(external_main, canvas_frame, button_frame, scale):
    global main
    main = external_main
    # 更新窗口标题
    main.kit.ui.update_window_title("插件：PyQt组件测试 / Plugin UI test")

    # 可以尝试打印当前样式
    # print("当前应用风格:", app.style().objectName())

    # -------------------------
    # 在 canvas_frame 中创建横向三列区域
    # -------------------------
    ratios = [
        (5, True),
        (1, False),
        (1, True),
        (3, False),
    ]
    splitter, subframes = main.kit.ui.create_split_frame(
        orient="horizontal", n=4, sashwidth=3, handle_color="#d0d0d0", bg="#f0f0f0", sashrelief="line", ratios=ratios
    )

    # 手动将 splitter 放入 canvas_frame，并占满
    canvas_layout = QVBoxLayout()
    canvas_layout.setContentsMargins(0, 0, 0, 0)  # 去掉边距
    canvas_layout.addWidget(splitter)
    canvas_frame.setLayout(canvas_layout)

    left_frame, center_frame, right_framem, tree_frame = subframes

    layout = QVBoxLayout()

    tree = QTreeWidget()
    tree.setColumnCount(2)
    tree.setHeaderLabels(["Name", "Value"])
    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    tree.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    # 填充一些测试数据
    for i in range(50):
        item = QTreeWidgetItem([f"Item {i}", str(i)])
        tree.addTopLevelItem(item)

    """layout.addWidget(tree)
    left_frame.setLayout(layout)"""

    extra_cols1 = [
        (None, 400, True),
        ("宽度", 50, False),
        ("高度", 50, False, 2, "combobox(选项1|选项2|选项3)", [
            lambda idx, row, widget, col: print(f"按钮点了 row={row}, col={col},选中索引:", idx)]),
        ("帧率", 50, False)
    ]
    tree_c1, tree1, tree_list1 = main.kit.ui.create_tree_view(left_frame, tree_index=3, extra_columns=extra_cols1,
                                                              mode="表格", show_checkbox=False)
    tree_c1.show()

    test_s = "测试文本12345"

    extra_cols = [
        (None, 400, True),
        ("宽度", 50, False, -1, "button(你好)", [
            lambda row: print(f"你好 被点击了{row}"),
            main.kit.mathkit.print_main_title]),
        ("高度", 50, False, 2, "combobox(选项1|选项2|选项3)", [
            lambda idx, row, widget, col, ts=test_s: print(
                f"测试文本：{ts},按钮点了 row={row}, col={col},按钮为：{widget}，选中索引:", idx)]),
        ("帧率", 50, False, 4, "button(打印)", [
            main.kit.mathkit.print_main_title,
            lambda widget: print(f"第二个回调执行:{widget}")])
    ]
    tree_c, tree, tree_list = main.kit.ui.create_tree_view(tree_frame, tree_index=1, extra_columns=extra_cols,
                                                           mode="表格")
    tree_c.show()

    extra_cols1 = [
        (None, 400, True),
        ("宽度", 50, False, -4),
        ("高度", 50, False, -1, "combobox(选项1|选项2|选项3)", [
            lambda idx, row, widget, col: print(f"按钮点了 row={row}, col={col},选中索引:", idx)]),
        ("帧率", 50, False)
    ]
    tree_c1, tree1, tree_list1 = main.kit.ui.create_tree_view(center_frame, tree_index=2, extra_columns=extra_cols1,
                                                              mode="文件")
    tree_c1.show()
    test_add_random_items(2, 10)

    test_layout = QVBoxLayout()
    btn_test = main.kit.ui.create_button("测试标题", lambda: print_win(main))

    test_layout.addWidget(btn_test)

    right_framem.setLayout(test_layout)
    widgets = main.kit.ui.get_trees_widgets(1, 0, "宽度")
    print(f"widgets:{widgets}")
    widgets.clicked.connect(lambda: print_win(main))

    try:
        test_w = main.kit.ui.get_trees_widgets(2, 1, 1)
        for i in range(test_w.count()):
            print(test_w.itemText(i))

    except Exception as e:
        print("错误:\n", e)

    # 启动子线程，每隔2秒调用一次 toggle_tree_checkbox(2)
    start_thread_timer()


def print_win(main):
    main.kit.mathkit.print_main_title()
    # widgets = main.kit.ui.get_trees_widgets(1, 0, "宽度")


def test_add_random_items(tree_id=1, count=5):
    """
    随机生成一些行数据，传入 tree_id 的表里
    tree_id: 要插入的树 ID
    count: 随机生成的行数
    """
    # 随机选择模式
    tree, mode = main.kit.ui.get_tree(tree_id, get_mode=True)

    for _ in range(count):
        # 随机复选框状态
        checked = random.choice([True, False])

        # 随机文件名
        fname = ''.join(random.choices(string.ascii_letters + string.digits, k=6)) + ".png"

        # 随机宽度、高度
        width = random.randint(100, 1920)
        height = random.randint(100, 1080)
        frame = random.randint(12,240)

        content = {
            "checked": checked,  # 复选框列
            None: fname,  # 文件名列
            "宽度": width,  # extra 列
            "高度": height,  # extra 列
            "帧率": frame,
        }
        """content = {
            0: checked,  # 复选框列
            1: fname,  # 文件名列
            2: width,  # extra 列
            3: height,  # extra 列
            4: width,
        }"""

        # 文件模式需要 fullpath
        if mode == "文件":
            # 模拟 fullpath
            content["fullpath"] = f"C:/temp/{fname}"

        main.kit.ui.add_item(tree_id, content)

    print(f"已随机添加 {count} 行数据到 tree_id={tree_id} ({mode} 模式)")


def thread_worker():
    """子线程工作函数，每隔2秒调用一次UI函数"""
    global _timer_running, main

    thread_id = threading.current_thread().ident
    print(f"子线程已启动，线程ID: {thread_id}")

    while _timer_running:
        try:
            if main is not None:
                # 在子线程中调用 toggle_tree_checkbox
                #print(f"[线程:{thread_id}] 调用 toggle_tree_checkbox(2)")
                main.kit.ui.toggle_tree_checkbox(2)

                # 在子线程中调用 get_tree_checkbox_visible
                is_visible = main.kit.ui.get_tree_checkbox_visible(2)
                #print(f"[线程:{thread_id}] 复选框显示状态: {is_visible}")

        except Exception as e:
            print(f"[线程:{thread_id}] 调用UI函数时出错: {e}")

        # 休眠2秒
        time.sleep(2)

    print(f"子线程结束，线程ID: {thread_id}")


def start_thread_timer():
    """启动子线程定时器"""
    global _timer_running, _thread

    # 停止之前的线程
    stop_thread_timer()

    # 启动新线程
    _timer_running = True
    _thread = threading.Thread(target=thread_worker, daemon=True)
    _thread.start()
    print("子线程定时器已启动，每隔2秒在子线程中调用 toggle_tree_checkbox(2)")


def stop_thread_timer():
    """停止子线程定时器"""
    global _timer_running, _thread

    _timer_running = False

    if _thread and _thread.is_alive():
        _thread.join(timeout=1.0)

    _thread = None
    print("子线程定时器已停止")
