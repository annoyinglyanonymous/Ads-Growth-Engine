"""Read verbs over the ads schema, plus one write verb for proposals.

Every module here except record.py imports from db (the ads_reader pool) and
never from db_owner. tests/test_read_only.py asserts that, which is what makes
"the read surface cannot mutate" checkable rather than merely stated.
"""
