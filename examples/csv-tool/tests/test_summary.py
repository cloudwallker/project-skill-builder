"""Nominal behavior checks for the small CSV starting project."""

import csv
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from summary import summarize


class SummaryTests(unittest.TestCase):
    def summarize_rows(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.csv'
            with path.open('w', newline='', encoding='utf-8') as stream:
                csv.writer(stream).writerows(rows)
            return summarize(path)

    def test_decimal_totals(self):
        result = self.summarize_rows([
            ['category', 'amount'],
            ['books', '0.1'],
            ['books', '0.2'],
            ['food', '2.50'],
        ])
        self.assertEqual(result, {'books': Decimal('0.3'), 'food': Decimal('2.50')})
        self.assertTrue(all(isinstance(value, Decimal) for value in result.values()))

    def test_category_whitespace_is_trimmed(self):
        result = self.summarize_rows([
            ['category', 'amount'],
            [' books ', '1.25'],
            ['\tbooks\t', '2.75'],
        ])
        self.assertEqual(result, {'books': Decimal('4.00')})

    def test_header_only_is_empty(self):
        self.assertEqual(self.summarize_rows([['category', 'amount']]), {})


if __name__ == '__main__':
    unittest.main()
