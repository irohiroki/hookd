#!/bin/bash
# upgrade.sh — update an installed hookd to the tip of the branch checked out in
# the server-local clone at /var/lib/hookd/repo.
#
# Usage (as root, on the hookd host):
#   /var/lib/hookd/repo/upgrade.sh
#
# Prerequisites:
#   - /var/lib/hookd/repo is a clone made by root; that directory, every ancestor
#     of it and every file inside it are owned by root and are not writable by
#     group or other
#   - the branch to follow is already checked out and tracks a remote branch
#   - pip3 is installed and hookd.service exists (see install.sh)
#
# Design notes:
#   - The repository path is hardcoded and no argument is accepted:
#     `pip3 install .` runs the project's build backend as root, so write access
#     to that tree is equivalent to root access. The script refuses to run unless
#     the whole path and tree are root-owned and not group/other writable.
#   - The worktree is force-synced to the upstream tip; local edits and local
#     commits in the clone are discarded by design.
#   - Only Python code is replaced. /etc/systemd/system/hookd.service and
#     /etc/hookd/routes_dir are left untouched so manual edits survive.
#   - This script lives inside the tree it updates. git replaces files by rename,
#     so the running shell keeps reading the old inode even if the file is
#     rewritten mid-run.

set -euo pipefail

REPO_DIR=/var/lib/hookd/repo
SERVICE=hookd
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
umask 022

die() { echo "error: $*" >&2; exit 1; }

require_root() {
    [[ "$EUID" -eq 0 ]] || die "must run as root"
}

assert_real_path() {
    [[ -d "$REPO_DIR" ]] || die "$REPO_DIR does not exist"
    local resolved
    resolved="$(cd "$REPO_DIR" && pwd -P)"
    [[ "$resolved" == "$REPO_DIR" ]] \
        || die "$REPO_DIR resolves to $resolved — symlinked paths are not accepted"
}

assert_root_owned() {
    local path="$1" owner mode
    owner="$(stat -c '%u' "$path")" || die "cannot stat $path"
    mode="$(stat -c '%a' "$path")"
    [[ "$owner" -eq 0 ]] || die "$path is not owned by root (uid $owner)"
    if (( 8#$mode & 8#22 )); then
        die "$path is writable by group or other (mode $mode)"
    fi
}

assert_root_owned_ancestors() {
    local path="" part
    local -a parts
    IFS=/ read -ra parts <<< "${REPO_DIR#/}"
    for part in "${parts[@]}"; do
        path="$path/$part"
        assert_root_owned "$path"
    done
}

assert_root_owned_tree() {
    local offender
    offender="$(find "$REPO_DIR" \
        \( ! -user root -o \( ! -type l -a -perm /022 \) \) -print -quit)"
    [[ -z "$offender" ]] \
        || die "$offender is not owned by root or is group/other writable"
}

assert_git_worktree() {
    git -C "$REPO_DIR" rev-parse --is-inside-work-tree > /dev/null 2>&1 \
        || die "$REPO_DIR is not a git worktree"
}

assert_repo_safe() {
    assert_real_path
    assert_root_owned_ancestors
    assert_root_owned_tree
    assert_git_worktree
}

branch_name() {
    git -C "$REPO_DIR" symbolic-ref --short --quiet HEAD \
        || die "$REPO_DIR has a detached HEAD — check out a branch first"
}

head_commit() {
    git -C "$REPO_DIR" rev-parse HEAD
}

upstream_of() {
    local branch="$1"
    git -C "$REPO_DIR" rev-parse --abbrev-ref --symbolic-full-name \
        "$branch@{upstream}" 2> /dev/null \
        || die "branch '$branch' has no upstream; set one with:" \
               "git -C $REPO_DIR branch --set-upstream-to=origin/$branch"
}

sync_worktree() {
    local branch="$1" remote upstream
    upstream="$(upstream_of "$branch")"
    remote="$(git -C "$REPO_DIR" config --get "branch.$branch.remote")"
    echo "Fetching $upstream"
    git -C "$REPO_DIR" fetch --quiet "$remote" "$branch"
    git -C "$REPO_DIR" reset --hard --quiet "$upstream"
    git -C "$REPO_DIR" clean -qfd
}

install_package() {
    echo "Installing hookd from $REPO_DIR"
    ( cd "$REPO_DIR" && pip3 install --quiet . )
}

restart_service() {
    echo "Restarting $SERVICE"
    systemctl restart "$SERVICE"
    sleep 2
    systemctl is-active --quiet "$SERVICE" \
        || die "$SERVICE is not running; check: journalctl -u $SERVICE -n 50 --no-pager"
}

main() {
    [[ $# -eq 0 ]] || die "usage: $0   (no arguments; the repository path is fixed)"
    require_root
    assert_repo_safe

    local branch before after dirty
    branch="$(branch_name)"
    before="$(head_commit)"
    dirty="$(git -C "$REPO_DIR" status --porcelain)"
    sync_worktree "$branch"
    after="$(head_commit)"

    if [[ "$before" == "$after" && -z "$dirty" ]]; then
        echo "Already up to date ($branch @ ${before:0:12})"
        exit 0
    fi

    install_package
    restart_service
    echo "hookd upgraded: ${before:0:12} -> ${after:0:12} ($branch)"
}

main "$@"
