/**
 * The durable option, in thirty lines and with nothing to maintain.
 *
 * The add-on's default channel is ntfy.sh, which needs no account at all but
 * keeps a message for only 12 hours (3.5 days with the delayed copies the
 * add-on publishes). A television switched off for a week is past that. This
 * Worker is the answer for anyone who wants the state to simply persist:
 * Cloudflare's free plan is 100,000 requests and 1,000 KV writes a day, which
 * is a household by two orders of magnitude, and there is no server process,
 * no container and no machine in the house.
 *
 * Deploy, once:
 *
 *   npm install -g wrangler
 *   wrangler kv namespace create ROOMWATCH
 *   # put the returned id into wrangler.toml
 *   wrangler deploy
 *
 * Then set, in the add-on on every television:
 *   Sync channel  -> Custom URL
 *   Sync URL      -> https://<your-worker>.workers.dev/<the 26-character topic>
 * The add-on prints that topic on its pairing screen. It is derived from the
 * pairing code, so it is unguessable (130 bits) and it is NOT the code itself.
 *
 * Deliberate choices:
 *
 * - **The path is the only credential, exactly as with ntfy.** There is no
 *   login to distribute to a television with a remote control. 130 bits is
 *   what makes that defensible, and the add-on signs every payload (HMAC under
 *   a separately derived key), so even a leaked path cannot be used to push a
 *   false playback position into somebody's library.
 * - **The key is validated before KV is touched.** Without this, any path at
 *   all becomes a free key-value store for strangers, on your account and
 *   against your quota.
 * - **A body cap.** One household's state is a few KB; 64 KB is generous and
 *   stops the same abuse by volume.
 * - **CORS is not enabled.** Nothing in this project runs in a browser, and an
 *   open CORS policy would let any web page use your quota.
 */

const KEY_PATTERN = /^[0-9A-HJKMNP-TV-Z]{26}$/;  // Crockford Base32, 26 chars
const MAX_BODY_BYTES = 64 * 1024;

export default {
  async fetch(request, env) {
    const key = new URL(request.url).pathname.slice(1);
    if (!KEY_PATTERN.test(key)) {
      // Deliberately the same answer for a malformed key and an unused one:
      // this endpoint should not help anyone map which homes exist.
      return new Response("not found\n", { status: 404 });
    }
    if (!env.ROOMWATCH) {
      return new Response("KV namespace ROOMWATCH is not bound\n", { status: 500 });
    }

    if (request.method === "GET") {
      const stored = await env.ROOMWATCH.get(key);
      if (stored === null) return new Response("not found\n", { status: 404 });
      return new Response(stored, {
        status: 200,
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    }

    if (request.method === "PUT" || request.method === "POST") {
      const body = await request.text();
      if (body.length > MAX_BODY_BYTES) {
        return new Response("payload too large\n", { status: 413 });
      }
      try {
        JSON.parse(body);
      } catch (_) {
        // Refusing non-JSON keeps this from becoming a general file host, and
        // the add-on only ever sends JSON.
        return new Response("body must be JSON\n", { status: 400 });
      }
      await env.ROOMWATCH.put(key, body);
      return new Response(null, { status: 204 });
    }

    return new Response("method not allowed\n", {
      status: 405,
      headers: { allow: "GET, PUT" },
    });
  },
};
