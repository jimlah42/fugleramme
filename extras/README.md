# Extras

Local additions for this frame, not part of upstream Fugleramme.

## Weekly bird email

`weekly_email.py` sends a "birds around home" email every Sunday morning, covering the week
before (Sunday to Saturday): the week's
collage, the new birds, the busiest time of day and the most-heard birds. It reads
BirdNET-Go's API and draws pictures with the frame's own renderer and artwork.

Settings go in `~/.config/fugleramme/weekly-email.json` (keep it private, `chmod 600`):

```json
{
  "username": "you@gmail.com",
  "app_password": "the 16-character Gmail app password",
  "from": "Bird frame <you@gmail.com>",
  "to": ["someone@example.com"],
  "cc": ["you@example.com"],
  "household": "Brindisi",
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 465,
  "birdnet_url": "http://localhost:8080"
}
```

The app password comes from https://myaccount.google.com/apppasswords (needs 2-Step
Verification on). Delete it there to stop the email from sending.

```bash
uv run python extras/weekly_email.py --preview /tmp/week.html   # look at this week's email
uv run python extras/weekly_email.py --send                     # send last week's now
bash extras/install-weekly-email.sh                             # schedule it for Sundays at 7:30am
systemctl list-timers fugleramme-weekly-email.timer             # when it runs next
journalctl -u fugleramme-weekly-email                           # what it did
```

A week with no birds sends nothing. Birds heard only once at low confidence are left
out as likely false alarms.
