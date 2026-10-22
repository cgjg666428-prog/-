"""江苏安全知识竞赛：离线复习、文本检索与本地练习。Python 3.10+。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def normalize(text):
    text = unicodedata.normalize('NFKC', str(text)).lower()
    text = re.sub(r'^\s*(?:第\s*)?\d+\s*[题.、:)]\s*', '', text)
    text = re.sub(r'[【\[](?:单选题?|多选题?|判断题?)[】\]]', '', text)
    return ''.join(c for c in text if c.isalnum())


def grams(text):
    return {text[i:i+2] for i in range(len(text)-1)} or {text}


def similarity(a, b):
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ga, gb = grams(a), grams(b)
    score = .65 * SequenceMatcher(None, a, b, autojunk=False).ratio() + .35 * len(ga & gb) / len(ga | gb)
    return score


def truth(text):
    n = normalize(text)
    if n in {'y', '正确', '对', '是', 'true', '√'} or str(text).strip() == '√':
        return 'Y'
    if n in {'n', '错误', '错', '否', 'false', '×'} or str(text).strip() in {'×', '✗'}:
        return 'N'
    return None


def split_query(text):
    """Recognize explicitly labelled A. / A、 / A: options, inline or multiline."""
    text = unicodedata.normalize('NFKC', text).strip()
    pattern = r'(?m)(?:^|[\s\u3000]+)([A-Ha-h])[ \t]*[.、:)][ \t]*'
    matches = list(re.finditer(pattern, text))
    if len(matches) < 2:
        # Screenshots often show radio buttons and letter labels without dots.
        bare = list(re.finditer(r'(?m)^[ \t]*(?:[○◯●□☐]\s*)?([A-Ha-h])(?:[ \t]+|\n)(?![.、:)])', text))
        if len(bare) >= 2:
            matches = bare
    if not matches:
        return text, {}
    labels = [m.group(1).upper() for m in matches]
    if len(set(labels)) != len(labels):
        raise ValueError('选项标号重复，请一次输入一道题。')
    options = {m.group(1).upper(): text[m.end():matches[i+1].start() if i+1 < len(matches) else len(text)].strip()
               for i, m in enumerate(matches)}
    return text[:matches[0].start()].strip(), options


def read_xlsx(path):
    """Read the provided six-column XLSX with Python's standard library."""
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            shared = [
                          ''.join(t.text or '' for t in si.findall('.//m:t', ns))
                          for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns)]
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        rel = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        targets = {r.attrib['Id']: r.attrib['Target'] for r in rel}
        questions, issues = [], []
        for sheet in wb.findall('m:sheets/m:sheet', ns):
            rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target = targets[rid]
            target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            root = ET.fromstring(z.read(target))
            rows = []
            for row in root.findall('m:sheetData/m:row', ns):
                values = {}
                for c in row.findall('m:c', ns):
                    col = re.sub(r'\d', '', c.attrib['r'])
                    v = c.find('m:v', ns)
                    raw = v.text if v is not None else ''
                    if c.attrib.get('t') == 's':
                        raw = shared[int(raw)]
                    elif c.attrib.get('t') == 'inlineStr':
                        raw = ''.join(t.text or '' for t in c.findall('.//m:t', ns))
                    values[col] = raw or ''
                rows.append((int(row.attrib['r']), values))
            header = next((i for i, (_, r) in enumerate(rows) if [r.get(c) for c in 'ABCDEF'] == ['序号', '题型', '类别', '题目', '选项', '答案']), None)
            if header is None:
                issues.append(f"工作表 {sheet.attrib['name']} 没有预期的六列表头，未导入")
                continue
            for rownum, row in rows[header+1:]:
                vals = [row.get(c, '').strip() for c in 'ABCDEF']
                if not any(vals):
                    continue
                ident, kind, category, stem, opts, answer = vals
                options = opts.split('|') if opts else []
                answers = [x for x in re.split(r'[|,，、\s]+', answer.upper()) if x]
                errors = []
                if kind not in {'单选题', '多选题', '判断题'} or not stem:
                    errors.append('题型或题干无效')
                if kind == '判断题':
                    if answers not in [['Y'], ['N']]:
                        errors.append('判断答案须为 Y/N')
                elif not options or not answers or any(a not in LETTERS[:len(options)] for a in answers):
                    errors.append('选项或答案无效')
                elif (kind == '单选题' and len(answers) != 1) or (kind == '多选题' and len(answers) < 2):
                    errors.append('答案数量与题型不符')
                if len(set(answers)) != len(answers):
                    errors.append('答案重复')
                if errors:
                    raise ValueError(f'{sheet.attrib["name"]} 第 {rownum} 行: {errors}')
                questions.append(dict(id=ident, kind=kind, category=category, stem=stem,
                                      options=options, answer=answers, sheet=sheet.attrib['name'], row=rownum))
    if not questions:
        raise ValueError('没有导入任何题目，请检查表头与文件。')
    counts = Counter(normalize(q['stem']) for q in questions)
    duplicates = {k: v for k, v in counts.items() if v > 1}
    return {'source': Path(path).name, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'questions': questions, 'report': {'total': len(questions), 'types': dict(Counter(q['kind'] for q in questions)),
            'categories': dict(Counter(q['category'] for q in questions)), 'duplicate_stems': duplicates, 'issues': issues}}


