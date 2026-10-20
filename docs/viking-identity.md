# Signing in with Viking Identity

Viking is the annotation environment these volumes are traced in. It loads the
EM images, takes the annotations, and writes the database this application
reads. Its **Identity Server** is therefore the authoritative answer to both
questions this site used to answer for itself — *who is this person* and
*which volumes may they see* — so when it is switched on, it wins.

This page is the operator's guide. The client-side protocol details come from
the integration note the Viking authors wrote for us.

## What happens when someone signs in

```text
Browser  --(1) "Sign in with Viking" ->  sbfsem-tools.com/auth/login
Browser  <-(2) redirect --------------   sbfsem-tools.com
Browser  --(3) sign in --------------->  identity.codepharm.net:5001
Browser  <-(4) redirect with code ----   identity.codepharm.net:5001
Browser  --(5) /auth/callback -------->  sbfsem-tools.com
         --(6) exchange code --------->  identity.codepharm.net:5001
         <-(7) access + refresh token-   identity.codepharm.net:5001
         --(8) which volumes? -------->  identity.codepharm.net:6001
```

This site is a **confidential OpenID Connect client** using the authorization
code flow with PKCE. Steps 6–8 are server to server: the browser never sees
the client secret and never calls the token endpoint. Note that the login
server and the permissions API are on **different ports**.

The tokens are kept in a server-side session — one file per signed-in browser
under `$SBFSEM_AUTH_DIR/viking-sessions/` — and the browser holds nothing but
a random id in an httpOnly cookie. They are on disk rather than in memory
because every change to this application redeploys it, and a restart should
not sign the lab out.

## Turning it on

**Production status (2026-09-10): on and complete.** `/etc/sbfsem/secrets.env`
exists on the Lightsail box, the login page offers "Sign in with Viking" with
the built-in login still below it, and the Viking authors have fixed their
side: `:5001/connect/authorize` now sends the browser to the management login
on `:4001` with an absolute return URL back to the issuer.

Off by default in the code. The server opts in through a file that is **not** in this
repository, because this repository is rsynced to the server on every deploy:

```bash
sudo install -d -m 0700 /etc/sbfsem
sudo tee /etc/sbfsem/secrets.env >/dev/null <<'EOF'
SBFSEM_VIKING_AUTH=1
SBFSEM_VIKING_CLIENT_SECRET="<the secret the Viking authors sent>"
EOF
sudo chmod 0600 /etc/sbfsem/secrets.env
sudo systemctl restart sbfsem-web
```

**Quote the secret.** The one in use contains spaces. systemd reads it either
way — it takes the rest of the line — but an unquoted value makes the file
unsafe to `source` from a shell, where bash tries to run the second word as a
command and leaves the variable *empty*. That failure is silent and looks
exactly like a wrong secret: the token endpoint answers `invalid_client`. It
cost half an hour on 2026-09-09.

To read the secret the service is actually using, take it from the process
rather than the file:

```bash
sudo tr '\0' '\n' < /proc/$(systemctl show sbfsem-web -p MainPID --value)/environ \
  | grep '^SBFSEM_VIKING_CLIENT_SECRET='
```

`deploy/sbfsem-web.service` reads it with `EnvironmentFile=-`, so a server
without the file still starts and keeps the built-in login.

Asking for Viking sign-in without a secret does **not** silently run without
one: a confidential client cannot complete the code exchange, so the feature
stays off and says so in the log.

| Variable | Default | What it does |
|---|---|---|
| `SBFSEM_VIKING_AUTH` | unset (off) | Offer "Sign in with Viking" at all |
| `SBFSEM_VIKING_CLIENT_SECRET` | — | Required; never commit it |
| `SBFSEM_VIKING_CLIENT_ID` | `sbfsem-tools` | The registered client |
| `SBFSEM_VIKING_AUTHORITY` | `https://identity.codepharm.net:5001/` | Login server |
| `SBFSEM_VIKING_PERMISSIONS_API` | `https://identity.codepharm.net:6001/` | Permissions API |
| `SBFSEM_VIKING_REDIRECT_URI` | `https://sbfsem-tools.com/auth/callback` | Must be registered there, exactly |
| `SBFSEM_VIKING_GATE_VOLUMES` | `1` | `0` signs people in without filtering volumes |
| `SBFSEM_VIKING_LAUNCH_URL` | `https://identity.codepharm.net:4001/VikingLaunch/CreateCode` | Where "Open in Viking" sends the browser |
| `SBFSEM_VIKING_LAUNCH_MAX_DOWNSAMPLE` | `32` | The most zoomed-out view "Open in Viking" asks for |

