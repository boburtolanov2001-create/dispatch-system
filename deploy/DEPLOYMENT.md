# Auto Deploy Setup

This project includes GitHub Actions deployment via `.github/workflows/deploy.yml`.

## What the workflow does

On every push to `main`, GitHub Actions will:

1. checkout the repository
2. run a Python compile check on `app.py`
3. connect to your server over SSH
4. sync source files to the server with `rsync`
5. skip live runtime JSON files during sync
6. install/update Python dependencies on the server
7. restart the systemd service

## PostgreSQL

This app now stores mutable state in PostgreSQL when `DATABASE_URL` is set.

Data moved to DB:

- login users
- driver assignments for `My Drivers`
- delivery address
- APPT
- ETA
- ETA status
- ETA delay
- notes

The live driver feed is synced from the SafeLane API into PostgreSQL. The app reads the dashboard from PostgreSQL, not from runtime JSON files.

## GitHub secrets to add

Add these repository secrets in GitHub:

- `SERVER_HOST`
- `SERVER_PORT`
- `SERVER_USER`
- `SERVER_SSH_KEY`
- `DEPLOY_PATH`
- `APP_SERVICE_NAME`

Your values:

- `SERVER_HOST`: `185.181.10.143`
- `SERVER_PORT`: `22`
- `SERVER_USER`: `root`
- `DEPLOY_PATH`: `/root/dispatch-system`
- `APP_SERVICE_NAME`: `dispatch-system`

## Server setup

Clone or create the target directory once:

```bash
mkdir -p /root/dispatch-system
```

Copy the service example from `deploy/dispatch-system.service` and adjust:

- `User`
- `Group`
- `WorkingDirectory`
- `ExecStart`

Then install it:

```bash
sudo cp deploy/dispatch-system.service /etc/systemd/system/dispatch-system.service
sudo systemctl daemon-reload
sudo systemctl enable dispatch-system
sudo systemctl start dispatch-system
```

Create `/root/dispatch-system/.env` on the server:

```bash
cat >/root/dispatch-system/.env <<'EOF'
DATABASE_URL=postgresql://dispatch_user:change_me@127.0.0.1:5432/dispatch_system
SAFELANE_USERNAME=sdglobal@gmail.com
SAFELANE_PASSWORD=change_me
SAFELANE_SYNC_INTERVAL_SECONDS=180
EOF
chmod 600 /root/dispatch-system/.env
```

You can use `.env.example` in the repo as the template.

## SafeLane sync

When `SAFELANE_USERNAME` and `SAFELANE_PASSWORD` are set, the app will:

1. sign in to SafeLane
2. store the current SafeLane token in PostgreSQL
3. sync active drivers into `driver_feed`
4. refresh the token automatically after `401`
5. repeat sync every 3 minutes by default

Dispatcher-edited state such as delivery address, APPT, ETA, ETA status, and notes stays in PostgreSQL and is not overwritten by SafeLane feed sync.

## SSH authentication

The workflow is configured for SSH key authentication, not password authentication.

Generate a deployment key on your server:

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f /root/.ssh/github_actions_deploy
```

Then:

1. add `/root/.ssh/github_actions_deploy.pub` to `/root/.ssh/authorized_keys`
2. put the private key from `/root/.ssh/github_actions_deploy` into GitHub secret `SERVER_SSH_KEY`

Do not put the root password into the repository or workflow file.

## Root service behavior

Because this server deploys as `root`, the workflow can restart the service directly without a sudoers rule.

## Important deployment behavior

These files are intentionally excluded from deploy sync so live server data is preserved:

- `.env`
- `tracked_drivers.json`
- `user_assignments.json`
- `users.json`
- `geo_cache.json`

That means deploy updates code, templates, scripts, and dependencies, but does not overwrite your live app state.

## Result

After secrets and the systemd service are in place, every push to `main` will:

1. upload code to `/root/dispatch-system`
2. install dependencies in `/root/dispatch-system/.venv`
3. compile-check `app.py`
4. restart `dispatch-system`
