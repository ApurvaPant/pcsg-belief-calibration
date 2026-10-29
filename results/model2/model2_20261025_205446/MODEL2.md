# T-MODEL2 — second extraction model (STEP 4)

**model: gpt-oss-20b (Nemotron 3 Super withdrawn from the hosted API on 2026-10-25)**

Same 12 test personas (seed 2026): [27, 31, 33, 34, 36, 37, 38, 39, 40, 41, 45, 47]

Assertions stored: gpt-oss-20b = 221 | Nemotron (main, same 12) = 229

Rule-A/C/E accuracy — gpt-oss: {'A': 0.905, 'C': 0.9729, 'E': 0.9774} | Nemotron: {'A': 0.9083, 'C': 0.9432, 'E': 0.9956}

## Per-signal calibration (rule A; persona-paired 95% CI)

| model | signal | n | accuracy | ECE | Brier | AUROC [95% CI] |
|---|---|---|---|---|---|---|
| gpt-oss-20b | verbalized | 221 | 0.905 | 0.0774 | 0.0942 | 0.4321 [0.3797, 0.4799] |
| gpt-oss-20b | self_consistency | 221 | 0.905 | 0.0859 | 0.084 | 0.7418 [0.6353, 0.8357] |
| Nemotron(main) | verbalized | 229 | 0.9083 | 0.0498 | 0.0943 | 0.4222 [0.3359, 0.4986] |
| Nemotron(main) | self_consistency | 229 | 0.9083 | 0.0728 | 0.0864 | 0.668 [0.5687, 0.7634] |

## Key question: does verbalized < self-consistency (AUROC) still hold?

- **gpt-oss-20b:** AUROC(self_consistency) − AUROC(verbalized) median = 0.3096, 95% CI [0.1864, 0.4115], P(gap>0) = 1.0
- **Nemotron (main, same 12):** median = 0.2481, 95% CI [0.0987, 0.3996], P(gap>0) = 1.0

A positive gap (CI excluding 0) = self-consistency separates correct from wrong better than verbalized confidence — the main study's finding. This table states whether it replicates under a different extraction model.