Endpoints are read from the discovery document
(`.well-known/openid-configuration`), never hard-coded, so a move on their
side needs no change here beyond the authority.

## The built-in login stays

Both ways in work at once. The login page offers Viking first and keeps the
username-and-password form below it, so nobody with a local account is locked
out on the day this is switched on. Local accounts are still maintained with
`sbfsem-users` (see [deployment](deployment.md)); when everyone has migrated,
removing `users.json` retires that half.

Signing out ends both: the local cookie is cleared and the browser is sent to
the identity server's `end_session` endpoint, so the next click on "Sign in
with Viking" is a real sign-in rather than a silent round trip.

## Which volumes a user sees

After sign-in the server asks `GET /Permissions/AccessibleVolumes` and keeps
the answer on the session, refreshing it every five minutes — so a grant made
in the Viking management site appears without anyone signing out.
Identity 1.1.0 (2026-10-08) answers that endpoint with 404; the server then
reads `GET /Permissions/UserAccessibleVolumeTree` and flattens its
organizational units. A recheck that suddenly finds nothing we can open keeps
the last good list, as an unreachable server does.

A volume whose Viking name is not in the table below is matched by its
`metadata.AnnotationServerName` instead (Identity 1.1.0): `RC1-Internal`, an
image set on the RC1 annotation server, opens as RC1.

Viking's names for the volumes are not ours. The mapping lives in one place,
`VolumeInfo.viking_name` in `src/sbfsem/core/volumes.py`:

| Viking | Here |
|---|---|
| `InferiorMonkey` | `NeitzInferiorMonkey` |
| `NM` | `NeitzNasalMonkey` |
| `TemporalMonkey` | `NeitzTemporalMonkey` |
| `cped` | `NeitzCped` |
| `RC1`, `RC2`, `RPC1`, `RPC2` | the same |

Two consequences worth knowing:

* **The identity server governs volumes this tool cannot render** — `Zia`,
  `Wohl`, `Yiu`, `RPC3` and about eighteen others. A grant on one of those is
  simply not offered in the volume picker. It is still recorded on the
  session and reported by `GET /api/viking/session` under `unsupported`, so
  it is not lost, only not shown.
* **The bundled demo volume is not Viking's to withhold.** It has no
  counterpart there and is never gated.

Enforcement is a single router-wide dependency in
`src/sbfsem_web/api/routes.py`, so it covers every route that names a volume —
in the path, in the query string, or in a request body — including routes
added later. `tests/unit/test_viking.py` sweeps the route table and fails if
any of them stops returning 403 for a volume that was not shared.

## Opening a volume from a link: `/open`

Agreed with the Viking authors on 2026-09-10, so their site and Viking's own
context menus can link straight into a render:

```text
https://sbfsem-tools.com/open?volume=RC2&cells=74420&location=769111
```

| Parameter | |
|---|---|
| `volume` | **Viking's** name for it (`InferiorMonkey`, `NM`, `cped`, `RC1`). Ours is accepted too, so a link copied out of the address bar keeps working. Required. |
| `cells` | Comma-separated structure ids. Optional; at most 50. |
| `technique` | A render pathway (`voxel2`, `original`, …). Optional; an unknown one falls back to the default rather than refusing the link. |
| `location` | A Viking Location ID. Optional; the view flies to that annotation once the cells load. Looked up in `volume`, because Location IDs, like structure IDs, are numbered per volume. |

The Identity website links `?volume={name}` only. Cells and technique are for
Viking's context menu, which is a separate codebase.

What `/open` could not honor (an unknown technique, cell ids that are not
numbers, more than 50 cells, a location that is not an id) is not an error: the
rest of the link opens, and the page says what was left out, all of it in one
note (an earlier version kept only the last problem) -- over the render for 15
seconds, where the load's own status line cannot overwrite it, and in the status
line.

`/open` is behind the login like everything else: a signed-out visitor is sent
through sign-in and lands back on the same URL, **query string included**.
Every failure is explained on the page — an unknown volume, one Viking governs
that this tool cannot render, or one this account was not granted — rather
than dropping someone into an empty viewer.

### A signed-out `/open` tries the identity server's session first

