"""在独立主线程进程中显示本机原生选择窗口，不读取或复制选择的文件。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading


class PickerUnavailable(RuntimeError):
    pass


class NativeFilePicker:
    def __init__(self):
        self.lock = threading.Lock()

    def choose(self, *, directory: bool, initial: Path, title: str) -> str | None:
        if not self.lock.acquire(blocking=False):
            raise PickerUnavailable('已有文件选择窗口打开，请先完成或取消选择。')
        try:
            # 使用同一解释器、参数列表和 JSON 管道，不执行 shell 或用户提供的命令。
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve())],
                input=json.dumps({'directory': directory, 'initial': str(initial), 'title': title}),
                capture_output=True, text=True, encoding='utf-8', timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            if result.returncode:
                raise PickerUnavailable('无法打开系统选择窗口，请在有桌面且支持 Tk 的后端环境中运行；文章可继续搜索，目录可手动填写。')
            value = json.loads(result.stdout)
            if value is not None and not isinstance(value, str):
                raise ValueError('invalid picker result')
            return value or None
        except subprocess.TimeoutExpired:
            raise PickerUnavailable('选择窗口已超时关闭，请重新点击浏览。') from None
        except (OSError, ValueError):
            raise PickerUnavailable('系统选择窗口未能返回路径，请重试或手动填写目录。') from None
        finally:
            self.lock.release()


def main():
    import tkinter as tk
    from tkinter import filedialog

    options = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes('-topmost', True)
        initial = Path(options['initial'])
        while not initial.is_dir() and initial != initial.parent:
            initial = initial.parent
        arguments = {'parent': root, 'title': options['title'], 'initialdir': str(initial)}
        if options['directory']:
            selected = filedialog.askdirectory(**arguments, mustexist=True)
        else:
            selected = filedialog.askopenfilename(**arguments, filetypes=[
                ('Markdown / MDX', ('*.md', '*.mdx', '*.markdown')), ('所有文件', '*'),
            ])
        sys.stdout.buffer.write(json.dumps(selected or None, ensure_ascii=True).encode('utf-8'))
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
