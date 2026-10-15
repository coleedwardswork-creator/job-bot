# Job bot

Every morning this pulls entry-level defense, finance, and investment roles in San Diego straight from company job boards. It drops anything senior or off-target, scores the rest, and posts them to your own dashboard website.

## The dashboard

- **Inbox:** everything new that fits, best matches on top
- **Save:** jobs you like
- **Later:** hides a job for 7 days, then it comes back to your Inbox
- **Applied:** jobs you've applied to, with the date
- **Pass:** jobs you're not interested in

You can also search, filter by defense / finance / federal, and hide anything that needs an active clearance. Jobs that come down from their company's site stay in your lists marked "no longer posted."

Your Save / Later / Applied / Pass choices are saved in the browser you use. To move them to another device, use Export, then Import on the other one.

## What it checks

- Company boards: Shield AI, Leidos, Cubic, LPL, PIMCO, Houlihan Lokey, Raymond James, Stifel, and Baird. Anduril and Epirus are included if you turn on LA/OC.
- USAJobs for federal roles around San Diego (NIWC Pacific, Navy, DCAA), GS-9 and below. This one is optional.
- Companies with no public feed (GA-ASI, Kratos, Northrop, BAE, L3Harris, Brandes) appear as links at the bottom of the dashboard.

## Setup (about 10 minutes, free)

1. **Unzip** the file. On a Mac, press Cmd + Shift + . in Finder to show the hidden `.github` folder.
2. **Make a repo.** On github.com, click + → New repository. Name it `job-bot`, set it to **Public**, and click Create. It has to be public for the free dashboard hosting. Nothing private goes in it, and your statuses stay in your browser.
3. **Upload the files.** Click "uploading an existing file" and drag in everything inside the `jobbot` folder, including `.github` and `docs`. Click Commit changes.
4. **Turn on the dashboard.** Go to Settings → Pages. Under Source, pick **GitHub Actions**.
5. **Run it once.** Go to the Actions tab and enable workflows if it asks. Click job-bot → Run workflow → Run workflow, and wait about 2 minutes.
6. **Open your dashboard** at `https://<your-github-username>.github.io/job-bot/` and bookmark it. It also works on your phone.

After that it updates on its own every morning around 7:50.

**Optional: federal jobs.** Request a free key at developer.usajobs.gov/apirequest. Then add two secrets in Settings → Secrets and variables → Actions: `USAJOBS_API_KEY` and `USAJOBS_EMAIL`.

## If something breaks

- **The run fails at "Save job list":** go to Settings → Actions → General → Workflow permissions, pick "Read and write permissions," and save.
- **The run fails at the Pages steps:** make sure Settings → Pages → Source is set to GitHub Actions.
- **The dashboard lists "Boards that failed":** open that company's careers page in your browser, copy the URL, and paste it into `config.toml`. You can edit it right on GitHub with the pencil icon.

## Changing what it looks for

Everything is in `config.toml`:

- **Add a company:** copy a `[[company]]` block and paste its careers URL
- **Include LA/OC:** set `include_la_oc = true`
- **Allow more experience:** change `max_years_required`
- **Tune titles:** edit `title_include` and `title_exclude`
