# Deploying Open Slot behind HTTPS

Open Slot has to be served over HTTPS: the page can tell a person's click from an agent's call
only by the `Sec-Fetch-Site` label a browser sends to HTTPS pages (and to `localhost`), so on plain
HTTP no Confirm button can work. This guide puts the server on one of two hosts. Both are sittings
of about half an hour, with someone at the keyboard to paste the Qloo key and click the create
button. If an assistant drives the console for you (in this project, Claude Code in your browser),
it fills every field below and stops: you review them, type the key yourself, and click the create
button yourself.

| | Render, free web service | One EC2 instance with Caddy |
|---|---|---|
| Use it | first | when Render is refused, suspended or too slow to wake |
| HTTPS | Render's own, on `<name>.onrender.com` | Caddy's, on `<ip-with-dashes>.sslip.io` |
| Sleeps | after 15 minutes without traffic, and wakes in about a minute | never |
| Workspaces | lost at each sleep, restart and deploy | lost when the service restarts |
| Response cache, call ledger | lost with the disk, at the same times | files that survive a restart of the service, not of the instance |
| Where the key goes | an environment variable field | one marked line of a script |
| If a card, phone or ID screen appears | stop and tell whoever drives | the same |

Everything below was read from the hosts' own documentation on 20 Oct 2026 (the pages are listed
at the end). What no one could check without an account is under "Not verified".

## What every deploy sets

The server reads its settings from the environment (`.env.example` lists every name). On a host:

| Setting | Why |
|---|---|
| `QLOO_API_KEY` | your own hackathon key; the only secret; typed by you and never by an assistant |
| `OPEN_SLOT_PROVIDER=live` | with no key, a live server refuses to start and says `key: missing`, so a forgotten key shows at the sitting and not to a judge; `fake` serves synthetic sample data, for a rehearsal |
| `OPEN_SLOT_ALLOWED_HOSTS` | the public host name, without a port; behind Render it is read from `RENDER_EXTERNAL_HOSTNAME` instead |
| `OPEN_SLOT_TRUST_PROXY=1` | a proxy sits in front, so the client address is the right-most `X-Forwarded-For` value |
| `OPEN_SLOT_DAILY_LIVE_CALLS` | the server's daily ceiling of live Qloo calls, from the quota the key's email states |
| `PORT` or `OPEN_SLOT_PORT` | the port to listen on; `PORT` wins when both are set; the default is 8760 |
| `OPEN_SLOT_BIND` | `0.0.0.0` where the host's proxy reaches the server over a private network (Render); the default `127.0.0.1` behind Caddy on the same machine |

The server refuses to start on a non-loopback bind with no allowed host, so it can never answer
every `Host`. It logs nothing per request, and its health check, `GET /healthz`, answers `ok` to any
`Host`.

## Render

### The form, field by field

In the Render dashboard: **New**, then **Web Service**, then the **Git Provider** tab. Connecting
GitHub is an OAuth consent that only you give; pick only the `open-slot` repository. (The
**Public Git Repository** tab does not support auto-deploys, so it is only for a throwaway test.)
The labels are the ones Render's own pages use.

| Field | Value |
|---|---|
| Name | `open-slot-qloo` |
| Language | `Python 3` |
| Branch | `main` |
| Region | `Oregon (US West)`, the form's default; a region cannot be changed after creation |
| Root Directory | empty |
| Build Command | `true` |
| Start Command | `python -m open_slot serve` |
| Compute | `Free` |
| Health Check Path (Advanced) | `/healthz` |
| Auto-Deploy | `On Commit` (the default), so publishing a change redeploys; switch it to `Off` under Settings to freeze the demo |

Environment Variables (Advanced), one row each:

| Key | Value |
|---|---|
| `QLOO_API_KEY` | you paste it; whoever drives the form types the key's name and leaves the value empty |
| `OPEN_SLOT_PROVIDER` | `live` |
| `OPEN_SLOT_BIND` | `0.0.0.0` |
| `PORT` | `10000` |
| `OPEN_SLOT_TRUST_PROXY` | `1` |
| `OPEN_SLOT_DAILY_LIVE_CALLS` | `600` |

