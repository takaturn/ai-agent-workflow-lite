#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'Herdr preflight: %s\n' "$1" >&2; exit 2; }
command -v herdr >/dev/null 2>&1 || fail 'herdr CLI not found'
command -v jq >/dev/null 2>&1 || fail 'jq is required'
[[ "${HERDR_ENV:-}" == 1 ]] || fail 'launch from a Herdr-managed pane (HERDR_ENV must be 1)'
[[ -n "${HERDR_PANE_ID:-}" ]] || fail 'HERDR_PANE_ID is missing'

# Environment variables are not sufficient: query the live Herdr instance.
pane="$(herdr pane get "$HERDR_PANE_ID")" || fail 'parent pane cannot be read'
jq -e --arg id "$HERDR_PANE_ID" '.result.pane.pane_id == $id' \
  >/dev/null <<<"$pane" || fail 'parent pane identity mismatch'
layout="$(herdr pane layout --pane "$HERDR_PANE_ID")" || fail 'parent layout cannot be read'
jq -e --arg id "$HERDR_PANE_ID" \
  '[.result.layout.panes[].pane_id] | index($id) != null' \
  >/dev/null <<<"$layout" || fail 'parent pane missing from live layout'

printf '%s\n' "$HERDR_PANE_ID"
