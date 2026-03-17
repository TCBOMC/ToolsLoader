import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from PyQt5.QtCore import Qt

def create_ui(main, canvas_frame, button_frame, scale):

    # 可以尝试打印当前样式
    #print("当前应用风格:", app.style().objectName())

    layout = QVBoxLayout()

    extra_cols = [
        ("当前文件名", 400, True),
        ("校验", 60, False, 0),
        ("预览文件名", 400, True)
    ]

    tree_container, tree, tree_file = main.kit.ui.create_tree_view(tree_index=1, extra_columns=extra_cols, show_checkbox=False)

    tree_layout = QVBoxLayout()
    tree_layout.setContentsMargins(0, 0, 0, 0)  # 去掉边距
    tree_layout.addWidget(tree_container)
    canvas_frame.setLayout(tree_layout)


