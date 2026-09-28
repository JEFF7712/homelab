#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export GIT_TERMINAL_PROMPT=0

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN is not set; skipping GitHub mirror sync."
  exit 0
fi

source_sha=${CI_COMMIT_SHA:?CI_COMMIT_SHA is required}
source_branch=${CI_DEFAULT_BRANCH:?CI_DEFAULT_BRANCH is required}
mirror_url=https://github.com/JEFF7712/homelab-new.git
source_is_current() {
  local tip
  tip=$(git ls-remote origin "refs/heads/$source_branch") || return 2
  [[ ${tip%%[[:space:]]*} == "$source_sha" ]]
}
check_source() {
  local status=0
  source_is_current || status=$?
  if [[ $status == 1 ]]; then
    echo "Source commit is superseded; skipping mirror sync."
    exit 0
  fi
  return "$status"
}
check_source
[[ $(git rev-parse HEAD) == "$source_sha" ]]

if [[ $(git rev-parse --is-shallow-repository) == true ]]; then
  git fetch --no-tags --unshallow origin
fi
mirror_work=$(mktemp -d "${TMPDIR:-/tmp}/github-mirror-XXXXXX")
trap 'rm -rf "$mirror_work"' EXIT
printf '#!%s\n' "$(command -v bash)" > "$mirror_work/askpass"
cat >> "$mirror_work/askpass" <<'ASKPASS'
case "$1" in
  *Username*) printf '%s\n' x-access-token ;;
  *Password*) printf '%s\n' "$GITHUB_TOKEN" ;;
  *) exit 1 ;;
esac
ASKPASS
chmod 0700 "$mirror_work/askpass"
export GIT_ASKPASS="$mirror_work/askpass"

mirror_warning="**NOTE - This repository is a mirror.** Active development happens "
mirror_warning+="on [GitLab](https://gitlab.com/JEFF7712/homelab-new)."
readme=$(cat README.md)
printf '%s\n\n%s' "$mirror_warning" "$readme" > README.md
git add README.md
git -c user.email=runner@gitlab.com -c user.name='GitLab Runner' \
  commit -m 'Automated: Add GitHub Mirror Warning'

for attempt in 1 2 3; do
  check_source
  remote_tip=$(git -c credential.helper= ls-remote "$mirror_url" refs/heads/main)
  remote_sha=${remote_tip%%[[:space:]]*}
  if git -c credential.helper= -c http.version=HTTP/1.1 push "$mirror_url" \
    "--force-with-lease=refs/heads/main:$remote_sha" HEAD:refs/heads/main; then
    exit 0
  fi
  if [[ $attempt != 3 ]]; then
    sleep $((attempt * 15))
  fi
done
exit 1
