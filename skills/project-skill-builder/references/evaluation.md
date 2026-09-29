# Checks and small behavior comparisons

Use four separate evidence categories:

1. Static validity: metadata, required files, links, source fields, statuses and managed hashes. It does not verify remote statements or instruction quality.
2. Public retrieval: actual repository reads, relevant skill content, applicable license and version evidence. A synthetic fixture does not count.
3. Controlled scenarios: missing key policy, incompatible candidates and changed project constraints with manual edits.
4. Behavior: execution of representative tasks from the same initial state, with the same confirmed requirements, once without the generated skill and once with it.

Choose small observable checks before generating a skill: a preserved public API, dependency compliance, exact arithmetic, clear invalid-input handling or bytes preserved during refresh. Do not grade stylistic resemblance to the proposed implementation. Keep hidden assertions out of the generation prompt. Record failures as well as passes, and keep the original outputs locally; publish only a concise summary without personal paths or conversations.

A valid comparison uses separate fresh agent contexts and identical starting files. Record task wording, model/agent context limitations, actual commands, assertions and results. If both pass, report a tie. A single pair cannot establish general quality or statistical significance. If execution is unavailable, use `not_run`, explain why and provide runnable cases.

For clarity, test an unknown error policy and observe whether a focused question is asked before silently selecting a policy. For composition, offer a runtime dependency conflicting with project constraints and an error-suppression candidate conflicting with required fail-fast behavior. For refresh, change a real project requirement, preserve user-overrides, and deliberately edit a managed reference to verify that a conflicting proposal stops without overwriting it.
