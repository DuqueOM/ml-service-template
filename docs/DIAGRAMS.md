# How the template works, in four diagrams

The rest of the documentation explains this template in prose, and prose is
the right format for a contract. It is the wrong format for the first hour.
This page is the first hour: four diagrams that show what actually happens,
each followed by the files that implement it, so a reader can go from picture
to source without searching.

Every diagram describes behaviour that exists in the tree today. Where a step
is optional or an adopter has to wire it, the diagram says so rather than
drawing it as if it were already running.

- [1. From a commit to a pod you can verify](#1-from-a-commit-to-a-pod-you-can-verify)
- [2. Who the code is, and where its secrets come from](#2-who-the-code-is-and-where-its-secrets-come-from)
- [3. The loop that closes](#3-the-loop-that-closes)
- [4. What governs the agent](#4-what-governs-the-agent)

---

## 1. From a commit to a pod you can verify

The deploy chain has one property worth understanding before any other: the
thing that is signed, the thing that is scanned and the thing that is admitted
to the cluster are the **same bytes**, because every step after the build
addresses the image by its digest rather than by its tag. A tag can be moved;
a digest cannot.

```mermaid
flowchart TD
    A["git push, or a vX.Y.Z tag"] --> B["CI: lint, tests, contract check, security audit"]
    B --> C["build job authenticates by OIDC<br/>no static cloud keys anywhere"]
    C --> D["docker build and push, tagged with the version"]
    D --> E["resolve the manifest digest<br/>sha256:..."]
    E --> F["cosign sign, by digest"]
    E --> G["syft generates a CycloneDX SBOM"]
    G --> H["cosign attest the SBOM, by digest"]
    F --> I["deploy-common.yml, one reusable job per environment"]
    H --> I
    I --> J["GitHub Environment protection<br/>dev auto, staging and prod gated, prod tag-only"]
    J --> K["risk_context pre-check<br/>live signals can escalate the mode to STOP and abort"]
    K --> L["kustomize pins the overlay image to that digest"]
    L --> M["kubectl apply -k on the target overlay"]
    M --> N["Kyverno admission: digest required, signature verified"]
    N --> O["rollout status, 600s budget"]
    O --> P["smoke test: readiness, then the auth path<br/>with a deliberately wrong key"]
    P --> Q["audit entry appended to ops/audit.jsonl"]
```

Two nodes are the adopter's to wire, and the diagram would be dishonest
without saying so. **Reviewer counts** live in GitHub's Environment settings,
not in the repository: the workflow declares the environment and names the
intended posture (dev automatic, staging one reviewer, production two plus a
wait timer), and an adopter who never configures those environments gets no
approval step. The **tag-only gate on production** is enforced in the
repository, twice — once as a job condition and once as a guard inside the
deploy job. **Kyverno** admits nothing until its policies are installed in the
cluster; they ship in `templates/k8s/policies/`.

Two other steps exist because the obvious version of them failed:

- **The digest pin.** Building, signing and then deploying a *tag* leaves a
  window in which the tag can point somewhere else. The build job emits a
  `service -> digest` map, and the deploy job rewrites the overlay with it
  before applying.
- **The smoke test's second probe.** Readiness alone went green on a
  deployment whose every authenticated request failed, because readiness never
  touches the secret backend. The second probe sends a key that is
  deliberately wrong: `401` proves the secret resolved and the comparison ran,
  `503` proves it did not. CI never holds the real key.

Files: [`templates/service/.github/workflows/deploy-gcp.yml`](../templates/service/.github/workflows/deploy-gcp.yml),
[`deploy-aws.yml`](../templates/service/.github/workflows/deploy-aws.yml),
[`deploy-common.yml`](../templates/service/.github/workflows/deploy-common.yml),
[`templates/k8s/policies/kyverno-image-verification.yaml`](../templates/k8s/policies/kyverno-image-verification.yaml).

---

## 2. Who the code is, and where its secrets come from

There is no credential file in this template — not in the repository, not in
the image, not on the node. Every actor proves its identity to the cloud with
a short-lived token, and the identities are separate on purpose: the job that
detects drift cannot read the API key, and the job that retrains cannot delete
a model.

```mermaid
flowchart LR
    subgraph TF["Terraform, per environment"]
        T1["WIF pool and provider<br/>trust is conditioned on this repository"]
        T2["five purpose-scoped identities<br/>ci, deploy, runtime, drift, retrain"]
        T3["secret entries<br/>only the runtime identity is granted access"]
    end
    subgraph GH["GitHub Actions"]
        G1["OIDC token<br/>subject names the repo and the environment"]
        G2["exchange for a cloud identity<br/>WIF on GCP, AssumeRoleWithWebIdentity on AWS"]
    end
    subgraph K8S["Cluster"]
        K1["ServiceAccount annotated with the cloud identity<br/>Workload Identity or IRSA"]
        K2["pod starts with no key on disk"]
        K3["get_secret resolves the id and caches it"]
    end
    T1 --> G1
    G1 --> G2
    T2 --> G2
    T2 --> K1
    K1 --> K2
    K2 --> K3
    T3 --> K3
    K3 --> R["request served, or 503 if the backend is unreachable"]
```

The part that is easy to get wrong is the **name**. Terraform decides what a
secret is called, and the pod has to ask for the same string — on GCP the
prefix and the key are joined with `-`, on AWS with `/`. That join lives in
one function, `secret_id()`, and a contract test evaluates the Terraform
interpolations against the Kubernetes overlays so the four layers cannot
quietly choose four different names.

Failure is closed, not open: if the secret backend cannot be reached in
staging or production, the service answers `503` rather than falling back to
an unauthenticated path.

Files: [`templates/service/common_utils/secrets.py`](../templates/service/common_utils/secrets.py),
[`templates/service/infra/terraform/gcp/wif.tf`](../templates/service/infra/terraform/gcp/wif.tf),
[`templates/service/infra/terraform/aws/iam-roles-split.tf`](../templates/service/infra/terraform/aws/iam-roles-split.tf),
[`templates/service/tests/test_runtime_identity_contract.py`](../templates/service/tests/test_runtime_identity_contract.py),
[`docs/decisions/ADR-051-runtime-identity-and-secret-addressing.md`](decisions/ADR-051-runtime-identity-and-secret-addressing.md).

---

## 3. The loop that closes

A monitoring setup that only emits metrics is an open loop: it can tell you
that something changed, but nothing downstream is obliged to act. This one
closes, and it closes through artefacts a human can read — a GitHub issue, a
quality-gate report, a champion-versus-challenger decision — rather than
through an automatic push to production.

```mermaid
flowchart TD
    R["POST /predict"] --> S["inference on a ThreadPoolExecutor<br/>the event loop is never blocked"]
    S --> T["prediction logger writes the event to object storage"]
    S --> U["Prometheus metrics, SLO rules, AlertManager"]
    T --> V["ground-truth ingester, daily"]
    V --> W["performance monitor, daily<br/>sliced metrics pushed to the gateway"]
    T --> X["drift job, daily<br/>PSI over quantile bins"]
    X -->|"drift above threshold"| Y["a GitHub issue is opened"]
    W -->|"sliced performance degrades"| Y
    Y --> Z["retrain workflow, dispatched by a human"]
    Z --> AA["Pandera validation, training, quality gates<br/>primary metric, fairness ratio, leakage check"]
    AA --> AB["champion versus challenger, statistically compared"]
    AB -->|"promote"| AC["model signed and registered<br/>re-enters diagram 1"]
    AB -->|"keep or blocked"| AD["promotion refused, decision recorded"]
```

The fairness gate uses a disparate impact ratio with a floor of `0.80` — the
US four-fifths rule. The generated service's `fairness.py` says plainly that
this is a starting point and not a universal threshold, and lists what the
metric cannot see: it is a group-level measure, it is unreliable on small
subgroups, and passing it does not by itself make a model fair.

Files: [`templates/service/k8s/base/cronjob-drift.yaml`](../templates/service/k8s/base/cronjob-drift.yaml),
[`cronjob-performance.yaml`](../templates/service/k8s/base/cronjob-performance.yaml),
[`templates/service/.github/workflows/drift-detection.yml`](../templates/service/.github/workflows/drift-detection.yml),
[`retrain-service.yml`](../templates/service/.github/workflows/retrain-service.yml),
[`templates/service/scripts/promote_model.sh`](../templates/service/scripts/promote_model.sh).

---

## 4. What governs the agent

The agentic surface is not load-bearing. Agents make the work faster; the
gates decide what ships. Everything an agent reads is generated from one
canonical tree, and a check fails CI when a generated adapter stops matching
its source — so a rule cannot be softened in one IDE's copy without the
difference showing up in a diff.

```mermaid
flowchart TD
    A["AGENTS.md<br/>canonical policy and the anti-pattern catalogue"] --> B["templates/config/agentic_manifest.yaml"]
    B --> C["agentic/rules"]
    B --> D["agentic/skills"]
    B --> E["agentic/workflows"]
    C --> F["generated adapters<br/>.claude/rules, .claude/skills, .cursor/rules, payload mirror"]
    D --> F
    E --> F
    F --> G["adapter sync check<br/>a drifted adapter fails CI"]
    A --> H["deterministic gates, run by make verify"]
    H --> I["doc coherence, traceable control claims, pin shape,<br/>deploy contract, payload scope, render safety, and more"]
    I --> J["CI lanes: Validate Templates, Policy Tests, Template-Context Tests"]
    G --> J
    J --> K["what ships is what the gates allowed"]
```

The same idea applies to the documentation you are reading. The coherence gate
reconciles claims that appear in more than one document — the version, the
anti-pattern count, the agentic surface counts, ADR numbering — against the
tree, so a number stated on this page cannot drift away from the directory it
describes without CI saying so.

Files: [`AGENTS.md`](../AGENTS.md),
[`templates/config/agentic_manifest.yaml`](../templates/config/agentic_manifest.yaml),
[`scripts/sync_agentic_adapters.py`](../scripts/sync_agentic_adapters.py),
[`scripts/check_doc_coherence.py`](../scripts/check_doc_coherence.py),
[`Makefile`](../Makefile).

---

## Where to go next

| You want to... | Go to |
| ---------------- | ------- |
| Run something in ten minutes | [QUICK_START.md](../QUICK_START.md) |
| Read the contract behind diagram 1 | [RUNBOOK.md](../RUNBOOK.md) |
| Read the contract behind diagram 2 | [docs/runbooks/gcp-wif-setup.md](runbooks/gcp-wif-setup.md), [aws-irsa-setup.md](runbooks/aws-irsa-setup.md) |
| Read the contract behind diagram 4 | [AGENTS.md](../AGENTS.md) |
| See why each decision was made | [docs/decisions/](decisions/) |
