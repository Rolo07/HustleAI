# Scheduled tasks

All times are **South African time (SAST, `Africa/Johannesburg`)**. The VPS
timers name that timezone explicitly, so they run correctly even if the server
clock is set to UTC. Run each task on **one machine only**: the Mac until the
VPS is live, then the VPS.

## Schedule

| Task | When | Command | What it does | Zoho requests |
| --- | --- | --- | --- | --- |
| Orders sync | **Every day, 22:00** | `hustleai-orders sync` | Compares every Zoho invoice with the Supabase copy, saves changes, flags deletions and reads missing line items until 100 of the day's requests remain. | A few for the listing, plus one per missing invoice detail |
| Reorder forecast | **Every Monday, 07:00** | `hustleai-forecast run --send` | Builds the forecast for customers expected 7–14 days out from the synced copy, saves it and sends it to Roland on WhatsApp. | One per expected or overdue customer, for contact details |
| WhatsApp gateway | **Always on** (not scheduled) | `hustleai-whatsapp serve` | Receives messages, runs Roland's commands, refers customers and sends held messages. | Only when a command needs Zoho |
| Daily summary | **Every day, 20:00** | Not built yet (PRD 08) | Will send Roland a summary of the day's activity. | — |

## Why these times

- **Zoho's daily limit** is 1,000 requests and resets at **midnight SAST**.
  Zoho's `X-Rate-Limit-Reset` header confirmed this on 2026-09-27. The sync
  runs at 22:00, two hours before the reset, so it uses what is left of the
  day without taking requests the day's work may still need.
- **The forecast** runs Monday at 07:00, after Sunday night's sync, so it uses
  fresh orders and reaches Roland at the start of the week.
- **The 24-hour rule.** A forecast sent when Roland hasn't messaged the business number
  in 24 hours is held. The approved template then asks him to reply, and the
  forecast follows his reply.

## Files

| Task | VPS (systemd) | Mac (launchd) |
| --- | --- | --- |
| Orders sync | [`hustleai-orders-sync.service`](deploy/schedule/hustleai-orders-sync.service) + [`.timer`](deploy/schedule/hustleai-orders-sync.timer) | [`com.hustleai.orders-sync.plist`](deploy/schedule/com.hustleai.orders-sync.plist) |
| Reorder forecast | [`hustleai-forecast.service`](deploy/schedule/hustleai-forecast.service) + [`.timer`](deploy/schedule/hustleai-forecast.timer) | [`com.hustleai.reorder-forecast.plist`](deploy/schedule/com.hustleai.reorder-forecast.plist) |
| WhatsApp gateway | [`hustleai-gateway.service`](deploy/schedule/hustleai-gateway.service) | Not supported: needs a public HTTPS address |

## Installing

**VPS:**

```sh
sudo cp deploy/schedule/hustleai-*.service deploy/schedule/hustleai-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hustleai-orders-sync.timer hustleai-forecast.timer hustleai-gateway.service
systemctl list-timers 'hustleai-*'     # shows the next run of each timer
```

Before enabling the forecast timer, run `hustleai-forecast start-date today`
once so the forecast only counts orders from go-live.

**Mac**, until the VPS is live. The Mac's timezone must be South African time:

```sh
cp deploy/schedule/com.hustleai.orders-sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.hustleai.orders-sync.plist
```

To stop a Mac task, run `launchctl unload` with the same path and delete the
file. Unload both Mac tasks before enabling the VPS timers.

## Current state (2026-09-27)

| Task | Installed |
| --- | --- |
| Orders sync | Not yet. The Mac install needs Roland's approval; commands above. |
| Reorder forecast | Not yet. Waiting for the VPS go-live and start date. |
| WhatsApp gateway | Not yet. Waiting for the business number and VPS. |

After a missed run, the systemd timers catch up once at boot (`Persistent=true`),
and launchd runs a missed job when the Mac wakes.
