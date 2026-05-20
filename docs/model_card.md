# Classifier Model Card

- **Base model:** microsoft/deberta-v3-small
- **Task:** Issue classification (bug/feature/docs/question)
- **Training data:** 25384 oversampled Terraform issues
- **Validation data:** 1373 issues (original, not oversampled)
- **Hyperparameters:** effective batch=16, lr_enc=2e-05, lr_head=2e-05, epochs=3
- **Validation accuracy:** 0.6605972323379461
- **Validation macro‑F1:** 0.19890350877192983
