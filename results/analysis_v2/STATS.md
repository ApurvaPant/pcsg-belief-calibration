# STATS — significance & recalibration robustness (analysis_v2)

Source: results/runs/main_20261015_000553/labelled.jsonl (read-only). OFFLINE; no API calls.
test n=459, fit n=459, fit+test personas=48.

## 2a. Persona-clustered PAIRED bootstrap — dAUROC = self_consistency - verbalized (test, 10000 reps, seed 2026)

- rule A: observed dAUROC = **0.2315**, 95% CI [0.1539, 0.3076], one-sided p(SC<=verb) = **0.0** (10000 reps)
- rule C: observed dAUROC = **0.1757**, 95% CI [0.0631, 0.2821], one-sided p(SC<=verb) = **0.0012** (10000 reps)

## 2b. Brier(Platt self_consistency) - base-rate Brier (test; persona-cluster bootstrap CI)
- Brier(Platt SC) = **0.0843**, base-rate Brier (p=0.8954) = **0.0936**
- difference = **-0.0094**, 95% CI [-0.0201, +0.0008] (negative = Platt SC beats the base rate)

## 2c. DeLong test — self_consistency vs verbalized AUROC (test)
- **sensitivity only (assumes independent samples)**: AUC(SC)=0.6802, AUC(verb)=0.4487, z=3.9375, p(two-sided)=8.232e-05

## 2d. QA (264 questions) — exact McNemar + cluster bootstrap
- **SKIPPED: only aggregate QA records exist** (results/runs/main_20261015_000553/qa_systems.json has per-system accuracy/coverage/sweep but no per-question correctness). Per-item Tier-2 answers and verdicts were not persisted, so exact McNemar PCSG-vs-RAG / PCSG-vs-full cannot be computed.

## 3. Recalibration robustness — leave-one-persona-out Platt (fit+test pooled, 48 folds)

| signal | OOF n | folds | Brier | ECE(15 eq-mass) | AUROC | original fit->test (Platt) |
|---|---|---|---|---|---|---|
| self_consistency | 918 | 48 | 0.0808 | 0.0327 | 0.6242 | Brier 0.0843, ECE 0.0224, AUROC 0.6802 |
| verbalized | 918 | 48 | 0.0973 | 0.0548 | 0.5338 | Brier 0.0937, ECE 0.0188, AUROC 0.5513 |
