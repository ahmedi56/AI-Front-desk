# Putting it on a server (VPS)

**Rule:** you or Claude may run a command on the server only after reading it and understanding it.
When Claude Code helps, use the `deploy-vps` skill. It shows every command first and waits for your yes.

## What you will have at the end
- Caddy (the front door): the only thing open to the internet (ports 80 and 443). It gets the https certificate by itself.
- The app and the worker (the background job), in Docker, running as a normal user, read-only files, no extra powers.
- Postgres, on a private network. No port open to the internet. The app logs in with a LIMITED database role.
- Encrypted daily backups, and an alert if the site goes down.

## Steps

1. **Buy the server and point the domain** (see `docs/SETUP-ACCOUNTS.md`, part 2).
2. **First login and hardening.** Add your SSH public key when you create the server. Log in as root once.
   Read `deploy/scripts/harden-server.sh`, then run it. It turns off password login, opens only ports 22/80/443,
   blocks guessing, turns on automatic security updates, and installs Docker.
   Open a second terminal and log in as the new user BEFORE closing the first one.
3. **Copy the project** to the server (`git clone` of your private repository, or `scp`). Put it in `/srv/frontdesk`.
4. **Make the secrets file.**
   ```bash
   cp .env.example .env && chmod 600 .env
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"        # for SECRET_KEY, DB_OWNER_PASSWORD, DB_APP_PASSWORD, VAPI_WEBHOOK_SECRET
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # for ENCRYPTION_KEY
   ```
   **Write ENCRYPTION_KEY in your password manager. If you lose it, connected calendars must be connected again.**
5. **Start everything.**
   ```bash
   cd deploy && docker compose --env-file ../.env up -d --build
   docker compose ps                  # app, worker, db, caddy should be "running"; migrate says "exited (0)" which is correct
   curl https://YOUR-DOMAIN/healthz   # {"ok":true}
   ```
   The app refuses to start in production if a key is missing or weak, and tells you which one (`docker compose logs app`).
6. **Create your admin login** (`docs/SETUP-ACCOUNTS.md`, part 9).
7. **Backups.** Create a passphrase file only root can read: `echo 'a long random passphrase' > /root/.frontdesk-backup-pass; chmod 600 /root/.frontdesk-backup-pass`.
   Add to root's crontab (`crontab -e`):
   ```
   15 3 * * *  /srv/frontdesk/deploy/scripts/backup.sh >> /var/log/frontdesk-backup.log 2>&1
   */5 * * * * /srv/frontdesk/deploy/scripts/uptime-check.sh https://YOUR-DOMAIN/healthz you@example.com
   ```
   **Copy the backups off the server** (a second place, for example an object-storage bucket with `rclone`, or another small server).
   A backup on the same machine is not a real backup.
   **Test a restore once a month:** `deploy/scripts/restore-test.sh /var/backups/frontdesk/<file>`.
8. **Outside uptime monitor.** Make a free account at UptimeRobot or Better Stack and watch `https://YOUR-DOMAIN/healthz` every 1-5 minutes, alert to your phone.
9. **Connect Twilio and Vapi** to your domain (`docs/SETUP-ACCOUNTS.md`), then do `docs/VERIFY-WITH-VENDORS.md`.

## Updating later
```bash
cd /srv/frontdesk && git pull && cd deploy && docker compose --env-file ../.env up -d --build
```
Run the tests first on your own computer. Take a backup before every update.

## If something looks wrong
- Turn the agent off for one business: dashboard -> "Turn OFF". For everything: set `GLOBAL_KILL_SWITCH=true` in `.env`, then `docker compose up -d`.
  Every contact is then handed to the owner with an alert.
- See `docs/RUNBOOK.md`.
