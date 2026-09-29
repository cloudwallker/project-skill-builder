"""A deliberately small starting project for skill behavior evaluations."""
import csv
from decimal import Decimal


def summarize(path):
    totals = {}
    with open(path, newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            category = row["category"].strip()
            totals[category] = totals.get(category, Decimal("0")) + Decimal(row["amount"])
    return totals
