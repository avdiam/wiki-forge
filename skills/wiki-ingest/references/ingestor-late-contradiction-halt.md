# wiki-ingestor — late-contradiction HALT (> 5 new)

> Moved out of the wiki-ingestor playbook (s137, script audit Wave 3) so ingests that don't hit this branch don't load it. The ingestor Reads it from the dispatch's `References:` directory when the branch fires. Every rule here is as binding as the playbook's.

- **> 5 NEW contradictions:** HALT immediately. Roll back **surgically** — touch only the files in `WRITE_LIST` (built during 3a–3d), never the broader working tree. This preserves any unrelated pending wiki/ edits the user may have had in flight when the ingest started.
  ```bash
  # Pseudocode — translate to a real bash loop with WRITE_LIST populated as a bash array:
  for f in "${WRITE_LIST[@]}"; do
    if git -C <wiki> ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
      git -C <wiki> restore -- "$f"      # tracked file: revert to HEAD
    else
      rm -f "<wiki>/$f"                   # untracked file: delete the page the agent created
    fi
  done
  ```
  The list is dedup-safe (`git restore` is idempotent; `rm -f` tolerates missing files), so a path written twice (e.g., source page touched in 3a and again in 3e) needs no special handling. If a single restore/rm errors, continue the loop — partial rollback of the rest is preferable to leaving everything in flight.
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