Someone following `/open` came from Viking, so they are a Viking user, and
since 2026-09-18 Viking's context menu goes through the identity server before
it reaches us (`SbfsemOpen/Redirect` on the management site: it signs the
browser in there if need be, then redirects to `/open`). The browser therefore
arrives holding an identity-server session, and a login page with a button on
it would only be a click in the way. So a signed-out `/open` — and only
`/open` — skips `/login`:

```text
/open?…  →  /auth/login?silent=1&next=/open?…  →  authorize?…&prompt=none
         →  /auth/callback?code=…              →  /open?…   (signed in, nothing shown)
```

`prompt=none` asks the identity server to reuse its session and show nothing.
With no session there it answers `error=login_required` straight back to
`/auth/callback` (measured on 2026-09-18: one 302, no login page), and the
callback hands over to our ordinary `/login?next=…` with no error on it, where
"Sign in with Viking" and the built-in form are both on offer. Nobody is worse
off than before; a built-in account pays one invisible round trip.

Two guards. The silent flag lives in our handshake cookie, so a hand-made
`/auth/callback?error=login_required` is still reported as a refusal. And a
second silent attempt within 60 seconds of one that *succeeded* means the
session did not stick (a refused cookie, an unwritable session file); it goes
to the login page instead of looping between the two servers, and the journal
says `did not leave a session behind`.

In the journal a reused session reads
`viking sign-in complete (silent, identity-server session reused)`.

## Opening a place in Viking: `/open-in-viking`

The other direction. Right-click a cell in the 3D view → **Open in Viking**
opens the volume in Viking at the annotation under the pointer (the nearest
one within 5 µm), or at the point itself when no annotation is that close;
from the cell list, at the cell's largest annotation. The browser is sent to

```text
/open-in-viking?volume=RC2&location=769111
/open-in-viking?volume=RC2&location=200549,-160700,1111,8
```

which answers with a redirect straight to the client, under **Viking's** name
for the volume:

```text
viking://open?volumeName=RC2&location=769111
```

| Parameter | |
|---|---|
| `volume` | Our name or Viking's. Required. The demo volume is refused (404): it is not a Viking volume. A volume this account was not granted is refused (403). |
| `location` | A Location ID, or `x,y,z[,downsample]` in Viking's pixel and section space (what the pick readout shows). Optional; without it Viking just opens the volume. |

The operating system hands the link to Viking. A Viking already open takes it
over its pipe and goes to the place. A cold start shows Viking's own sign-in
window (which remembers the account, so often only the password is asked),
selects the volume by name and goes to the place (`Program.cs` and
`LoginWindow` in their Legacy branch). No token of ours and no server-to-server
call is involved. The first time, the browser asks whether it may open Viking.

**Launch mode.** `SBFSEM_VIKING_LAUNCH_MODE=code` sends the browser to the
management site's launch page instead:

```text
https://identity.codepharm.net:4001/VikingLaunch/CreateCode?volumeName=RC2&location=769111
```

Their `CreateCode` mints a one-use launch code under the browser's own
identity-server session and redirects to
`viking://open?code=…&volume=…&volumeName=…&location=…`, and Viking exchanges
the code for a token so that no sign-in is asked. That was the default until
2026-09-28. Since Viking 1.2.67 the annotation service refuses the token a
code is exchanged for, so a cold start by code opens a Viking that can load
nothing (Troubleshooting, below). `direct`, the default, is the stopgap until
[Viking-Server] deploys that fix; then set `code` in `/etc/sbfsem/secrets.env`
or the unit and restart.

The downsample in `x,y,z,downsample` is capped at 32 on the way out. Viking
applies whatever it is sent, with no upper limit, and starts at 32 when it is
sent none, which is what a launch by Location ID gets. A whole-cell view here
corresponds to 700 to 1000: on RC2 that would open Viking on a field of 3 mm
around a volume 0.25 mm across. At 32 the field is about 100 µm. A closer view
is sent as it is, and the pick readout still shows the true value.

The interface opens the link in a tab of its own (named, so it is reused from
one launch to the next). In launch mode `code`, a browser with no session at
the identity server is shown their sign-in page there, and that must not
replace the render. Once
the link has gone to Viking the tab closes itself (below).

### The launch page: `?page=1`

Without `page`, the endpoint is the 302 above and nothing else. With `page=1`
-- what the interface asks for -- it answers with `static/viking-launch.html`,
which makes the same navigation from *our* origin. Two things can only be said
from here, because once the browser is at the identity server there is no
script of ours left to say them:

* **Viking is a Windows application.** On macOS or Linux the page refuses
  before navigating: *Viking client can only be run on Windows.* The interface
  checks first as well, so on those machines no tab is opened at all.
