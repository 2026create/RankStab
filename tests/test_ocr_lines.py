"""Check the row-position heuristic and preserve raw recognized characters."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ocr_lines import boxes_to_markdown, cluster_lines, line_box_stats
from src.flatten import md_to_plain, plain_stats


def box(x, y, width=10, height=10):
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]


class TextAdapterTests(unittest.TestCase):
    def test_empty_and_blank_recognition(self):
        self.assertEqual(boxes_to_markdown([], []), '')
        self.assertEqual(boxes_to_markdown([box(0, 0)], ['']), '')
        self.assertEqual(line_box_stats([], []), (0, 0))

    def test_out_of_order_boxes_and_same_row_fragments(self):
        boxes = [box(20, 0), box(0, 30), box(0, 1)]
        texts = ['右', '下一行', '左']
        self.assertEqual(boxes_to_markdown(boxes, texts), '左 右\n下一行')
        self.assertEqual(line_box_stats(boxes, texts), (3, 2))

    def test_raw_width_case_and_traditional_characters_are_preserved(self):
        self.assertEqual(boxes_to_markdown([box(0, 0)], ['Ａb網絡，']), 'Ａb網絡，')

    def test_line_threshold_is_inclusive(self):
        items = [dict(x=0, y=0, h=10, text='A'), dict(x=20, y=6, h=10, text='B')]
        self.assertEqual(cluster_lines(items), ['A B'])
        items[1]['y'] = 6.01
        self.assertEqual(cluster_lines(items), ['A', 'B'])

    def test_adapter_uses_row_order_for_multiple_columns(self):
        # This is the declared adapter behavior, useful for interpreting order-test failures.
        boxes = [box(0, 0), box(0, 30), box(100, 0), box(100, 30)]
        self.assertEqual(boxes_to_markdown(boxes, ['L1', 'L2', 'R1', 'R2']), 'L1 R1\nL2 R2')

    def test_flatten_preserves_literal_unicode_and_math_content(self):
        self.assertEqual(md_to_plain('**Ａ網絡** $x+1$'), 'Ａ網絡 x+1')
        self.assertEqual(md_to_plain(''), '')
        self.assertEqual(plain_stats('**X**')['plain_chars'], 1)


if __name__ == '__main__':
    unittest.main()
