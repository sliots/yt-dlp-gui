# PO Token integration (v2.0.13)

The default download path keeps the configured file/browser cookies and uses
YouTube's `web_creator` client with the bgutil HTTP PO Token provider. Disabling
PO Token in Settings restores yt-dlp's default client selection.

## Deployment

Keep your existing Compose volume mappings, then rebuild and start both services:

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 bgutil-provider
```

The GUI image installs the provider plugin ZIP for the standalone yt-dlp binary.
The plugin and sidecar are both pinned to 2.0.0; update them together in the
Dockerfile and Compose file. Updating yt-dlp from the GUI does not update either.

Existing config files acquire `po_token_enabled = true` and
`po_token_base_url = "http://bgutil-provider:4416"` through default merging.
Settings changes apply to the next download round. Keep valid logged-in cookies
and the existing browser profile mount or cookies.txt file.

For an existing single-container deployment, start the provider on the same Docker
network and set its reachable HTTP address in Settings. Replacing only the GUI
image does not start the provider.

Do not publish provider port 4416 publicly: the service is unauthenticated.
The provider receives token binding/challenge data; only use a trusted instance.
The configured download proxy is forwarded by the plugin to the provider.
That proxy must be reachable from BOTH containers; localhost is container-local.

## Verification

### WSL / Ubuntu Deployment

`docker-compose.wsl.yml` preserves the owner's port 55595, data directories and
read-only Firefox profile mount. It builds `yt-dlp-gui:local` from this checkout;
unpublished changes are not available by pulling
`ghcr.io/sliots/yt-dlp-gui:nightly`.
Run these commands in Ubuntu:

```sh
cd /mnt/c/dev/yt-dlp-gui
sudo docker compose -f docker-compose.wsl.yml config --quiet
sudo docker compose -f docker-compose.wsl.yml up -d --build
```

If an existing deployment still owns port 55595, update or stop that deployment
first. Do not remove its persistent data. For an existing Compose project, retain
its project name with `-p PROJECT_NAME` to avoid creating a duplicate deployment.

For the browser cookie mode, keep `cookies_source = "browser"`,
`cookies_browser = "firefox"` and `cookies_browser_profile = "/firefox-profile"`
in Settings. Keep PO Token enabled and the service address
`http://bgutil-provider:4416`.

Use `-f docker-compose.wsl.yml` and service name `app` instead of `yt-dlp-gui`
in the verification command below.

### Single Video Check

First test one known accessible video without downloading media:

```sh
docker compose exec yt-dlp-gui /usr/local/bin/yt-dlp \
  --plugin-dirs /opt/yt-dlp-plugins \
  --cookies /app/config/cookies.txt \
  --extractor-args 'youtube:player_client=web_creator' \
  --extractor-args 'youtubepot-bgutilhttp:base_url=http://bgutil-provider:4416' \
  --simulate --verbose 'https://www.youtube.com/watch?v=VIDEO_ID'
```

Substitute your actual cookies path, or use `--cookies-from-browser` with the same
browser/profile specification configured in Settings. Add your configured
`--proxy` when applicable. Expect `bgutil:http-2.0.0` in the provider list and a
message about generating a PO Token for `web_creator` when a token is requested.
Never share cookies or unredacted verbose logs.

Provider warnings now remain visible in the GUI logs. If downloads fail, check
provider health, matching versions, proxy reachability, account access and cookie
expiry. No anonymous fallback is performed. PO Tokens do not grant membership,
bypass access permissions, or guarantee that YouTube bot checks will succeed.

## Upstream References

- https://github.com/yt-dlp/yt-dlp/issues/17497#issuecomment-5361357999
- https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
- https://github.com/Brainicism/bgutil-ytdlp-pot-provider/tree/2.0.0