* **A launch that nothing answered.** No browser API says whether a scheme has
  a handler, but a launch takes the focus away from the page -- the browser's
  "Open Viking?" question does, and so does Viking's own window. An external
  protocol also leaves the current document in place (measured in Chrome:
  after `location.replace('viking://…')` with no handler, the page is still
  there, at its own URL, with no error page and no blur). So a page that still
  has the focus six seconds later was handed `viking://` and nothing answered:
  *Viking client is not installed.*, with the installer's address
  (`VIKING_INSTALL_URL` in `viking.py`, the https form -- Chrome refuses an
  insecure download of an executable). The notice withdraws itself if the
  focus goes later, so a slow first start is not called an absence, and
  nothing is claimed when the focus is simply elsewhere
  (`document.hasFocus()` is false).

If the browser has no session at the identity server, their sign-in page
replaces the launch page as before and neither notice appears.

### Viking and Viking Test

Two programs answer to a link. James Anderson's Legacy branch builds the
signed client and, with the MSBuild switch `VikingTestChannel=true`, an
unsigned test build that installs beside it (`VikingChannelIdentity.cs`):

| | signed | test |
|---|---|---|
| pack id, Start menu | `Viking`, Viking | `VikingTest`, Viking Test |
| link scheme (HKCU, no admin) | `viking://` | `viking-test://` (leaves `viking://` alone) |
| installer | `…/Software/Viking/Viking-win-Setup.exe` | `…/Software/VikingTest/Viking-win-Setup.exe` |

Both read the same query (`VikingDeepLinkParser.TryFindOpenUrl` accepts either
prefix), so `viking-test://open?volumeName=&location=` is our direct link on the
other scheme. His `CreateCode` redirects to `viking://` only, so a link for the
test build is always the direct one (launch mode `code` does not apply to it).
The two share their single-instance pipes (`VikingLegacy.Activation.*`): his
notes say a link to the signed program reaches a Viking Test already open on the
volume. Not tested here.

`/open-in-viking?client=test&then=viking` and `static/viking-launch.html` carry
the choice (`client` the program to launch, `then` the other, offered when
nothing answers). What each launch saw is kept in the browser
(`static/js/vikingclient.js`, `localStorage` key `sbfsem.vikingClients`, the
session's pick in `sessionStorage`): which scheme took the focus and when, which
did not. The decision is a pure function of that record (the node tests in
`tests/js/test_vikingclient.mjs`).

**Why the other program is a button and not tried by the page.** Measured
2026-10-16 in Chrome for Testing 148: a script that starts an external-protocol
launch some seconds after its page loaded is refused, with the console message
`Not allowed to launch 'viking-test://…' because a user gesture is required.`
(also with an "Always allow" profile), and the same launch made in a real click is
let through. The first launch the page makes as it loads is the one allowed
without a gesture, as it always was. So after the first program leaves the page
focused for the whole wait, the notice appears as before and a button offers the
other one; a timer would fall silently on Chrome's rule. (`tools/check_app.py
--only viking` pins this: it needs a profile with "Always allow" set for the
server's origin, as headless Chrome on macOS takes no more input once the
question is open.)

**What a page cannot know.** Nothing tells it whether `viking-test://` has a
handler without launching it. A machine that has both is told apart from one
that has only the signed Viking by a person: the first open of a session asks
only once the browser has seen both answer, and *Choose Viking client…* in the
cell menu is how the second one is first seen. If [Viking-Client] ever gives
`viking-test://` a host that starts nothing (a no-UI probe), the page could ask
silently.

### The tab closes itself

Once the link has gone to Viking the launch page closes its own tab, and the
render it was opened from is in front again. Not sooner: the browser's
"Open Viking?" question belongs to that tab, and closing the tab while it is
asked cancels the launch. A lost focus alone proves nothing, since the
question takes the focus too. The tab closes on either of:

* **the focus coming back after it left**: the question was answered, either
  way (the launch goes out before the focus returns), or Viking had the focus
  and the user came back to the browser;
* **the page being hidden while it is still the page**: Viking's window covers
  the browser, or the user turned to another tab.

It stays open in three cases. While *Viking client is not installed.* is
showing, because the installer's address is the point of it (a later loss of
focus withdraws the notice, as before, and the tab then closes like any
other). When the browser had no session at the identity server, because the
tab then holds their sign-in page and no script of ours; the next launch
reuses that tab and closes it as usual. And in a tab opened by hand with
history behind it, which a page may not close; it says the tab can be closed
instead.

