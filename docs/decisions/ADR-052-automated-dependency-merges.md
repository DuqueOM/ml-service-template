# ADR-052 — Dependency updates merge on green; a human decides only where a boundary moves

- **Status**: Accepted
- **Date**: 2026-09-21
- **Deciders**: template maintainer
- **Related**: ADR-048 (co-installation groups), ADR-049 (runtime/training partition),
  D-05 (compatible-release pinning), ADR-019 (CI autofix policy)

## Context

On 2026-09-21 Dependabot had twenty-one pull requests open across two waves in
this repository alone. Reviewing them one at a time was measured: the review of
each came down to reading a version number, and the three decisions that
actually mattered were not per-PR at all.

- **Two were policy, and belonged in configuration.** `shap` cannot cross the
  numpy 1.x boundary D-05 draws, and `eslint 10` in a sibling repository needs
  a config migration. Both had been proposed repeatedly, and each repetition
  cost a review.
- **Four were red by construction, and belonged in configuration too.**
  `check_cicd_template_drift.py` requires the runtime workflows and their
  payload copies to pin identical action versions. The Dependabot entry used a
  singular `directory:` per tree, so every action bump arrived as half a
  change: #186, #187, #197 and #199 all failed that gate, and none of them was
  wrong. A comment in that file already said scanning both directories "lets
  one PR satisfy it" — the intent was recorded and the implementation was two
  entries.
- **The rest were exactly what CI is for.** #193 and #201 were refused by pip
  itself: `ResolutionImpossible`, `shap depends on numpy>=2` against the pinned
  `numpy~=1.26`. No human judgement was added by reading that twice.

Meanwhile the repository already has what makes automation safe: required
status checks on `main`, gates that measure the things a dependency can break
(pin coherence, the runtime/training partition, action drift), and a scaffold
smoke test that renders and lints a generated service.

## Decision

**A dependency update merges when the required checks pass. A human is asked
only when a boundary might move.**

1. **Auto-merge for patch, minor and digest.**
   `.github/workflows/dependabot-auto-merge.yml` reads Dependabot's metadata
   and queues `gh pr merge --auto --squash`. GitHub performs the merge only
   after every required check has passed; a failure leaves the PR open with
   the failure attached. The workflow grants no exception to the gates — it
   removes the wait, not the gate.
2. **Majors are never auto-merged.** A major asks whether a boundary should
   move, which CI cannot answer. It arrives on its own PR, outside every
   group, and waits.
3. **Grouping follows blast radius, not convenience.** Patch and minor travel
   together per ecosystem so a pin shared between files moves in one PR.
   Grouping every update-type was tried and produced #159: twenty-six packages
   in one diff, including numpy 1.26 → 2.x, and it disagreed with itself
   across lanes.
4. **One PR must be able to satisfy every gate.** Where a gate spans two
   directories, the Dependabot entry spans them too, with `directories:`.
5. **A refusal is an `ignore` entry with its reason inline.** Closing a PR
   silently means the same proposal returns next week and is reviewed again.
   The numpy and shap entries carry the measurement that justifies them.
6. **The generated service inherits all of it.**
   `templates/service/.github/dependabot.yml` and the payload's copy of the
   workflow ship the same policy, including the D-05 boundaries. An adopter
   should not have to rediscover why numpy is pinned.

## Consequences

- **Review effort moves from per-PR to per-boundary.** What reaches a human is
  a major, or a PR whose checks fail. Both are decisions.
- **The quality of the automation is now the quality of the gates.** If a gate
  does not measure what a dependency can break, auto-merge will merge that
  breakage on green. This is the real cost of the decision, and it is the
  reason the same change does not turn on auto-merge where CI cannot see:
  `github-readme-stats` has 242 tests and no workflow that runs them, so its
  updates were validated by hand and its Dependabot config is being retired
  instead.
- **`main` moves without a human in the loop.** Acceptable here because every
  merge is squashed, every required check is enforced for administrators too,
  and `MIGRATION.md` plus the release notes are written per release rather
  than per merge.
- **Majors can accumulate.** That is the intended pressure: a major left open
  is a boundary nobody has decided about, and it should be visible as such.

## What this does NOT change

- **No gate is relaxed, and no check becomes advisory.** Auto-merge waits for
  the same required contexts a human would.
- **Security updates are unaffected.** They arrive through the same lanes and
  are subject to the same checks.
- **Release discipline is unaffected.** Dependency merges land on `main`; a
  release still needs its notes, its verification table and its tag.

## Alternatives considered

- **Keep reviewing each PR.** Measured above: twenty-one PRs, one judgement
  call per boundary and none per PR. It also trains the reviewer to skim,
  which is worse than not reviewing.
- **Auto-merge everything, majors included.** The two refusals of the day
  would both have been merged into a tree where `pip install` cannot resolve.
  Kept out by CI, yes — but then the branch sits red and the next update is
  blocked behind it.
- **Scheduled batch with a single weekly review.** Closer to this decision
  than it looks, and it still spends a human on patch bumps. The batching this
  ADR keeps is inside the ecosystem groups, where it also serves the pin
  coherence gate.
