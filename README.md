# Above the Noise (ATN)

A signal log by Sangría. The feed is built from the #above-the-noise Slack channel, hosted on GitHub Pages and embedded on sangria.agency/atn.

## How it works

```
Slack #above-the-noise ──(every 15 min)──> GitHub Action ──> docs/feed.json + docs/img/ ──> GitHub Pages ──> Wix embed
```

- `docs/index.html`: the page (design, filters, dark mode). Reads `feed.json` on load.
- `docs/feed.json`: every post. Rewritten by the sync, never edited by hand.
- `docs/img/`: card images pulled from Slack, resized to 760px wide.
- `scripts/sync_slack.py`: reads the channel and updates the two items above.
- `.github/workflows/sync.yml`: checks Slack every 15 minutes and publishes when there are new posts.

## Posting format in Slack

```
#Campaign: KFC Nederland got Pickle Obsessed
POST: https://lnkd.in/...
ARTICLE: https://...
(attach one image)
```

- First line: one or more tags (`#AI + #Film:`), a colon, then the headline. Code-block formatting is optional.
- `LABEL: link` lines become link chips. A bare link gets labeled Post, Reel, Video or Article automatically.
- Any other text line becomes a short description under the headline.
- The first attached image becomes the card background. With no image, the card uses its tag color.
- Messages that don't start with `#Tag:` are ignored, so the channel can still hold conversation.
- Edits and deletions in Slack show up on the next sync for posts from the last 7 days.

## Setup

1. **Slack app**: create it at api.slack.com/apps (From scratch). Give the bot the `channels:history` and `files:read` scopes, install it, and copy the `xoxb-` token. Then run `/invite @<app name>` in #above-the-noise.
2. **Repo settings**
   - Secrets and variables → Actions → Secrets: `SLACK_BOT_TOKEN` = the xoxb token
   - Secrets and variables → Actions → Variables: `SLACK_CHANNEL_ID` = `C0B1T18SH0Q`
   - Pages → Source: **GitHub Actions**
3. **First run**: go to Actions → "Sync from Slack and publish" → Run workflow.

## Manual controls

- **Update now**: Actions → Run workflow.
- **Rebuild everything from Slack**: Run workflow with "full resync" checked.
- **Change the schedule**: edit the `cron` line in `.github/workflows/sync.yml`. Times are in UTC.
