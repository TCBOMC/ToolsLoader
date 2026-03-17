import sys
import os
import subprocess
import zipfile
import re
import time
import shutil

from PyQt5 import QtCore
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QTextEdit
from PyQt5.QtCore import QThread, pyqtSignal

PIP_MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://pypi.org/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.mirrors.ustc.edu.cn/simple",
]


CREATE_NO_WINDOW = 0x08000000

# =======================
# DPI 设置（尽可能在 Windows 上启用 per-monitor DPI awareness）
# =======================
# 启用 Qt 高 DPI 模式（推荐）
QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)


class InstallWindow(QWidget):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("依赖安装")
        self.resize(250, 150)

        layout = QVBoxLayout()

        self.label = QLabel("准备安装依赖...")
        layout.addWidget(self.label)

        self.text = QTextEdit()
        self.text.setReadOnly(True)

        font = self.text.font()
        font.setPointSizeF(font.pointSizeF() * 0.75)  # 按比例缩小
        self.text.setFont(font)

        layout.addWidget(self.text)

        self.setLayout(layout)

    def set_dep(self, name):
        print(f"安装主程序依赖中：{name}")
        self.label.setText(f"安装主程序依赖中：{name}")

    def append(self, text):
        self.text.append(text)


class PipInstallThread(QThread):

    log_signal = pyqtSignal(str)

    def __init__(self, python_exe, package, tools_libs):
        super().__init__()
        self.python_exe = python_exe
        self.package = package
        self.tools_libs = tools_libs
        self.success = False

    def run(self):

        for mirror in PIP_MIRRORS:

            cmd = [
                self.python_exe,
                "-m",
                "pip",
                "install",
                self.package,
                "-i", mirror,
                "--target", self.tools_libs
            ]

            print(f"\n使用源: {mirror}\n")
            self.log_signal.emit(f"\n使用源: {mirror}\n")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=CREATE_NO_WINDOW
            )

            for line in process.stdout:
                self.log_signal.emit(line.rstrip())

            process.wait()

            if process.returncode == 0:
                self.success = True
                return

            print(f"源 {mirror} 安装失败")
            self.log_signal.emit(f"源 {mirror} 安装失败")


def parse_requirements(script_path):
    deps = []
    in_block = False

    with open(script_path, "r", encoding="utf-8") as f:
        for line in f:

            stripped = line.strip()

            if stripped in ["# requirements", "# requirements start", "# deps"]:
                in_block = True
                continue

            if stripped in ["# requirements_end", "# requirements end", "# end"]:
                break

            if not in_block:
                continue

            if stripped == "" or not stripped.startswith("#"):
                break

            dep = stripped[1:].strip()
            if dep:
                deps.append(dep)

    return deps


def find_main_script(base_dir):

    main_script_dir = os.path.join(base_dir, "main_script")

    if os.path.exists(main_script_dir):
        for fname in os.listdir(main_script_dir):
            if fname.endswith(".py"):
                return os.path.join(main_script_dir, fname)

    for fname in os.listdir(base_dir):
        if fname.endswith(".py"):
            return os.path.join(base_dir, fname)

    return None


def install_package_gui(window, python_exe, package, tools_libs):

    window.set_dep(package)

    thread = PipInstallThread(python_exe, package, tools_libs)

    thread.log_signal.connect(window.append)

    thread.start()

    while not thread.wait(50):
        QApplication.processEvents()

    if not thread.success:
        print(f"所有 pip 源安装失败: {package}")
        raise RuntimeError(f"所有 pip 源安装失败: {package}")

def copy_resources(meipass_dir, target_dir):
    """
    将 _MEIPASS/resources 下的所有文件复制到 target_dir，保持目录结构，不覆盖已存在的文件
    """
    resources_dir = os.path.join(meipass_dir, "resources")
    if not os.path.exists(resources_dir):
        return

    for root, dirs, files in os.walk(resources_dir):
        # 相对路径，从 resources 开始
        rel_path = os.path.relpath(root, resources_dir)
        dest_root = os.path.join(target_dir, rel_path) if rel_path != "." else target_dir
        os.makedirs(dest_root, exist_ok=True)

        for f in files:
            src_file = os.path.join(root, f)
            dst_file = os.path.join(dest_root, f)
            if not os.path.exists(dst_file):
                shutil.copy2(src_file, dst_file)


