---
id: 017
title: Add the vyrmy-claude app, isolated like a desktop slot
status: todo
depends_on: [016]
---

## Goal
Installing Claude Code from the store gives the owner Claude Code sessions on the Umbrel, used from claude.ai/code and the Claude apps. They reach the internet only through the app's own Squid, and nothing on the LAN or the Umbrel.

## Files
- `vyrmy-claude/umbrel-app.yml` (new)
- `vyrmy-claude/docker-compose.yml` (new)
- `sync/tests/test_desktop_slots.py` (existing): cover `vyrmy-claude`'s Squid configuration and addresses too
- `README.md` (existing): the layout table
- `docs/architecture/ARCHITECTURE.md` (existing): results, and decision 26 if the fallback in Risks is needed

## Contract
From ARCHITECTURE.md, Contracts, "Claude app". Copy the Squid and nginx services from `vyrmy-desktop-1/docker-compose.yml` and change only the addresses, the `acl clients` line and the page.

Manifest copy:

- `name: Claude Code`
- `tagline: Long-running Claude Code sessions, used from claude.ai and the Claude app`
- `description`: three paragraphs.
  1. Claude Code runs on the Umbrel and keeps working when your computer is off. You start and follow sessions in claude.ai/code or the Claude app, where the Umbrel appears as "Umbrel", up to four at a time.
  2. It needs a Claude Pro, Max or Team plan. After installing, sign in once. Over SSH, run `docker exec -it vyrmy-claude_claude_1 tmux attach -t claude`, open the link it shows on any device, paste the code back, and answer yes to Remote Control and to trusting the folder. Press Ctrl+B, then D, to leave it running.
  3. Sessions can reach the internet, but not your home network or the Umbrel. To work on GitHub repositories, run `gh auth login` in the same terminal with a fine-grained token for only those repositories. Anything a session can read, it could send out, so keep other credentials off this app.
- `releaseNotes`: "First release."

## Steps
1. Write the compose file and manifest. Run `scripts/pin-image` for the image that task 016 pinned.
2. Locally, with umbreld's project name (`vyrmy-claude`), an external `umbrel_main_network`, `app_proxy` dropped and the image from task 016:
   - all three services start;
   - the container is only on `vyrmy-claude_claude`;
   - the placeholder answers on the main network;
   - from the Claude container:
     - `getent hosts example.com` fails;
     - `curl https://example.com` without the proxy fails;
     - with the proxy it returns 200;
     - `192.168.10.2`, `10.21.0.1` and `172.17.0.1` through the proxy get 403;
     - `git clone https://github.com/vyrmy/umbrel-edge` works;
     - `git clone git@github.com:...` fails.
3. Check Remote Control through the proxy, assumption 4:
   - With a real sign-in (the owner's, here or on the Umbrel), the server shows as connected and claude.ai/code lists Umbrel.
   - If it cannot connect through Squid, follow the fallback in Risks and amend decision 26.
4. Extend `test_desktop_slots.py`:
   - no IPv4-mapped range in `vyrmy-claude`'s `dst` ACLs;
   - `acl clients` matches the container's address;
   - the proxy variables point at Squid's address.
5. Choose port `18120` after checking no installed or store app uses it, then add the README row.

## Acceptance
- On the Umbrel, after the one-time sign-in:
  - Two sessions started from claude.ai/code at once each run a command in `/home/claude/work` and show their tool calls live.
  - Closing the browser leaves both running, and they finish their work.
- `docker restart vyrmy-claude_claude_1`: Umbrel reappears in claude.ai/code within a minute, and the earlier sessions are listed again.
- From a session, `curl -x "" https://example.com` fails. `curl http://192.168.10.1` and `curl http://10.21.0.1` get Squid's 403. `curl https://example.com` succeeds.
- A session asked to switch to bypass mode cannot.
- `192.168.10.2:18120` shows the page from Contracts after Umbrel's login.

## Tests
The local run in step 2 and the Umbrel checks above, recorded in this file; the extended `test_desktop_slots.py`.

## Out of scope
Access to Umbrel apps or Traefik from a session. More than one slot. A Docker socket or any way to run containers from a session. Edge config changes: the app's placeholder hostname needs none.
