# Architecture

## Components

`plugin.video.roomwatchsync` combines two Kodi extension points:

- a background service observes video playback, uploads progress, downloads remote state, and applies resume points to Kodi's local library;
- a video plugin renders the server's unfinished items and resolves a selected source path with a configurable rewind.

The server is a stateless HTTP API in front of one SQLite database. All `/v1/*` requests require the same household Bearer token. The server timestamps accepted writes, so client clock drift cannot reorder updates.

One server deployment represents one household. Kodi clients find each other indirectly by sharing that deployment's URL and token; there is no LAN peer discovery. The public GitHub repository distributes code and install packages, not a shared public database.

## Media identity

The client prefers stable external IDs in this order: IMDb, TMDb, TVDb. If none is available, it hashes a normalized identity:

- movie: title and year;
- episode: show title, season and episode;
- other video: title and duration bucket.

Kodi database IDs are deliberately excluded because they differ between devices.

## Write semantics

Each playback sample replaces the previous record for the same media key. Server receipt order is authoritative. A playback ending near completion is marked completed and omitted from the default Continue Watching response.

## Privacy and threat model

The database contains viewing metadata and source paths, not media bytes. A shared token prevents accidental unauthenticated access but is not user isolation. Run on a trusted LAN; use TLS/VPN across untrusted networks.

## Known limits

- Quick launch requires a source path valid on the receiving device.
- Some third-party plugins expose resolved or expiring stream URLs instead of reusable plugin URLs.
- Simultaneous playback of the same title on two televisions uses last server write wins.
- A public multi-household hosted service would need accounts, pairing codes, per-tenant storage and abuse controls; those are outside this self-hosted MVP.
