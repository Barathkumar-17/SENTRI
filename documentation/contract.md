# The contract

`contract.yaml` tells Sentri how to start your app, which journeys must survive, and how users log in. A
person approves it before any score counts: `sentri run` refuses an unapproved contract unless you pass
`--allow-unapproved`. `sentri plan` drafts one from recon; you review, edit and approve it.

## Example (browser app)

```yaml
version: 1

app:
  run: "npm start"                 # command mode; or docker: {context: ".", dockerfile: "Dockerfile"}
  port: 3000
  base_url: "http://127.0.0.1:3000"
  startup_timeout_s: 60
  health_path: "/"
  clean: ["data/app.db"]           # deleted before every start

auth:
  login_flow: login                # a flow below that logs in

flows:
  - id: login
    attack: false                  # helper flow: used to log in, never attacked
    start: "/signup"
    steps:
      - fill: {selector: "input[name=email]", value: "{email}"}
      - fill: {selector: "input[name=password]", value: "{password}"}
      - click: "#signup"
      - goto: "/login"
      - fill: {selector: "input[name=email]", value: "{email}"}
      - fill: {selector: "input[name=password]", value: "{password}"}
      - click: "button[type=submit]"
    success: {url_contains: "/dashboard"}

  - id: create_task
    description: "Logged-in user creates a task with an attachment"
    requires_login: true
    start: "/tasks/new"
    steps:
      - fill: {selector: "input[name=title]", value: "{title}"}
      - upload: {selector: "input[type=file]", file: "{file}"}
      - click: "button#save"
    success: {text_visible: "Task created"}
    list_selector: "[data-testid=task-row]"   # lets the duplicates check count records
    list_page: "/tasks"

attacks_enabled: [empty_input, double_submit, expired_session, slow_network, bad_file, rapid_repeat]
```

## API apps and token login

For apps that are mostly an API, or whose interface is drawn on a canvas (Flutter web), use token login. Recon
reads the OpenAPI spec if there is one. Flows are optional.

```yaml
version: 1
app:
  run: "{python} -m uvicorn main:app --host 127.0.0.1 --port 8765"
  port: 8765
  env: {DATABASE_FILE: "sentri.db"}
  clean: ["sentri.db"]
  health_path: "/docs"
auth:
  api:
    register: {path: "/auth/register", json: {username: "{username}", password: "{password}"}}
    login: {path: "/auth/login", json: {username: "{username}", password: "{password}"}}   # optional
    token_field: "token"           # dotted path into the JSON response, e.g. "data.access_token"
    header: "Authorization"
    scheme: "Bearer"
```

Each identity (`a`, `b`) registers its own fresh account. Its token is attached to every request that
identity makes **to the app** (never to another origin, also not through a redirect).

## Fields

### `app`
| Field | Default | Meaning |
|---|---|---|
| `run` or `docker` | one, or neither | How Sentri starts the app. `{python}` in `run` is the interpreter running Sentri. Give neither, plus `base_url`, for an app that is already running (URL mode: Sentri never starts, resets or stops it, and replays run on its live state) |
| `docker.context`, `dockerfile`, `container_port`, `env` | `.`, `Dockerfile` | Docker mode |
| `cwd` | `.` | Working directory for command mode, relative to the contract |
| `env` | `{}` | Extra environment for the app. `PORT` is set to `port` unless you set it here. |
| `port` | required with `run`/`docker` | Port Sentri connects to |
| `base_url` | `http://localhost:<port>` | Its origin is "the app": nothing outside it is attacked or counted |
| `startup_timeout_s` | 60 | Port open and health check below 500 within this time |
| `health_path` | `/` | Must answer below 500 before attacks start |
| `reset` / `reset_command` | `restart` | Clean state between attempts: restart the app, or run a command |
| `clean` | `[]` | Files deleted before every start (SQLite `-wal`/`-shm` included), inside the app folder only |
| `log_patterns_extra` | `[]` | Extra regexes that count as an exception in the error text the app sends back |
| `ignore_console` | `favicon.ico` | Regexes for browser console messages to ignore (baseline suite and agentic phase alike) |
| `offsite_assets` | `allow` | `block` also stops off-origin scripts, styles, images and fonts. Every other request to another origin (links, form posts, popups, fetch calls, websockets, and redirects from the app to another site) is always blocked |

### `auth`
Exactly one of `login_flow` (the id of a browser flow) or `api` (token login, above).

**Without an `auth` block there is only the logged-out identity.** Cross-user checks (user `b` reading user
`a`'s pages or records), the direct-by-id check, and most login-boundary tests (what a logged-out visitor can
reach that a signed-in user can) then do not run; the run feed says so at the start. Add `login_flow` or `api`
to get them.

`start_as: a` (or `b`, optional) is a neutral **test precondition**: the agent's exploration begins already logged
in as that test account. Sentri logs it in through the same login as any identity switch, right after the last app
reset (so the reset cannot wipe it), and verifies it against real app state: recon must have proved a login-only page
(logged out was redirected or refused), and the server must actually serve that page to this session. If the login
fails, the session is not recognised, or recon proved no such page, the run says so (a `setup` event and a note)
and starts logged out instead of pretending. It does not show that the agent can log in by itself; the agent still
chooses everything it does and may switch to the logged-out user or the other account. Without it, exploration
starts logged out. See `examples/flow-shop/contract.agent.authed.yaml`.

### `flows[]`
| Field | Meaning |
|---|---|
| `id` | Lowercase id, unique |
| `start` | Path the flow begins at |
| `steps` | `fill`, `click`, `upload`, `select`, `press`, `wait_for`, `goto` |
| `success` | `url_contains`, `text_visible` and/or `selector_visible`; all given must hold |
| `requires_login` | Log in (with a fresh account) before the flow |
| `attack` | `false` for helper flows |
| `list_selector`, `list_page`, `expected_creates` | Let the duplicates check count records; without one of these, duplicates are not checked (a repeated save may be an idempotent update) |
| `action_timeout_s` | Per-flow timeout (default 10) |

The **submit step** of a flow is its last `click` or `press`.

### Placeholders
`{email}`, `{password}`, `{username}`, `{title}`, `{name}`, `{text}`, `{number}` and `{file}` (a small valid PNG)
are generated fresh for each attempt from a seed, so a run can be replayed. Pin values in `fixtures:`.
`{seed_email}` and `{seed_password}` read `SENTRI_SEED_EMAIL` / `SENTRI_SEED_PASSWORD` for apps that need an
existing account. Nothing is hard-coded.

### Other
| Field | Meaning |
|---|---|
| `version` | Always `1` |
| `meta.approved_by`, `meta.approved_at` | Written by `sentri contract approve` |
| `defaults` | `action_timeout_s` (10), `settle_ms` (1500), `slow_network_timeout_multiplier` (3) |
| `attacks_enabled` | Which of the six baseline attacks to run |
| `goals` | Optional plain-language goals for the agentic phase, e.g. `["Buy something and check the order shows up"]` |
| `duplicate_db` | Ignored: verdicts are black-box, so duplicates are counted on the list page (`list_selector`) |

Validate with `sentri contract check contract.yaml`; errors name the YAML location, e.g.
`flows[1].steps[2].upload.selector: field required`.
