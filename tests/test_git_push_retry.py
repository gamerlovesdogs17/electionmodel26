from __future__ import annotations

import subprocess

import pytest

from midterms.ops import git_push

LOCAL = "b" * 40
BASE = "a" * 40
OTHER = "c" * 40


def _result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(["git"], returncode, stdout, stderr)


def _fake_git(*, remote_heads: list[str], pushes: list[subprocess.CompletedProcess[str]]):
    heads = iter(remote_heads)
    push_results = iter(pushes)

    def run(args):
        if args[:2] == ["rev-parse", "HEAD"]:
            return _result(stdout=f"{LOCAL}\n")
        if args[:2] == ["ls-remote", "--exit-code"]:
            return _result(stdout=f"{next(heads)}\trefs/heads/main\n")
        if "push" in args:
            return next(push_results)
        raise AssertionError(args)

    return run


def test_transient_timeout_retries_and_verifies_exact_remote(monkeypatch):
    monkeypatch.setattr(
        git_push,
        "_git",
        _fake_git(
            remote_heads=[BASE, BASE, BASE, LOCAL],
            pushes=[
                _result(1, stderr="error: RPC failed; HTTP 408\nfatal: remote hung up"),
                _result(0, stdout="ok"),
            ],
        ),
    )
    monkeypatch.setattr(git_push.time, "sleep", lambda _: None)

    report = git_push.push_head_with_retry(
        remote="origin", branch="main", expected_remote_sha=BASE,
    )

    assert report["ok"] is True
    assert report["attempts"] == 2
    assert len(report["transient_failures"]) == 1


def test_ambiguous_push_failure_is_success_only_when_remote_has_local_commit(monkeypatch):
    monkeypatch.setattr(
        git_push,
        "_git",
        _fake_git(
            remote_heads=[BASE, LOCAL],
            pushes=[_result(1, stderr="RPC failed; HTTP 408")],
        ),
    )

    report = git_push.push_head_with_retry(
        remote="origin", branch="main", expected_remote_sha=BASE,
    )

    assert report["accepted_after_ambiguous_failure"] is True
    assert report["remote_sha"] == LOCAL


def test_concurrent_branch_change_fails_closed(monkeypatch):
    monkeypatch.setattr(
        git_push,
        "_git",
        _fake_git(remote_heads=[OTHER], pushes=[]),
    )

    with pytest.raises(git_push.GitPushError, match="diverged"):
        git_push.push_head_with_retry(
            remote="origin", branch="main", expected_remote_sha=BASE,
        )


def test_non_transient_rejection_is_not_retried(monkeypatch):
    calls = {"push": 0}
    fake = _fake_git(
        remote_heads=[BASE, BASE],
        pushes=[_result(1, stderr="remote: permission denied")],
    )

    def count(args):
        if "push" in args:
            calls["push"] += 1
        return fake(args)

    monkeypatch.setattr(git_push, "_git", count)

    with pytest.raises(git_push.GitPushError, match="attempt 1"):
        git_push.push_head_with_retry(
            remote="origin", branch="main", expected_remote_sha=BASE,
        )
    assert calls["push"] == 1
