#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export GIT_TERMINAL_PROMPT=0

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN is required for GitHub mirror sync." >&2
  exit 1
fi
export GITHUB_TOKEN

source_sha=${CI_COMMIT_SHA:?CI_COMMIT_SHA is required}
source_branch=${CI_REPO_DEFAULT_BRANCH:-${CI_DEFAULT_BRANCH:?default branch is required}}
mirror_url=https://github.com/JEFF7712/homelab.git
mirror_work=$(mktemp -d "${TMPDIR:-/tmp}/github-mirror-XXXXXX")
trap 'rm -rf "$mirror_work"' EXIT
printf '#!%s\n' "$(command -v bash)" > "$mirror_work/source-credential"
cat >> "$mirror_work/source-credential" <<'CREDENTIAL'
if [[ $1 == get ]]; then
  printf '%s\n' username=homelab-ci-source "password=${FORGEJO_SOURCE_READ_TOKEN:-}"
fi
CREDENTIAL
chmod 0700 "$mirror_work/source-credential"
source_git() {
  git -c credential.helper= \
    -c "credential.http://git.internal:3000.helper=$mirror_work/source-credential" \
    -c "credential.https://git.rupan.dev.helper=$mirror_work/source-credential" \
    "$@"
}
source_is_current() {
  local tip
  tip=$(source_git ls-remote origin "refs/heads/$source_branch") || return 2
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
git diff --quiet
git diff --cached --quiet

if [[ $(git rev-parse --is-shallow-repository) == true ]]; then
  source_git fetch --no-tags --unshallow origin
fi
source_git fetch --refetch --no-filter --no-tags origin "$source_sha"
printf '#!%s\n' "$(command -v bash)" > "$mirror_work/credential"
cat >> "$mirror_work/credential" <<'CREDENTIAL'
if [[ $1 == get ]]; then
  printf '%s\n' username=x-access-token "password=$GITHUB_TOKEN"
fi
CREDENTIAL
chmod 0700 "$mirror_work/credential"
mirror_git() {
  git -c credential.helper= \
    -c "credential.http://git.internal:3000.helper=$mirror_work/source-credential" \
    -c "credential.https://git.rupan.dev.helper=$mirror_work/source-credential" \
    -c "credential.https://github.com.helper=$mirror_work/credential" "$@"
}

mirror_warning="**NOTE - This repository is a mirror.** Active development happens "
mirror_warning+="on [Forgejo](https://git.rupan.dev/JEFF7712/homelab)."
readme=$(cat README.md)
printf '%s\n\n%s' "$mirror_warning" "$readme" > README.md
git add README.md
git -c user.email=ci@rupan.dev -c user.name='Homelab CI' \
  commit -m 'Automated: Add GitHub Mirror Warning'

for attempt in 1 2 3; do
  check_source
  remote_tip=$(mirror_git ls-remote "$mirror_url" refs/heads/main)
  remote_sha=${remote_tip%%[[:space:]]*}
  if [[ -n "$remote_sha" ]]; then
    mirror_git fetch --refetch --no-tags "$mirror_url" "$remote_sha"
    if ! git merge-base --is-ancestor "$remote_sha" HEAD; then
      snapshot=$(git -c user.email=ci@rupan.dev -c user.name='Homelab CI' \
        commit-tree 'HEAD^{tree}' -p HEAD -p "$remote_sha" \
        -m 'Automated: Preserve public mirror history')
      git reset --hard "$snapshot"
    fi
  fi
  check_source
  if mirror_git -c http.version=HTTP/1.1 push "$mirror_url" HEAD:refs/heads/main; then
    exit 0
  fi
  if [[ $attempt != 3 ]]; then
    sleep $((attempt * 15))
  fi
done
exit 1
