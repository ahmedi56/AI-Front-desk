---
name: deploy-vps
description: Secure a new VPS and deploy the AI Front Desk with Docker and Caddy, with backups, uptime alerts and a kill switch. Always ask the person's permission before running any command on the server.
---

# Deploy to a VPS

A VPS is a rented server. You help the person set one up safely. You never run a command on the server without showing it first, explaining it in simple English, and getting a clear yes.

## Before you start

- Confirm that tests pass (`acceptance-test`) and that `security-review` has no open High findings.
- Ask the person for: the server's IP address, the domain name, and confirmation that they created an SSH key. Never ask for their passwords or personal keys.
- Recommended server size to start: 2 vCPU, 4 GB RAM, at least 40 GB SSD. Check the provider's current price. Monthly billing, no long contract for the first server.

## Steps (show each command, explain it, wait for a yes)

1. **First login and a normal user.** Create a non-root user with sudo. Copy the SSH key to it.
2. **Lock the doors.** Turn off password login and direct root login over SSH. Test a second login before closing the first.
3. **Firewall.** Allow only SSH (restricted to the person's IP if possible), port 80 and port 443. Block everything else.
4. **fail2ban.** Install it to block addresses that keep guessing passwords.
5. **Automatic security updates.** Turn on unattended upgrades.
6. **Docker.** Install Docker. Write `docker-compose.yml` with three containers: the app, Postgres and Caddy. Postgres must not be published to the internet. Containers run as a normal user.
7. **Secrets.** Put them in a `.env` file on the server with strict file permissions, never in Git.
8. **Caddy and HTTPS.** Point the domain to the server. Caddy gets the security certificate automatically.
9. **Start and test.** Start the containers. Check the health URL. Send a test text or call from the person's phone.
10. **Backups.** Daily database dump, encrypted, copied off the server. Document where the keys are kept.
11. **Restore test.** Restore the latest backup onto a separate test server or database. Do not skip this step.
12. **Uptime alerts.** Set up a free uptime monitor that alerts the person's phone.
13. **Kill switch.** Make one command or button that turns the agent off and routes every contact to a person.
14. **Write the runbook** in `docs/runbook.md`: how to deploy, restart, restore, rotate keys and use the kill switch, in simple English.

## Rules

- Never delete data or stop services without asking.
- Never open a database port to the internet.
- Never store the server password in a file. Use SSH keys.
- If a command fails, stop, explain what happened, and ask what to do. Do not try random fixes.
- After finishing, use the `explain-decisions` skill.
