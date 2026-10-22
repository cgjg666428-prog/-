"""Visible, user-controlled following of one local practice region. No image files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from result_view import configure_result_tags, display_best_result

BASE = Path(__file__).resolve().parent


def enable_dpi_awareness():
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def region_config(rect, interval_ms=400):
    if len(rect) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in rect):
        raise ValueError('区域坐标须为四个整数')
    x, y, width, height = rect
    if width < 120 or height < 80 or width > 10000 or height > 10000:
        raise ValueError('请框选完整题干和全部选项，区域至少 120 × 80 像素')
    if not 250 <= interval_ms <= 2000:
        raise ValueError('读取间隔须在 250 至 2000 毫秒之间')
    return dict(x=x, y=y, width=width, height=height, interval_ms=interval_ms)


class ScreenReader:
    def __init__(self, rect, interval_ms=400):
        if sys.platform != 'win32':
            raise ValueError('实时跟随需要 Windows 10/11 和系统中文 OCR 组件')
        config = region_config(rect, interval_ms)
        powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        self.events = queue.Queue()
        self.stopped = threading.Event()
        self.process = subprocess.Popen(
            [str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
             (BASE / 'windows_live.ps1').read_text(encoding='utf-8')],
            env=dict(os.environ, SAFETY_STUDY_REGION=json.dumps(config)),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding='utf-8', errors='replace', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        diagnostics = []
        reported_error = False
        try:
            for line in self.process.stdout:
                if self.stopped.is_set():
                    break
                try:
                    event = json.loads(line.lstrip('\ufeff'))
                    if isinstance(event, dict) and 'event' in event:
                        self.events.put(event)
                        reported_error |= event['event'] == 'error'
                except ValueError:
                    diagnostics.append(line.strip())
                    diagnostics = diagnostics[-8:]
        except (ValueError, OSError) as e:
            diagnostics.append(str(e))
        finally:
            if not self.stopped.is_set() and not reported_error:
                self.events.put({'event': 'error', 'message': '区域读取已中断。' + '\n'.join(diagnostics)})

    def stop(self):
        self.stopped.set()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.thread.join(timeout=1)
        if self.process.stdout:
            self.process.stdout.close()


class LivePanel(ttk.Frame):
    def __init__(self, root, parent, bank, prepare, summarize, format_result):
        super().__init__(parent, padding=12)
        self.root, self.bank = root, bank
        self.prepare, self.summarize, self.format_result = prepare, summarize, format_result
        self.rect = None
        self.reader = None
        self.closed = False
        self.selector = None
        self.last_event = time.monotonic()
        self.status = tk.StringVar(value='未开启。将本地练习题放在左侧，工具放在右侧。')
        self.region_label = tk.StringVar(value='尚未选择左侧题目区域。')
        ttk.Label(self, text='分屏训练 · 实时跟随', font=('Microsoft YaHei', 17)).pack(anchor='w', pady=(0, 8))
        ttk.Label(self, text='只需选定一次左侧题干和全部选项，开始后换题会自动更新答案。', wraplength=650).pack(anchor='w')
        tools = ttk.Frame(self)
        tools.pack(fill='x', pady=12)
        ttk.Button(tools, text='工具靠右', command=self.dock_right).pack(side='left', padx=(0, 6))
        self.select_button = ttk.Button(tools, text='选择左侧题目区域', command=self.select_region)
        self.select_button.pack(side='left', padx=(0, 6))
        self.start_button = ttk.Button(tools, text='开始跟随', command=self.start)
        self.start_button.pack(side='left', padx=(0, 6))
        self.stop_button = ttk.Button(tools, text='暂停', command=self.stop, state='disabled')
        self.stop_button.pack(side='left')
        ttk.Label(self, textvariable=self.region_label, wraplength=650).pack(anchor='w')
        ttk.Label(self, textvariable=self.status, wraplength=650).pack(anchor='w', pady=8)
        self.answer = ttk.Label(self, text='等待开启实时跟随', wraplength=650, font=('Microsoft YaHei', 14))
        self.answer.pack(anchor='w', pady=12)
        self.details = ScrolledText(self, height=15, font=('Microsoft YaHei', 10), wrap='word')
        configure_result_tags(self.details)
        self.details.pack(fill='both', expand=True)
        ttk.Label(self, text='用于本地训练。开启时仅在内存中读取选定区域，不保存图片；点“暂停”停止读取。', wraplength=650).pack(anchor='w', pady=(10, 0))
        self.bind('<Configure>', self._resize)
        self.root.after(100, self.poll)

    def _resize(self, event):
        self.answer.config(wraplength=max(280, event.width - 32))

    def dock_right(self):
        width, height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f'{max(620, width // 2)}x{max(500, height-90)}+{max(0, width-max(620, width//2))}+20')

    def set_region(self, rect):
        region_config(rect)
        self.rect = tuple(rect)
        self.region_label.set(f'选定区域：左上角 ({rect[0]}, {rect[1]})，大小 {rect[2]} × {rect[3]} 像素。')
        self.status.set('区域已选定，点击“开始跟随”。左侧布局变化后请重新选择区域。')

    def select_region(self):
        self.stop()
        if self.selector and self.selector.winfo_exists():
            self.selector.destroy()
        overlay = self.selector = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.geometry(f'{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0')
        overlay.attributes('-topmost', True)
        overlay.attributes('-alpha', .30)
        canvas = tk.Canvas(overlay, bg='#12365c', cursor='crosshair', highlightthickness=0)
        canvas.pack(fill='both', expand=True)
        canvas.create_text(24, 22, anchor='nw', fill='white', font=('Microsoft YaHei', 16), text='拖动框选左侧一道练习题的题干与全部选项；Esc 取消')
        state = {}
        def down(event):
            state['start'] = (event.x_root, event.y_root)
            if 'box' in state:
                canvas.delete(state['box'])
            state['box'] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline='#f9d45c', width=4)
        def move(event):
            if 'start' in state:
                canvas.coords(state['box'], state['start'][0], state['start'][1], event.x_root, event.y_root)
        def finish(event):
            if 'start' not in state:
                return
            x, y = state['start']
            rect = (min(x, event.x_root), min(y, event.y_root), abs(x-event.x_root), abs(y-event.y_root))
            overlay.destroy()
            try:
                self.set_region(rect)
            except ValueError as e:
                self.status.set(str(e))
        canvas.bind('<ButtonPress-1>', down)
        canvas.bind('<B1-Motion>', move)
        canvas.bind('<ButtonRelease-1>', finish)
        overlay.bind('<Escape>', lambda _: overlay.destroy())
        overlay.grab_set()
        overlay.focus_force()

    def start(self):
        if self.reader:
            return
        if self.rect is None:
            self.status.set('请先点击“选择左侧题目区域”，框选题干和全部选项。')
            return
        self.root.update_idletasks()
        x, y, w, h = self.rect
        rx, ry, rw, rh = self.root.winfo_rootx(), self.root.winfo_rooty(), self.root.winfo_width(), self.root.winfo_height()
        if max(x, rx) < min(x+w, rx+rw) and max(y, ry) < min(y+h, ry+rh):
            self.status.set('题目区域与本工具重叠。请先点“工具靠右”，并让所选区域完整显示在左侧。')
            return
        try:
            self.reader = ScreenReader(self.rect)
        except (OSError, ValueError) as e:
            self.status.set('无法开启：' + str(e))
            return
        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')
        self.last_event = time.monotonic()
        self.answer.config(text='正在启动本地文字识别…')
        self.status.set('开启中；点击“暂停”可随时停止。')

    def stop(self):
        if self.reader:
            self.reader.stop()
            self.reader = None
        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')
        self.status.set('已暂停，停止读取所选区域。')
        self.answer.config(text='已暂停；重新开始后显示当前题目的答案。')

    def handle_event(self, event):
        self.last_event = time.monotonic()
        kind = event.get('event')
        if kind == 'ready':
            self.status.set('跟随中 · 正在等待读取稳定的题目文字。')
        elif kind == 'changing':
            self.answer.config(text='题目区域正在变化，等待文字稳定…')
            self.details.delete('1.0', 'end')
        elif kind == 'text':
            raw = event.get('text', '')
            try:
                text = self.prepare(raw)
                # The search still evaluates the runner-up for confidence.
                results = self.bank.search(text, limit=1)
                self.answer.config(text=self.summarize(results).replace('截图当前选项', '左侧当前选项'))
                display_best_result(self.details, results, self.format_result)
            except ValueError as e:
                self.answer.config(text='暂未识别到完整题目，请确认左侧已显示题干和全部选项。')
                self.details.delete('1.0', 'end')
                self.details.insert('1.0', str(e) + '\n\n原始文字：\n' + raw)
            self.status.set(f"跟随中 · 更新于 {time.strftime('%H:%M:%S')} · 本次识别 {event.get('elapsed_ms', 0)} 毫秒")
        elif kind == 'error':
            message = event.get('message', '未知错误')
            self.stop()
            self.answer.config(text='识别已暂停。请检查系统中文 OCR 组件和左侧显示区域。')
            self.details.delete('1.0', 'end')
            self.details.insert('1.0', message)

    def poll(self):
        if self.closed:
            return
        reader = self.reader
        if reader:
            while self.reader is reader:
                try:
                    event = reader.events.get_nowait()
                except queue.Empty:
                    break
                self.handle_event(event)
            if self.reader and time.monotonic() - self.last_event > 30:
                self.handle_event({'event': 'error', 'message': '超过 30 秒没有收到识别响应。请确认电脑未锁屏，然后重新开始。'})
        self.root.after(100, self.poll)

    def close(self):
        self.closed = True
        self.stop()
        if self.selector and self.selector.winfo_exists():
            self.selector.destroy()
