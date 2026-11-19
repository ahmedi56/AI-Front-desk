#!/bin/bash
# Every 5 minutes from cron. If the site is down, send an email (needs "mail" set up) and write a line to the log.
# A free outside monitor (UptimeRobot / Better Stack) pointing at https://YOUR-DOMAIN/healthz is MORE reliable,
# because it still works when this whole server is down. Use both.
URL="${1:?usage: uptime-check.sh https://your-domain/healthz [alert-email]}"
EMAIL="${2:-}"
if ! curl -fsS --max-time 10 "$URL" > /dev/null; then
  echo "$(date -u +%FT%TZ) DOWN $URL" >> /var/log/frontdesk-uptime.log
  [ -n "$EMAIL" ] && echo "Front desk health check failed: $URL" | mail -s "FRONT DESK DOWN" "$EMAIL" || true
  exit 1
fi
