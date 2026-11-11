# Garmin Training Report

A self-hosted Garmin Connect Personal API report generator. It runs on GitHub Actions, fetches your previous day's activities, downloads FIT files, analyzes power intervals, and publishes Markdown and JSON reports into your own repository.

> **Privacy first:** reports contain personal health and activity data. Keep your generated-report repository private. This template intentionally excludes personal reports and credentials.

## Features

- Daily scheduled run with GitHub Actions.
- Garmin Connect Personal API authentication using a repository secret.
- Previous-day activity summaries and activity details.
- FIT-file power analysis: power-zone time, sustained work intervals, interval averages, first-half/second-half changes, and comparisons between repetitions in the same zone.
- Markdown and JSON output in `reports/`.
- Manual run for a selected report date.

## Create your own repository

1. Open the source template repository on GitHub.
2. Click **Use this template → Create a new repository**.
3. Choose your own account or organization and a repository name.
4. For privacy, choose **Private**.
5. Create the repository. Your copy is independent; later changes to the source repository are not automatically applied.

If you cannot see **Use this template**, the source repository owner may need to grant you at least **Read** access. A private template is not publicly discoverable and cannot be used by people who do not have access to it.

## Configure your repository

### 1. Add your Garmin API key

In your new repository, go to **Settings → Secrets and variables → Actions → New repository secret**.

Create this secret:

- Name: `GARMIN_API_KEY`
- Secret: your own Garmin Connect Personal API key

Do not paste the key into source code, workflow files, issues, or chat messages. Do not use another person's key. This project does not need your Garmin password or a manually copied access token.

### 2. Set your personal training configuration

Go to **Settings → Secrets and variables → Actions → Variables → New repository variable** and optionally create:

| Variable | Example | Purpose |
|---|---:|---|
| `FTP_WATTS` | `250` | Your current FTP in watts; default is 250 |
| `TIMEZONE` | `Asia/Shanghai` | IANA timezone used to determine the previous calendar day and format local timestamps |

Use your own FTP value. The default is only a placeholder, not a recommended training value. Common timezone examples include `Asia/Shanghai`, `America/Los_Angeles`, and `Europe/London`.

The scheduled workflow runs at **00:00 UTC**. With `TIMEZONE=Asia/Shanghai`, this is 08:00 China Standard Time. If you want the workflow to run at a different local time, edit the `cron` expression in `.github/workflows/daily-report.yml` (GitHub Actions cron is in UTC).

### 3. Enable Actions write access

The workflow commits generated reports back to your repository. In **Settings → Actions → General → Workflow permissions**, allow **Read and write permissions** for `GITHUB_TOKEN`.

The workflow itself requests only `contents: write`.

## Run your first report

1. Open **Actions → Garmin Daily Training Report**.
2. Select **Run workflow**.
3. Leave `report_date` empty to fetch yesterday in your configured timezone, or enter a date as `YYYY-MM-DD`.
4. Open the run and inspect the logs.
5. When successful, find `reports/YYYY-MM-DD.md` and `reports/YYYY-MM-DD.json`.

The first scheduled run occurs at the next configured cron time. Manual runs are useful for validating your API key and configuration first.

## Output and metric notes

- `reports/YYYY-MM-DD.md`: readable daily summary and power-interval analysis.
- `reports/YYYY-MM-DD.json`: structured activity and analysis data.
- Power zones are calculated from the configured FTP.
- Sustained work intervals require at least 120 seconds of qualifying samples. Power is smoothed with a trailing 5-second mean for zone classification.
- TSS is an estimate derived from normalized power, duration, and configured FTP. It is not Garmin's official Training Load.
- Some activities may not provide power data or a downloadable FIT file; those activities will still appear with a note that interval analysis is unavailable.
- Averages across activities are arithmetic means of activity-level averages, not duration-weighted session averages.

## Troubleshooting

| Symptom | Check |
|---|---|
| `Missing GARMIN_API_KEY` | Add the repository secret with the exact name `GARMIN_API_KEY`. |
| Garmin token request fails | Confirm your Personal API access/key is valid and has not been revoked. |
| HTTP 401 | The generated access token was rejected; verify the key and Garmin API access. |
| HTTP 429 | Garmin rate limiting; wait and retry later. |
| No report commit appears | Check whether the report files changed and whether Actions has read/write repository permission. |
| FIT analysis unavailable | The activity may not expose a FIT download or usable power records. The summary report can still be generated. |

## Project layout

```text
.
├── .github/workflows/daily-report.yml
├── reports/                 # Generated personal reports (ignored by Git)
├── src/garmin_report.py
├── .gitignore
└── requirements.txt
```

## References

- [Garmin Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api/)
- [Personal API FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq)
- [Personal API Swagger](https://developer.garmin.com/gc-developer-program/personal/swagger-docs/)

## License and use

Check Garmin's current Personal API terms and eligibility requirements before using this project. This repository is provided as a starting point; you are responsible for your own API access, credentials, data handling, and training settings.
