"""离线运行：python -m unittest -v test_study.py"""
import json
from pathlib import Path
import random
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
from study import Bank, answer_text, cli_practice, image_search, image_summary, map_options, normalize, practice_options, prepare_ocr_text, split_query


class StudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bank = Bank(json.loads((Path(__file__).parent / 'bank.json').read_text(encoding='utf-8')))

    def test_counts(self):
        self.assertEqual(len(self.bank.questions), 1000)
        self.assertEqual(self.bank.data['report']['types'], {'单选题': 350, '多选题': 300, '判断题': 350})

    def test_all_answers_and_shuffled_options(self):
        rng = random.Random(42)
        for q in self.bank.questions:
            self.assertTrue(all(answer_text(q)))
            items = practice_options(q, rng)
            options = {chr(65+i): value for i, (value, _) in enumerate(items)}
            mapped, _ = map_options(q, options)
            expected = sorted(chr(65+i) for i, (_, original) in enumerate(items) if original in q['answer'])
            # Repeated option content must cause an explicit refusal to map.
            if len({normalize(value) for value in options.values()}) < len(options):
                self.assertIsNone(mapped)
            else:
                self.assertEqual(mapped, expected, q['id'])

    def test_exact_search_representative(self):
        for q in self.bank.questions[::31]:
            results = self.bank.search(q['stem'])
            self.assertEqual(results[0]['score'], 100)
            self.assertEqual(normalize(results[0]['question']['stem']), normalize(q['stem']))

    def test_fuzzy_search(self):
        for q in self.bank.questions[::97]:
            s = q['stem']
            damaged = s[:len(s)//2] + s[len(s)//2+1:]
            results = self.bank.search(damaged)
            self.assertEqual(normalize(results[0]['question']['stem']), normalize(q['stem']), q['id'])

    def test_query_parse(self):
        self.assertEqual(split_query('题干\nＡ．甲\nＢ、乙\nＣ：丙'), ('题干', {'A': '甲', 'B': '乙', 'C': '丙'}))
        self.assertEqual(split_query('题干 A. 甲 B. 乙'), ('题干', {'A': '甲', 'B': '乙'}))
        with self.assertRaises(ValueError):
            split_query('题干\nA.甲\nA.乙')

    def test_ocr_option_labels(self):
        self.assertEqual(split_query('题干\nA . 甲\nB . 乙'), ('题干', {'A': '甲', 'B': '乙'}))
        self.assertEqual(split_query('题干\n○ A 甲\n○ B 乙'), ('题干', {'A': '甲', 'B': '乙'}))

    def test_ocr_cleanup(self):
        raw = '单 选 题 （ 2 分 ）\n涉 密 计 算 机 可 以 使 用 以 下\n设 备 。\nA. 无 线 键 盘\nB. 有 线 鼠 标\nC. 无 线 鼠 标\nD. 无 线 网 卡'
        cleaned = prepare_ocr_text(raw)
        self.assertTrue(cleaned.startswith('涉密计算机'))
        with patch('study.ocr_file', return_value=raw):
            original, query, results = image_search(self.bank, 'fixture.png')
        self.assertEqual(original, raw)
        self.assertEqual(query, cleaned)
        self.assertEqual(results[0]['mapped'], ['B'])
        self.assertIn('匹配答案：有线鼠标', image_summary(results))

    def test_ocr_no_text(self):
        with patch('study.ocr_file', return_value='  '):
            with self.assertRaises(ValueError):
                image_search(self.bank, 'blank.png')

    def test_ocr_low_confidence_summary(self):
        result = self.bank.search('涉密计算机可以使用以下设备')[0]
        result['score'] = 40
        self.assertIn('未找到可靠匹配', image_summary([result]))
        self.assertIn('未找到匹配', image_summary([]))

    def test_short_and_unrelated_input(self):
        with self.assertRaises(ValueError):
            self.bank.search('？')
        self.assertEqual(self.bank.search('zzzzzzzzzzzzzz'), [])

    def test_ambiguous_duplicate_confidence(self):
        q = dict(self.bank.questions[0])
        duplicate = dict(q, id='other', answer=['B'])
        results = Bank({'questions': [q, duplicate]}).search(q['stem'])
        self.assertNotEqual(results[0]['confidence'], '高')

    def test_negation_and_number_change(self):
        q = self.bank.questions[0]
        result = Bank({'questions': [q]}).search(q['stem'].replace('可以', '不可以'))[0]
        self.assertIn('正误或否定关键词不同', result['cautions'])
        self.assertNotEqual(result['confidence'], '高')
        q = dict(q, stem='紧急号码为12339。')
        result = Bank({'questions': [q]}).search('紧急号码为12337。')[0]
        self.assertIn('数字与原题不同', result['cautions'])

    def test_unsafe_mapping(self):
        q = self.bank.questions[0]
        self.assertIsNone(map_options(q, {'A': '缺少选项'})[0])
        self.assertIsNone(map_options(q, dict.fromkeys('ABCD', q['options'][0]))[0])

    def test_filter(self):
        q = next(q for q in self.bank.questions if q['kind'] == '多选题')
        results = self.bank.search(q['stem'], kind='多选题', category=q['category'])
        self.assertEqual(results[0]['question']['id'], q['id'])

    def test_terminal_practice(self):
        q = self.bank.questions[0]
        one = Bank({'questions': [q]})
        seed = 7
        rng = random.Random(seed)
        rng.sample([q], 1)
        items = practice_options(q, rng)
        correct = ''.join(chr(65+i) for i, (_, a) in enumerate(items) if a in q['answer'])
        output = StringIO()
        with patch('builtins.input', side_effect=['ZZ', correct]), redirect_stdout(output):
            cli_practice(one, count=1, seed=seed)
        self.assertIn('本轮完成：1/1', output.getvalue())
        self.assertIn('请输入本题有效', output.getvalue())
        output = StringIO()
        with patch('builtins.input', return_value='q'), redirect_stdout(output):
            cli_practice(one, count=1)
        self.assertIn('已结束', output.getvalue())


if __name__ == '__main__':
    unittest.main()
