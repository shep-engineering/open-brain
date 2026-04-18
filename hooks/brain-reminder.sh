#!/usr/bin/env bash
# brain-reminder.sh — UserPromptSubmit hook for Open Brain
# Injects a mandatory reminder before the AI processes each user message.
# Install: copy to ~/.claude/hooks/ and add to ~/.claude/settings.json

echo "<user-prompt-submit-hook>MANDATORY: If you have not booted both brains yet, do it NOW before any other action. (1) Call mcp__open-brain__boot_session(project, source='claude') — V1 brain: guardrails, history, action items. (2) Call mcp__open-brain-v2__boot_session_v2(project, task, source='claude') — V2 brain: typed rules and patterns. After booting, search V1 for: (a) the task topic, (b) 'user preferences formatting rules'. Write new memories to BOTH brains: V1 via mcp__open-brain__capture_context, V2 via mcp__open-brain-v2__capture_context_v2.</user-prompt-submit-hook>"
exit 0
