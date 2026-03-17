import multiprocessing
import threading
import traceback
import time
from PyQt5.QtCore import QObject
# from plugin_worker import plugin_thread_process_wrapper

def plugin_thread_process_wrapper(target, return_queue, *args, **kwargs):
    """
    顶层函数包装插件 target，Windows spawn 下必须是可 import 顶级函数
    """
    try:
        target(*args, **kwargs)
        return_queue.put("OK")
    except Exception as e:
        return_queue.put(f"[ERROR]{str(e)}")
        traceback.print_exc()

class thread_m(QObject):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.plugin_threads = {}  # { plugin_name: { thread_id: {"thread":..., "process":..., "stop_flag":...} } }
        self._thread_counter = 0

    # ============================================================
    # 线程管理：为每个插件维护线程、支持强制停止
    # ============================================================
    def start_plugin_thread(self, target, *args, plugin_name=None, **kwargs):
        """
        启动插件线程，线程内部再启动一个子进程，可被强制终止。
        target 可以是类方法或普通函数，Windows 下会自动通过顶层中转函数调用。
        """
        if plugin_name is None:
            plugin_name = self.main.current_plugin_name

        thread_id = self._thread_counter
        self._thread_counter += 1

        # 使用顶层中转函数避免 pickle 错误
        return_queue = multiprocessing.Queue()
        process = multiprocessing.Process(
            target=plugin_thread_process_wrapper,
            args=(target, return_queue, *args),
            kwargs=kwargs
        )

        def thread_wrapper():
            process.start()
            while process.is_alive():
                # 检测是否被要求停止
                stop_flag = self.plugin_threads.get(plugin_name, {}).get(thread_id, {}).get("stop_flag")
                if stop_flag and stop_flag.value:
                    print(f"[线程] 强制终止 plugin={plugin_name} tid={thread_id}")
                    process.terminate()
                    process.join()
                    return
                time.sleep(0.1)
            process.join()
            # 获取返回值（可选）
            try:
                result = return_queue.get_nowait()
                print(f"[线程] plugin={plugin_name} tid={thread_id} 返回值: {result}")
            except:
                pass

        stop_flag = multiprocessing.Value("b", False)  # bool类型共享内存
        t = threading.Thread(target=thread_wrapper, daemon=True, name=f"{plugin_name}-thread-{thread_id}")

        # 保存线程信息
        self.plugin_threads.setdefault(plugin_name, {})[thread_id] = {
            "thread": t,
            "process": process,
            "stop_flag": stop_flag
        }

        t.start()
        print(f"[线程启动] plugin={plugin_name} tid={thread_id}")
        return thread_id

    def stop_plugin_threads(self, plugin_name=None):
        """强制停止某插件所有线程"""
        if plugin_name is None:
            plugin_name = self.main.current_plugin_name

        if plugin_name not in self.plugin_threads:
            return

        threads = self.plugin_threads[plugin_name]
        print(f"[线程停止] 开始终止插件 {plugin_name} 的 {len(threads)} 个线程")

        for tid, item in threads.items():
            stop_flag = item["stop_flag"]
            process = item["process"]
            stop_flag.value = True  # 通知线程停止
            # 主线程可以直接杀掉子进程
            if process.is_alive():
                print(f"[线程停止] 强制终止子进程 plugin={plugin_name} tid={tid}")
                process.terminate()

        # 等待线程退出
        for tid, item in list(threads.items()):
            t = item["thread"]
            if t.is_alive():
                t.join(timeout=1.0)
            print(f"[线程结束] plugin={plugin_name} tid={tid}")

        del self.plugin_threads[plugin_name]