def run_main_with_deps(window, python_exe, main_script, exe_dir, tools_libs):
    last_missing = None
    main_started = False
    start_time = time.time()

    requirements_checked = False
    declared_deps = []

    meipass_dir = getattr(sys, "_MEIPASS", exe_dir)

    while True:

        print(f"尝试启动主程序： {main_script}")
        env = os.environ.copy()
        env["PYTHONPATH"] = tools_libs + os.pathsep + env.get("PYTHONPATH", "")
        env["_MEIPASS"] = meipass_dir
        env["PYINSTALLER_FROZEN"] = "True"

        setup_code = f"""
import sys, os, runpy
sys._MEIPASS = r'{meipass_dir}'
sys.frozen = True
runpy.run_path(r'{main_script}', run_name="__main__")
"""

        process = subprocess.Popen(
            [python_exe, "-u", "-X", "utf8", "-c", setup_code],
            cwd=exe_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW
        )

        output_lines = []

        while True:
            line = process.stdout.readline()

            if line:
                line = line.rstrip()
                print(line)
                output_lines.append(line)

            if line == "" and process.poll() is not None:
                break

            if window:
                QApplication.processEvents()

                if not main_started:
                    main_started = True
                    start_time = time.time()

                if main_started and time.time() - start_time > 2:
                    window.close()

            time.sleep(0.02)

        # 读剩余buffer
        rest = process.stdout.read()
        if rest:
            print(rest)
            output_lines.append(rest)

        process.wait()

        full_output = "\n".join(output_lines)

        if process.returncode == 0:
            return

        match = re.search(
            r"ModuleNotFoundError: No module named '([a-zA-Z0-9_]+)'",
            full_output
        )

        if match:
            missing = match.group(1)

            # =========================
            # 第一次缺依赖 -> 安装声明依赖
            # =========================
            if not requirements_checked:
                declared_deps = parse_requirements(main_script)
                requirements_checked = True

                if declared_deps:
                    if not window:
                        window = InstallWindow()
                        window.show()

                    for dep in declared_deps:
                        install_package_gui(window, python_exe, dep, tools_libs)

                    # 安装完 requirements 后重新运行
                    continue

            # =========================
            # requirements 已处理 -> 按缺失模块安装
            # =========================
            if missing == last_missing:
                raise RuntimeError(f"依赖 {missing} 安装失败")

            if not window:
                window = InstallWindow()
                window.show()

            install_package_gui(window, python_exe, missing, tools_libs)
            last_missing = missing

        else:
            raise RuntimeError(full_output)


def main():

    exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(__file__)
    meipass_dir = getattr(sys, "_MEIPASS", exe_dir)

    # 复制 resources 文件夹内容到 exe 目录
    copy_resources(meipass_dir, exe_dir)

    tools_libs = os.path.join(exe_dir, "tools_libs")
    os.makedirs(tools_libs, exist_ok=True)

    cache_dir = os.path.join(exe_dir, "python_embedded_cache")
    python_exe = os.path.join(cache_dir, "python.exe")

    zip_path = os.path.join(meipass_dir, "python312.zip")

    if not os.path.exists(python_exe):

        os.makedirs(cache_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(cache_dir)

    main_script = find_main_script(meipass_dir)

    if not main_script:
        return

    app = QApplication(sys.argv)

    window = None

    try:

        run_main_with_deps(None, python_exe, main_script, exe_dir, tools_libs)

    except Exception as e:

        if window:
            window.append(str(e))
            window.append("程序出错")
        else:
            print(f"程序出错: {e}")

        input("程序出错，按回车退出...")


if __name__ == "__main__":
    main()

# 打包：pyinstaller --noconfirm --onefile --windowed --name=newUI -i icon.ico luncher9.py --add-data "icon.ico;." --add-data "python312.zip;." --add-data "uiqt0_2_3_3.py;main_script"--add-data "fonts;fonts" --add-data "resources;resources"