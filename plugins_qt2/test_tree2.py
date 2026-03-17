from PyQt5.QtWidgets import (
    QLabel, QPushButton, QLineEdit, QTextEdit, QListWidget,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QSlider, QComboBox, QCheckBox, QRadioButton,
    QCalendarWidget, QTabWidget, QWidget, QVBoxLayout, QGroupBox
)
from PyQt5.QtCore import Qt

def create_ui(main, canvas_frame, button_frame, scale):
    """
    Plugin UI entry point called by host program.

    :param main: the main window instance (has update_window_title, etc.)
    :param canvas_frame: widget for main plugin area (top)
    :param button_frame: widget for plugin assistant / edit area (bottom)
    :param scale: UI scale (float)
    """
    # 更新窗口标题
    main.kit.ui.update_window_title("插件：PyQt组件测试 / Plugin UI test")

    # -------------------------
    # 上方按钮栏内容
    # -------------------------
    btn_layout = QVBoxLayout()
    btn_layout.addWidget(QPushButton("按钮 1"))
    btn_layout.addWidget(QPushButton("按钮 2"))
    button_frame.setLayout(btn_layout)

    # -------------------------
    # 在 canvas_frame 中创建横向三列区域
    # -------------------------
    splitter, subframes = main.kit.ui.create_split_frame(
        orient="horizontal", n=4, sashwidth=3, handle_color="#d0d0d0", bg="#f0f0f0", sashrelief="line"
    )

    # 手动将 splitter 放入 canvas_frame，并占满
    canvas_layout = QVBoxLayout()
    canvas_layout.setContentsMargins(0, 0, 0, 0)  # 去掉边距
    canvas_layout.addWidget(splitter)
    canvas_frame.setLayout(canvas_layout)

    left_frame, center_frame, right_framem, tree_frame= subframes

    # -------------------------
    # Left Column 内容
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

    extra_cols = [
        (None, 400, True)
    ]

    tree_container, tree, tree_file = main.kit.ui.create_tree_view(tree_index=1, extra_columns=extra_cols)

    tree_layout = QVBoxLayout()
    tree_layout.setContentsMargins(0, 0, 0, 0)  # 去掉边距
    tree_layout.addWidget(tree_container)
    tree_frame.setLayout(tree_layout)

    # -------------------------
    # Center Column 内容
    # -------------------------
    center_layout = QVBoxLayout()
    center_layout.addWidget(QLabel("📑 中间区域 / Center Area"))
    center_layout.addWidget(QTextEdit("这里可以放其他控件"))
    center_frame.setLayout(center_layout)

    # -------------------------
    # Right Column 内容
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

    right_framem.setLayout(right_layout)



    print("[demo_plugin] UI loaded successfully")
