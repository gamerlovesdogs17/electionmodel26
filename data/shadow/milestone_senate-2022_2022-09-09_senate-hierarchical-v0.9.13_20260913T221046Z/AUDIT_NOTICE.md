# First publishable milestone shadow (Milestone-0)

- **shadow_id:** `milestone_senate-2022_2022-09-09_senate-hierarchical-v0.9.13_20260913T221046Z`
- **cycle:** senate-2022 as-of 2022-09-09 (D−60)
- **model_version:** senate-hierarchical-v0.9.13
- **spine:** pymc (`pymc`)
- **milestone:** first_publishable
- **freeze_before_truth:** true
- **sealed:** write-once under `data/shadow/`

This is a retained frozen evaluation for independent audit — not a live public probability product. Do not overwrite sealed prediction files.

## Notes

- Shadow forecast spine is pymc (production). Nested LOO / OOF stack weights remain fast-scored until a later refresh — no silent remapping.
- Archive scope: 2018–2024 official; 2014/2016 provisional.
- Not a public live probability product.
- Acceptance gates overall ok=True (fail=[] partial=['G5', 'G9', 'G10'])
