# Validation report — senate-hierarchical-v0.9.26

Generated: 2026-10-18T20:59:07.190559+00:00
Current status refreshed: 2026-10-18T21:07:06.839981+00:00
Primary holdout: 2022

## Current authoritative status

- Evidence bundle: eb-89a17ef758cdd2d0
- Validated model spec: 707caa93f165813ee59183fb3e5805ab060d9369fd6eec539a45cbe6ab50da7d
- Release identity: truth_v1_v0.9.26_eb-89a17ef758cdd2d0
- Release identity verified: True
- Run coherence: True
- Research acceptance: True
- Promotion eligible: True
- Forecast publishable: True
- PUBLIC_LIVE_ENABLED: False
- Publication surface: research_only

## Cycle-cross-fitted production-stack reliability

- n: 260
- Brier: 0.048500573365096604
- Calibration slope: 1.0043286959808522
- Calibration intercept: -0.013994308957503315
- Flagged overconfident bins: 0

## Stack weights

```json
{
  "pymc": 0.4069741289275109,
  "state_space": 0.5930258710724892
}
```

## Exploratory legacy primary-holdout diagnostic

- n: 34
- Brier: 0.06308417721492626
- Mean 90% interval score: 46.2812025344663

## Peer release gate

```json
{
  "ok": false,
  "control_ok": false,
  "control_soft": true,
  "control_gaps": {
    "ddhq": 0.39024000000000003,
    "kalshi": 0.28113108910891094
  },
  "primary_control_gap": 0.28113108910891094,
  "max_abs_control_gap": 0.39024000000000003,
  "race_scores": {
    "ddhq": {
      "n": 9,
      "brier_vs_peer_favorite": 0.2584716881333334,
      "mae": 0.21002888888888893,
      "crps_proxy": 0.21002888888888893,
      "ok": false
    },
    "kalshi": {
      "n": 7,
      "brier_vs_peer_favorite": 0.059914580285714295,
      "mae": 0.10703705313602398,
      "crps_proxy": 0.10703705313602398,
      "ok": true
    }
  },
  "race_ok": true,
  "mean_peer_disagreement": 0.10895576394019407,
  "null_margin_races": [
    "senate-2026-AK",
    "senate-2026-MT"
  ],
  "method": "ensemble_stack+limited_validation_exception_model-v1+limited_validation_alaska_rcv_model-v1",
  "core_method": "pymc",
  "generic_ballot": 7.118492730150463,
  "reasons": [
    "control gap vs primary peer large (gap=0.281 > 0.25); gaps={'ddhq': 0.39024000000000003, 'kalshi': 0.28113108910891094} \u2014 informational under blueprint \u00a72 (peers not averaged)",
    "null margins in races: ['senate-2026-AK', 'senate-2026-MT']"
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
