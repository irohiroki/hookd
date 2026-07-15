"""User-identity switching for child processes."""

import os
import pwd


def _drop_to_user(username):
    """Called in the child process (after fork, before exec) to switch UID/GID.

    Requires CAP_SETUID and CAP_SETGID on the parent process
    (set via AmbientCapabilities in hookd.service).
    """
    pw = pwd.getpwnam(username)
    os.initgroups(username, pw.pw_gid)
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)


def _make_preexec(owner):
    """Return a preexec_fn closure for the given owner, or None for admin routes."""
    if owner is None:
        return None
    return lambda: _drop_to_user(owner)


def _owner_env(owner):
    """Return {USER, HOME} overrides for owner, or empty dict for admin routes."""
    if owner is None:
        return {}
    try:
        pw = pwd.getpwnam(owner)
        return {'USER': owner, 'HOME': pw.pw_dir}
    except KeyError:
        return {}
