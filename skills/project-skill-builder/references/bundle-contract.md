# Generated bundle contract (schema 1)

Required files:

```text
<name>/
  SKILL.md
  agents/openai.yaml
  bundle-manifest.json
  references/project-context.md
  references/decisions.md
  references/sources.md
  references/user-overrides.md
  evals/cases.json
```

SKILL.md has YAML frontmatter with a lowercase hyphenated `name` (at most 64 characters) and a specific `description` (at most 1024 characters). Use a plain one-line or quoted description, or a simple YAML block scalar. The standard-library validator supports this restricted metadata format, not the entire YAML specification. Avoid frontmatter aliases and complex YAML. Project context records constraints, evidence, assumptions and dependencies. Decisions explain candidate strengths, conflicts and what was actually retained. Sources contain provenance and any required copied/adapted license notices. User overrides start with an empty, clearly marked space for persistent user instructions; subsequent refreshes preserve its exact bytes.

The manifest has schema_version 1, matching name, version string, sources array, stages object, and managed_files mapping relative POSIX paths to lowercase SHA-256. Each source has id, kind, url, path, license, status, reason and version `{commit, read_at}`. `license` must be verified for adopted sources. Fixture URL is null and its path identifies a controlled local fixture; public sources use HTTPS URLs. Commit is null or a verified 40-character SHA; read_at is an ISO-8601 timestamp. The script checks format and consistency, while the agent must verify the evidence itself.

`requirements`, `discovery`, `reading`, `license` use `complete`, `partial`, `not_run`, or `failed`; `static` and `behavior` use `passed`, `failed`, or `not_run`. Fixture-only input cannot establish complete public discovery. Unverified source licensing must remain visible even when a candidate was rejected.

If behavior is `passed`, add `behavior_results` pointing to a local JSON file:

```json
{"schema_version": 1, "scenarios": [{"id": "valid-input", "baseline": {"status": "passed"}, "generated": {"status": "passed"}}]}
```

This is a results format, not permission to fabricate a baseline. Add actual commands, assertions, observations and test counts. Use behavior `not_run` when there is no executed comparison matching this format; describe noncomparative checks separately in project context.

`seal` verifies the bundle and computes hashes for its generated files, excluding the manifest itself and user-overrides. Only seal a new bundle or an intentionally edited proposed version. A hash warning on an existing bundle is evidence of a manual change. Required resources and all local Markdown links must exist within the bundle; neither parent traversal nor symlinks are allowed. Windows drive paths are forbidden in the manifest. Absolute external web links are not bundle resources.

The delivered bundle is independent of source skill installations. A project-file snapshot contains hashes and relative paths, not file content or absolute root paths. Read only the explicit safe project files needed to detect future changes. The agent still needs to inspect their current meaning on refresh.
