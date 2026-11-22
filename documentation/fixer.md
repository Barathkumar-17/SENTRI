# Fixer

`sentri fix BUG-007` asks the model for a patch to a confirmed bug and opens a **draft** pull request only if
the patch passes a fixed gate. It never merges, never marks a bug fixed, and never pushes outside a
`sentri/fix-*` branch. A person reviews and merges.

## Steps
1. Read the bug and its latest failing evidence. The source files named in the stack traces are the context;
   without a stack trace the fixer declines.
2. Ask the model for a unified diff and a short summary.
3. Apply it in a separate git worktree.
4. Run the gate. On a pass: commit, push the branch and open a draft pull request with the gate results.

## The gate (plain code, in order, stopping at the first failure)

| Check | How |
|---|---|
| No test files edited | Checked first, on the diff's paths |
| Original failure gone | The original check, 3 attempts, must pass against the patched app |
| Held-out variant passes | The same attack with an input the fixer never saw (a timing variant for timing bugs) |
| Revert brings the failure back | Without the patch, the original check must still fail |

The fixer needs a model and, for the pull request, `git` and `gh`. `--no-pr` runs the gate without pushing.