Left unset on purpose: `OPEN_SLOT_ALLOWED_HOSTS` (Render sets `RENDER_EXTERNAL_HOSTNAME` to the
service's `onrender.com` name, and the server reads it, so nobody has to guess the final name before
**Create**), `OPEN_SLOT_PORT`, and `PYTHON_VERSION`.

Why these values:
- **Name `open-slot-qloo`.** `open-slot.onrender.com` already belongs to an unrelated app, so the plain
  name would send a judge who guesses the address to the wrong product. The address becomes
  `https://open-slot-qloo.onrender.com`, or carries a suffix if that is taken; the service page shows it.
- **Python.** The app needs 3.11 or newer. The repository's `.python-version` reads `3.13`, and
  Render uses it (a `PYTHON_VERSION` variable would override it, so none is set); without it a
  service created today gets 3.14.8, which also runs the app. The deploy log should name the version.
- **Build Command `true`.** There is nothing to install: the app uses only the standard library.
  Render requires a build command, and its own examples use `true` for the same case.
- **Start Command `python -m ...`.** Render's pages show `python`, never `python3`, in start commands.
  If the log says `python: command not found`, change it to `python3 -m open_slot serve`.
- **`PORT=10000`.** Render's pages call 10000 the default port and list `PORT` among the optional
  variables, so it is set to make the port certain. The server binds the port in `PORT`.
- **No Blueprint.** A `render.yaml` would also work, but a Blueprint's compute plan defaults to a
  paid one unless `plan: free` is written, and whether Blueprints ask for a card is unverified.

### The sitting, about 25 minutes

1. Sign in to Render. A card, phone or ID screen at any step: stop and say so.
2. The assistant fills the form above, leaving the value of `QLOO_API_KEY` empty, and says so.
3. You read every field against the tables. You paste the key into the `QLOO_API_KEY` value yourself
   and click **Create Web Service** (the first-deploy page calls the button **Deploy**).
4. Watch the deploy page. Render builds, starts the service and sends it health checks; when every
   check passes it shows **Live**. Expect one to three minutes (an estimate: the build is a clone).
   If the checks fail for 15 minutes Render cancels the deploy.
5. Open the service's `onrender.com` address in a private window and read the result (below).

### What a free service does

- After 15 minutes with no inbound traffic Render spins it down. The next visit shows Render's own
  loading page for about a minute, and then an empty workspace: the server forgets every workspace
  when its process ends, and the page's **Try the sample programme** starts again. A tab left open
  from before says "Reload the page to start a new workspace".
- Render sends health checks every few seconds. Its pages do not say whether they count as traffic,
  so whether the service sleeps at all is read from the first walk (see "Not verified"). One service
  that stayed awake all month would use 744 of the workspace's 750 free instance hours, so it would
  not be suspended either way.
- The disk is ephemeral: the response cache and the call ledger are lost at each sleep, restart and
  deploy, which is why the daily ceiling bounds one run of the server here and not a calendar day.
- Render may restart a free service at any time, and the 750 free hours belong to the workspace, shared
  by every free service in it.

## EC2 with Caddy

### The launch wizard, field by field

Open EC2 in a region your account allows: `ap-southeast-2` (Sydney) is the one this project's own
account allows, and an organisation policy can block others. The labels are the console's.

| Field | Value |
|---|---|
| Name | `open-slot` |
| Application and OS image | `Amazon Linux 2023`, `x86_64` |
| Instance type | `t3.micro` |
| Key pair | `Proceed without a key pair` |
| Network | the default VPC; Auto-assign public IP `Enable` |
| Security group | new, `open-slot-sg`; inbound `TCP 80` and `TCP 443` from `0.0.0.0/0`; no SSH |
| Storage | the default 8 GiB `gp3` |
| Advanced details, Metadata version | `V2 only (token required)` |
| Advanced details, User data | the whole of [`deploy/ec2/user-data.sh`](../deploy/ec2/user-data.sh), pasted as text |
| Number of instances | `1` |

Port 80 is for the certificate's HTTP-01 challenge and for the redirect to HTTPS; port 443 is the
site. There is no SSH and no key pair, so nothing can be changed on the instance afterwards: a wrong
key or a typo means launching again, which gives a new address and a new certificate.

