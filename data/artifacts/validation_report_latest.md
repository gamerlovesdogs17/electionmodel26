# Validation report — senate-hierarchical-v0.9.23

Generated: 2026-10-05T02:36:23.367597+00:00
Current status refreshed: 2026-10-05T02:46:34.013899+00:00
Primary holdout: 2022

## Current authoritative status

- Evidence bundle: eb-09a25672b5b14bbf
- Validated model spec: bdaa0b84f78855ff88490265eaa271d17172d5c82e5f96b54208f415fbb828c9
- Release identity: truth_v1_v0.9.23_eb-09a25672b5b14bbf
- Release identity verified: True
- Run coherence: True
- Research acceptance: True
- Promotion eligible: True
- Forecast publishable: True
- PUBLIC_LIVE_ENABLED: False
- Publication surface: research_only

## Cycle-cross-fitted production-stack reliability

- n: 260
- Brier: 0.04780733512393962
- Calibration slope: 1.0277592451274935
- Calibration intercept: -0.012766053411955226
- Flagged overconfident bins: 0

## Stack weights

```json
{
  "pymc": 0.21914105930674144,
  "state_space": 0.7808589406932586
}
```

## Exploratory legacy primary-holdout diagnostic

- n: 34
- Brier: 0.06143148553447393
- Mean 90% interval score: 46.08168227764366

## Peer release gate

```json
{
  "ok": false,
  "control_ok": false,
  "control_soft": true,
  "control_gaps": {
    "ddhq": 0.39782000000000006,
    "kalshi": 0.28871108910891097
  },
  "primary_control_gap": 0.28871108910891097,
  "max_abs_control_gap": 0.39782000000000006,
  "race_scores": {
    "ddhq": {
      "n": 9,
      "brier_vs_peer_favorite": 0.24721232777777777,
      "mae": 0.18267777777777777,
      "crps_proxy": 0.18267777777777777,
      "ok": false
    },
    "kalshi": {
      "n": 7,
      "brier_vs_peer_favorite": 0.07234866634285717,
      "mae": 0.08039167664059821,
      "crps_proxy": 0.08039167664059821,
      "ok": true
    }
  },
  "race_ok": true,
  "mean_peer_disagreement": 0.10895576394019407,
  "null_margin_races": [
    "senate-2026-AK"
  ],
  "method": "ensemble_stack+limited_validation_exception_model-v1+limited_validation_alaska_rcv_model-v1",
  "core_method": "pymc",
  "generic_ballot": 6.184921674810612,
  "reasons": [
    "control gap vs primary peer large (gap=0.289 > 0.25); gaps={'ddhq': 0.39782000000000006, 'kalshi': 0.28871108910891097} \u2014 informational under blueprint \u00a72 (peers not averaged)",
    "null margins in races: ['senate-2026-AK']"
  ],
  "thresholds": {
    "max_abs_control_gap": 0.25,
    "race_brier": 0.22,
    "race_mae": 0.22,
    "control_gap_hard_fail": false
  },
  "note": "Peers are a release gate for integrity/race calibration only \u2014 never averaged into the ensemble (blueprint \u00a72). Chamber control gap vs markets is soft/informational.",
  "path": "/home/runner/work/electionmodel26/electionmodel26/data/artifacts/peer_gate_latest.json"
}
```

## Chamber reconcile (audit P0.2)

- ok: True
- failures: []

## Poll coverage (audit P0.3)

- ok: True
- failures: []

## Acceptance gates (Milestone-0 / G1–G11)

- ok: True
- pass/partial/fail: 11/0/0
- failures: []

## Limitations

- VoteHub documents no /polls/archive — historical polls prefer FTE CC BY (Wayback/sealed polls-page).
- Licensed Cook/IE feeds are not redistributed; Wikipedia multi-rater is production ratings.
- House / Electoral College intentionally out of scope.
- fast hierarchical-t is a non-production approximation; production prefers pymc / ensemble_stack.
- Peer Brier/CRPS is an integrity diagnostic only — peers are never averaged into the ensemble.
- Pre-P0.3 cycle_replay artifacts may be non-comparable (wrong historical race universe / synthetic polls).
- Chamber reconcile uses the 2014–2024 official-ballot/certified-margin archive; the poll-coverage production gate remains 2018–2024.
- P2.1 nested-component-loo freezes before truth and prohibits model-identity weight remapping.
- PUBLIC_LIVE_ENABLED remains false; live publication requires a separate explicit future review.
