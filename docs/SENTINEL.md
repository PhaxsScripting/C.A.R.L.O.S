# Sentinel reference node

Sentinel is a small authenticated wake/status service for a separate always-on
Linux device. It has no language model, microphone, room recording, project
files or copy of Carlos memory. The workstation cannot wake itself after its
CPU and network stack stop. An independent device, firmware support and a
supported NIC are required for real remote wake.

The release includes a reference implementation and a safe simulation. No
independent node is provisioned automatically. The workstation status remains
NOT_INSTALLED until a real node is configured and tested.

```sh
PYTHONPATH=carlos/core python3 -m ev.sentinel simulate
PYTHONPATH=carlos/core python3 tools/check-sentinel.py
```

Simulation builds a 102-byte magic packet in memory and uses synthetic signed
heartbeats with real temporary SQLite replay storage. It sends no network
packet and makes no physical wake claim. The second command starts an owned
Unix-socket server, checks a signed status reply, stops it and verifies cleanup.

## Provisioning an independent node

Use an unprivileged service account and an owner-only directory (0700). Place
node.json in that directory with permissions 0600. Generate a different random
32-byte key for each approved identity; store keys only in private files.
The configuration fields are:

| Field | Meaning |
| --- | --- |
| devices | Map of explicit identity to key (64 hex characters), capabilities and optional boolean revoked |
| target_mac | One fixed nonzero unicast workstation MAC |
| broadcast | One fixed IPv4 broadcast address reachable from the node |
| host_device | Optional exact identity allowed to send heartbeat |

Capabilities are status, wake and heartbeat. Only host_device may have
heartbeat. The workstation must send authenticated heartbeats while awake;
clients with status/wake privileges cannot claim the workstation is online.

```sh
python3 -m ev.sentinel serve --config /private/node/node.json
```

The default endpoint is node.sock beside the configuration, with permissions
0600. Existing paths are never replaced. Root execution is rejected. Adding
--allow-wake explicitly enables UDP wake packets to the configured target;
this does not establish that the firmware or NIC accepts them.

TCP is optional and requires an explicit unprivileged port, TLS certificate
and private TLS key. It binds only 127.0.0.1. Expose it through an authenticated
private network or a separately configured HTTPS proxy. Plaintext TCP,
arbitrary bind addresses and unsigned response fallback are unsupported.
The service requires TLS 1.2 or newer; clients verify the normal trust chain
or an explicitly configured trusted CA. Do not disable certificate checks.

## Client

An owner-only client file contains device, key and exactly one socket or url.
HTTPS URLs use the fixed /v1/request path, without inline credentials,
query or fragment. An optional ca_certificate selects a trusted CA file.

```sh
python3 -m ev.sentinel request --client-config /private/client.json --action status
python3 -m ev.sentinel request --client-config /private/client.json --action heartbeat
```

Only the configured host identity can send the second request. ONLINE means a
valid signed host heartbeat arrived within 15 seconds; it does not prove a
desktop or model is ready. Without that evidence, status is UNKNOWN. A restart,
expired heartbeat, revoked identity or changed host key clears the online claim.
packet_sent means dispatch was acknowledged by the local socket, never that
the computer woke. There is no automatic retry after an uncertain dispatch.

## Protocol and limits

Version 1 requests bind device, action, integer timestamp and random nonce
with HMAC-SHA256. Responses use a separate HMAC domain and bind the request
nonce, identity, action, timestamp, packet flag and host state. Approved-device
keys, capabilities and revocations are reread for every request. Old unsigned
prototype responses are rejected.

Requests and replies are limited to 2 KiB. Up to 64 distinct identities are
allowed. Requests require clocks within 30 seconds. Per-identity requests are
limited to one per second, wakes to one per ten seconds, and node-wide wakes
to one per two seconds. Clients have five-second total deadlines, do not follow
redirects and ignore proxy environment variables.

Private SQLite stores only nonce/rate records. Replay protection survives a
restart. Nonces remain for 120 seconds; the 4,096-entry limit fails closed
rather than evicting a young nonce. Clock rollback fails closed until clocks
recover. Secret files/database reject symlinks, hardlinks and public access;
these checks are not a sandbox against another process with the same UID.
The service emits no HTTP access log or secret-bearing error response.

SIGTERM and SIGINT close the database and remove only the owned socket.
There is no new workstation autostart service. Hardware provisioning,
firewall/proxy exposure and sleeping-host wake remain separate acceptance work.
