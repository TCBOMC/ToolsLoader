# ToolsLoader
python工具脚本加载器及工具集

<img width="1364" height="887" alt="屏幕截图 2026-03-17 211610" src="https://github.com/user-attachments/assets/a25999ca-fc11-4420-8979-6ca9ed009454" />

---
# 目前包含的工具
- 剪贴板监听
- 批量文件提取
- 文件hash校验与重命名
- 内封字幕提取
- 字幕文本替换
- 批量字幕调轴

---
# 打包与执行
- 直接运行根目录下名称以uiqt开头的脚本以启动主程序
- 打包时使用以下指令打包（注意第3个add-data中脚本名称需要使用最新版主程序或指定的版本）
  ```
  pyinstaller --noconfirm --onefile --windowed --name=newUI -i icon.ico luncher9.py --add-data "icon.ico;." --add-data "python312.zip;." --add-data "uiqt0_2_3_3.py;main_script" --add-data "fonts;fonts" --add-data "resources;resources"
- 程序支持热加载/修改脚本，程序启动后修改/新增kit/plugin脚本时仅需点击UI左上角的↻刷新按钮即可完成脚本的加载与重载
---
