import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from PyQt5.QtCore import Qt

def create_ui(main, canvas_frame, button_frame, scale):

    # 可以尝试打印当前样式
    #print("当前应用风格:", app.style().objectName())

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

    layout.addWidget(tree)
    canvas_frame.setLayout(layout)


