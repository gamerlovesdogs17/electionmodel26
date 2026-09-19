# Validation report — senate-hierarchical-v0.9.21

Generated: 2026-09-19T19:05:07.968335+00:00
Primary holdout: 2022

## Stack weights

```json
{
  "last_election_swing": 0.19466485083338164,
  "equal_weight_polls": 0.032613878705476926,
  "shrinkage_polls": 0.01333823271636273,
  "pymc": 0.10682012920103916,
  "state_space": 0.2103163494311376,
  "poll_only_state_space": 7.018999103367987e-05,
  "ridge_fundamentals": 0.44217636912156827
}
```

## Calibration (60-day lead)

- n: 34
- Brier: 0.05691536629815605
- Mean 90% interval score: 43.28371220551403

## Peer release gate

```json
{
  "ok": true,
  "control_ok": false,
  "control_soft": true,
  "control_gaps": {
    "ddhq": 0.356375,
    "kalshi": 0.31657301980198016
  },
  "primary_control_gap": 0.31657301980198016,
  "max_abs_control_gap": 0.356375,
  "race_scores": {
    "ddhq": {
      "n": 10,
      "brier_vs_peer_favorite": 0.22321035156250005,
      "mae": 0.12411249999999999,
      "crps_proxy": 0.12411249999999999,
      "ok": false
    },
    "kalshi": {
      "n": 10,
      "brier_vs_peer_favorite": 0.15701035156249998,
      "mae": 0.11267341824598616,
      "crps_proxy": 0.11267341824598616,
      "ok": true
    }
  },
  "race_ok": true,
  "mean_peer_disagreement": 0.15949111626578813,
  "null_margin_races": [],
  "method": "ensemble_stack+overlays",
  "core_method": "pymc",
  "generic_ballot": 5.852513615064197,
  "reasons": [
    "control gap vs primary peer large (gap=0.317 > 0.25); gaps={'ddhq': 0.356375, 'kalshi': 0.31657301980198016} \u2014 informational under blueprint \u00a72 (peers not averaged)"
  ],
  "thresholds": {
    "max_abs_control_gap": 0.25,
    "race_brier": 0.22,
    "race_mae": 0.22,
    "control_gap_hard_fail": false
  },
  "note": "Peers are a release gate for integrity/race calibration only \u2014 never averaged into the ensemble (blueprint \u00a72). Chamber control gap vs markets is soft/informational.",
  "path": "C:\\Users\\zydlo\\OneDrive\\Desktop\\electionmodel26\\data\\artifacts\\peer_gate_latest.json"
}
```

## Chamber reconcile (audit P0.2)

- ok: True
- failures: []

## Poll coverage (audit P0.3)

- ok: True
- failures: []

## Acceptance gates (Milestone-0 / G1–G11)

- ok: False
- pass/partial/fail: 7/2/2
- failures: ['G8', 'G10']

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