The sign-in case needs the care it gets. Being replaced also hides a page
(Firefox 156 fires `pagehide`, then `visibilitychange` to hidden), and a close
made on that event shut the sign-in tab the moment it appeared. So the close
on *hidden* is made from a queued task, which never runs during an unload, and
is skipped after `pagehide`.

Measured 2026-09-22 in headless Chrome for Testing 148 and Firefox 156, with
Windows emulated and every request to the identity server answered locally
(204 for a hand-off the page survives, a page of our own for sign-in): the tab
closes when the render is brought to the front (Chrome after 9 ms, Firefox
after 1 s, its background-timer interval) and on a blur followed by a focus;
it stays on a focus with no blur before it, through the not-installed notice,
under the sign-in page, and in a tab with history (the browsers refuse, and
the page says so). Not measurable off Windows, and not yet seen there: that
Chrome's "Open Viking?" question takes the focus from the page and gives it
back when answered, and whether Viking's window covers the browser enough for
Windows to report it hidden. If Viking does not cover it, the tab closes when
the user comes back to the browser.

## When the identity server is unreachable

A signed-in session keeps working on its last known permissions rather than
locking everyone out the moment the identity server hiccups — this site's
availability should not be the product of two servers'. That cannot go on
forever, though: past 24 hours without a successful refresh the session is
dropped, because a revoked grant has to take effect eventually.

That holds for both things that are renewed: the permissions (every five
minutes) and the access token, which falls due first (about an hour after
sign-in). A refresh that could not be asked (a connection or timeout error,
the discovery document unreadable) or that the server answered with a 5xx, or
with a page that is not a token, is an outage, and the session goes on. A
refresh the identity server *refuses* (a 4xx, such as a spent or revoked
grant) is its answer, not an outage: the session ends at once and the person
signs in again.

The identity server is asked in a worker thread, never on the event loop, so a
slow or absent one holds up only the request that is renewing its session: the
others (health checks included) go on. One request at a time renews a given
session, and a request that finds it being renewed uses the answer it has,
whatever its age: only the request that is renewing decides whether the
session is too old. A refresh that fails is not tried again for a minute
(`PERMISSION_RETRY`), where every request used to pay its ten-second timeout
again.

## Troubleshooting

**"You are signed in to Viking, but no volumes have been shared with your
account yet."** The account exists and the sign-in worked; it simply has no
grants. Registering this client does not by itself give anyone access to
data — each person needs a Viking Identity account and a per-user or
per-group grant made in the Viking Identity management site.

**A link from Viking stops at our login page although the person is signed
in at the identity server.** The journal will show `silent viking sign-in: no
session to reuse (login_required)`. The session `:5001` looks for is the
cookie `.AspNet.SharedVikingIdentity`, host-only on `identity.codepharm.net`
(cookies carry no port, so a sign-in on `:4001` sets it for both), protected
with a key ring the two processes share. Check in the browser that the cookie
exists after a `:4001` sign-in; if it does, the fault is theirs to look at:
whether `:5001` can unprotect it (their words, 2026-09-18).

**"Open in Viking" shows the identity server's generic error page.** That was
their `CreateCode` on 2026-09-18, fixed the same day. Signed out it must
answer 302 to `/Account/Login?ReturnUrl=/VikingLaunch/CreateCode?...`:

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' \
  'https://identity.codepharm.net:4001/VikingLaunch/CreateCode?volumeName=RC2&location=769111'
