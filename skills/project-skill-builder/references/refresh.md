# Refresh without losing user changes

Refresh is manual and requested by the user. Keep the installed bundle and its old managed hashes as the baseline. Re-read current project contracts and source evidence. Generate a new proposed bundle elsewhere, then seal and validate it. Do not modify the current bundle during generation.

If an old snapshot exists, produce a new explicit-file snapshot and compare:

```text
python <this-skill>/scripts/project_snapshot.py compare --before <old.json> --after <new.json>
python <this-skill>/scripts/refresh_bundle.py <current> <proposed>
```

Changes identify what to re-read; they do not decide policy by themselves. For example, switching monetary units from decimal currency units to integer cents changes parsing, arithmetic and expected outputs. Update the new workflow and cases together. Keep the previous decision history in the backup rather than appending an ever-growing prompt.

The refresh script compares the old managed hash, current local bytes, and proposed bytes. Unedited generated files may be updated or removed. User-overrides always survives. User-added files survive unless the new bundle needs the same path, which is a conflict. If a user edited or deleted a managed file and the proposal also changes it, the entire apply stops. Local edits with an unchanged proposal remain local edits against the old baseline hash.

Preview writes nothing. `--apply` creates an external history backup before replacing a nonconflicting bundle; it may save a separate candidate on conflict while leaving current files intact. Read its JSON status, conflicts, preserved paths and backup path. Show changes and resolve conflicts explicitly with the user. Never reseal the current bundle to make a conflict disappear. Do not auto-refresh on every ordinary project task.
