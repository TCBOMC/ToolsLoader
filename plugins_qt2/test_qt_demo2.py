# demo_plugin.py

from PyQt5.QtWidgets import (
    QLabel, QPushButton, QLineEdit, QTextEdit, QListWidget,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QSlider, QComboBox, QCheckBox, QRadioButton,
    QCalendarWidget, QTabWidget, QWidget, QVBoxLayout, QGroupBox
)
from PyQt5.QtCore import Qt


def create_ui(main, left_frame, right_frame, scale):
    """
    Plugin UI entry point called by host program.

    :param main: the main window instance (has update_window_title, etc.)
    :param left_frame: widget for main plugin area
    :param right_frame: widget for plugin assistant / edit area
    :param scale: UI scale (float)
    """

    # ✅ 更新标题
    main.kit.ui.update_window_title("插件：PyQt组件测试 / Plugin UI test")

    # -------------------------
    # Left Frame Layout
    # -------------------------
    left_layout = QVBoxLayout()

    left_layout.addWidget(QLabel("⬇ 输入区域 / Input area:"))
    left_layout.addWidget(QLineEdit("输入框 / QLineEdit"))
    left_layout.addWidget(QTextEdit("多行文本框 / QTextEdit"))

    left_layout.addWidget(QLabel("📋 列表 / QListWidget"))
    list_widget = QListWidget()
    list_widget.addItems(["条目 A", "Item B", "項目 C"])
    left_layout.addWidget(list_widget)

    left_layout.addWidget(QLabel("🌳 树控件 / TreeWidget"))
    tree = QTreeWidget()
    tree.setHeaderLabels(["Name", "Value"])
    root = QTreeWidgetItem(["Root", "1"])
    root.addChild(QTreeWidgetItem(["Child", "2"]))
    tree.addTopLevelItem(root)
    left_layout.addWidget(tree)

    left_frame.setLayout(left_layout)

    # -------------------------
    # Right Frame Layout
    # -------------------------
    right_layout = QVBoxLayout()

    group = QGroupBox("⚙️ 控件测试 / Controls")
    group_layout = QVBoxLayout()
    group_layout.addWidget(QCheckBox("开启功能"))
    group_layout.addWidget(QRadioButton("模式 A"))
    group_layout.addWidget(QRadioButton("模式 B"))
    combo = QComboBox()
    combo.addItems(["Option 1", "Option 2", "Option 3"])
    group_layout.addWidget(combo)
    group.setLayout(group_layout)
    right_layout.addWidget(group)

    right_layout.addWidget(QLabel("📆 Calendar"))
    right_layout.addWidget(QCalendarWidget())

    right_layout.addWidget(QLabel("📊 进度 / Progress"))
    progress = QProgressBar()
    progress.setValue(50)
    right_layout.addWidget(progress)

    slider = QSlider(Qt.Horizontal)
    slider.setValue(50)
    slider.valueChanged.connect(progress.setValue)
    right_layout.addWidget(slider)

    right_layout.addWidget(QLabel("📑 表格 / Table"))
    table = QTableWidget(2, 2)
    table.setItem(0, 0, QTableWidgetItem("A1"))
    table.setItem(1, 1, QTableWidgetItem("B2"))
    right_layout.addWidget(table)

    btn = QPushButton("点击→修改标题")
    btn.clicked.connect(lambda: main.kit.ui.update_window_title("你点了按钮！"))
    right_layout.addWidget(btn)

    tabs = QTabWidget()
    tab1 = QWidget(); tab1.setLayout(QVBoxLayout())
    tab1.layout().addWidget(QLabel("Tab 内容 1"))
    tab2 = QWidget(); tab2.setLayout(QVBoxLayout())
    tab2.layout().addWidget(QLabel("Tab 内容 2"))
    tabs.addTab(tab1, "Tab A")
    tabs.addTab(tab2, "Tab B")
    right_layout.addWidget(tabs)

    right_frame.setLayout(right_layout)

    print("[demo_plugin] UI loaded successfully")