def answer_text(q):
    if q['kind'] == '判断题':
        return ['正确' if q['answer'] == ['Y'] else '错误']
    return [q['options'][LETTERS.index(a)] for a in q['answer']]


def map_options(q, options):
    """Map content, never assume bank letters survive a shuffle."""
    if not options:
        return None, '未提供当前选项；字母仅对应题库原顺序'
    expected = (['正确', '错误'] if q['kind'] == '判断题' else q['options'])
    if len(options) != len(expected):
        return None, '当前选项数量与题库不一致，未映射'
    mapping, used = {}, set()
    for i, content in enumerate(expected):
        scores = []
        for label, value in options.items():
            if q['kind'] == '判断题':
                score = float(truth(value) == ('Y' if i == 0 else 'N'))
            else:
                score = similarity(content, value)
            scores.append((score, label))
        scores.sort(reverse=True)
        exact_unique = scores[0][0] == 1 and (len(scores) == 1 or scores[1][0] < 1)
        if not exact_unique and (scores[0][0] < .88 or (len(scores) > 1 and scores[0][0] - scores[1][0] < .12)) or scores[0][1] in used:
            return None, '选项存在缺失、相似或重复内容，未能可靠映射'
        mapping[i] = scores[0][1]
        used.add(scores[0][1])
    indexes = [0 if q['answer'] == ['Y'] else 1] if q['kind'] == '判断题' else [LETTERS.index(a) for a in q['answer']]
    return sorted(mapping[i] for i in indexes), '已按选项内容映射，请核对题干'


class Bank:
    def __init__(self, data):
        self.data = data
        self.questions = data['questions']
        self.normal = [normalize(q['stem']) for q in self.questions]
        self.index = defaultdict(set)
        self.exact = defaultdict(list)
        for i, s in enumerate(self.normal):
            self.exact[s].append(i)
            for g in grams(s):
                self.index[g].add(i)

    def search(self, text, limit=5, kind='', category=''):
        stem, options = split_query(text)
        n = normalize(stem)
        if len(n) < 4:
            raise ValueError('请输入至少 4 个有效字符的题干。')
        hits = Counter()
        for g in grams(n):
            hits.update(self.index.get(g, ()))
        eligible = {i for i, q in enumerate(self.questions) if (not kind or q['kind'] == kind) and (not category or q['category'] == category)}
        candidates = set(i for i, _ in Counter({i: count for i, count in hits.items() if i in eligible}).most_common(100)) | (set(self.exact.get(n, [])) & eligible)
        ranked = []
        for i in candidates:
            q = self.questions[i]
            if kind and q['kind'] != kind or category and q['category'] != category:
                continue
            score = similarity(n, self.normal[i])
            cautions = []
            if re.findall(r'\d+', n) != re.findall(r'\d+', self.normal[i]):
                score *= .85
                cautions.append('数字与原题不同')
            if any((w in n) != (w in self.normal[i]) for w in ['不', '错误', '正确', '不能', '不得', '无需']):
                score *= .85
                cautions.append('正误或否定关键词不同')
            mapped, note = map_options(q, options)
            if options and mapped is None:
                score *= .90
            ranked.append(dict(question=q, score=round(score * 100, 2), mapped=mapped,
                               mapping_note=note, cautions=cautions))
        ranked.sort(key=lambda r: r['score'], reverse=True)
        ranked = ranked[:max(2, limit)]
        for i, result in enumerate(ranked):
            margin = result['score'] - ranked[i+1]['score'] if i+1 < len(ranked) else result['score']
            # Only the first candidate can receive high confidence.
            result['confidence'] = ('高' if i == 0 and result['score'] >= 93 and margin >= 8 and not result['cautions']
                                    else '中' if result['score'] >= 75 else '低')
            result['answer_text'] = answer_text(result['question'])
        return ranked[:limit]


