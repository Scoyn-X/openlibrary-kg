"""Downstream tasks built on the openlibrary KG.

Current scope:
  - issue_localization: given a GitHub issue's title+body, rank candidate
    files/functions in the openlibrary codebase that are most likely to
    contain the change required to fix it.
  - ground_truth: fetch closed issues from openlibrary's GitHub that were
    fixed by a merged PR, and record the files the PR touched. These pairs
    serve as evaluation data.
"""
