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
    echo "Rebase hit conflicts, invoking Claude to resolve..."
    claude -p --dangerously-skip-permissions "/goal A rebase onto upstream/main is in progress and paused on conflicts. Resolve the conflicts and run git rebase --continue, repeating until the rebase is complete."

    # verify the rebase actually completed before pushing
    if test -d (git rev-parse --git-dir)/rebase-merge; or test -d (git rev-parse --git-dir)/rebase-apply
        echo "Error: rebase still in progress after Claude, aborting..."
        git rebase --abort
        exit 1
    end
end

git push --force-with-lease; or exit 1
cd /tmp
pipx reinstall mycli; or exit 1
