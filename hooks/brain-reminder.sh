#!/usr/bin/env bash
# brain-reminder.sh — UserPromptSubmit hook for Open Brain
# Injects a mandatory reminder before the AI processes each user message.
# Install: copy to ~/.claude/hooks/ and add to ~/.claude/settings.json

echo "<user-prompt-submit-hook>MANDATORY: Before taking ANY action, search Open Brain for: (1) the task topic, (2) 'user preferences formatting rules'. Call mcp__open-brain__search TWICE before proceeding. If you skip this, the PreToolUse hook will block you.</user-prompt-submit-hook>"
exit 0
