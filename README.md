# GenuineFactoryParts ARI PartStream Scraper

Scrapes parts catalog from [genuinefactoryparts.com](https://www.genuinefactoryparts.com/en_US/ari-partstream.html) via the ARI PartStream JSON API.

**Target path:** MTD Merged Data Staging → Troy-Bilt → 11-Push Walk-Behind Mowers → 2024 & 2025 Models → all Assemblies → all Parts

**Output:** `parts.csv` with columns:

| Column | Description |
|---|---|
| `unique_key` | `<full_path>\|<oem>` — used for upsert deduplication |
| `path` | Full hierarchy path to the assembly (e.g. `MTD Merged Data Staging - Troy-Bilt - 11-Push Walk-Behind Mowers - 2025 Models - 11A-02BT066 TB90B (2025) - Blade Adapter`) |
| `ref` | Reference number on the diagram |
| `oem` | OEM part number |
| `description` | Part description |
| `updated_at` | UTC timestamp of last scrape |

Logs go to `scraper.log` and stdout (start time, records collected / new / updated / errors).

---

## Requirements

- Python 3.10+
- `pip install requests beautifulsoup4`

---

## Local run

```bash
pip install requests beautifulsoup4
python scraper.py
```

On subsequent runs, existing records are **upserted**: new parts are added, changed descriptions/refs are updated, duplicates are skipped.

---

## Loading results into Google Sheets

1. Open [Google Sheets](https://sheets.google.com) → **File → Import → Upload** → select `parts.csv`.
2. Choose "Replace spreadsheet" or "Append rows" as needed.

For automated upload via Google Sheets API, see `sheets_upload.py` (optional helper — requires a service account JSON key from Google Cloud Console).

---

## Deploy on Windows Server — Task Scheduler

### One-time setup

```powershell
# 1. Clone / copy the project folder to the server, e.g. C:\scrapers\pars1
# 2. Install dependencies
pip install requests beautifulsoup4

# 3. Create the scheduled task (runs daily at 06:00)
$action  = New-ScheduledTaskAction -Execute "python" -Argument "C:\scrapers\pars1\scraper.py"
$trigger = New-ScheduledTaskTrigger -Daily -At "06:00"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "GFP_Scraper" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest
```

### Manual trigger

```powershell
Start-ScheduledTask -TaskName "GFP_Scraper"
```

### View last run status

```powershell
Get-ScheduledTaskInfo -TaskName "GFP_Scraper"
```

---

## Deploy via GitHub Actions

Create `.github/workflows/scrape.yml` in your repository:

```yaml
name: Scrape Parts

on:
  schedule:
    - cron: '0 4 * * *'   # daily at 04:00 UTC
  workflow_dispatch:        # allow manual trigger

jobs:
  scrape:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install requests beautifulsoup4

      - name: Run scraper
        run: python scraper.py

      - name: Commit updated CSV
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add parts.csv scraper.log
          git diff --cached --quiet || git commit -m "chore: update parts.csv [skip ci]"
          git push
```

> **Note:** The workflow commits the updated `parts.csv` back to the repository after each run. If you prefer to store results elsewhere (e.g. Google Drive, S3), replace the last step with an upload action.

---

## How upsert works

Each row has a `unique_key` = `path|oem`.  
On every run the script:
1. Loads the existing `parts.csv` into memory (keyed by `unique_key`).
2. Scrapes fresh data.
3. For each part: **insert** if key is new, **update** if ref/description changed, **skip** if identical.
4. Writes the merged result back to `parts.csv`.

This guarantees no duplicate rows and preserves history of part changes.
