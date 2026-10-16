# Kubernetes (browser sink)

In the cluster there is no speaker. The catcher runs with
`SOUND_SINKS=browser`, and the player page behind the HTTPRoute is where the
announcements are heard: on a laptop, a wall tablet, or the office PC with the
Bluetooth speaker attached.

The image has Piper and a German voice (`de_DE-thorsten-medium`) built in,
so the pod speaks without a volume or a download at start.

## Render and apply

The KCL module in `kcl/` follows led-catcher's. Releases push it as a
kustomize base to `ghcr.io/stuttgart-things/homerun2-sound-catcher-kustomize`.

```bash
kcl run kcl/main.k -Y tests/kcl-deploy-profile.yaml \
  -D config.httpRouteHostname=sound.example.com \
  -D config.apiToken=change-me | yq '.manifests[]' | kubectl apply -n homerun2 -f -
```

| Option | Default | What |
|---|---|---|
| `config.redisAddr`, `config.redisPort`, `config.redisPassword` | `redis-stack.homerun2…`, `6379`, – | Redis |
| `config.redisStreams` | `messages` | Comma-separated streams |
| `config.soundMode` | `full` | `standalone`: no Redis, only `POST /play` |
| `config.historySize` | `20` | Announcements the page shows |
| `config.apiToken` | – | Creates the `-api` Secret; enables `/play` and mute. Without it they do not exist |
| `config.httpRouteEnabled`, `…ParentRefName`, `…Hostname` | off | Gateway API route |
| `config.httpRouteSseTimeout` | – | Request timeout for `/api/events`, e.g. `0s` (none) |
| `config.profileData` | the default rules | `profile.yaml`, mounted from a ConfigMap |
| `config.extraEnvVars` | – | Anything else from the README's environment table |

## Things that are different in a pod

- **Exactly one replica, `Recreate`.** The history lives in the pod's memory.
  Two pods would split the consumer group's messages, and each page would see
  only one pod's half. The schema rejects `replicas` above 1. A pod restart
  empties the history, and open pages reload to whatever the new pod has.
- **Server-sent events through the gateway.** `/api/events` is one
  long-running response. Envoy-based gateways end a request after 15s by
  default, so the stream would drop and reconnect over and over. Set
  `httpRouteSseTimeout: "0s"` where the gateway supports HTTPRoute timeouts
  (Gateway API v1.2+). Without it the page still works: it reconnects, reloads
  the list, and plays whatever came in during the last 60 seconds. The server
  pings every 10s, which keeps idle-timeout proxies open.
- **Read-only root filesystem.** Synthesized speech is cached in the `/tmp`
  emptyDir (256Mi).
- **Resources.** Piper needs memory while it speaks: the requests are
  192Mi/100m and the limits 512Mi/1 CPU.
- **Who can listen.** Anyone who reaches the route hears the announcements
  and sees the history. Put the route behind your gateway's auth if that
  matters. The write endpoints need the token anyway.
