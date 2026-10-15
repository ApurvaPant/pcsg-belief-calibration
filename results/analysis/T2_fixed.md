# T2 corrected (fixed persona-id attribution) - run main_20261015_000553

n_fit=459 n_test=459 | personas 60 (24/24/12) | label A (default)

| signal | recalibrator | ECE(10) | ECE(15q) | MCE | Brier | NLL | AUROC |
|---|---|---|---|---|---|---|---|
| verbalized | raw | 0.055 | 0.055 | 0.3 | 0.1033 | 0.3677 | 0.4487 |
| verbalized | temperature | 0.0559 | 0.0559 | 0.3158 | 0.1045 | 0.3691 | 0.4487 |
| verbalized | platt | 0.0188 | 0.0188 | 0.022 | 0.0937 | 0.3323 | 0.5513 |
| verbalized | isotonic | 0.0109 | 0.0 | 0.0109 | 0.0938 | 0.3356 | 0.5 |
| self_consistency | raw | 0.0777 | 0.0719 | 0.3208 | 0.0922 | 0.8805 | 0.6802 |
| self_consistency | temperature | 0.0311 | 0.0174 | 0.1888 | 0.0869 | 0.3038 | 0.6802 |
| self_consistency | platt | 0.0224 | 0.0073 | 0.1084 | 0.0843 | 0.2987 | 0.6802 |
| self_consistency | isotonic | 0.0365 | 0.0255 | 0.3591 | 0.0925 | 0.3176 | 0.6802 |
| evidence | raw | 0.0658 | 0.0875 | 0.0658 | 0.0997 | 0.4003 | 0.4487 |
| evidence | temperature | 0.0323 | 0.0323 | 0.2048 | 0.0972 | 0.3489 | 0.4487 |
| evidence | platt | 0.0175 | 0.0175 | 0.021 | 0.0937 | 0.3326 | 0.5513 |
| evidence | isotonic | 0.0109 | 0.0 | 0.0109 | 0.0938 | 0.3356 | 0.5 |
