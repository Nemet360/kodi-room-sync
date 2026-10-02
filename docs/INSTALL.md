# Installation

## Server

1. Install Docker and Docker Compose on an always-on device.
2. Copy `.env.example` to `.env`.
3. Replace the token with a long random value.
4. Run `docker compose up -d --build`.
5. Open `http://SERVER-IP:8844/health` from another device on the LAN.

## Kodi devices

1. Enable installation from unknown sources in Kodi.
2. Install the repository ZIP.
3. Install Room Watch Sync from its Video add-ons category.
4. Open add-on settings and enter the server URL, token and a unique device name.
5. Restart Kodi.

Use the same server URL and token on every television. Keep different device names to make diagnostics readable.

## Network troubleshooting

- Android/Google TV must be able to reach the server's LAN IP; `localhost` points to the television itself.
- Permit TCP port `8844` in the server firewall.
- Do not include a trailing slash in the server URL.
- If the server is behind HTTPS, its certificate must be trusted by the Kodi device.
