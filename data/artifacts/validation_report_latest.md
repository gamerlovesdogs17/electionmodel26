# Validation report — senate-hierarchical-v0.9

Generated: 2026-09-13T16:56:45.705695+00:00
Primary holdout: 2022

## Stack weights

```json
{
  "last_election_swing": 0.1264075367822936,
  "equal_weight_polls": 0.15608110823084928,
  "shrinkage_polls": 0.17591829122496674,
  "fast_hierarchical_t": 0.18104159247789697,
  "state_space": 0.1940869161670225,
  "poll_only_state_space": 3.2863477009926e-05,
  "ridge_fundamentals": 0.16643169163996094
}
```

## Calibration (60-day lead)

- n: 33
- Brier: 0.03719729638687999
- Mean 90% interval score: 23.25002385480888

## Limitations

- VoteHub documents no /polls/archive — historical polls prefer FTE CC BY Datasette.
- Licensed Cook/IE feeds are not redistributed; Wikipedia multi-rater is production ratings.
- House / Electoral College intentionally out of scope.
- fast hierarchical-t is a non-production approximation; production prefers pymc / ensemble_stack.
