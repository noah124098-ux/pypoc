---
name: project-ec2-deployment
description: Windows Server EC2 at 3.239.215.143 used as the primary dev environment from 2026-05-14; user works directly on the EC2 over RDP, not via VS Code Remote-SSH
metadata:
  type: project
---

The user has a Windows Server EC2 instance at `3.239.215.143` (DNS: `ec2-3-239-215-143.compute-1.amazonaws.com`).

**Decision (2026-05-14):** User does NOT want to connect to EC2 from their laptop's VS Code. Development happens directly on the EC2 over RDP — VS Code, Claude Code CLI, and all tooling installed on the EC2 itself.

**EC2 setup completed:**
- OpenSSH server installed and running (sshd) — though port 22 is currently blocked at the AWS Security Group (user's IP `106.219.176.10` not whitelisted). Not needed since work happens directly on EC2.
- Chocolatey installed
- Git, Python 3.12, VS Code, Node.js LTS installed via choco
- Claude Code CLI installed via `npm install -g @anthropic-ai/claude-code`
- Repo cloned to `C:\Users\Administrator\pypoc`
- `.venv` created in repo, `pip install -r requirements.txt` succeeded
- 80/80 tests pass

**Default user on EC2:** `Administrator`. Working directory: `C:\Users\Administrator\pypoc`.

**RDP connection method:** mstsc to `3.239.215.143`, username `Administrator`, password decrypted from `temp.pem` via AWS Console → EC2 → Connect → RDP client tab → Get password → upload .pem.

**Live agent on EC2:** Deferred. If/when running 24/7, will need:
- A separate SmartAPI app whitelisted with EC2's elastic IP (the leaked-key app is permanently neutralized — see [[feedback-angel-one-data-only]])
- An Elastic IP allocation so the public IP doesn't change on reboot
- A Windows service wrapper (NSSM recommended) so the agent survives RDP disconnects and reboots

**How to apply:**
- Always assume the working tree is at `C:\Users\Administrator\pypoc` when running on EC2.
- Use PowerShell (not cmd, not bash) for all shell commands on EC2.
- venv activation: `.\.venv\Scripts\Activate.ps1`
- Code editor of choice: `code .` from inside the project dir.
- For Claude Code CLI: `claude` from inside the project dir, or use the VS Code extension.

See [[project-nse-trading-agent]], [[feedback-angel-one-data-only]].
