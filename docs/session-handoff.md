# Session Handoff — 2026-08-28

Quick-resume note so context survives a Claude Code restart.

## Where we are
- Chose 3 pet-project ideas (see `docs/project-ideas.md`). **Starting with #1: Second Opinion**
  (multi-agent decision-making arena).
- Set up the **Atlassian MCP** connection (Jira + Confluence) via `.mcp.json`
  (`mcp-remote` → `https://mcp.atlassian.com/v1/mcp`), modeled on the ai-native-starter-pack.
- OAuth is **done** — tokens cached in `~/.mcp-auth`; `claude mcp list` shows `atlassian: ✔ Connected`.
- Auto-approved the server in `.claude/settings.local.json` (`enabledMcpjsonServers: ["atlassian"]`).

## Why a restart is needed
The `atlassian` MCP server was added mid-session, so its `mcp__atlassian__*` tools aren't loaded
into the current session. Relaunch to pick them up.

## How to resume
```bash
claude --continue      # restores this conversation AND loads the atlassian tools
```

## Next steps (not started yet — no project code)
1. Test both connections read-only:
   - List **KAN** board issues (https://osavelyev.atlassian.net/jira/software/projects/KAN/list)
   - Read the **Second Opinion** Confluence page
     (https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/folder/622593/Second+Opinion)
2. Then plan the Second Opinion project.
