"""Who to attribute a command-line action to.

Lifted from growth-engine/auth.py, which carries 328 lines of sign-in,
operator lists and the approval guard -- none of which belongs in a repo that
approves nothing. Only this one function came, because only this one is used:
`meta_ads --add-account` records who added it, and a pull records who started
it.

THE PREFIX IS LOAD-BEARING. growth-engine's assert_can_approve reads `cli:` to
tell a script from a person, and an audit trail that mixes "chris@example.com"
with "cli:Renegade" is telling the truth about two different provenances.
Flattening them into one format would hide that, so the prefix stays even
though nothing on this side reads it -- the rows are written into a database
that the other side does read.
"""

from __future__ import annotations


def cli_operator(explicit: str | None = None) -> str:
    """-> an identity string for a CLI action.

    Deliberately NOT the first name in an operator list. That would attribute
    every command to whoever happens to be listed first, which is fabrication
    dressed up as a default.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    import getpass
    try:
        return f"cli:{getpass.getuser()}"
    except Exception:      # noqa: BLE001 -- no OS identity available
        return "cli:unknown"