```

**"Open in Viking" starts a Viking that shows no annotations and says
"Location ID … was not found in volume …".** This is launch mode `code` only;
the default since 2026-09-28, `direct`, sends no launch code and a cold start
signs in in Viking's own window instead (above). It happens when the link has to
start Viking itself (a cold start): six cold starts on Viking 1.2.69, on
2026-09-23 (UTC), were all refused. The cause is on their side and reported in
[context/to-janderson-identity-server-2026-09-21-launch-annotation-denied.rst](context/to-janderson-identity-server-2026-09-21-launch-annotation-denied.rst).
The hand-off itself has worked since 1.2.67: Viking exchanges the code at
`POST :6001/api/viking/launch-exchange`, shows no login window and selects
the volume from the link. The token it gets back is what fails. It is a
66-character reference token with audience `Viking.Annotation` (their client
`api` issues reference tokens, not JWTs), and their annotation service
accepts only JWTs with audience `Viking.Annotation.API`. Every annotation
call is denied, and Viking reports the denied `GetLocationByID` as "not
found" (the Location exists). Their fix is two server changes, JWTs from
`api` and the annotation service accepting `Viking.Annotation`; neither was
deployed at 18:30 UTC on 2026-09-23. Viking 1.2.61 stopped earlier, at its
ordinary login window with the code spent
([context/to-janderson-identity-server-2026-09-18-open-in-viking-measured.rst](context/to-janderson-identity-server-2026-09-18-open-in-viking-measured.rst)):
it read the exchange answer's keys in snake_case, their server answers
camelCase, and so it saw no token. 1.2.67 reads camelCase. 1.2.61 also
re-centered a Location-ID launch on the volume's `DefaultSection` (RPC1: 534).
The `UseDefaultPosition` reset that did it is gone from 1.2.67, but no cold
start has shown the linked Location since, so that fix is untested. Nothing on
our side is wrong: `location=<id>` and `location=x,y,z,ds` are exactly what
`VikingDeepLinkParser` reads.

A Viking already open on the volume and signed in by hand takes the link over
a named pipe and needs neither a code nor a login (on 1.2.69, three such links
were all fetched with no denial). But only the Viking that owns the volume's
pipe listens, and one that finds the pipe taken never takes it over, even
after the owner closes: on 2026-09-23 a Viking started by hand one second
after a cold start never took a link, and the next two links each started
another Viking from cold. None listens before it has finished signing in and
loading, either. That is a client fault, in the same note. For now, close
every Viking, start one by hand on the volume, sign in, and click the link
once it has loaded.

Since 1.2.69 Viking keeps a log of every start,
`%LocalAppData%\Viking\Logs\<MM.dd.yyyy HH.mm.ss>.log`, a new file each time,
named for the local start time (on a Pacific-time machine, a start at
05:24:38 UTC on 2026-09-23 is `09.22.2026 22.24.38.log`); startup also
appends `[Viking] Startup log: <path>` to `debug_output.txt` next to
`Viking.exe` (`%LocalAppData%\Viking\current`). Read the log after that
Viking has closed. Until then Viking holds the file open:
`[IO.File]::ReadAllBytes` cannot open it, a directory listing can show 0
bytes, and the last lines can still be in Viking's write buffer. A cold start
logs `[TokenInjector] Attached Authorization Bearer. AccessTokenLength=66` (a
hand sign-in logged 1170), then `SecurityAccessDeniedException` on every
annotation call; `Another instance already listens` marks a Viking that will
never take a link. A launch handed to a running Viking leaves a short log of
its own (1,167 bytes in our tests) that stops at `Viking:`. That process
exits without closing its log, so its `Deep-link forward ack` line is lost.

**The sign-in bounces back to the login page.** Check the log for
`viking code exchange failed`. The usual causes are a wrong client secret and
a redirect URI that does not match the registered one *exactly*.

To test the client secret on its own, without needing a real authorization
code, post a deliberately fake one to the token endpoint. `invalid_grant`
means the secret was accepted and only the code was rejected — which is the
answer you want. `invalid_client` means the secret is wrong (or empty; see
the quoting note above).

```bash
curl -sS -X POST https://identity.codepharm.net:5001/connect/token \
  -d grant_type=authorization_code -d code=not-a-real-code \
  -d redirect_uri=https://sbfsem-tools.com/auth/callback \
  -d code_verifier=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --data-urlencode client_id=sbfsem-tools \
  --data-urlencode "client_secret=$SECRET"
```

**Testing it locally.** Two loopback callbacks are registered, both on port
8765 (the port `.claude/launch.json` uses):

```bash
SBFSEM_VIKING_AUTH=1 \
SBFSEM_VIKING_CLIENT_SECRET="<the secret>" \
SBFSEM_VIKING_REDIRECT_URI=http://localhost:8765/auth/callback \
SBFSEM_AUTH_DIR=/tmp/sbfsem-dev-auth \
  .venv/bin/uvicorn sbfsem_web.app:app --port 8765
```

The redirect URI must match a registered one *exactly*, port included. The
handshake cookie follows its scheme automatically — over plain `http` a
`Secure` cookie would never come back, and the callback would fail on a state
mismatch that looks nothing like its cause.

**Nothing changed after setting the variables.** `EnvironmentFile` is read at
service start; `systemctl restart sbfsem-web` is required, and a redeploy does
not re-read `/etc/sbfsem/secrets.env` unless the service actually restarts.