### The key line

The script has one line you edit:

```
QLOO_API_KEY=            # <<< the one line to edit: the Qloo key goes here, after the =
```

Whoever drives the console pastes the script with that line empty and tells you so. You click into the
text box, put the cursor straight after the `=`, type the key (no quotes, no spaces) and nothing else.
The assistant takes no screenshot while the key is in the box. With `PROVIDER=live` (the default) and an
empty key the script stops before installing anything and says why in the instance's system log; with
`PROVIDER=fake` it needs no key and serves synthetic sample data, for a rehearsal.

Where the key ends up: AWS keeps the script on the instance (`/var/lib/cloud/instances/<id>/`) and
shows it to anyone signed in to the account who opens the instance's user data, and any process on the
instance can read it through the metadata service. The script copies the key into `/etc/open-slot.env`,
readable by root only, for the service. The key is free, personal and deactivated after judging.

### The sitting, about 20 minutes

1. Sign in to the AWS console if the session has lapsed. A card, phone or ID screen: stop and say so.
2. The assistant opens EC2 and fills the wizard above. You review it field by field.
3. You type the key into the marked line, check the summary panel and click **Launch instance**.
4. After about three to five minutes (an estimate: boot, `dnf`, a download, a certificate) the
   instance's **Actions**, **Monitor and troubleshoot**, **Get system log** shows these lines, and
   the last one is the signal:

   ```
   open-slot: Python 3.13.x at /usr/bin/python3.13
   open-slot: this instance is <ip>, so the site is https://<ip-with-dashes>.sslip.io/
   open-slot: the app answers on 127.0.0.1:8760 (live), and the Caddy checksum matched
   open-slot: python3.13, caddy 2.11.7 (checksum ok), certificate ok after <n> seconds: https://<...>/
   ```

   Caddy's and the app's own lines are in the same log. After ten minutes with no certificate the
   script says so and stops; the cause is in Caddy's lines. A rate limit on `sslip.io` is answered by
   launching again with `DNS_NAME=nip.io` in the script, which is the same service under its other name.
5. Open `https://<ip-with-dashes>.sslip.io/` in a private window and read the result (below).

What the script does: installs Python 3.13 (else 3.12, else 3.11) from Amazon's repositories, because
`/usr/bin/python3` stays Python 3.9 for the life of AL2023 and the app needs 3.11 or newer; reads the
public address from the instance metadata (version 2, token first); downloads the repository's
`main` branch; downloads Caddy 2.11.7 and checks its SHA-512 against the release's published checksum
before running it; writes a two-line Caddyfile that proxies the site name to `127.0.0.1:8760`; and
starts two services. The app listens on loopback only, with the public name as its one allowed
`Host`; Caddy terminates TLS, redirects HTTP to HTTPS, passes `Host`, `Origin` and `Sec-Fetch-Site`
through unchanged, and appends the client address to `X-Forwarded-For`.

**Never stop the instance.** Without an Elastic IP the public address survives a reboot but not a stop;
a stop would change the name, and the certificate with it. Terminating it, with `open-slot-sg`, is the
last step after judging.

Cost: an instance and a public IPv4 address are billed by the hour, to the account's credits on a free
plan. Read the Billing page before launching; this guide needs no card.

## Reading the result

Do these on the hosted address, in a private window, after the service is Live (or the log says
"certificate ok"):

1. `GET /healthz` answers `ok`. It also answers from a foreign `Host`; the page and the API do not.
2. Open the page. It must say **Live: Qloo is being called** (or **Synthetic sample data** if you chose
   `fake`), with no plain-HTTP banner and no "your browser does not label requests" banner.
3. In the browser's developer console on the page, run

   ```js
   fetch("/api/state").then(r => r.json()).then(s => console.log(s.actor, s.actorWhy, s.limiterKey, s.server))
   ```

   Expected: `person same_origin forwarded` and `{provider: "live", mode: "live", key: "set"}`.
   `actorWhy` is `no_fetch_metadata` if something between the browser and the server strips the
   `Sec-Fetch-*` labels, and then no person's click counts; on Render that would be the proxy, so this is
   the line to read first. `limiterKey` is `forwarded` when the proxy's address is trusted correctly;
   `peer` means every visitor looks like one client to the workspace-creation limit (the total of 120 a
   minute still holds).
