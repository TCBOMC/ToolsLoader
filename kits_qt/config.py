import os
import json
import threading
import queue
from PyQt5.QtCore import QObject, pyqtSignal

# ----------------- SignalManager ----------------- #
class SignalManager(QObject):
    # 异步信号
    save_config_signal = pyqtSignal(str, dict)
    load_config_signal = pyqtSignal(str, object)  # callback
    save_config_all_signal = pyqtSignal(dict)
    load_config_all_signal = pyqtSignal(object)  # callback
    save_config_public_signal = pyqtSignal(dict)
    load_config_public_signal = pyqtSignal(object)  # callback
    save_config_main_signal = pyqtSignal(dict)
    load_config_main_signal = pyqtSignal(object)  # callback

    def __init__(self, data, config_file, parent=None):
        super().__init__(parent)
        self.data = data
        self.config_file = config_file
        self._write_queue = queue.Queue()
        self._write_thread = threading.Thread(target=self._write_worker, daemon=True)
        self._write_thread.start()

        # 绑定信号到槽
        self.save_config_signal.connect(self._save_plugin)
        self.load_config_signal.connect(self._load_plugin)
        self.save_config_all_signal.connect(self._save_all)
        self.load_config_all_signal.connect(self._load_all)
        self.save_config_public_signal.connect(self._save_public)
        self.load_config_public_signal.connect(self._load_public)
        self.save_config_main_signal.connect(self._save_main)
        self.load_config_main_signal.connect(self._load_main)

    # ---------- 写队列线程 ---------- #
    def _write_worker(self):
        while True:
            data_to_save = self._write_queue.get()
            if data_to_save is None:
                break  # 支持安全退出
            try:
                with open(self.config_file, "w", encoding="utf-8") as f:
                    json.dump(data_to_save, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"配置保存失败: {e}")
            self._write_queue.task_done()

    # ---------- 内部保存方法 ---------- #
    def _enqueue_save(self):
        # 拷贝一份数据，防止修改时冲突
        self._write_queue.put(json.loads(json.dumps(self.data)))

    # ---------- 异步槽函数 ---------- #
    def _save_plugin(self, plugin_name, content):
        self.data["config"]["Plugins"][plugin_name] = content
        self._enqueue_save()

    def _load_plugin(self, plugin_name, callback):
        result = self.data["config"]["Plugins"].get(plugin_name, None)
        if callback:
            callback(result)

    def _save_all(self, content):
        self.data["config"] = content
        self._enqueue_save()

    def _load_all(self, callback):
        if callback:
            callback(self.data["config"])

    def _save_public(self, content):
        self.data["config"]["Public"] = content
        self._enqueue_save()

    def _load_public(self, callback):
        if callback:
            callback(self.data["config"]["Public"])

    def _save_main(self, content):
        self.data["config"]["ToolsLoader"] = content
        self._enqueue_save()

    def _load_main(self, callback):
        if callback:
            callback(self.data["config"]["ToolsLoader"])

    # ---------- 同步接口 ---------- #
    def load_config_sync(self, plugin_name):
        return self.data["config"]["Plugins"].get(plugin_name, None)

    def load_config_all_sync(self):
        return self.data["config"]

    def load_config_public_sync(self):
        return self.data["config"]["Public"]

    def load_config_main_sync(self):
        return self.data["config"]["ToolsLoader"]

    # ---------- 安全关闭方法 ---------- #
    def shutdown(self):
        """程序关闭前调用，保存最新数据并安全退出写线程"""
        # 先将最新 data 写一次
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"配置保存失败: {e}")

        # 发送 None 到队列，让写线程退出
        self._write_queue.put(None)
        self._write_thread.join()


# ----------------- Config 类 ----------------- #
class config(QObject):
    def __init__(self, main=None):
        super().__init__()
        self.main = main
        self.config_file = os.path.join(os.getcwd(), "config.json")
        # 空配置
        self.default_data = {
            "config": {
                "ToolsLoader": {},
                "Public": {},
                "Plugins": {},
            }
        }

        # 初始化 self.data 并创建文件（如果不存在）
        if not os.path.exists(self.config_file):
            self.data = self.default_data.copy()
            self._save_file_initial()
        else:
            self.data = self._load_file()

        # 初始化 SignalManager
        self.signal_manager = SignalManager(self.data, self.config_file, self.main)

    # ---------- 文件操作 ---------- #
    def _save_file_initial(self):
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"初始配置保存失败: {e}")

    def _load_file(self):
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"加载配置失败: {e}")
            return self.default_data.copy()

    # ---------- 异步接口 ---------- #
    def save_plugin(self, content, plugin_name = None):
        if plugin_name is None:
            plugin_name = self.main.current_plugin_name
        self.signal_manager.save_config_signal.emit(plugin_name, content)

    def load_plugin_async(self, callback, plugin_name = None):
        if plugin_name is None:
            plugin_name = self.main.current_plugin_name
        self.signal_manager.load_config_signal.emit(plugin_name, callback)

    def save_all(self, content):
        self.signal_manager.save_config_all_signal.emit(content)

    def load_all_async(self, callback):
        self.signal_manager.load_config_all_signal.emit(callback)

    def save_public(self, content):
        self.signal_manager.save_config_public_signal.emit(content)

    def load_public_async(self, callback):
        self.signal_manager.load_config_public_signal.emit(callback)

    def save_main(self, content):
        self.signal_manager.save_config_main_signal.emit(content)

    def load_main_async(self, callback):
        self.signal_manager.load_config_main_signal.emit(callback)

    # ---------- 同步接口 ---------- #
    def load_plugin(self, plugin_name = None):
        if plugin_name is None:
            plugin_name = self.main.current_plugin_name
        return self.signal_manager.load_config_sync(plugin_name)

    def load_all(self):
        return self.signal_manager.load_config_all_sync()

    def load_public(self):
        return self.signal_manager.load_config_public_sync()

    def load_main(self):
        return self.signal_manager.load_config_main_sync()

    # ---------- 程序退出调用 ---------- #
    def shutdown(self):
        """程序退出前调用，确保保存最新数据"""
        self.signal_manager.shutdown()
