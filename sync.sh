#!/usr/bin/env fish
if test (git branch --show-current) != main
    echo "Error: sync.sh must run on the main branch"
    exit 1
end

git fetch origin; or exit 1
# tags come from upstream only; --tags against both remotes can clobber each other
git fetch --tags upstream; or exit 1

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
    set -l resolve_prompt '/goal A rebase onto upstream/main is in progress and paused on conflicts. Resolve the conflicts and run git rebase --continue, repeating until the rebase is complete.

Conflict resolution rules, learned from the 2026-08-02 incident:
- This branch is a patch stack on top of upstream/main. For each conflict, recover the intent of the commit being replayed (git show it) and preserve its pre-rebase behavior. Do not mechanically keep both sides.
- Never keep the same argument, import, or line from both sides. That is exactly how the incident happened: the resolution kept a cursor= keyword argument from each side of one PromptSession call, producing a SyntaxError that broke even mycli --help.
- When upstream adds code whose behavior a patch already covers or deliberately overrides, keep the patch line and drop the upstream one.
- Before every git rebase --continue, run: python3 -m compileall -q mycli
  If it fails, fix the resolution before continuing, so every replayed commit at least parses and the stack stays bisectable.'
    claude -p --dangerously-skip-permissions $resolve_prompt

    # verify the rebase actually completed before pushing
    if test -d (git rev-parse --git-dir)/rebase-merge; or test -d (git rev-parse --git-dir)/rebase-apply
        echo "Error: rebase still in progress after Claude, aborting..."
        git rebase --abort
        exit 1
    end
end

# catches the case where Claude aborted the rebase instead of completing it
if not git merge-base --is-ancestor upstream/main HEAD
    echo "Error: HEAD does not contain upstream/main, rebase did not complete"
    exit 1
end

# smoke: auto-resolved conflicts must at least byte-compile before anything ships
if not python3 -m compileall -q mycli
    echo "Error: source fails to compile after rebase, not pushing"
    exit 1
end

git push --force-with-lease; or exit 1
# keep fork tags current, otherwise setuptools-scm versions the pip build off a stale tag
git push origin --tags; or exit 1

# converge pipx state: mycli at HEAD with catppuccin injected
set -l head_short (git rev-parse --short=9 HEAD)
set -l state (pipx list --json 2>/dev/null | python3 -c "import json,sys; m = json.load(sys.stdin)['venvs']['mycli']['metadata']; print(m['main_package']['package_version']); print('yes' if 'catppuccin' in m['injected_packages'] else 'no')" 2>/dev/null)

if string match -q "*+g$head_short*" -- "$state[1]"
    echo "mycli already at +g$head_short, skipping reinstall"
else if test -z "$state[1]"
    pipx install 'git+https://github.com/amzyang/mycli'; or exit 1
else
    pipx reinstall mycli; or exit 1
end

if test "$state[2]" != yes
    pipx inject mycli 'catppuccin[pygments]'; or exit 1
end

# smoke: the installed CLI must survive its import chain end to end
if not ~/.local/bin/mycli --help >/dev/null
    echo "Error: installed mycli fails --help smoke check"
    exit 1
end

# keep the fish completion file in sync with the installed CLI;
# write via temp file so a failed generation never truncates the live one
set -l completion_file ~/.config/fish/completions/mycli.fish
if ~/.local/bin/mycli --completions fish > $completion_file.tmp
    mv $completion_file.tmp $completion_file
else
    rm -f $completion_file.tmp
    echo "Error: failed to regenerate fish completions"
    exit 1
end