def ocr_file(path, executable='tesseract', engine='auto'):
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}:
        raise ValueError('OCR 仅接受手动指定的本地图片。')
    errors = []
    if engine in {'auto', 'windows'} and sys.platform == 'win32':
        powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        environment = dict(os.environ, SAFETY_STUDY_IMAGE=str(path))
        # Pass the image path as data; never interpolate it into shell code.
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command',
                                 (BASE / 'windows_ocr.ps1').read_text(encoding='utf-8')], env=environment,
                                capture_output=True, encoding='utf-8', errors='replace', timeout=90,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode == 0:
            return json.loads(result.stdout.lstrip('\ufeff'))['text']
        errors.append('Windows OCR：' + result.stderr[-1400:])
    if engine == 'windows':
        raise ValueError('\n'.join(errors) or 'Windows OCR 仅适用于 Windows 10/11。')
    exe = shutil.which(executable)
    if not exe:
        raise ValueError('未能启动本地中文识别。Windows 10/11 请安装简体中文的基本输入/OCR 语言组件；也可安装 Tesseract 和 chi_sim+eng 语言包。\n' + '\n'.join(errors))
    result = subprocess.run([exe, str(path), 'stdout', '-l', 'chi_sim+eng', '--psm', '6'],
                            capture_output=True, encoding='utf-8', errors='replace', timeout=90,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('OCR 失败：' + result.stderr[-1200:])
    return result.stdout


def prepare_ocr_text(text):
    """Strip common practice headers, preserving the raw OCR separately."""
    text = re.sub(r'(?<=[\u3400-\u9fff])[ \t]+(?=[\u3400-\u9fff])', '', text)
    lines = text.strip().splitlines()
    while lines and (not lines[0].strip() or re.fullmatch(r'\s*(?:第\s*)?\d+\s*(?:[/／]\s*\d+|题)?\s*', lines[0])):
        lines.pop(0)
    text = '\n'.join(lines)
    text = re.sub(r'^\s*(?:\d+\s*[.、)]\s*)?[【\[(]?(?:单选题|多选题|判断题)[】\])]?(?:\s*[(（]\s*\d+(?:\.\d+)?\s*分\s*[)）])?\s*[:：]?\s*', '', text)
    return text.strip()


def image_search(bank, path, executable='tesseract', engine='auto', limit=5):
    raw = ocr_file(path, executable, engine)
    if not normalize(raw):
        raise ValueError('没有识别到文字，请使用清晰的单题截图。')
    query = prepare_ocr_text(raw)
    return raw, query, bank.search(query, limit)


def image_summary(results):
    if not results:
        return '未找到匹配题目，请核对识别文字。'
    top = results[0]
    if top['score'] < 75:
        return f"未找到可靠匹配（最高 {top['score']:.2f}/100），请核对识别文字。下方仅列候选题。"
    label = '匹配答案' if top['confidence'] == '高' else '候选答案（需复核）'
    letters = '、'.join(top['mapped']) if top['mapped'] else '未能确认截图选项字母'
    return f"{label}：{'；'.join(top['answer_text'])}\n截图当前选项：{letters}\n置信度：{top['confidence']}，匹配分数：{top['score']:.2f}/100"


def format_result(result):
    q = result['question']
    opts = ('Y. 正确\nN. 错误' if q['kind'] == '判断题' else '\n'.join(f'{LETTERS[i]}. {o}' for i, o in enumerate(q['options'])))
    return (f"匹配分数 {result['score']:.2f}/100 · 置信度 {result['confidence']}\n"
            f"题库 #{q['id']} · {q['kind']} · {q['category']} · {q['sheet']} 第 {q['row']} 行\n"
            f"{q['stem']}\n题库原答案：{'、'.join(q['answer'])}（{'；'.join(result['answer_text'])}）\n{opts}\n"
            f"当前输入选项答案：{'、'.join(result['mapped']) if result['mapped'] else '未映射'}\n"
            f"{result['mapping_note']}\n{'；'.join(result['cautions'])}\n")


def practice_options(q, rng):
    items = [('正确', 'Y'), ('错误', 'N')] if q['kind'] == '判断题' else [(v, LETTERS[i]) for i, v in enumerate(q['options'])]
    rng.shuffle(items)
    return items


def cli_practice(bank, count=10, kind='', category='', seed=None):
    """Terminal practice is available even without Tk/Tcl."""
    pool = [q for q in bank.questions if (not kind or q['kind'] == kind) and (not category or q['category'] == category)]
    if not pool:
        raise ValueError('当前范围没有题目')
    rng = random.Random(seed)
    selected = rng.sample(pool, min(count, len(pool)))
    correct_count, wrong = 0, []
    for number, q in enumerate(selected, 1):
        items = practice_options(q, rng)
        expected = {LETTERS[i] for i, (_, original) in enumerate(items) if original in q['answer']}
        print(f"\n{number}/{len(selected)}  #{q['id']} {q['kind']} / {q['category']}\n{q['stem']}")
        for i, (content, _) in enumerate(items):
            print(f'{LETTERS[i]}. {content}')
        while True:
            try:
                raw = input('输入答案字母（多选如 AC，q 结束）：').strip().upper()
            except (EOFError, KeyboardInterrupt):
                raw = 'Q'
            if raw == 'Q':
                print(f'已结束，本轮答对 {correct_count} 题。')
                return
            compact = re.sub(r'[\s,，|、]+', '', raw)
            answer = set(compact)
            if not answer or not answer <= set(LETTERS[:len(items)]) or (q['kind'] != '多选题' and len(answer) != 1):
                print('请输入本题有效的答案字母。')
                continue
            break
        ok = answer == expected
        correct_count += int(ok)
        print(('回答正确' if ok else '回答有误') + '。当前顺序答案：' + '、'.join(sorted(expected)) + '；内容：' + '；'.join(answer_text(q)))
        if not ok:
            wrong.append(q['id'])
    print(f"\n本轮完成：{correct_count}/{len(selected)}。本轮错题编号：{'、'.join(wrong) or '无'}")


def gui(bank):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter.scrolledtext import ScrolledText
    from live_follow import LivePanel, enable_dpi_awareness
    from result_view import configure_result_tags, display_best_result
    enable_dpi_awareness()
    try:
        root = tk.Tk()
    except tk.TclError as e:
        raise ValueError('桌面组件 Tcl/Tk 无法启动。请修复 Python 的 Tcl/Tk 组件，或使用 python study.py practice 进行终端练习。详情：' + str(e)) from e
    root.title('安全知识 · 离线复习与练习')
    root.geometry('1050x820')
    ttk.Label(root, text=f"已载入 {len(bank.questions)} 题 · 考前复习与本地练习", font=('Microsoft YaHei', 16)).pack(pady=12)
    notebook = ttk.Notebook(root)
    notebook.pack(fill='both', expand=True, padx=16, pady=8)
    live_panel = LivePanel(root, notebook, bank, prepare_ocr_text, image_summary, format_result)
    notebook.add(live_panel, text='实时跟随')
    search_tab, practice_tab = ttk.Frame(notebook), ttk.Frame(notebook)
    notebook.add(search_tab, text='题目检索')
    notebook.add(practice_tab, text='本地练习 / 错题')
    ttk.Label(search_tab, text='打开本地单题截图，即可识别并显示匹配答案；也可粘贴题干与选项。').pack(anchor='w', pady=6)
    query = ScrolledText(search_tab, height=8, font=('Microsoft YaHei', 11))
    query.pack(fill='x')
    tools = ttk.Frame(search_tab)
    tools.pack(fill='x', pady=6)
    output = ScrolledText(search_tab, font=('Microsoft YaHei', 11), wrap='word')
    configure_result_tags(output)
    output.pack(fill='both', expand=True)
    image_status = tk.StringVar(value='支持 PNG、JPG 等本地图片。Windows 优先使用系统中文识别。')
    ttk.Label(search_tab, textvariable=image_status, wraplength=980).pack(anchor='w', pady=4)
    def lookup():
        try:
            results = bank.search(query.get('1.0', 'end'), limit=1)
            display_best_result(output, results, format_result)
        except Exception as e:
            messagebox.showerror('检索提示', str(e))
    def load_text(image=False):
        path = filedialog.askopenfilename(filetypes=[('本地图片', '*.png *.jpg *.jpeg *.tif *.tiff *.bmp')] if image else [('UTF-8 文本', '*.txt')])
        if not path:
            return
        try:
            if image:
                import threading
                import queue
                completed = queue.Queue()
                image_button.config(state='disabled')
                image_status.set('正在离线识别截图并检索题库，请稍候…')
                def poll():
                    try:
                        ok, value = completed.get_nowait()
                    except queue.Empty:
                        root.after(100, poll)
                        return
                    image_button.config(state='normal')
                    if ok:
                        raw, content, results = value
                        replace_query(content)
                        display_best_result(output, results, format_result,
                                            prefix=image_summary(results) + '\n\n',
                                            suffix='\n截图原始识别文字：\n' + raw)
                        image_status.set('截图识别与检索完成。请核对上方文字，尤其是否定词、数字与选项。')
                    else:
                        image_status.set('识别失败，可按提示补齐 OCR 组件或改用文本。')
                        messagebox.showerror('图片识别提示', value)
                def worker():
                    try:
                        completed.put((True, image_search(bank, path, limit=1)))
                    except Exception as e:
                        completed.put((False, str(e)))
                threading.Thread(target=worker, daemon=True).start()
                root.after(100, poll)
            else:
                replace_query(Path(path).read_text(encoding='utf-8-sig'))
        except Exception as e:
            messagebox.showerror('导入提示', str(e))
    def replace_query(content):
        query.delete('1.0', 'end')
        query.insert('1.0', content)
    ttk.Button(tools, text='检索候选答案', command=lookup).pack(side='left')
    ttk.Button(tools, text='导入本地文本', command=load_text).pack(side='left', padx=8)
    image_button = ttk.Button(tools, text='打开截图并显示答案', command=lambda: load_text(True))
    image_button.pack(side='left')

    history_path = BASE / 'practice_history.json'
    history_warning = ''
    try:
        history = json.loads(history_path.read_text(encoding='utf-8')) if history_path.exists() else {}
        if not isinstance(history, dict):
            raise ValueError('记录格式无效')
    except (ValueError, OSError):
        history = {}
        history_warning = '历史记录无法读取，本次仅保存在内存。请备份并移走旧 practice_history.json 后重启。'
    history_safe = not history_warning
    filters = ttk.Frame(practice_tab)
    filters.pack(fill='x', pady=10)
    mode, kind, category = tk.StringVar(value='随机练习'), tk.StringVar(value='全部题型'), tk.StringVar(value='全部类别')
    for var, values in [(mode, ['随机练习', '顺序练习', '错题重练']), (kind, ['全部题型', '单选题', '多选题', '判断题']),
                        (category, ['全部类别'] + sorted({q['category'] for q in bank.questions}))]:
        ttk.Combobox(filters, textvariable=var, values=values, state='readonly', width=18).pack(side='left', padx=4)
    question_label = ttk.Label(practice_tab, wraplength=940, font=('Microsoft YaHei', 12))
    question_label.pack(anchor='w', pady=15)
    choices = ttk.Frame(practice_tab)
    choices.pack(fill='x')
    feedback = tk.StringVar(value=history_warning or '选择练习范围后，点击“下一题”。')
    ttk.Label(practice_tab, textvariable=feedback, wraplength=940, font=('Microsoft YaHei', 11)).pack(anchor='w', pady=16)
    state = {'q': None, 'items': [], 'vars': [], 'submitted': False, 'cursor': -1}
    rng = random.Random()
    def key(q):
        return hashlib.sha256((q['kind'] + q['stem'] + '|'.join(q['options']) + '|'.join(q['answer'])).encode()).hexdigest()
    def next_question():
        pool = [q for q in bank.questions if (kind.get() == '全部题型' or q['kind'] == kind.get()) and
                (category.get() == '全部类别' or q['category'] == category.get()) and
                (mode.get() != '错题重练' or history.get(key(q), {}).get('wrong', 0) > 0)]
        if not pool:
            messagebox.showinfo('练习', '当前范围没有题目。错题本保留曾答错的题目。')
            return
        state['cursor'] += 1
        q = pool[state['cursor'] % len(pool)] if mode.get() == '顺序练习' else rng.choice(pool)
        state.update(q=q, items=practice_options(q, rng), vars=[], submitted=False)
        question_label.config(text=f"#{q['id']}  {q['kind']} / {q['category']}\n\n{q['stem']}")
        for widget in choices.winfo_children():
            widget.destroy()
        single = tk.StringVar(value='')
        for i, (content, _) in enumerate(state['items']):
            if q['kind'] == '多选题':
                v = tk.BooleanVar(value=False)
                state['vars'].append(v)
                w = ttk.Checkbutton(choices, text=f'{LETTERS[i]}. {content}', variable=v)
            else:
                w = ttk.Radiobutton(choices, text=f'{LETTERS[i]}. {content}', variable=single, value=str(i))
            w.pack(anchor='w', pady=6)
        state['single'] = single
        feedback.set('请选择答案，再点击“检查答案”。多选题须全部选对且不多选。')
    def check():
        q = state['q']
        if not q or state['submitted']:
            return
        selected = ([i for i, v in enumerate(state['vars']) if v.get()] if q['kind'] == '多选题' else
                    [int(state['single'].get())] if state['single'].get() else [])
        if not selected:
            messagebox.showinfo('练习', '请先选择答案。')
            return
        correct = {i for i, (_, original) in enumerate(state['items']) if original in q['answer']}
        ok = set(selected) == correct
        record = history.setdefault(key(q), {'attempts': 0, 'wrong': 0})
        record['attempts'] += 1
        record['wrong'] += int(not ok)
        state['submitted'] = True
        notice = ''
        if history_safe:
            try:
                temp = history_path.with_suffix('.tmp')
                temp.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
                temp.replace(history_path)
            except OSError as e:
                notice = f'\n本次记录未能写入文件：{e}'
        else:
            notice = '\n' + history_warning
        feedback.set(('回答正确' if ok else '回答有误') + '\n本题当前顺序答案：' + '、'.join(LETTERS[i] for i in sorted(correct)) +
                     '\n答案内容：' + '；'.join(answer_text(q)) + f"\n累计练习 {record['attempts']} 次，答错 {record['wrong']} 次。" + notice)
    buttons = ttk.Frame(practice_tab)
    buttons.pack(anchor='w', pady=8)
    ttk.Button(buttons, text='检查答案', command=check).pack(side='left')
    ttk.Button(buttons, text='下一题', command=next_question).pack(side='left', padx=10)
    def close():
        live_panel.close()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank', type=Path, default=BASE / 'bank.json')
    sub = parser.add_subparsers(dest='command')
    imp = sub.add_parser('import', help='导入六列表头 XLSX 题库')
    imp.add_argument('xlsx', type=Path)
    search = sub.add_parser('search', help='检索单道题')
    source = search.add_mutually_exclusive_group(required=True)
    source.add_argument('--text')
    source.add_argument('--file', type=Path)
    source.add_argument('--image', type=Path)
    search.add_argument('--tesseract', default='tesseract')
    search.add_argument('--ocr-engine', choices=['auto', 'windows', 'tesseract'], default='auto')
    search.add_argument('--top', type=int, choices=range(1, 11), default=1)
    search.add_argument('--kind', choices=['单选题', '多选题', '判断题'], default='')
    search.add_argument('--category', default='')
    search.add_argument('--json', action='store_true')
    sub.add_parser('stats', help='题库统计与导入报告')
    sub.add_parser('gui', help='打开桌面界面（默认）')
    practice = sub.add_parser('practice', help='终端随机练习，不需要桌面组件')
    practice.add_argument('--count', type=int, choices=range(1, 1001), default=10)
    practice.add_argument('--kind', choices=['单选题', '多选题', '判断题'], default='')
    practice.add_argument('--category', default='')
    practice.add_argument('--seed', type=int)
    args = parser.parse_args()
    try:
        if args.command == 'import':
            data = read_xlsx(args.xlsx)
            args.bank.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(data['report'], ensure_ascii=False, indent=2))
            return
        bank = Bank(json.loads(args.bank.read_text(encoding='utf-8')))
        if args.command == 'stats':
            print(json.dumps(bank.data['report'], ensure_ascii=False, indent=2))
        elif args.command == 'practice':
            cli_practice(bank, args.count, args.kind, args.category, args.seed)
        elif args.command == 'search':
            raw = None
            if args.image:
                raw = ocr_file(args.image, args.tesseract, args.ocr_engine)
                text = prepare_ocr_text(raw)
            else:
                text = args.text if args.text is not None else args.file.read_text(encoding='utf-8-sig')
            results = bank.search(text, args.top, args.kind, args.category)
            if args.json:
                print(json.dumps({'ocr_text': raw, 'results': results} if raw is not None else results, ensure_ascii=False, indent=2))
            else:
                if raw is not None:
                    print(image_summary(results) + '\n\n截图识别文字：\n' + raw + '\n')
                print('\n'.join(format_result(r) for r in results) or '没有候选题目。')
        else:
            gui(bank)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, ET.ParseError, subprocess.TimeoutExpired, ImportError) as e:
        parser.exit(1, f'运行失败：{e}\n请参阅 README.md。\n')


if __name__ == '__main__':
    main()
