# Technical Debt Notes

Items to address in the next session (with GitHub connector available).

## 1. README.md — Update repository URL

The README currently points to `https://github.com/anthropics/wiki-forge.git`
(was a placeholder replacement during audit). Update to the actual repository
URL once the GitHub remote is configured.

## 2. Git commit author cleanup

Commits `6c55aac` and `87d01d9` have `Author: AI Bot <bot@example.com>` from
Gemini's session. Rebase or amend these to use the correct author identity
before pushing to the public remote.
