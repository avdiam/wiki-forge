# Goal: Add Interactive Dependency Tests and Guided Installation

Based on your feedback, we will centralize all dependency checks into `wiki-init` so they are evaluated exactly once. During this initialization phase, the LLM will actively diagnose missing prerequisites, securely use its web-search capabilities to find current installation instructions, and provide the user with ready-to-use guidance.

## Proposed Changes

### Modify `skills/wiki-init/SKILL.md`

We will rewrite the `wiki-init` playbook to integrate a robust diagnostic engine and guided setup workflow.

#### 1. Add "Step 0: Pre-flight Diagnostic Checks"
- Before touching any files, the LLM will run a `Bash` command to check `python --version` and `git --version`.
- **If missing:** The LLM will be explicitly prompted to use its `WebSearch` tool to look up up-to-date, OS-specific installation instructions (direct URLs, package manager commands). It will then present these findings to the user and halt until the user confirms the prerequisites are installed.

#### 2. Revise "Step 5: Optional Setup for Semantic Search"
- The LLM will run `python wiki/.scripts/setup_search.py --check` instead of unconditionally asking the setup question.
- **If dependencies (`onnxruntime`, `tokenizers`, `numpy`, `zvec`) are missing:** 
  - The LLM will briefly use `WebSearch` to verify the latest `pip install` commands and package stability.
  - It will present the user with ready guidance: *"You're missing the semantic search and fast vector search modules. Here are the required packages: `pip install onnxruntime tokenizers numpy zvec`."*
  - It will ask the user for permission to execute this `pip install` command on their behalf and automatically proceed with `python wiki/.scripts/setup_search.py --yes` if approved.

## Rationale
- **Zero Query Overhead:** Removing checks from `wiki-query` guarantees lighting-fast response times during day-to-day operations.
- **Dynamic Accuracy:** Instructing the LLM to use `WebSearch` ensures the user receives the most current installation links (e.g., Python 3.12+ links, latest homebrew commands) instead of outdated hardcoded strings.

## Verification Plan
1. Manually simulate a `/wiki-init` execution to ensure the updated markdown prompts correctly trigger the LLM's `WebSearch`.
2. Inspect `wiki-init/SKILL.md` to ensure no syntax or formatting degrades during the update.
