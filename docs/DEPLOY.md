# Deploying the web app

The app (FastAPI backend + Leaflet frontend) runs as a Docker container on a
free **Hugging Face Space**, and rudraanshpatel.in shows it on a project page
(`/projects/groundwater-recharge/`) inside an iframe. GitHub Pages alone can't
host it because it only serves static files and the app needs Python.

Every push to `main` that touches the app or its data re-deploys the Space
automatically through `.github/workflows/deploy-space.yml`.

## One-time setup (about 10 minutes, all in the browser)

1. **Hugging Face account.** Sign up at https://huggingface.co/join. Using the
   username `rudraanshpatel` keeps the app URL as
   `https://rudraanshpatel-groundwater-recharge-bhopal.hf.space`, which the
   website page already points to.
2. **Access token.** https://huggingface.co/settings/tokens -> *Create new token*
   -> token type **Write** -> name it `github-deploy` -> *Create token*. Copy it.
3. **Give the token to GitHub.** In this repository: *Settings* -> *Secrets and
   variables* -> *Actions* -> *New repository secret*. Name `HF_TOKEN`, value =
   the token -> *Add secret*.
4. **Run the deploy once.** *Actions* tab -> *Deploy to Hugging Face Space* ->
   *Run workflow* -> *Run workflow*. It takes about a minute and prints the
   Space and app URLs in the run summary. The Space is created on the first run.
5. **Wait for the build.** Open the Space page
   (https://huggingface.co/spaces/rudraanshpatel/groundwater-recharge-bhopal).
   The first Docker build takes 3-5 minutes; the status turns to *Running*.
6. **Check the website.** Once the website PR is merged, open
   https://rudraanshpatel.in/projects/groundwater-recharge/.

If your Hugging Face username is different, either add a repository
*variable* (not secret) `HF_SPACE` = `yourname/groundwater-recharge-bhopal`,
or just change the two URLs in the website's
`projects/groundwater-recharge/index.html` to
`https://yourname-groundwater-recharge-bhopal.hf.space/`.

## Good to know

- **Sleep.** Free Spaces sleep after 48 hours without visitors. The next visit
  wakes them up in about 30-60 seconds; the website page says so while it loads.
  Before a demo or viva, open the app once a few minutes early.
- **What gets deployed.** `backend/`, `frontend/`, `Dockerfile`,
  `requirements-app.txt`, `data/processed/`, `data/raw/boundaries/` and the
  validation outputs (about 30 MB). Raw downloads and float layers are not
  needed to serve the map.
- **Run the same container locally** (needs Docker Desktop):

  ```powershell
  docker build -t gwr .
  docker run -p 7860:7860 gwr
  ```

  then open http://localhost:7860.
- **Other hosts.** The Dockerfile works on any Docker host (Render, Railway,
  Fly.io, a VPS). It listens on `$PORT` (default 7860). The app uses about
  200-350 MB of RAM, so 512 MB free tiers are tight.
