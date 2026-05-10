#!/usr/bin/env bash
# detect-correction.sh — UserPromptSubmit hook.
# Scans user messages for correction signals (frustration, profanity, ALL CAPS,
# explicit negations) and injects a directive to save the correction to Open
# Brain immediately as a pinned guardrail.
#
# This creates a feedback loop: corrections automatically become guardrails
# that future sessions load at boot, so the same mistake isn't repeated.
#
# Install: copy to ~/.claude/hooks/ and add to settings.json UserPromptSubmit.
# See settings.snippet.json.

set -e

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('prompt',''))" 2>/dev/null \
       || python3 -c "import sys,json; print(json.load(sys.stdin).get('prompt',''))" <<< "$INPUT" 2>/dev/null)

if [ -z "$PROMPT" ]; then
  exit 0
fi

# Count ALL CAPS words (3+ letters)
CAPS_COUNT=$(echo "$PROMPT" | grep -oE '\b[A-Z]{3,}\b' | wc -l)

HAS_CORRECTION=false

if echo "$PROMPT" | grep -qiE '\b(wrong|stop|don.t|do not|never|idiot|stupid|wtf|wth)\b'; then
  HAS_CORRECTION=true
fi

if echo "$PROMPT" | grep -qE '(^|\. |\! |\? )No[,. !]|^No$|^NO '; then
  HAS_CORRECTION=true
fi

if [ "$CAPS_COUNT" -ge 3 ]; then
  HAS_CORRECTION=true
fi

if echo "$PROMPT" | grep -qiE '\b(fuck|shit|damn|hell|crap)\b'; then
  HAS_CORRECTION=true
fi

if [ "$HAS_CORRECTION" = "true" ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"CORRECTION DETECTED: The user is correcting you or expressing frustration. IMMEDIATELY after responding to their concern, call mcp__open-brain__remember with the correction as a guardrail (type_override=guardrail). Include: WHAT you did wrong, WHAT the correct behavior is, and WHY it matters. Do NOT skip this. Do NOT wait until later. Save it NOW."}}\n'
fi

exit 0
