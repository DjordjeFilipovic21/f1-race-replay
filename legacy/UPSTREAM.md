# Legacy upstream workflow

The desktop application in this directory is based on the upstream project:

<https://github.com/IAmTomShaw/f1-race-replay>

The expected remote name is `upstream`, and the upstream branch is `main`.

## Normal update procedure

From the repository root, add the upstream remote once if it is not already
configured:

```bash
git remote get-url upstream >/dev/null 2>&1 || \
  git remote add upstream https://github.com/IAmTomShaw/f1-race-replay.git
```

Then verify the remote and fetch the upstream branch:

```bash
git remote -v
git fetch upstream main
git checkout main
git merge upstream/main
```

Review the merge before continuing. Git may detect moved desktop files as
renames, but rename detection is heuristic and can be affected by later edits.
Do not assume that a detected rename preserves the intended repository layout.

After every merge, inspect both the migrated files under `legacy/` and any new
files created at the repository root. Root-level files may belong to the modern
browser replay, canonical pipeline, or contracts rather than to the legacy
desktop application, and must be classified before they are moved.
