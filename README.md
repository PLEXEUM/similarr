# 🎬 Similarr

**Automatically find and add similar movies to Radarr based on your Plex watch history.**

Uses TMDB's "similar movies" endpoint and optional LLM suggestions to discover movies similar to what you've recently watched, then adds missing titles to Radarr.

---

## How It Works

1. Checks Plex for movies played in the last X days
2. For each movie, finds similar content using:
   - **TMDB similar movies** (always, fast & reliable)
   - **LLM suggestions** (optional, more intelligent recommendations)
3. Filters results by rating, vote count, and release status
4. Checks Radarr to see which movies you already have
5. Adds missing movies to Radarr (up to user-defined limits)

---

## Quick Start

### 1. Clone or download Similarr

```bash
git clone https://github.com/PLEXEUM/similarr.git
cd similarr
```

### 2. Create your config file

```bash
mkdir config
cp .env.example config/.env
```

Edit `config/.env` with your settings (see Configuration Options below).

### 3. Start the container

```bash
docker-compose up -d
```

### 4. Check the logs

```bash
docker logs similarr
```

Or view the log file: `logs/similarr.log`

---

## Configuration Options

### Plex

| Setting | Description |
|---------|-------------|
| `PLEX_URL` | Your Plex server URL (e.g., http://192.168.0.77:32400) |
| `PLEX_TOKEN` | Your Plex authentication token |

### Radarr

| Setting | Description |
|---------|-------------|
| `RADARR_URL` | Your Radarr URL (e.g., http://192.168.0.77:7878) |
| `RADARR_API_KEY` | From Radarr Settings → General |
| `RADARR_ROOT_FOLDER_PATH` | Where Radarr stores movies (e.g., /media/movies) |
| `RADARR_QUALITY_PROFILE_ID` | Optional - leave empty to use first available |

### TMDB

| Setting | Description |
|---------|-------------|
| `TMDB_API_KEY` | Get from themoviedb.org (free account) |

### Similarity Mode

| Setting | Description |
|---------|-------------|
| `SIMILARITY_MODE` | `tmdb_only` or `hybrid` (TMDB + LLM) |
| `TMDB_SIMILAR_LIMIT` | How many TMDB similar to fetch per source (default: 20) |

### LLM (hybrid mode only)

| Setting | Description |
|---------|-------------|
| `OPENAI_API_KEY` | For OpenAI API (or leave blank for local) |
| `OPENAI_BASE_URL` | For Ollama, LM Studio, etc. |
| `LLM_MODEL` | Model name (default: gpt-4o-mini) |
| `LLM_MAX_RECOMMENDATIONS` | LLM suggestions per source (default: 5) |

### Volume Control

| Setting | Description |
|---------|-------------|
| `MAX_SIMILAR_PER_SOURCE` | Max similar movies to add per source movie (default: 3) |
| `MAX_ADDITIONS_PER_RUN` | Safety limit - max total adds per run (default: 10) |
| `RECENT_DAYS` | Days back to check Plex history (default: 7) |
| `SKIP_HOURS` | Don't retry same source movie within X hours (default: 24) |

### Quality Filters

| Setting | Description |
|---------|-------------|
| `MIN_TMDB_RATING` | Minimum rating (0-10) - skip lower rated (default: 6.0) |
| `MIN_VOTE_COUNT` | Minimum votes - skip low confidence (default: 100) |
| `SKIP_IF_ALREADY_WATCHED` | Skip movies already watched on Plex (default: true) |
| `HIDE_FUTURE_RELEASES` | Skip unreleased movies (default: true) |

### Radarr Add Behavior

| Setting | Description |
|---------|-------------|
| `AUTO_SEARCH_AFTER_ADD` | Trigger Radarr search immediately? (default: false) |

### Dry Run

| Setting | Description |
|---------|-------------|
| `DRY_RUN` | Simulate without actually adding (default: false) |

### Logging

| Setting | Description |
|---------|-------------|
| `LOG_LEVEL` | DEBUG, INFO, WARNING, ERROR (default: INFO) |

---

## Manual Run

Run the script immediately (not waiting for schedule):

```bash
docker exec -it similarr python /app/similarr.py
```

---

## View Results

- **Radarr UI** – New movies will appear in your library
- **Docker logs** – `docker logs similarr`
- **Log file** – `logs/similarr.log`
- **Last run results** – `logs/similarr_last_run.json`

---

## Schedule

The script runs automatically at 2:00 AM daily. To change the schedule, edit the environment variable in `docker-compose.yml`:

```yaml
environment:
  - CRON_SCHEDULE=0 11 * * *
```

Cron format: `minute hour day month weekday` (using 24-hour time)

---

## Dry Run Mode

To test without actually adding anything to Radarr:

1. Set `DRY_RUN=true` in your `.env`
2. Run Similarr
3. Review what would be added
4. Set `DRY_RUN=false` to actually add movies

---

## How Hybrid Mode Works

When `SIMILARITY_MODE=hybrid`:

- Always fetches TMDB similar movies (baseline)
- Also asks LLM for intelligent suggestions
- Resolves LLM suggestions to TMDB IDs
- Merges results, removes duplicates
- Prioritizes TMDB results (they're already in your language/region)

This gives you the best of both worlds: reliable TMDB similar + creative LLM suggestions.

---

## Logging

**INFO mode** (default): Shows normal operation

**DEBUG mode**: Shows API requests, responses, and detailed comparison data

To enable DEBUG, change `LOG_LEVEL=DEBUG` in `.env` and restart:

```bash
docker-compose restart
```

Log rotation: Keeps last 1000 lines when file exceeds 10MB.

---

## Troubleshooting

**"Missing required environment variables"**

- Ensure `config/.env` exists and has all required fields

**"Plex connection failed"**

- Verify `PLEX_URL` is correct and Plex is running
- Verify `PLEX_TOKEN` is valid (Plex Web UI → Settings → Devices)

**"Radarr connection failed"**

- Verify `RADARR_URL` is correct and Radarr is running
- Verify `RADARR_API_KEY` is correct (Settings → General)

**"No quality profiles found"**

- Create at least one quality profile in Radarr (Settings → Quality Profiles)
- Or set `RADARR_QUALITY_PROFILE_ID` explicitly

**No movies are being added**

- Check if any movies were played within `RECENT_DAYS`
- Check if similar movies already exist in Radarr
- Check if similar movies pass rating/vote filters
- Run with `LOG_LEVEL=DEBUG` to see detailed filtering

---

## Files

| File | Purpose |
|------|---------|
| `similarr.py` | Main script |
| `config/.env` | Your settings (you create this) |
| `logs/similarr.log` | Script output |
| `logs/similarr_last_run.json` | Last run results (detailed) |

---

## Requirements

- Docker Desktop (Windows/Mac) or Docker Engine (Linux)
- Plex server running and accessible
- Radarr running and accessible
- TMDB API key (free)
- OpenAI API key (optional - only for hybrid mode)

---

## License

MIT

---

**Similarr** – Discover and add movies you'll love. 🎬