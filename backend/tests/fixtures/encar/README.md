# Encar parser fixtures

These are small, anonymised text snapshots of the fields that the scanner reads
after a detail page finishes loading. They intentionally contain no seller
contact details, photographs, cookies, query strings or full HTML.

When Encar changes a visible label or a parser bug is reported, add the smallest
sanitised reproduction here first, then add a regression assertion in
`test_parser_fixtures.py`.
