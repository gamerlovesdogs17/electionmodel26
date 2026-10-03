# Validation report — senate-hierarchical-v0.9.22

Generated: 2026-10-03T20:30:34.584941+00:00
Primary holdout: 2022

## Stack weights

```json
{
  "pymc": 0.21914105930674144,
  "state_space": 0.7808589406932586
}
```

## Calibration (60-day lead)

- n: 34
- Brier: 0.06143148553447393
- Mean 90% interval score: 46.08168227764366

## Peer release gate

```json
{
  "ok": true,
  "control_ok": true,
  "control_soft": true,
  "control_gaps": {
    "ddhq": 0.28312000000000004,
    "kalshi": 0.17401108910891094
  },
  "primary_control_gap": 0.17401108910891094,
  "max_abs_control_gap": 0.28312000000000004,
  "race_scores": {
    "ddhq": {
      "n": 10,
      "brier_vs_peer_favorite": 0.23011602932000003,
      "mae": 0.174254,
      "crps_proxy": 0.174254,
      "ok": false
    },
    "kalshi": {
      "n": 8,
      "brier_vs_peer_favorite": 0.12122831174999998,
      "mae": 0.12009587920546376,
      "crps_proxy": 0.12009587920546376,
      "ok": true
    }
  },
  "race_ok": true,
  "mean_peer_disagreement": 0.10895576394019407,
  "null_margin_races": [],
  "method": "ensemble_stack",
  "core_method": "pymc",
  "generic_ballot": 4.413521232216102,
  "reasons": [],
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
- pass/partial/fail: 10/0/1
- failures: ['G10', 'COHERENCE']

## Limitations

- VoteHub documents no /polls/archive — historical polls prefer FTE CC BY (Wayback/sealed polls-page).
- Licensed Cook/IE feeds are not redistributed; Wikipedia multi-rater is production ratings.
- House / Electoral College intentionally out of scope.
- fast hierarchical-t is a non-production approximation; production prefers pymc / ensemble_stack.
- Peer Brier/CRPS is a release gate only — peers are never averaged into the ensemble.
- Pre-P0.3 cycle_replay artifacts may be non-comparable (wrong historical race universe / synthetic polls).
- Chamber reconcile + poll coverage gates (2018–2024) are required before treating holdout scores as validated.
- P2.1 nested-component-loo: freeze-before-truth; no fast→pymc weight remapping.
- Milestone-0 archive scope is 2018–2024; 2014/2016 provisional. Public live probabilities wait on gate green + pymc shadow.
