# Garmin Training Report Template

A self-hosted Garmin Connect Personal API reporting project. GitHub Actions can fetch activity and sleep data, analyze FIT files, and publish Markdown/JSON reports. Optional Intervals.icu integration creates reviewed weekly training-plan drafts and can import an explicitly approved plan into your calendar.

> **Privacy first:** this is a public template repository. Do not add personal API keys here or run it as your personal report repository. Use **Use this template → Create a new repository**, choose **Private**, and configure secrets in that private copy. Generated activity and sleep reports contain personal data.

## Automation features

| Workflow | Trigger | Result |
|---|---|---|
| Garmin Daily Training Report | Daily at 00:00 UTC; manual date selection | Daily Markdown/JSON report with FIT power-interval and elevation/climb analysis |
| Garmin Weekly Training Report | Monday at 00:00 UTC; optional Monday date | Previous complete Monday–Sunday report |
| Garmin Latest Sleep Data | Manual; optional sleep date | Sleep Markdown/JSON report |
| Intervals.icu Weekly Training Plan Draft | Manual | Creates a draft under plans/drafts/ and opens a review Issue; does not modify the calendar |
| Intervals.icu Import Approved Plan | Manual, with explicit confirmation | Imports the selected reviewed JSON plan and verifies calendar entries |

The daily and weekly schedules are expressed in UTC. 00:00 UTC is 08:00 in China Standard Time.

## Create your private report repository

1. Select **Use this template → Create a new repository**.
2. Create the new repository under your account or organization.
3. Set its visibility to **Private**.
4. Configure the secrets and variables below in that private copy.
5. Enable Actions write permissions if you want workflows to commit generated reports.

Do not configure personal credentials or generate reports in this public template repository.

## Configure GitHub Actions

Open **Settings → Secrets and variables → Actions** in your private copy.

### Required secrets

| Secret | Used by | Purpose |
|---|---|---|
| GARMIN_API_KEY | Daily, weekly, and sleep reports | Garmin Connect Personal API key |
| INTERVALS_API_KEY | Weekly training-plan draft and import | Intervals.icu API key |
| DEEPSEEK_API_KEY | Weekly training-plan draft | DeepSeek API key for generating the plan |

Garmin and Intervals.icu secrets are only needed if you use those workflows. DEEPSEEK_API_KEY is only needed for the AI-generated plan draft. Never commit keys or tokens to source files, reports, Issues, or chat messages.

### Optional repository variables

| Variable | Default | Purpose |
|---|---:|---|
| FTP_WATTS | 250 | FTP in watts used for power zones, IF/TSS estimates, and training-plan context |
| TIMEZONE | Asia/Shanghai | IANA timezone used for report dates and local timestamps |

Set FTP_WATTS to your own current value. The default of 250 W is a template placeholder, not a training recommendation.

### Enable workflow write permissions

For workflows that commit reports or drafts, go to **Settings → Actions → General → Workflow permissions** and enable **Read and write permissions** for GITHUB_TOKEN.

The report workflows request contents: write; the weekly-plan draft workflow also requests issues: write to open a review Issue. The calendar-import workflow only needs repository read access; the Intervals.icu API key authorizes the external calendar write.

## Run the workflows

- **Daily report:** Actions → Garmin Daily Training Report → Run workflow. Leave report_date empty to report yesterday, or enter YYYY-MM-DD.
- **Weekly report:** Actions → Garmin Weekly Training Report → Run workflow. Leave week_start empty for the previous complete week, or provide a Monday date in YYYY-MM-DD format.
- **Sleep report:** Actions → Garmin Latest Sleep Data → Run workflow. Leave sleep_date empty to use the latest available record, or enter YYYY-MM-DD.
- **Training-plan draft:** run Intervals.icu Weekly Training Plan Draft, then review the generated Markdown/JSON under plans/drafts/ and the review Issue.
- **Calendar import:** only after reviewing the plan, run Intervals.icu Import Approved Plan, enter the JSON path, and check confirm_import.

Draft generation never writes to the Intervals.icu calendar. Import is a separate, manually triggered workflow with an explicit confirmation checkbox.

## Output and metric notes

- Daily reports contain activity summaries, FIT-derived power intervals, and elevation/climb analysis when FIT data is available.
- Weekly reports aggregate activities, distance, duration, elevation, estimated TSS, power-zone time, and recognized work intervals.
- TSS is an estimate based on normalized power, duration, and configured FTP; it is not Garmin's official Training Load.
- FIT analysis may be unavailable for activities without downloadable FIT data or usable power records.
- Sleep reports preserve the Garmin API response alongside a readable Markdown summary.
- Personal reports are ignored by Git by default; report workflows explicitly force-add generated output in your private repository.

## Project layout

    .github/workflows/
      daily-report.yml
      weekly-report.yml
      sleep-report.yml
      weekly-training-plan.yml
      import-intervals-plan.yml
    src/
      garmin_report.py
      garmin_weekly_report.py
      intervals_training_plan.py
    scripts/
      garmin_sleep_report.py
    plans/drafts/       Generated training-plan drafts
    reports/            Generated personal reports
    requirements.txt

## References

- Garmin Connect Personal API: https://developer.garmin.com/gc-developer-program/personal/personal-api/
- Intervals.icu API: https://intervals.icu/api-docs.html
- DeepSeek API: https://api-docs.deepseek.com/

Check each provider's current terms and API requirements before use. You are responsible for your API access, credentials, privacy settings, and training configuration.
