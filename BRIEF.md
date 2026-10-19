# BRIEF — Live prototype on Vercel

_Last decision change: 2026-10-19. The user asked for the simplest possible version: this is a
prototype linked from a portfolio. Progress lives in git; evidence lives in test and run output._

## Goal
A **Live** link from the user's portfolio opens the app on Vercel, and the visitor can use it
straight away.

**Why:** People viewing the portfolio can try the real flood model with one click. Success: the
link opens the dashboard, the figures are the engine's, and the example workflow can be run.

## Scope
- **In:** frontend and Django API on one Vercel project, at one address; the visitor enters demo
  mode automatically; a temporary SQLite database and workflow folder in `/tmp`, rebuilt when the
  app starts; production settings (HTTPS, allowed hosts, CSRF, secret from the environment).
- **Not doing:** a hosted database, accounts or registration, email or SMS codes, the upload
  service, live AI, keeping visitors' data, a custom domain.

## Acceptance criteria
Status: **proposed**

- AC1: Opening the live URL goes straight to the dashboard as **Presentation Demo**, with no
  sign-in step.
- AC2: Overview, Risk Map, Exposure, Loss Analysis and Reports show the engine's figures. The
  reference scenario, common tier, shows KES 5,103,936,640.53.
- AC3: In Submissions, a visitor can process the bundled example (labelled as a replay), approve
  it and run the model, and sees the results. Model runs can start a run of the supplied portfolio.
- AC4: The app says that demo data is temporary and resets.
- AC5: File upload says the service is unavailable, and Administration says access denied.
- AC6: On Vercel, debug is off and error pages show no stack traces.
- AC7: Local development and all existing tests are unchanged.

## Decisions
| ID | Decision | Rejected alternative and why | Revisit if |
| --- | --- | --- | --- |
| D1 | SQLite and workflow files in `/tmp`, set up when the app starts; sessions in signed cookies | Hosted Postgres: more setup than a prototype needs | Visitors' work must be kept, or many visitors land on different instances |
| D2 | Automatic demo entry on Vercel only | Keep the button: one more click for every visitor | The owner wants to sign in as admin on the live site |

## Assumptions
| ID | Assumption | Wrong? | Impact | Status | How checked |
| --- | --- | --- | --- | --- | --- |
| A1 | With few visitors, one visitor's requests usually reach the same instance, so the workflow steps find each other's files | M | M | open | preview deploy; a lost submission only needs to be started again |
| A2 | The Python bundle (Django, pandas, numpy, rasterio, data) fits Vercel's 500 MB limit | L | H | open | preview deploy. Limit [sourced: vercel.com/docs/functions/limitations] |
| A3 | Responses stay under 4.5 MB | L | M | confirmed | spike: portfolio response 1.36 MB |

## Next action
On approval of the criteria: add the Vercel configuration and production settings, run the tests,
then make one preview deployment from the user's Vercel account and check AC1–AC6 on it.
