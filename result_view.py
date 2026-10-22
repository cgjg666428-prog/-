"""Compact top-match rendering shared by live and manual search views."""
import re


def configure_result_tags(widget):
    widget.tag_configure('bank_answer', font=('Microsoft YaHei', 18, 'bold'),
                         foreground='#000000', spacing1=8, spacing3=8)
    widget.tag_configure('option', font=('Microsoft YaHei', 12, 'bold'),
                         foreground='#000000', spacing3=4)
    widget.tag_configure('mapped_answer', font=('Microsoft YaHei', 14, 'bold'),
                         foreground='#000000', spacing1=6, spacing3=6)


def display_best_result(widget, results, formatter, prefix='', suffix=''):
    """Display just the first ranked result; preserve search-time confidence."""
    configure_result_tags(widget)
    widget.delete('1.0', 'end')
    if prefix:
        widget.insert('end', prefix)
    if results:
        for line in formatter(results[0]).splitlines(keepends=True):
            if line.startswith('题库原答案：'):
                tag = 'bank_answer'
            elif line.startswith('当前输入选项答案：'):
                tag = 'mapped_answer'
            elif re.match(r'^[A-Z][.、]\s', line):
                tag = 'option'
            else:
                tag = ''
            widget.insert('end', line, (tag,) if tag else ())
    else:
        widget.insert('end', '没有匹配题目。\n')
    if suffix:
        widget.insert('end', suffix)
    widget.see('1.0')
