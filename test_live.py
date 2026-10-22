"""No screen reading in unit tests: python -m unittest -v test_live.py"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from live_follow import LivePanel, ScreenReader, region_config
from study import Bank, prepare_ocr_text, image_summary, format_result


class LiveTests(unittest.TestCase):
    def make_panel(self):
        panel = LivePanel.__new__(LivePanel)
        panel.bank = Bank(json.loads((Path(__file__).parent / 'bank.json').read_text(encoding='utf-8')))
        panel.prepare, panel.summarize, panel.format_result = prepare_ocr_text, image_summary, format_result
        panel.answer, panel.status, panel.details = Mock(), Mock(), Mock()
        panel.start_button, panel.stop_button = Mock(), Mock()
        panel.reader = None
        return panel

    def test_region_validation(self):
        self.assertEqual(region_config((10, 20, 600, 400))['width'], 600)
        for rect in [(0, 0, 10, 10), (0, 0, 800), (0, 0, 800, True), (0, 0, -2, 500)]:
            with self.assertRaises(ValueError):
                region_config(rect)
        with self.assertRaises(ValueError):
            region_config((0, 0, 800, 500), 20)

    def test_changed_content_clears_old_answer(self):
        panel = self.make_panel()
        panel.handle_event({'event': 'changing'})
        self.assertIn('正在变化', panel.answer.config.call_args.kwargs['text'])
        panel.details.delete.assert_called_once()

    def test_text_maps_current_options(self):
        panel = self.make_panel()
        panel.handle_event({'event': 'text', 'text': '涉密计算机可以使用以下设备。\nA.无线键盘\nB.无线网卡\nC.无线鼠标\nD.有线鼠标', 'elapsed_ms': 200})
        answer = panel.answer.config.call_args.kwargs['text']
        self.assertIn('匹配答案：有线鼠标', answer)
        self.assertIn('左侧当前选项：D', answer)

    def test_blank_clears_answer(self):
        panel = self.make_panel()
        panel.handle_event({'event': 'text', 'text': ''})
        self.assertIn('暂未识别到完整题目', panel.answer.config.call_args.kwargs['text'])

    def test_error_stops_reading(self):
        panel = self.make_panel()
        reader = panel.reader = Mock()
        panel.handle_event({'event': 'error', 'message': 'No Chinese OCR'})
        reader.stop.assert_called_once()
        self.assertIsNone(panel.reader)
        self.assertIn('已暂停', panel.answer.config.call_args.kwargs['text'])

    def test_missing_selection_does_not_start(self):
        panel = self.make_panel()
        panel.rect = None
        panel.start()
        self.assertIsNone(panel.reader)
        self.assertIn('请先点击', panel.status.set.call_args.args[0])

    def test_wrong_platform_reports_requirement(self):
        with patch('live_follow.sys.platform', 'linux'):
            with self.assertRaisesRegex(ValueError, 'Windows'):
                ScreenReader((0, 0, 800, 600))


if __name__ == '__main__':
    unittest.main()
