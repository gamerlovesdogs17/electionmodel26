"""Push a generated artifact commit with fail-closed transient retry handling.

Research rebuilds can create a moderately large Git pack.  A remote HTTP
timeout is ambiguous: the ref may have been updated even though ``git push``
returned nonzero.  This helper verifies the remote ref after every attempt,
accepts only the exact local commit, retries known transport failures, and
stops if another commit moves the destination branch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections.abc import Sequence
from typing import Any


class GitPushError(RuntimeError):
    """Raised when an artifact commit cannot be pushed without losing lineage."""


_TRANSIENT_MARKERS = (
    "http 408",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "rpc failed",
    "unexpected disconnect",
    "remote end hung up unexpectedly",
    "connection reset",
    "connection timed out",
    "operation timed out",
    "could not resolve host",
    "failed to connect",
)


def _git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part).strip()


def _required_output(args: Sequence[str], label: str) -> str:
    result = _git(args)
    if result.returncode != 0:
        raise GitPushError(f"{label} failed: {_combined_output(result)}")
    value = result.stdout.strip()
    if not value:
        raise GitPushError(f"{label} returned no value")
    return value


def _remote_head(remote: str, branch: str) -> tuple[str | None, str]:
    result = _git(["ls-remote", "--exit-code", remote, f"refs/heads/{branch}"])
    output = _combined_output(result)
    if result.returncode != 0:
        return None, output
    first = result.stdout.strip().splitlines()[0]
    sha = first.split()[0] if first.split() else ""
    return (sha or None), output


def _is_transient(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


def push_head_with_retry(
    *,
    remote: str,
    branch: str,
    expected_remote_sha: str,
    attempts: int = 5,
    base_delay_seconds: float = 5.0,
) -> dict[str, Any]:
    """Push HEAD and prove the destination ref equals it.

    ``expected_remote_sha`` is the evidence commit checked out by the rebuild.
    The branch may remain at that commit between retries or advance to the
    exact local artifact commit.  Any other ref is a concurrent change and
    aborts the operation.
    """
    if attempts < 1:
        raise ValueError("attempts must be positive")
    if base_delay_seconds < 0:
        raise ValueError("base_delay_seconds cannot be negative")

    local_sha = _required_output(["rev-parse", "HEAD"], "local HEAD lookup")
    transient_failures: list[dict[str, Any]] = []

    for attempt in range(1, attempts + 1):
        remote_before, lookup_error = _remote_head(remote, branch)
        if remote_before == local_sha:
            return {
                "ok": True,
                "local_sha": local_sha,
                "remote_sha": remote_before,
                "attempts": attempt - 1,
                "accepted_after_ambiguous_failure": bool(transient_failures),
                "transient_failures": transient_failures,
            }
        if remote_before is None:
            if attempt == attempts or not _is_transient(lookup_error):
                raise GitPushError(f"remote HEAD lookup failed: {lookup_error}")
            failure = {
                "attempt": attempt,
                "stage": "remote_lookup",
                "message": lookup_error,
            }
            transient_failures.append(failure)
        elif remote_before != expected_remote_sha:
            raise GitPushError(
                "destination branch diverged before artifact push: "
                f"expected {expected_remote_sha}, found {remote_before}"
            )
        else:
            push = _git(
                [
                    "-c",
                    "http.version=HTTP/1.1",
                    "-c",
                    "http.lowSpeedLimit=0",
                    "push",
                    "--porcelain",
                    remote,
                    f"HEAD:refs/heads/{branch}",
                ]
            )
            push_output = _combined_output(push)
            if push_output:
                print(push_output, flush=True)

            remote_after, after_error = _remote_head(remote, branch)
            if remote_after == local_sha:
                return {
                    "ok": True,
                    "local_sha": local_sha,
                    "remote_sha": remote_after,
                    "attempts": attempt,
                    "accepted_after_ambiguous_failure": push.returncode != 0,
                    "transient_failures": transient_failures,
                }
            if remote_after not in (None, expected_remote_sha):
                raise GitPushError(
                    "destination branch diverged during artifact push: "
                    f"expected {expected_remote_sha} or {local_sha}, found {remote_after}"
                )
            failure_output = "\n".join(
                value for value in (push_output, after_error) if value
            )
            if push.returncode == 0 and remote_after is not None:
                raise GitPushError(
                    "git push returned success but the destination ref did not advance"
                )
            if attempt == attempts or not _is_transient(failure_output):
                raise GitPushError(
                    f"artifact push failed on attempt {attempt}: {failure_output}"
                )
            transient_failures.append(
                {
                    "attempt": attempt,
                    "stage": "push",
                    "message": failure_output,
                }
            )

        delay = min(60.0, base_delay_seconds * (2 ** (attempt - 1)))
        print(
            f"Transient Git transport failure; retrying attempt {attempt + 1}/{attempts} "
            f"after {delay:g}s.",
            flush=True,
        )
        time.sleep(delay)

    raise GitPushError("artifact push exhausted retries")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Push an artifact commit with transient retry and exact ref verification."
    )
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--expected-remote-sha", required=True)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--base-delay-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)
    report = push_head_with_retry(
        remote=args.remote,
        branch=args.branch,
        expected_remote_sha=args.expected_remote_sha,
        attempts=args.attempts,
        base_delay_seconds=args.base_delay_seconds,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the workflow
    raise SystemExit(main())
