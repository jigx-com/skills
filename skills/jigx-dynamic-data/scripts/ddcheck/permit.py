"""The only way to get permission to write Dynamic Data: a person typing at a terminal.

`ask()` prints the write plan to the controlling terminal (/dev/tty) and reads the answer from it.
Piped stdin is never read, so `echo <name> | ddcheck seed ...` cannot confirm, and an agent's
shell, which has no controlling terminal, cannot write at all: it is told to hand the command to
a person. There is deliberately no --yes flag and no environment variable that skips this.

`api.patch_rows` refuses to run without a Permit for exactly that solution, so a future code
path cannot write without going through `ask()`.
"""
import fnmatch
import os
import sys

_KEY = object()


class Permit:
    def __init__(self, key, solution, solution_id, rows):
        if key is not _KEY:
            raise RuntimeError("a write Permit can only come from permit.ask()")
        self.solution, self.solution_id, self.rows = solution, solution_id, rows


def protected(solution, patterns):
    """The first protect pattern the solution name matches, or None."""
    for p in patterns or []:
        if fnmatch.fnmatch(solution.lower(), p.lower()):
            return p
    return None


def _open_tty():
    if os.environ.get("CI"):
        return None
    try:
        return open("/dev/tty", "r+")
    except OSError:
        return None


def ask(solution, solution_id, org, user, plan_lines, rows, protect=()):
    """Return a Permit when the person types the target solution's name, else exit."""
    hit = protected(solution, protect)
    if hit:
        sys.exit(f"REFUSED: '{solution}' matches the profile's protect pattern '{hit}'. "
                 "ddcheck never writes to it.")
    tty = _open_tty()
    if tty is None:
        sys.exit("REFUSED: writing Dynamic Data needs a person at a terminal, and this process has "
                 "none (an agent's shell, CI, or a pipe). Nothing was written. Give the person the "
                 "exact command so they can run it in their own terminal.")
    with tty:
        tty.write("\n=== ddcheck WRITE ===\n")
        tty.write(f"  login:    {user or 'JIGX_AUTH_TOKEN (identity unknown)'}\n")
        tty.write(f"  org:      {org}\n")
        tty.write(f"  target:   {solution} ({solution_id})\n")
        for line in plan_lines:
            tty.write(f"  {line}\n")
        tty.write(f"Type '{solution}' to write these {rows} rows; anything else aborts: ")
        tty.flush()
        answer = tty.readline().strip()
    if answer != solution:
        sys.exit("aborted; nothing written")
    return Permit(_KEY, solution, solution_id, rows)
