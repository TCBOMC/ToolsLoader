# 声明插件依赖（推荐方式）
PLUGIN_REQUIREMENTS = [
    "openai"
]

from openai import OpenAI
from PyQt5.QtWidgets import (
    QLabel, QPushButton, QLineEdit, QTextEdit, QListWidget,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QSlider, QComboBox, QCheckBox, QRadioButton,
    QCalendarWidget, QTabWidget, QWidget, QVBoxLayout, QGroupBox, QFrame
)
from PyQt5.QtCore import Qt


def run():
    client = OpenAI(
        api_key="sk-oIsquXMa0kWdNMUiVpdAYHctXAr3NKyrpN2QF35vfwKSRfJt",
        base_url="https://api.hunyuan.cloud.tencent.com/v1"
    )

    resp = client.chat.completions.create(
        model="hunyuan-large",
        messages=[
            {"role": "user", "content": "你好，请用一句话介绍你自己"}
        ]
    )

    print("AI回复：")
    print(resp.choices[0].message.content)
    return resp.choices[0].message.content

def create_ui(app, main_frame, edit_frame, scale):
    main_layout = QVBoxLayout()
    main_layout.setContentsMargins(10, 0, 10, 10)  # 去掉边距
    message_box = QTextEdit("")
    main_layout.addWidget(message_box)
    main_frame.setLayout(main_layout)
    message = run()
    message_box.setText(message)


