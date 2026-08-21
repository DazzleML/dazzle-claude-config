#!/bin/sh
# verify_both_labels.sh -- is `ccs status` right about "differs on both sides"?
#
# Independent of ccs. For each path given, asks git whether the LIVE copy is
# byte-identical (after CRLF normalisation) to ANY commit in the path's
# history. If it is, live has not changed since that commit and the drift is
# one-sided (checkout ahead) -- whatever status says.
#
# Origin: 2026-08-21, the dev machine. status reported 29 files as "both"; this
# procedure showed 22 were one-sided. Root cause and fix:
#   private/claude/2026-08-21__03-27-24__dev-workflow-process__status-mislabels-one-sided-drift-as-two-sided.md
# Kept as the oracle for that DWP's AC-9: after the fix, every "both" verdict
# from status must agree with this script.
#
# Usage:
#   verify_both_labels.sh <checkout> <live-root> <repo-relative-path>...
#   e.g.
#   verify_both_labels.sh "$CCS_CHECKOUT_DIR" ~/.claude \
#       dotclaude/commands/ask.md dotclaude/CLAUDE.md
#
# <repo-relative-path> is the checkout path (dotclaude/...); the live path is
# derived by stripping the territory prefix. Pass ~/claude as <live-root> for
# userclaude/ entries.

CO="$1"; LIVE_ROOT="$2"; shift 2
[ -n "$CO" ] && [ -n "$LIVE_ROOT" ] && [ $# -gt 0 ] || { sed -n '2,24p' "$0"; exit 2; }

norm() { sed 's/\r$//'; }
one=0; two=0; untracked=0

for p in "$@"; do
  rel="${p#dotclaude/}"; rel="${rel#userclaude/}"
  live="$LIVE_ROOT/$rel"
  if [ ! -f "$live" ]; then printf '  %-48s live MISSING\n' "$p"; continue; fi
  if ! git -C "$CO" ls-files --error-unmatch "$p" >/dev/null 2>&1; then
    printf '  %-48s UNTRACKED in git -- no history; checkout copy is a local snapshot\n' "$p"
    untracked=$((untracked+1)); continue
  fi
  match=""
  for sha in $(git -C "$CO" log --format=%h --follow -- "$p"); do
    if git -C "$CO" show "$sha:$p" 2>/dev/null | norm | cmp -s - <(norm < "$live"); then match="$sha"; break; fi
  done
  head=$(git -C "$CO" log -1 --format=%h -- "$p")
  if [ -n "$match" ] && [ "$match" = "$head" ]; then
    printf '  %-48s live == HEAD -> IN SYNC\n' "$p"
  elif [ -n "$match" ]; then
    printf '  %-48s ONE-SIDED checkout-ahead (live == %s)\n' "$p" "$match"; one=$((one+1))
  else
    # live matches no commit -- but that only proves LIVE changed. Check the
    # checkout side: if HEAD's content equals the oldest commit's, the checkout
    # never moved, and this is ONE-SIDED live-ahead (2026-08-21: three files
    # were miscalled two-sided by the earlier version of this script).
    oldest=$(git -C "$CO" log --format=%h --follow -- "$p" | tail -1)
    if [ -n "$oldest" ] && git -C "$CO" show "$oldest:$p" 2>/dev/null | norm | cmp -s - <(git -C "$CO" show "$head:$p" | norm); then
      printf '  %-48s ONE-SIDED live-ahead (checkout unchanged since %s)\n' "$p" "$oldest"; one=$((one+1))
    else
      printf '  %-48s genuinely TWO-SIDED (live matches no commit AND checkout moved)\n' "$p"; two=$((two+1))
    fi
  fi
done

echo ""
echo "one-sided: $one   two-sided: $two   untracked: $untracked"
