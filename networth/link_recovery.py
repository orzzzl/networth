"""The second copy of a Link flow's recovery record, held by the Mac.

**Why a copy on a second machine exists at all** (`DESIGN.md` §4, rev 17; the
disaster row it answers is in §14a.1, and §15 inventories the file). The
`link_token` is the key to `/link/token/get`, and by **F7** that call is the only
way a completed Hosted Link session's `public_token` can be retrieved — there is
no frontend integration, so nothing ever appears in the owner's browser to paste.
Writing the token durably on the VPS survives a *process crash*. It does not
survive **loss of the VPS**, and that is the case §14a is about: the owner
finishes Link, **F2a** spends the slot at that instant, and the disk dies before
the exchange. The newest archive predates the Link, so it holds neither an
`access_token` for the new Item nor the `link_token` that could still fetch one.
Without a copy somewhere else the Item is stranded *immediately* — not at the end
of any window — and F7's recovery path is unreachable.

**The copy is inert on its own, which is why it may live beside the backup key.**
`/link/token/get` needs `client_id`, `secret` *and* the `link_token`. This machine
holds only the third. If the VPS is gone the owner re-reads the first two from
Plaid's dashboard, where they have been all along; the `link_token` is the one
input no dashboard can reissue. So this file widens nothing that
``~/agents/secrets/`` did not already carry (§15).

**What it may not contain, and this is the part a previous revision got wrong.**
Rev 17 wrote a `session_retention_expires_at` into this record. Both of Plaid's
clocks start at the session's ``finished_at``, and at the moment this record is
created **the owner has not opened the page** — there is no ``started_at``, no
``finished_at``, and therefore no deadline to record. Any value written there
would have been *guessed from mint time* and then read back as a measurement.
So the record carries only what mint time actually knows, and
:func:`record_has_no_deadline` exists so that rule is asserted rather than
remembered.

**Two clocks are kept apart here for the same reason** (issue #3, and the same
discipline §8.1 applies to data). ``link_token_expires_at`` is **Plaid's**: the
``expiration`` field of the mint response, governing the *link token*.
``url_lifetime_seconds`` is **ours**: what this program asked for, governing how
long the hosted URL stays openable, and it is *not echoed back*, so ``None`` means
"did not ask, Plaid's default applies, we do not know it" rather than any number.

    **Divergence from `DESIGN.md`, raised rather than absorbed.** The document
    names this field ``hosted_url_expires_at`` in **three** places — §4's
    narrative, §7's ``link_flow`` table, and §15's secrets inventory — so this is
    not one sentence to reword. What the mint response actually carries is the
    *link token's* expiry, and the URL's lifetime is the other clock, the one
    that is never echoed back. Storing Plaid's number under a name that says
    "URL" would merge exactly the two clocks this project keeps separating, and
    it would do it inside the record the disaster procedure reads. The field is
    therefore named for what it holds. **The document and this module disagree on
    a name and agree on the content**; which one changes is a review decision,
    not one this module should make quietly.

    §7's copy is the one to look at first: its comment reads "30 minutes for a
    hosted link **token**, measured (§4)" directly under a column named for the
    **URL**, and §4's probe table measured the token lifetime while listing
    ``url_lifetime_seconds`` as a separate request parameter that "can widen it".
    The two clocks are already named in the same breath there.

``reap_after`` is computed **from this machine's clock, at the moment the record
is created** — deliberately not ``minted_at + …``. ``minted_at`` is stamped on the
VPS, and deriving a local deadline from a remote stamp is the cross-machine clock
comparison §9.1 rule 1 refuses; it is the defect rev 17 shipped in the backup
canary. Written on one machine, compared on that machine, and generous by
construction: reaping late costs one inert file, reaping early destroys the
disaster copy while the flow is still live.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from networth.tokenstore import Secret

#: ``30 min`` URL lifetime + ``30 min`` token lifetime + ``6 h`` session retention,
#: rounded up (`DESIGN.md` §4). A *local hygiene bound*, not a recovery deadline:
#: nothing consults it to decide whether an exchange is still possible, and the
#: real deadlines are derived from observed session timestamps once they exist.
REAP_AFTER: Final = timedelta(hours=7)

#: Mode 0600 file in a mode 0700 directory — the same posture as
#: :class:`~networth.tokenstore.TokenStore`, because the contents are the same
#: kind of thing: short-lived material that can retrieve a ``public_token``.
_FILE_MODE: Final = 0o600
_DIR_MODE: Final = 0o700

#: §15's inventoried location on ``zelengs-macbook-air-2``, beside the backup key.
_DEFAULT_MAC_RECOVERY_DIRECTORY: Final = "~/agents/secrets/networth-link-recovery"
RECOVERY_DIRECTORY_ENV: Final = "NETWORTH_LINK_RECOVERY_DIR"

_FLOW_ID_RE: Final = re.compile(r"\A[0-9a-f]{32}\Z")

#: Written into every record and checked on read. A record whose shape this
#: module does not recognise is refused rather than half-parsed: the one moment
#: it is read is a disaster on a 30-minute clock, which is the worst possible
#: time to discover a field means something else now.
SCHEMA: Final = "networth.link-recovery.1"

#: Field names this record is **forbidden** to carry. Not a style rule: a
#: deadline here is a number guessed from mint time that a later reader would
#: treat as measured (§4, rev 18). See :func:`record_has_no_deadline`.
FORBIDDEN_FIELDS: Final = frozenset(
    {
        "session_retention_expires_at",
        "public_token_expires_at",
        "exchange_deadline",
        "finished_at",
        "started_at",
    }
)


#: The one line of a VPS transcript the Mac is meant to read rather than show.
#: A marker rather than "the last line" or "the JSON-looking one": the mint runs
#: at the end of a transport that prints its own commit, origin, identity and
#: workspace, so the driver is reading a mixed stream and has to identify the
#: payload by something the stream cannot produce by accident.
MINT_WIRE_MARKER: Final = "networth-link-mint-v1:"

#: The return leg of the same transcript, and the only thing that authorises a
#: deletion. The mint travels VPS → Mac carrying material; this travels VPS → Mac
#: carrying an *outcome*, so it is shown rather than withheld — there is nothing
#: in it to protect. It exists because the two ends of a completion are on two
#: machines: the exchange happens where the client secret is, and the record it
#: retires is on the Mac, which therefore has to be *told* rather than to infer.
#: An exit status cannot say it — a zero means "this process did not fail", and
#: `--retrieve-only` also exits zero while leaving the flow deliberately live.
COMPLETION_WIRE_MARKER: Final = "networth-link-complete-v1:"

#: The only outcome that retires a record. Spelled as the design spells it
#: (`DESIGN.md` §4: "deleted by `link.sh` on `EXCHANGED`") rather than as a bare
#: boolean, so a future outcome that is *also* terminal has to be named here and
#: considered rather than falling into `not failed`.
EXCHANGED: Final = "EXCHANGED"


class LinkRecoveryError(Exception):
    """A recovery record could not be written, read back, or trusted."""


class RecordExists(LinkRecoveryError):
    """Refusing to overwrite an existing record for this flow.

    The same refusal, and for the same reason, as
    :meth:`~networth.tokenstore.TokenStore.put`: a blind re-write is how one
    flow's recovery material is destroyed by another's, and the direction that
    goes wrong is the unrecoverable one.
    """


class CorruptRecord(LinkRecoveryError):
    """The file exists and is not a record this module wrote."""


class SecondCopyUnverified(LinkRecoveryError):
    """The record did not read back identical to what was written.

    **The caller's obligation on this exception is to print no URL.** By **F2a**
    no slot is spent until Link completes, so refusing here costs nothing at all
    — it is the same "last moment the design can still refuse" that the backup
    canary occupies (§14a.1). Continuing would hand the owner an openable URL
    whose only means of recovery is a file this machine just failed to store.
    """


def _aware(value: datetime, *, field: str) -> datetime:
    """Reject a naive timestamp at the boundary (the §8.1 discipline)."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise LinkRecoveryError(
            f"{field} must be timezone-aware; a naive instant has no meaning here"
        )
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    """What this machine keeps so a lost VPS is not a stranded slot.

    ``link_token`` is a :class:`~networth.tokenstore.Secret`, so it cannot reach a
    log line, an f-string or a traceback frame without someone writing
    ``.reveal()`` on purpose. ``__repr__`` renders the instants and redacts the
    rest: the timestamps are this record's diagnostic value and they name no
    institution and no person, while ``flow_id`` is withheld because it is the
    handle that says *which* of the owner's Link attempts this is.
    """

    flow_id: str
    link_token: Secret
    minted_at: datetime
    link_token_expires_at: datetime | None
    url_lifetime_seconds: int | None
    reap_after: datetime
    second_copy_verified_at: datetime | None = None
    second_copy_holder: str | None = None

    def __post_init__(self) -> None:
        if not _FLOW_ID_RE.match(self.flow_id):
            # The rejected value is never repeated: a caller that arrived here by
            # passing token material where a flow id belonged would otherwise have
            # the module that exists to contain material be the one that prints it.
            raise LinkRecoveryError("not a usable flow_id; expected a minted flow id")
        object.__setattr__(self, "minted_at", _aware(self.minted_at, field="minted_at"))
        object.__setattr__(self, "reap_after", _aware(self.reap_after, field="reap_after"))
        if self.link_token_expires_at is not None:
            object.__setattr__(
                self,
                "link_token_expires_at",
                _aware(self.link_token_expires_at, field="link_token_expires_at"),
            )
        if self.second_copy_verified_at is not None:
            object.__setattr__(
                self,
                "second_copy_verified_at",
                _aware(self.second_copy_verified_at, field="second_copy_verified_at"),
            )

    def __repr__(self) -> str:
        verified = (
            "None" if self.second_copy_verified_at is None else repr(self.second_copy_verified_at)
        )
        return (
            "RecoveryRecord(flow_id=<redacted>, link_token=<redacted>, "
            f"minted_at={self.minted_at!r}, "
            f"link_token_expires_at={self.link_token_expires_at!r}, "
            f"url_lifetime_seconds={self.url_lifetime_seconds!r}, "
            f"reap_after={self.reap_after!r}, "
            f"second_copy_verified_at={verified}, "
            f"second_copy_holder={self.second_copy_holder!r})"
        )

    def expired(self, now: datetime) -> bool:
        """Whether the local hygiene bound has passed.

        **Not** a statement about whether the flow is still recoverable. The
        unattended puller deletes on this and only this, because it authenticates
        with the restricted key whose dispatcher cannot read a flow's status
        (§15) — asking a question the Mac has no way to ask is what rev 17's
        version did.
        """
        return _aware(now, field="now") >= self.reap_after

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "schema": SCHEMA,
            "flow_id": self.flow_id,
            "link_token": self.link_token.reveal(),
            "minted_at": self.minted_at.isoformat(),
            "link_token_expires_at": (
                None
                if self.link_token_expires_at is None
                else self.link_token_expires_at.isoformat()
            ),
            "url_lifetime_seconds": self.url_lifetime_seconds,
            "reap_after": self.reap_after.isoformat(),
            "second_copy_verified_at": (
                None
                if self.second_copy_verified_at is None
                else self.second_copy_verified_at.isoformat()
            ),
            "second_copy_holder": self.second_copy_holder,
        }
        # Sorted so two serialisations of the same record are byte-identical,
        # which is what makes the read-back check below a comparison of bytes
        # rather than of a parse. A read-back that re-parses can agree while the
        # file on disk is truncated.
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> RecoveryRecord:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CorruptRecord("recovery record is not JSON") from exc
        if not isinstance(payload, dict):
            raise CorruptRecord("recovery record is not an object")
        if payload.get("schema") != SCHEMA:
            # The value is not echoed: this file holds token material, and the
            # branch that runs when it is malformed is exactly the branch where
            # its contents are arbitrary.
            raise CorruptRecord(f"recovery record is not {SCHEMA!r}")
        present = FORBIDDEN_FIELDS & set(payload)
        if present:
            raise CorruptRecord(
                f"recovery record carries {sorted(present)}, which mint time cannot know (§4)"
            )
        try:
            return cls(
                flow_id=payload["flow_id"],
                link_token=Secret(payload["link_token"]),
                minted_at=datetime.fromisoformat(payload["minted_at"]),
                link_token_expires_at=_optional_instant(payload, "link_token_expires_at"),
                url_lifetime_seconds=payload["url_lifetime_seconds"],
                reap_after=datetime.fromisoformat(payload["reap_after"]),
                second_copy_verified_at=_optional_instant(payload, "second_copy_verified_at"),
                second_copy_holder=payload["second_copy_holder"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptRecord("recovery record is missing or misshapes a field") from exc


def _optional_instant(payload: dict[str, Any], field: str) -> datetime | None:
    raw = payload[field]
    if raw is None:
        return None
    return datetime.fromisoformat(raw)


def record_has_no_deadline(text: str) -> bool:
    """Whether a serialised record is free of guessed-deadline fields.

    Exported so the rule §4 states in prose is *asserted* somewhere. A rule that
    lives only in a paragraph is one refactor away from being reintroduced, and
    the way this one comes back is someone helpfully adding the field a reader
    "obviously needs".
    """
    payload = json.loads(text)
    return not (FORBIDDEN_FIELDS & set(payload))


def reap_after_from(now: datetime) -> datetime:
    """The local hygiene bound, from **this** machine's clock. See :data:`REAP_AFTER`."""
    return _aware(now, field="now") + REAP_AFTER


@dataclass(frozen=True, slots=True)
class MintResult:
    """What one machine's mint has to tell the other, and nothing more.

    **Deliberately not a serialised** :class:`RecoveryRecord`. The record carries
    two fields the VPS is not allowed to decide: ``reap_after``, which
    :func:`reap_after_from` computes from *the Mac's* clock because deriving a
    local deadline from a remote stamp is the cross-machine comparison §9.1 rule
    1 refuses; and ``second_copy_verified_at``, which is a statement about a
    write that has not happened yet. A wire format that carried them would let
    the far side hand this machine a verification it never performed.

    So the wire carries the mint's *observations* — and the hosted URL, which is
    not part of the record at all and is the thing the driver is holding back
    until the record is verified.

    ``link_token`` is the credential here, so this object has the same
    ``__repr__`` discipline as :class:`RecoveryRecord`: the timestamps render,
    the material does not.
    """

    flow_id: str
    link_token: Secret
    minted_at: datetime
    link_token_expires_at: datetime | None
    url_lifetime_seconds: int | None
    hosted_link_url: str

    def __repr__(self) -> str:
        return (
            "MintResult(flow_id=<redacted>, link_token=<redacted>, "
            f"minted_at={self.minted_at!r}, "
            f"link_token_expires_at={self.link_token_expires_at!r}, "
            f"url_lifetime_seconds={self.url_lifetime_seconds!r}, "
            "hosted_link_url=<redacted>)"
        )

    def to_wire(self) -> str:
        """One line: the marker, then compact JSON. Never more than one line.

        The transcript this travels in is read line by line, so a payload that
        could contain a newline would be a payload that could be split by the
        stream it rides in. ``json.dumps`` escapes newlines, which is what makes
        the single-line claim hold for any value rather than for the values we
        expect.
        """
        payload = {
            "flow_id": self.flow_id,
            "link_token": self.link_token.reveal(),
            "minted_at": self.minted_at.isoformat(),
            "link_token_expires_at": (
                None
                if self.link_token_expires_at is None
                else self.link_token_expires_at.isoformat()
            ),
            "url_lifetime_seconds": self.url_lifetime_seconds,
            "hosted_link_url": self.hosted_link_url,
        }
        return MINT_WIRE_MARKER + json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_wire(cls, line: str) -> MintResult:
        if not line.startswith(MINT_WIRE_MARKER):
            raise CorruptRecord("not a mint payload line")
        try:
            payload = json.loads(line[len(MINT_WIRE_MARKER) :])
        except json.JSONDecodeError as exc:
            # The value is never echoed. This is the branch where the remainder
            # of the line is arbitrary, and it is arbitrary *and* adjacent to a
            # link token — the one combination that must not reach a message.
            raise CorruptRecord("the mint payload is not JSON") from exc
        if not isinstance(payload, dict):
            raise CorruptRecord("the mint payload is not an object")
        present = FORBIDDEN_FIELDS & set(payload)
        if present:
            raise CorruptRecord(
                f"the mint payload carries {sorted(present)}, which mint time cannot know (§4)"
            )
        try:
            return cls(
                flow_id=payload["flow_id"],
                link_token=Secret(payload["link_token"]),
                minted_at=_aware(datetime.fromisoformat(payload["minted_at"]), field="minted_at"),
                link_token_expires_at=_optional_instant(payload, "link_token_expires_at"),
                url_lifetime_seconds=payload["url_lifetime_seconds"],
                hosted_link_url=payload["hosted_link_url"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptRecord("the mint payload is missing or misshapes a field") from exc

    def as_record(self, *, now: datetime) -> RecoveryRecord:
        """The record this Mac will write, with **this machine's** reap deadline."""
        return RecoveryRecord(
            flow_id=self.flow_id,
            link_token=self.link_token,
            minted_at=self.minted_at,
            link_token_expires_at=self.link_token_expires_at,
            url_lifetime_seconds=self.url_lifetime_seconds,
            reap_after=reap_after_from(now),
        )


@dataclass(frozen=True, slots=True)
class CompletionOutcome:
    """What the VPS reports back so the Mac may retire a record.

    Deliberately tiny, and deliberately **not** a mirror of :class:`MintResult`.
    The mint wire carries a credential and is withheld from the transcript; this
    carries a flow id and a verb, both of which are already printed in plain text
    all over the same run, so it is passed straight through to the owner.

    **It names the flow.** A driver that deleted "the record" on seeing any
    success marker would delete whichever record it was asked about while the
    transcript described a different one — which is possible the moment two
    rehearsals overlap, and the failure it produces is the destruction of the
    disaster copy of a flow that is still live. So the reader compares this field
    against the flow it was asked to retire and refuses on a mismatch.
    """

    flow_id: str
    outcome: str

    def to_wire(self) -> str:
        payload = {"flow_id": self.flow_id, "outcome": self.outcome}
        return COMPLETION_WIRE_MARKER + json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_wire(cls, line: str) -> CompletionOutcome:
        if not line.startswith(COMPLETION_WIRE_MARKER):
            raise CorruptRecord("not a completion payload line")
        try:
            payload = json.loads(line[len(COMPLETION_WIRE_MARKER) :])
        except json.JSONDecodeError as exc:
            raise CorruptRecord("the completion payload is not JSON") from exc
        if not isinstance(payload, dict):
            raise CorruptRecord("the completion payload is not an object")
        flow_id = payload.get("flow_id")
        outcome = payload.get("outcome")
        if not isinstance(flow_id, str) or not _FLOW_ID_RE.match(flow_id):
            # Checked here rather than at the deletion site: this value is about to
            # be turned into a filename, and the same rule `reap_expired` applies to
            # a name it finds on disk applies to a name that arrives over a wire.
            raise CorruptRecord("the completion payload does not name a flow id")
        if outcome != EXCHANGED:
            raise CorruptRecord(
                f"the completion payload reports {outcome!r}, and only "
                f"{EXCHANGED!r} retires a record"
            )
        return cls(flow_id=flow_id, outcome=outcome)


def mac_recovery_directory() -> Path:
    """Where ``zelengs-macbook-air-2`` keeps its second copies (§15).

    Resolved in one place because two programs on this Mac have to agree on it —
    ``scripts/link-start.sh`` writes the record and ``complete-hosted-link
    --from-tty`` reads it — and a path spelled twice is a path that drifts. There
    is **no VPS fallback**: this directory belongs to the Mac, and ``AGENTS.md``
    forbids either host's code reaching into the other's, which is exactly the
    bug rev 13 of ``DESIGN.md`` described (VPS code opening a file on a laptop).

    The environment variable exists so the tests can point it somewhere
    disposable. It names a *directory*, never material, so overriding it moves
    where a record is written and can never disclose one.
    """
    override = os.environ.get(RECOVERY_DIRECTORY_ENV)
    if override:
        return Path(override).expanduser()
    return Path(_DEFAULT_MAC_RECOVERY_DIRECTORY).expanduser()


def record_path(directory: Path, flow_id: str) -> Path:
    if not _FLOW_ID_RE.match(flow_id):
        raise LinkRecoveryError("not a usable flow_id; expected a minted flow id")
    return Path(directory) / f"{flow_id}.json"


def ensure_directory(directory: Path) -> Path:
    path = Path(directory)
    path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    # `mkdir` honours the mode only on creation, so an existing directory with
    # looser bits would be accepted silently. This holds the posture on every
    # run rather than on the first one.
    path.chmod(_DIR_MODE)
    return path


def store_and_verify(
    directory: Path,
    record: RecoveryRecord,
    *,
    holder: str,
    now: datetime,
) -> RecoveryRecord:
    """Write the record, ``fsync`` it, read it back, and stamp the verification.

    This is §4's second copy: pulled to this Mac, ``fsync``ed and read back
    **before the URL is printed**. The read-back is a byte comparison against
    what was written, not a re-parse of it, because a truncated file can still
    parse into an object that looks right.

    Raises rather than returns on every failure, and the caller's obligation on
    any of them is the same: **print no URL**. Refusing costs nothing (**F2a**).
    """
    directory = ensure_directory(directory)
    verified = RecoveryRecord(
        flow_id=record.flow_id,
        link_token=record.link_token,
        minted_at=record.minted_at,
        link_token_expires_at=record.link_token_expires_at,
        url_lifetime_seconds=record.url_lifetime_seconds,
        reap_after=record.reap_after,
        second_copy_verified_at=_aware(now, field="now"),
        second_copy_holder=holder,
    )
    payload = verified.to_json()
    path = record_path(directory, record.flow_id)

    # `O_EXCL` rather than a pre-check: the refusal happens in the kernel, so two
    # concurrent writers cannot both find nothing and have the second destroy the
    # first's material. The same invariant `TokenStore.put` holds, for the same
    # reason and at the same cost.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    except FileExistsError as exc:
        raise RecordExists(f"a recovery record already exists at {path}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise SecondCopyUnverified(f"could not write the recovery record at {path}") from exc

    _fsync_directory(directory)

    try:
        read_back = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SecondCopyUnverified(f"could not read back the recovery record at {path}") from exc
    if read_back != payload:
        raise SecondCopyUnverified(
            f"the recovery record at {path} did not read back identical to what was written"
        )
    return verified


def load(directory: Path, flow_id: str) -> RecoveryRecord:
    """Read one flow's record. Used by the owner-attended recovery procedure."""
    path = record_path(directory, flow_id)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise LinkRecoveryError(f"no recovery record at {path}") from exc
    except OSError as exc:
        raise LinkRecoveryError(f"could not read the recovery record at {path}") from exc
    return RecoveryRecord.from_json(text)


def delete(directory: Path, flow_id: str) -> bool:
    """Remove one flow's record; ``False`` if it was already gone.

    §4 gives this two callers and no more: the driver deletes on a successful
    exchange (it holds the interactive key and has just read the outcome), and
    the unattended puller deletes on :meth:`RecoveryRecord.expired`. Anything
    that would need to ask the VPS for a flow's *status* is not implementable on
    the stated topology and is not offered here.
    """
    path = record_path(directory, flow_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    _fsync_directory(Path(directory))
    return True


def _fsync_directory(directory: Path) -> None:
    """Make the *name* durable, not just the bytes under it."""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True, slots=True)
class ReapOutcome:
    """What one unattended sweep did, named per file so it can be journalled."""

    deleted: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    discarded: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.deleted or self.discarded)


def reap_expired(directory: Path, *, now: datetime) -> ReapOutcome:
    """Delete the records whose local hygiene bound has passed.

    §4 bounds a *crashed* flow's record with :data:`REAP_AFTER` on **this
    machine's** clock, and §15 is why the bound is all this can use: the
    unattended puller authenticates with the restricted key, whose dispatcher
    cannot ask the VPS for a flow's status. So this deletes on
    :meth:`RecoveryRecord.expired` and on nothing else — it never decides that a
    flow is finished, only that this copy of it has outlived its purpose.

    **A record that will not parse is the interesting case**, and it is the one
    the harness produces: :func:`store_and_verify` creates the file with
    ``O_EXCL`` and writes into it, with no temporary name, so a crash between
    those two steps leaves a real ``<flow>.json`` holding a fragment. It has no
    readable ``reap_after``, so an expiry-only sweep would keep it **forever** —
    which is precisely the "records become permanent" failure this exists to
    stop, reintroduced through the back door.

    It is not deleted on sight either, because "unparseable" and "being written
    right now" look identical from outside, and the file may hold a live token
    someone can still salvage by hand. The file's own mtime is a local fact that
    needs no parsing, so an unreadable record is discarded once *it* is older
    than :data:`REAP_AFTER` and reported until then.

    Never raises for a missing directory or a file that vanishes underneath it:
    this runs inside an unattended backup pull, and a hygiene sweep must not be
    able to fail the thing it is a passenger on.
    """

    directory = Path(directory)
    if not directory.is_dir():
        return ReapOutcome()

    moment = _aware(now, field="now")
    deleted: list[str] = []
    kept: list[str] = []
    discarded: list[str] = []
    unreadable: list[str] = []

    for path in sorted(directory.glob("*.json")):
        flow_id = path.stem
        # The name is the only evidence this module owns the file, and it is
        # checked before the file is read or unlinked rather than after. The
        # glob alone is not ownership: this is a dedicated secrets directory,
        # but a `notes.json` or any future metadata beside the records parses as
        # nothing, and the malformed branch below deletes what it cannot parse
        # once it is old enough. Without this line that branch reclassifies a
        # neighbour as a crashed partial record and removes it.
        if not _FLOW_ID_RE.match(flow_id):
            continue
        try:
            record = RecoveryRecord.from_json(path.read_text(encoding="utf-8"))
        except (LinkRecoveryError, OSError, ValueError):
            try:
                written = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if moment - written >= REAP_AFTER:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                else:
                    discarded.append(flow_id)
                    continue
            unreadable.append(flow_id)
            continue

        if not record.expired(moment):
            kept.append(flow_id)
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        deleted.append(flow_id)

    if deleted or discarded:
        # Make the removals durable, but never fail the sweep over it: the files
        # are already unlinked, and this runs inside an unattended backup pull.
        with contextlib.suppress(OSError):
            _fsync_directory(directory)

    return ReapOutcome(
        deleted=tuple(deleted),
        kept=tuple(kept),
        discarded=tuple(discarded),
        unreadable=tuple(unreadable),
    )
