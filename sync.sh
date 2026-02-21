#!/usr/bin/env fish
git fetch -t --tags --all; or exit 1

# pre-rebase checks
if not git diff --quiet; or not git diff --cached --quiet
    echo "Error: working tree is not clean, commit or stash changes first"
    exit 1
end

if not git rev-parse --verify upstream/main >/dev/null 2>&1
    echo "Error: upstream/main not found"
    exit 1
end

if test -d (git rev-parse --git-dir)/rebase-merge; or test -d (git rev-parse --git-dir)/rebase-apply
    echo "Error: a rebase is already in progress"
    exit 1
end

git rebase upstream/main
if test $status -ne 0
    echo "Error: rebase failed, aborting..."
    git rebase --abort
    exit 1
end

git push --force-with-lease; or exit 1
cd /tmp
pipx reinstall mycli; or exit 1
