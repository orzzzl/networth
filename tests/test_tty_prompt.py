"""Exercise the credential prompt on a real controlling PTY, using only fake input.

The no-terminal refusal test cannot catch a prompt that fails to open a real TTY.
These children run only the prompt helper: no recovery file or Plaid API is read.
"""

from __future__ import annotations

import os
import pty
import select
import subprocess
import sys
import termios
import time

from tests.conftest import REPO_ROOT

_CHILD_SETUP = """
import fcntl
import termios
from networth.commands.complete_hosted_link import _read_from_tty
from networth.config import ConfigError

# setsid() removed the parent's terminal. stdout is our synthetic PTY slave;
# acquire it explicitly, while stdin remains a pipe full of unrelated fake data.
fcntl.ioctl(1, termios.TIOCSCTTY, 0)
original = termios.tcgetattr(1)
"""


def _exercise_prompt(source: str, replies: list[tuple[bytes, bytes]]) -> bytes:
    master, slave = pty.openpty()
    transcript = bytearray()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-c",
                # Pin the source even if another checkout owns the interpreter's
                # editable install, so this test exercises the code under review.
                f"import sys\nsys.path.insert(0, {str(REPO_ROOT)!r})\n" + _CHILD_SETUP + source,
            ],
            cwd=REPO_ROOT,
            stdin=subprocess.PIPE,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
        )
        assert process.stdin is not None
        process.stdin.write(b"unrelated-piped-input\n")
        process.stdin.close()
        deadline = time.monotonic() + 15

        def read_until(marker: bytes) -> None:
            # A traceback can quote the prompt string; only an actual unfinished
            # prompt at the end of output is permission to send synthetic input.
            while not transcript.endswith(marker):
                remaining = deadline - time.monotonic()
                assert remaining > 0, f"PTY timed out: {bytes(transcript)!r}"
                readable, _, _ = select.select([master], [], [], min(remaining, 0.1))
                if readable:
                    transcript.extend(os.read(master, 4096))
                elif process is not None and process.poll() is not None:
                    raise AssertionError(f"prompt child exited: {bytes(transcript)!r}")

        for prompt, answer in replies:
            read_until(prompt)
            assert not termios.tcgetattr(slave)[3] & termios.ECHO, bytes(transcript)
            os.write(master, answer)
        read_until(b"PROMPT_TEST_PASSED\r\n")
        assert process.wait(timeout=5) == 0, bytes(transcript)
        return bytes(transcript)
    finally:
        os.close(master)
        os.close(slave)
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.stdin is not None:
                process.stdin.close()


def test_real_tty_two_prompts_do_not_echo_and_restore_terminal() -> None:
    transcript = _exercise_prompt(
        """
first = _read_from_tty('FIRST_PROMPT: ')
assert first == 'synthetic-first-value'
assert termios.tcgetattr(1) == original
second = _read_from_tty('SECOND_PROMPT: ')
assert second == 'synthetic-second-value'
assert termios.tcgetattr(1) == original
print('PROMPT_TEST_PASSED')
""",
        [
            (b"FIRST_PROMPT: ", b"synthetic-first-value\n"),
            (b"SECOND_PROMPT: ", b"synthetic-second-value\n"),
        ],
    )
    assert b"synthetic-first-value" not in transcript
    assert b"synthetic-second-value" not in transcript
    assert b"unrelated-piped-input" not in transcript


def test_real_tty_eof_is_refused_and_restores_terminal() -> None:
    transcript = _exercise_prompt(
        """
try:
    _read_from_tty('EOF_PROMPT: ')
except ConfigError as exc:
    assert 'terminal closed before the prompt was answered' in str(exc)
else:
    raise AssertionError('EOF must not become a credential')
assert termios.tcgetattr(1) == original
print('PROMPT_TEST_PASSED')
""",
        [(b"EOF_PROMPT: ", b"\x04")],
    )
    assert b"unrelated-piped-input" not in transcript
