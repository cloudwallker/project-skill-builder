# CSV summary example

Synthetic evaluation project. Python 3.9+, runtime standard library only.

Repeated tasks: maintain CSV aggregation, add output options, handle malformed rows, and update tests/docs.

Input schema: `category,amount`. Amounts are decimal currency units. Use exact decimal arithmetic. Category whitespace is trimmed. Rows with a missing category, malformed amount, or missing columns produce a clear row-numbered error; processing fails rather than silently dropping data. An empty data section produces an empty result. Preserve public APIs unless the request explicitly changes them.

Run: `python -m unittest discover -s tests -v`.
