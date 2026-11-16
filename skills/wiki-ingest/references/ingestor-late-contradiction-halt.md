# wiki-ingestor — late-contradiction HALT (> 5 new)

> Moved out of the wiki-ingestor playbook (s137, script audit Wave 3) so ingests that don't hit this branch don't load it. The ingestor Reads it from the dispatch's `References:` directory when the branch fires. Every rule here is as binding as the playbook's.

- **> 5 NEW contradictions:** HALT immediately. Roll back **surgically** — touch only the files in `WRITE_LIST` (built during 3a–3d), never the broader working tree. This preserves any unrelated pending wiki/ edits the user may have had in flight when the ingest started.
  Three plain commands, no loop (the playbook's one-plain-command rule applies). First, find which `WRITE_LIST` paths are tracked:
  ```bash
  git -C <wiki> ls-files -- <every path in WRITE_LIST, wiki-relative>
  ```
  The paths it prints are tracked (pages you edited): revert them to HEAD.
  ```bash
  git -C <wiki> restore -- <the tracked paths>
  ```
  The `WRITE_LIST` paths it did **not** print are untracked (pages you created): delete them.
  ```bash
  rm -f <wiki>/<untracked path 1> <wiki>/<untracked path 2> ...
  ```
  Skip a command whose path list is empty. Deduplicate `WRITE_LIST` first (a path written twice, e.g. the source page touched in 3a and again in 3e, appears once). If one command errors, still run the other: partial rollback is better than leaving everything in flight. A denial is the exception: stop and report it per the playbook rule, listing the paths not yet rolled back.
  Do **not** run step 4 (index regen), do **not** commit, do **not** append to log. Return:
  ```text
  HALT: late-discovered contradictions exceeded threshold (>5).
  Plan-listed contradictions: <N>
  New contradictions found during writes: <M>
  Conflict list:
  - [[page-1]] — <claim>
  - [[page-2]] — ...
  Rollback complete. No commit, no log entry.
  ```
  The user will redirect — re-run the takeaway pass with the new
  contradictions in scope, or escalate as paradigm-shifting material.