4. Typing `/api/state` into the address bar of the same browser gives `agent` and `typed_address`:
   an address typed in the bar is never a click.
5. Walk the golden path: **Try the sample programme**, **Confirm** each act (the button must work),
   **Run the agent**, open a card, **Propose nights**, **Assign**, **Draft inquiry**, name the sender,
   **Approve inquiry**, then run `approve_inquiry` in the agent console: `not_a_tool`.
6. Connect a client to the agent address the page shows under "Connect your own agent": `curl` the
   README's `tools/list` example against `<agent address>/mcp`, or run
   `python3 -m open_slot mcp --address <agent address>` and send it one line.

When something is off:

| What you see | What it means | What to do |
|---|---|---|
| Render deploy fails, log says `key: missing` | `OPEN_SLOT_PROVIDER=live` and no key | paste the key into `QLOO_API_KEY`, or set `OPEN_SLOT_PROVIDER` to `fake` |
| Render deploy fails, log names `OPEN_SLOT_ALLOWED_HOSTS` | `RENDER_EXTERNAL_HOSTNAME` was empty | set `OPEN_SLOT_ALLOWED_HOSTS` to the name the dashboard shows |
| `/healthz` is `ok`, the page is 403 `bad_host` | the `Host` the proxy forwards is not the allowed name | set `OPEN_SLOT_ALLOWED_HOSTS` to the `onrender.com` name; if it persists the proxy rewrites `Host`: use EC2 |
| the page is 502 on EC2 | the app is not running | read the system log: the app's lines say why (a wrong key is a 401 from Qloo, not a crash) |
| the browser warns about the certificate | Caddy has not got one yet, or fell back to its own | wait, then read Caddy's lines in the system log |
| `actorWhy` is `no_fetch_metadata` on the HTTPS page | labels are stripped on the way | the five human verbs cannot work; use the other host |
| Render shows its loading page | the free service is waking | wait about a minute |

## After the deadline

The hosted demo has to stay up through the end of judging. Afterwards: on Render, suspend or delete the
service; on EC2, terminate the instance and delete `open-slot-sg`. The key is deactivated by Qloo after
judging.

## Not verified

No one could check these without an account, and the first sitting reads them:
- That Render's `python` is the version `.python-version` names, and that the deploy log says so.
- That Render's proxy forwards `Host` and `Sec-Fetch-Site` unchanged and appends exactly one
  `X-Forwarded-For` value. The result steps above read both.
- Whether Render's health checks keep a free service awake.
- That Let's Encrypt issues for the `sslip.io` name on the first try, and that the account's policy
  allows the second security-group rule and the new instance.
- The times given above: they are estimates.

## Sources, read on 20 Oct 2026

- Render: `https://render.com/docs/free.md`, `web-services.md`, `health-checks.md`,
  `environment-variables.md`, `configure-environment-variables.md`, `python-version.md`, `deploys.md`
  and `blueprint-spec.md`, all under `https://render.com/docs/`.
- AWS: <https://docs.aws.amazon.com/linux/al2023/ug/python.html> (3.11, 3.12, 3.13 and 3.14 are
  packaged; `/usr/bin/python3` stays 3.9), the AL2023 package list for release 2023.12.20260930
  (`python3.13` is 3.13.15), and <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/user-data.html>
  (user data is limited to 16 KB, runs as root, runs on first launch only and is not deleted).
- Caddy: <https://caddyserver.com/docs/automatic-https> (a public name gets a public certificate, an
  IP address only a local one, so the name is needed), <https://caddyserver.com/docs/running>,
  the `reverse_proxy` page (`Host` passes through, `X-Forwarded-*` from clients is ignored), and the
  v2.11.7 release's published checksums.
- <https://sslip.io/>: the name is a DNS answer for the address in it, and a certificate for it is
  obtained with the HTTP-01 challenge; if it is rate-limited, try `nip.io`.
