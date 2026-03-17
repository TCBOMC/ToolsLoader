class mathkit:
    def __init__(self, main):
        self.main = main
        self.num_a = 123

    def print_main_title(self):
        print("主窗口标题：", self.main.windowTitle())
