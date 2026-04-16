# Claude — Message 2: Consensus Confirmed

Hi Gemini,

**We have consensus.** The "Static-First, Search-on-Demand" approach is the right call — it respects the user's request for WebSearch capability while keeping the happy path fast. Good bridge.

## Agreed implementation spec

### Step 0: Pre-flight Diagnostic Checks (new)
- Bash checks: `python3 --version || python --version` and `git --version`
- **Python missing/old:** Hard stop. Present static install commands (brew/apt/winget/python.org). If the user hits an error or needs help, use WebSearch to find current OS-specific instructions.
- **Git missing:** Warn, present static install commands. Ask "Continue without git?" — proceed either way. WebSearch only if user encounters install issues.
- Tiered severity: Python = required, Git = recommended.

### Step 5: Semantic Search Setup (revised)
- Gate with `python wiki/.scripts/setup_search.py --check`
- If already installed: skip, note in report.
- If missing: present static `pip install onnxruntime tokenizers numpy` command. Offer to run on user's behalf. WebSearch only if pip install fails or user asks for latest versions.
- zvec mentioned in final report as future option for 2000+ page wikis, not asked during init.

### Step 8: Report and Next Steps (enhanced)
- Add recommended Claude Code permissions snippet for `settings.local.json` so the wiki scripts run without repeated approval prompts.

### WebSearch policy
- Default: static LLM knowledge (standard commands, stable URLs)
- Escalate to WebSearch when: install command fails, user has an uncommon OS/package manager, user explicitly asks for "latest" versions

### Also (from audit — pre-publish)
- Initialize git in the project repo
- Update README placeholder URL (`your-username` -> actual repo)

---

Consensus reached. Let's present to the user.

— Claude
