"""What state a crashed Mac-side recovery left, read from local evidence alone.

`07b` criterion 4: *"Crash injection **after the exchange response and before,
during, and after** the emergency write; each leaves a state the next run can
classify correctly."* This module is the classifier that criterion is about, and
`tests/test_link_crash.py` is the injection.

**The defect it closes is a sentence, not a missing feature.** Before this module
the next run's only reading of an existing artifact was
:meth:`EmergencyArtifactSink.prepare`'s refusal — *"an artifact already exists at
{path}; it will not be overwritten. Move it aside — it may hold an earlier
recovery"* — and that one message covers two states whose correct handling is
**opposite**:

* the artifact opens and is complete, so the credential is durable and the owner
  must restore from it and must **not** exchange again; and
* the artifact does not open, so the exchange may have happened and this file
  will never yield the credential.

Telling the owner to *move aside* a file in the first state is the worse half,
because the advice is followed by a re-run, and a re-run means a second exchange.
This project measured what a second exchange does (`06a` (ii), Sandbox
2026-09-16/17, recorded on row `07b`): the duplicate was **ACCEPTED** and the
first `access_token` stayed healthy. So the wire does not protect the owner here —
at-most-once is what we assumed Plaid enforces, not what was observed — and
whether an accepted duplicate consumes a second lifetime Item is *unmeasured*,
which is exactly the cost **F2a** exists to prevent. A recovery procedure that
recommends the re-run is therefore the same defect `07b` was created to close,
arriving through the recovery path instead of the original one.

**How many honest classes there are, measured rather than argued.** The criterion
names three injection points; they do not produce three classes, and the place
they collapse is *not* where `06a` (iii) found its collapse:

* **during** and **after** the write are distinguishable, unlike the VPS flow's
  first two boundaries. The artifact is the evidence and openability under the
  escrowed key is a durable property of it, so **after** the write is
  :attr:`CrashState.CREDENTIAL_DURABLE` — a proof, since the credential is read
  back out.

  **During** the write is weaker than this module first claimed, and PR #136's
  review (F1) is what measured the difference. A torn envelope fails its tag
  comparison, and so does a well-formed but *wrong* 32-byte backup key, and so
  does a file that is not one of `03a`'s envelopes at all: one comparison, three
  causes, **and nothing on this disk separates them.** So it lands in
  :attr:`CrashState.ARTIFACT_UNVERIFIED` and not in
  :attr:`CrashState.CREDENTIAL_LOST`. Calling it loss was not a pessimistic
  rounding of an unknown — it was the *expensive* rounding: the owner acts on
  "this will never give up a credential" by giving up on the file, and the
  reproduction restored access and the correct key afterwards and got
  :attr:`CrashState.CREDENTIAL_DURABLE` back out of the identical bytes.
  :attr:`CrashState.CREDENTIAL_LOST` now requires that the envelope *opened*,
  which is the only evidence that proves the content rather than the attempt.
* **before** the write collapses with *before the exchange*, and that pair is
  irreducible here. Both leave the recovery record present and no artifact, and
  **the Mac keeps no attempt log** — where the VPS can reach `STRANDED_KNOWN`
  from a `request_id` row written after a successful response
  (`tests/sandbox/crash_boundaries.py`), this host writes the `request_id` only
  *inside* the artifact and otherwise merely prints it. So the Mac's window is
  **wider than the VPS's**, and :attr:`CrashState.EXCHANGE_UNDECIDABLE` is the
  honest name for it.

That widening is what codex's #129 review named as owed — *"before enabling an
incomplete sink through the CLI, preserve the remaining response metadata durably
too; the old recovery record alone does not contain an exchange request ID"* — and
**closing it is deliberately not done here.** A durable marker written before the
credential is a second barrier ahead of the credential write, which is the trade
`06a` (iii) considered and declined for the VPS: it *"only trades this window for
a larger one in which the credential is lost."* Whether the Mac's different
evidence set changes that answer is a design decision with a counter-argument on
the record, so it belongs in a reviewed spec change rather than in the module that
merely reports the window honestly.

**This classifier never decides whether to exchange, and that is structural.**
:attr:`CrashState.EXCHANGE_UNDECIDABLE` is also the state a perfectly healthy
*first* run is in — record present, no artifact yet — so a classifier wired as a
refusal on that state would block every legitimate recovery. It is consulted where
the ambiguity actually exists and is currently resolved wrongly: the
existing-artifact branch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from networth import link_recovery
from networth.link_sink import (
    ArtifactReadFailure,
    ArtifactUnreadable,
    read_artifact,
    read_failure_cause,
)


class CrashState(Enum):
    """What local evidence proves, named by what the owner must do about it.

    Honest, *not necessarily distinct* — the principle `06a` (iii) settled. Two
    boundaries that leave the same evidence get the same answer, and
    manufacturing a difference between them is the defect rather than the fix.
    """

    #: The artifact opens under the escrowed key and is complete. The credential
    #: **is** durable. Restore from it; do not exchange again and do not move the
    #: file. Reached by a crash after the write, whether or not the recovery
    #: record was retired afterwards.
    CREDENTIAL_DURABLE = "the credential is durable in the artifact"

    #: Something is at the artifact path, it **opened and authenticated** under the
    #: escrowed key, and what came out is not a usable credential for this flow: a
    #: different schema (a `03a` backup archive shares this envelope), a missing
    #: required field, no fence, or another flow's recovery. This file will not
    #: produce the credential and the exchange may already have happened.
    #:
    #: **Authentication having succeeded is what makes this a proof**, and that is
    #: the whole of PR #136's F1 correction. A file that will not *open* lands in
    #: :attr:`ARTIFACT_UNVERIFIED` instead, because a wrong key fails exactly like
    #: a torn write and the owner's next action differs.
    CREDENTIAL_LOST = "an artifact is present and holds no usable credential"

    #: Something is at the artifact path and we could not get far enough to say
    #: what it holds — the bytes would not read, or they would not authenticate
    #: under the key we were handed.
    #:
    #: **This state exists because its absence was a bug.** F1: ``classify`` read
    #: every failure of :func:`~networth.link_sink.read_artifact` as
    #: :attr:`CREDENTIAL_LOST`, so a well-formed but *wrong* 32-byte backup key, and
    #: a transient ``EACCES`` on the artifact, each told the owner that the only
    #: surviving credential would never be recoverable — while the file sat there
    #: still holding it, which the reproduction proves by restoring access and the
    #: correct key and getting :attr:`CREDENTIAL_DURABLE` back out of the *same
    #: bytes*. Retain the artifact; verify the key and the access; only then is
    #: there anything to conclude.
    ARTIFACT_UNVERIFIED = "an artifact is present and could not be verified either way"

    #: No artifact, and the recovery record is still here. Either nothing has run
    #: yet or a run died after the exchange response and before the write, and
    #: **nothing on this disk distinguishes them.** The irreducible window, wider
    #: on this host than on the VPS.
    EXCHANGE_UNDECIDABLE = "no artifact; whether the token was spent is undecidable here"

    #: No artifact and no record. Nothing to recover and nothing to finish.
    NOTHING_HERE = "no artifact and no recovery record"

    #: The evidence could not be inspected. Refuses rather than guessing, for the
    #: reason :meth:`EmergencyArtifactSink.prepare` gives about ``lexists``: a
    #: check that reports every error as "nothing is there" re-creates the bug it
    #: was added to close, one level up.
    CANNOT_TELL = "the evidence could not be read"


@dataclass(frozen=True)
class Classification:
    """A state plus the measurement it came from.

    [detail] is what was observed, so a transcript carries the reason and not
    only the verdict — the recovery it describes happens once, under a 30-minute
    clock, and *"why does it say that"* is not a question the owner will have
    time to answer from the code.
    """

    state: CrashState
    detail: str
    #: ``True``/``False``/``None`` for present / absent / **could not be
    #: established**. Tri-state because PR #136's review found the third collapsed
    #: into the second (N1): an ``lstat`` failure on the recovery directory set
    #: this ``False``, and the CLI then printed *"record absent"* and *"no record
    #: remains to exchange from"* as facts, under the one state whose entire
    #: meaning is that nothing was established.
    record_present: bool | None

    @property
    def credential_is_durable(self) -> bool:
        """Whether a credential provably survived. Never a default."""
        return self.state is CrashState.CREDENTIAL_DURABLE

    @property
    def rerun_may_exchange_again(self) -> bool:
        """Whether re-running this recovery risks a second exchange.

        True for every state that still holds a record, including
        :attr:`CrashState.CREDENTIAL_DURABLE` — the credential being safe does
        not make a re-run safe, and that is the confusion the old refusal
        message created. **False only where a record is known to be absent**, so
        an unestablished presence answers True: *may* is the honest modality for
        an unknown, and it is also the side that does not invite a second
        exchange. Read :attr:`record_present` directly to tell the two Trues
        apart.
        """
        return self.record_present is not False


def classify(
    *,
    recovery_directory: Path,
    flow_id: str,
    artifact_path: Path,
    key_bytes: bytes | None,
) -> Classification:
    """Read the two pieces of local evidence and say what they prove.

    ``key_bytes`` is passed in rather than loaded here: the one caller that
    matters is inside :meth:`EmergencyArtifactSink.prepare`, which has already
    proven the key, and a second load would be a second opinion about whether the
    escrowed key is usable. ``None`` is accepted and is **not** treated as an
    absent artifact — a file we cannot open because we have no key is
    :attr:`CrashState.CANNOT_TELL`, never "no credential here".
    """

    record_present = _entry_exists(link_recovery.record_path(recovery_directory, flow_id))
    if record_present is None:
        return Classification(
            state=CrashState.CANNOT_TELL,
            detail="cannot tell whether a recovery record is present",
            # `None`, not `False` — N1. Reporting an unestablished presence as an
            # absence is how "we could not look" became "there is nothing to look at".
            record_present=None,
        )

    artifact_present = _entry_exists(artifact_path)
    if artifact_present is None:
        return Classification(
            state=CrashState.CANNOT_TELL,
            detail=f"cannot tell whether an artifact exists at {artifact_path}",
            record_present=record_present,
        )

    if not artifact_present:
        if record_present:
            return Classification(
                state=CrashState.EXCHANGE_UNDECIDABLE,
                detail=(
                    f"no artifact at {artifact_path} and the recovery record for {flow_id} "
                    "is still here; this host records no exchange attempt, so a run that "
                    "died after the response looks exactly like one that never started"
                ),
                record_present=True,
            )
        return Classification(
            state=CrashState.NOTHING_HERE,
            detail=f"no artifact at {artifact_path} and no recovery record for {flow_id}",
            record_present=False,
        )

    if key_bytes is None:
        return Classification(
            state=CrashState.CANNOT_TELL,
            detail=(
                f"something is at {artifact_path} and the escrowed backup key was not "
                "available to open it, so whether it holds the credential is unknown"
            ),
            record_present=record_present,
        )

    try:
        payload = read_artifact(artifact_path, key_bytes=key_bytes)
    except (ArtifactUnreadable, OSError) as exc:
        cause = read_failure_cause(exc)
        if cause is ArtifactReadFailure.ACCESS_FAILED:
            return Classification(
                state=CrashState.CANNOT_TELL,
                detail=(
                    f"an artifact is present at {artifact_path} and its bytes could not "
                    f"be read, so nothing about its contents was established: {exc}"
                ),
                record_present=record_present,
            )
        if cause is ArtifactReadFailure.UNAUTHENTICATED:
            return Classification(
                state=CrashState.ARTIFACT_UNVERIFIED,
                detail=(
                    f"an artifact is present at {artifact_path} and did not authenticate "
                    f"under the key supplied, which a wrong backup key, a torn write and "
                    f"a file that is not one of these envelopes all do identically: {exc}"
                ),
                record_present=record_present,
            )
        return Classification(
            state=CrashState.CREDENTIAL_LOST,
            detail=(
                f"an artifact is present at {artifact_path}, opened and authenticated "
                f"under the key supplied, and does not yield a credential: {exc}"
            ),
            record_present=record_present,
        )

    # `read_artifact` has already validated the schema, the required fields and the
    # fence, so reaching here *is* completeness. The flow id is checked because the
    # path is named by the caller: an artifact for a different flow at this path
    # proves a credential is durable for some other recovery, which must not be read
    # as this one's.
    if payload.get("flow_id") != flow_id:
        return Classification(
            state=CrashState.CREDENTIAL_LOST,
            detail=(
                f"the artifact at {artifact_path} opens and is complete but belongs to "
                f"flow {payload.get('flow_id')!r}, not {flow_id!r}"
            ),
            record_present=record_present,
        )

    return Classification(
        state=CrashState.CREDENTIAL_DURABLE,
        detail=(
            f"the artifact at {artifact_path} opens under the escrowed key and carries "
            f"every required field and the fence for flow {flow_id}"
        ),
        record_present=record_present,
    )


def _entry_exists(path: Path) -> bool | None:
    """``True``/``False``/``None`` for exists / absent / cannot tell.

    ``os.lstat`` and not ``Path.exists``, for both of that call's failures: it
    follows symlinks, so a dangling link at the artifact path answers ``False``
    while ``O_EXCL`` would still refuse the name; and from 3.13 it swallows every
    ``OSError``, so an unreadable parent also answers ``False``. Three outcomes
    are the point — a classifier that cannot see must say so.

    ``NotADirectoryError`` counts as absent, matching
    :func:`networth.commands.retire_hosted_link._observe_record` and for its
    reason: ``ENOTDIR`` on a non-directory component *states* there is no file at
    this pathname, one component earlier. Every other ``OSError`` — ``EACCES``,
    ``ELOOP`` — leaves the question unanswered, and a symlink loop is the
    opposite of an absence.
    """
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return None
    return True
