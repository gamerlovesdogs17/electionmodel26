"""Validation package with lazy numerical replay imports."""

__all__ = ["replay_cycle", "replay_all_cycles"]


def replay_cycle(*args, **kwargs):
    from midterms.validation.cycle_replay import replay_cycle as implementation

    return implementation(*args, **kwargs)


def replay_all_cycles(*args, **kwargs):
    from midterms.validation.cycle_replay import replay_all_cycles as implementation

    return implementation(*args, **kwargs)
