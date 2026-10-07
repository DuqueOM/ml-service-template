# README standard

**Standard version: 1** · Shared, byte for byte, by `ml-service-template` and
`ml-platform`. A change to it is made in both repositories, in the same
session, with the version bumped — a standard that differs between the two
repositories it governs is two standards.

## Why the README has a standard at all

The README is the one document every visitor reads, usually first and often
only. Both repositories are templates in the sense that matters here: someone
adopts them and builds on what the README says exists. That makes the README
the most consequential claim either repository makes, and the least checked.

The failure this standard exists to prevent was measured, not imagined. In
October 2026 one README was still describing its first day, two months and 175
commits later — "Phase 0, the projects are not built yet" over three working
projects — while the other had grown to 5,700 words that repeated the
documents it linked to. One understated what existed and the other buried it,
and neither was wrong on purpose. Nothing read either README against anything.

## What a README must answer

GitHub's guidance on READMEs names the questions a visitor brings: what the
project does, why it is useful, how to get started, where to get help, and who
maintains it. These repositories add one, because they are adopted rather than
merely used: **what exists today, as opposed to what is planned — and how a
reader can check.**

Everything else belongs in `docs/`, organised by what the reader is trying to
do (the Diátaxis split: tutorials, how-to guides, reference, explanation), and
the README links to it rather than repeating it.

## Required structure

These sections, as level-2 headings with exactly these names, in this order.
Nothing else at level 2. Level-3 headings inside a section are allowed.

| # | Heading | What it holds |
| --- | --- | --- |
| — | `# <repository name>` | The title, then a single-line `>` blockquote under 120 characters: what this is, for whom. Then badges, if any |
| 1 | `## Status` | A **generated** block between `<!-- BEGIN README STATUS -->` and `<!-- END README STATUS -->`: what is proven, at which evidence layer, and when the last independent audit ran. Derived from the repository's source of truth by its README check, never typed |
| 2 | `## What it is` | Why it exists, who it is for, and who it is **not** for. The scope boundary is part of the answer |
| 3 | `## Quick start` | At most five shell commands, copy-paste runnable, each version pinned — or bounded exactly as the repository's own CI installs it, so the quick start runs what was verified |
| 4 | `## What you get` | A table of capabilities. Every row names the document or gate that shows it is real |
| 5 | `## Architecture` | One diagram or layout tree, the rule that holds it together, and links to the decisions behind it |
| 6 | `## How claims are verified` | The gates, the independent audit, and the evidence layers (L1 contract · L2 component · L3 cluster · L4 cloud), linked, not re-explained |
| 7 | `## Documentation` | A map of `docs/` by purpose: tutorials, how-to guides, reference, explanation and decisions |
| 8 | `## Related repositories` | The canonical table below, verbatim |
| 9 | `## Contributing, security and support` | Where to contribute, report a vulnerability, get help, and who maintains it |
| 10 | `## License` | The licence, linked |

## Rules for what the sections say

1. **Present tense is for what exists.** A planned capability is named as
   planned and links to where it is planned. A README that describes the
   intended system as if it were built is the failure above.
2. **Every number is generated or gated.** A count, a version, a percentage or
   a date in the README either comes from the generated status block or is
   checked by a gate. A typed number is a number that will be wrong.
3. **Link, do not repeat.** A section summarises in a few lines and links to
   the document that holds the detail. If a paragraph would make sense as the
   opening of a `docs/` page, it belongs there.
4. **Badges report checks, not adjectives.** Allowed: a workflow's status, the
   latest release, the licence, the supported Python versions — and a badge
   showing a number only where a gate in the same repository already compares
   that number with its source, named in the README check with the gate that
   holds it. Anything else states a claim, and a claim belongs in the text
   where a gate can read it. At most six.
5. **A budget, because length is how a README stops being read.** At most 250
   lines and 2,000 words, the generated block included.
6. **Relative links for this repository's files**, so they resolve on GitHub,
   in a clone and in a fork alike.

## Related repositories

Both READMEs carry this table exactly as written here.

<!-- BEGIN RELATED REPOSITORIES -->
| Repository | Role |
| --- | --- |
| [ML-MLOps-Portfolio](https://github.com/DuqueOM/ML-MLOps-Portfolio) | End-to-end tabular ML services where these patterns were first proven; ml-service-template was extracted from it |
| [ml-service-template](https://github.com/DuqueOM/ml-service-template) | A governed Copier template for **one** tabular ML service on Kubernetes, on GKE and EKS |
| [ml-platform](https://github.com/DuqueOM/ml-platform) | A multi-project ML platform template: shared libraries, orchestration and governance under projects of different kinds. It consumes ml-service-template for serving, and where the two disagree on serving, containers, manifests or supply chain, ml-service-template is authoritative |
| [agent-local](https://github.com/DuqueOM/agent-local) | A local multi-tier LLM agent core with a deterministic policy gate. Its core moved into ml-platform, which is now authoritative; agent-local is a one-way export of it |
<!-- END RELATED REPOSITORIES -->

## Enforcement

Each repository runs a README check in CI. Its logic lives in
`scripts/readme_standard.py`, byte-identical in both repositories like this
file; each repository's `scripts/check_readme.py` adds only its own status
sources. It reads the README's structure the way CommonMark does — ATX, setext
and HTML headings, headings nested in lists or quotes, inline, reference and
HTML images, fenced and indented code — and fails when:

- a required section is missing, renamed, out of order, joined by another
  level-2 section in any spelling, or written as anything but an ATX `##` heading;
- the status block is absent or differs from what the check generates now;
- the related-repositories table differs from the one in this file;
- a badge is not one of the allowed kinds, sits outside the title area, or
  there are more than the rule allows;
- the quick start runs more commands than the rule allows — counting every
  command a line chains with `&&`, `||`, `;` or a pipe — or installs or runs a
  tool without a version, clones without naming a ref, or pipes into a shell;
- the README exceeds the line or word budget.

Every limit is read from this file's text, not restated in the checker, and
this file is pinned by its SHA-256 in `scripts/readme_standard.py`: an edit
here fails both repositories until the pin is updated in both, which is what
"shared byte for byte" means in practice.

The check also regenerates the status block and the related table
(`--write`), so keeping them current is one command, not an edit.
