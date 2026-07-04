# Deploying to Fly.io

The app ships as a **single Docker image**: the [Dockerfile](Dockerfile) builds the
React frontend, then serves it and the FastAPI backend from one process on port 8080.
The image is verified to build and run the full match flow end-to-end.

## One-time setup

1. **Install the CLI and sign in** (if you haven't):
   ```bash
   brew install flyctl        # or: curl -L https://fly.io/install.sh | sh
   fly auth login
   ```

2. **Create the app.** [fly.toml](fly.toml) already has the config; either keep the
   name `entity-matching` (must be globally unique — change it if taken) or let Fly
   pick one:
   ```bash
   fly apps create entity-matching     # or: fly launch --no-deploy --copy-config
   ```
   If you change the name, update the `app = "..."` line in `fly.toml`.

3. **Create the data volume.** Uploads and match results are written to `/data`, which
   `fly.toml` mounts from a volume named `em_data`. Create it in the same region as
   `primary_region` (`iad`):
   ```bash
   fly volumes create em_data --size 1 --region iad
   ```

## Deploy

```bash
fly deploy
```

Fly builds the image on its **remote builder** (no local Docker needed), boots one
machine, and runs the `/health` check. When it's green:

```bash
fly open        # opens https://<your-app>.fly.dev
```

## What the config does

- **Single always-on machine** (`min_machines_running = 1`). The job registry is
  in-memory and results live on the machine's volume, so every request in a session
  must reach the same instance — don't scale to multiple machines without moving jobs
  + storage to a shared backend (Redis + object storage / Postgres).
- **2 GB RAM** (`shared-cpu-2x`) — Splink + DuckDB + pandas need real memory for
  100k-row files. Bump `memory` in `fly.toml` if you hit OOM on larger inputs.
- **Persistent `/data` volume** — uploads/results survive restarts and redeploys.
  (Status polling after a restart still works: the API reports `done` when results
  exist on disk, even though the in-memory job is gone.)

## Useful commands

```bash
fly logs                       # stream logs
fly status                     # machine + health state
fly ssh console                # shell into the running machine
fly scale memory 4096          # give it 4 GB
fly deploy --local-only        # build locally instead of the remote builder
```

## Build/run locally (optional sanity check)

```bash
docker build -t entity-matching .
docker run -p 8080:8080 -v em_data:/data entity-matching
# -> http://localhost:8080
```

## Notes & hardening for real use

- **No auth** — anyone with the URL can upload files and run matches. Put it behind
  Fly's [private networking], Tailscale, or add app-level auth before exposing it.
- **Uploads are unbounded** — add a size limit / rate limiting if this is public.
- For multi-user scale, move the job registry to Redis and storage to S3/Postgres so
  you can run more than one machine.

[private networking]: https://fly.io/docs/networking/private-networking/
