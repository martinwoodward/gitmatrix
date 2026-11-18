# GitMatrix REST API v1

Everything the web UI does goes through this JSON API, so anything on the LAN (scripts,
CI jobs, Home Assistant's REST integrations) can do it too. Base URL:
`http://gitmatrix.local/api/v1` (or the device's IP; `http://192.168.4.1` in setup mode).
Phase 6 adds MQTT and Home Assistant discovery on top of the same state and events.

- [Conventions](#conventions)
- [Status and events](#status-and-events): `status`, `events`
- [Display and audio](#display-and-audio): `brightness`, `volume`, `sound`
- [Plugins](#plugins): list, show, next/prev, order, enable/disable, settings
- [Notifications](#notifications): `notify`
- [Sounds library](#sounds-library): `sounds`
- [Plugin store](#plugin-store): `store`
- [Settings, WiFi, Bluetooth, system](#settings-wifi-bluetooth-system)

## Conventions

**Auth.** Open on the LAN by default. If an API token is set (Settings page, or
`POST settings {"api_token": "..."}`), every `/api/v1/*` route needs
`Authorization: Bearer <token>` and answers `401 {"error": "unauthorized"}` without it.
Pages served by the device set the token as an `HttpOnly; SameSite=Strict` cookie, so
the browser UI keeps working. Writes authenticated by that cookie must come from the
device's own origin. This keeps other web sites and casual scripts out. It does not stop
someone on your LAN who has a browser, and plain HTTP has no TLS.

**Bodies.** Send JSON with `Content-Type: application/json`. Form-encoded bodies also
work. Bodies over 64 KB get 413 before they are read. The exception is a raw sound
upload, which can be up to 256 KB.

**Errors.** Errors are JSON: `{"error": "message"}`, with 400 (bad input), 401, 404
(unknown plugin or sound), 409 (conflict), 413 (too large), 429 (busy or rate limited,
often with `retry_ms`) or 503 (subsystem not running). Some 409s carry
`"audio_streaming": true`, which means a flash write is refused while Bluetooth audio
plays. Retry with `"pause_audio": true` (this disconnects the speaker) or with
`"queue": true` where offered.

**Async work.** 202 means accepted and still running: a notification queued, a sound
started, a scan or install begun.

The examples below use these shell variables:

```sh
GM=http://gitmatrix.local/api/v1
AUTH='Authorization: Bearer my-token'     # only if a token is set
```

## Status and events

### `GET status`

Everything at a glance. Cheap enough to poll every few seconds.

```sh
curl -s -H "$AUTH" $GM/status
```

```json
{
  "version": "0.1.0", "firmware": "0.1.0", "uptime_ms": 81234,
  "plugin": "contributions", "mode": "plugin",
  "now_showing": {"name": "contributions", "title": "Contributions"},
  "menu_open": false, "safe_mode": false, "plugins": 6, "delay": 30,
  "brightness": 60, "volume": 50,
  "wifi": {"mode": "sta", "connected": true, "ssid": "home", "ip": "192.168.1.42", "hostname": "gitmatrix", "...": "..."},
  "bluetooth": {"name": "GitMatrix", "connected": true, "streaming": false, "pairable": true, "pairable_supported": true},
  "input": {"seq": 12, "last": {"kind": "turn", "value": 1, "age_ms": 5300}},
  "encoder": {"position": 4, "seq": 12, "last": {"action": "turn", "delta": 1, "position": 4}},
  "events": {"seq": 57, "boot": 1739201234, "pollers": 1},
  "memory": {"free": 182000, "used": 230000},
  "flash": {"total": 14680064, "free": 9502720},
  "token_required": true,
  "notifications": {"active": null, "queued": [], "shown": 3, "dropped": 0, "last": {"...": "..."}, "last_error": null}
}
```

| Field | |
|---|---|
| `plugin`, `now_showing` | the plugin in the rotation (it stays put while a notification or the menu covers it) |
| `mode` | `plugin`, `menu` or `notification`: what's on the screen right now |
| `encoder.position` | net knob detents since boot (+ clockwise). `encoder.last` is the last knob event, in the same shape as the [events](#get-events) |
| `input` | the raw last input event (kept for compatibility) |
| `events.seq`, `events.boot` | the newest event's seq and this boot's id; see below |
| `memory`, `flash` | bytes free/used (RAM) and free/total (filesystem) |
| `notifications` | the active note, the queue, counters (as `GET notify`) |

### `GET events`

A bounded log of what happened on the device. Read it once, or long-poll it to react
within ~50 ms without polling `status`.

| Query | |
|---|---|
| `since` | the `next` value from your previous call. Leave it out to get everything still buffered, straight away |
| `timeout` | seconds to wait for something newer than `since` (default 25, max 30, `0` = don't wait) |
| `types` | a comma list to return only some types, e.g. `encoder,plugin` |

```json
{
  "events": [
    {"seq": 58, "type": "encoder", "action": "turn", "delta": 1, "position": 5, "age_ms": 120},
    {"seq": 59, "type": "plugin", "name": "weather", "previous": "contributions", "age_ms": 104}
  ],
  "next": 59, "dropped": 0, "reset": false, "boot": 1739201234
}
```

- `next` is the newest seq. Pass it as `since` next time. It moves past events that
  your `types` filter skipped.
- `dropped` is how many events between your `since` and the oldest one still held were
  lost. The ring holds 64 events. With `types` this can count losses of other types too.
- `reset: true` means your `since` is ahead of the device, so it has rebooted. Every
  buffered event is returned. `boot` also changes on every boot.
- At most 2 long-polls can wait at once (the device has little RAM and few sockets). A
  third gets `429 {"error": "too many long-polls", "retry_ms": 1000}`. Calls that answer
  straight away (no `since`, `timeout=0`, or something already newer) don't count
  towards the limit.
- Events are observed, not consumed. The knob still drives the device as normal.
- `age_ms` is how long ago the event happened. It is relative because the device may
  not know the wall-clock time.

| `type` | Fields |
|---|---|
| `encoder` | `action`: `turn` (with `delta`, ±detents), `press`, `release`, `long_press`; `position` |
| `plugin` | `name`, `previous`: the plugin in the rotation changed (knob, timer, API, menu) |
| `mode` | `mode` (`plugin` / `menu` / `notification`), `previous` |
| `notification` | `action: start` with `id`, `plugin`, `text`, `priority`; `action: end` with `id`, `dismissed` |
| `bluetooth` | `connected`, `streaming`, `previous` (checked every 0.5 s) |
| `wifi` | `connected`, `ip`, `previous` (checked every 0.5 s) |
| `brightness` | `value` (0-100), `previous` |
| `volume` | `value` (0-100), `previous` |

The first events after boot report the starting plugin, mode, WiFi, Bluetooth,
brightness and volume, each with `previous: null`.

A long-poll loop in shell:

```sh
since=$(curl -s -H "$AUTH" "$GM/events?timeout=0" | jq .next)
while true; do
  out=$(curl -s -H "$AUTH" "$GM/events?since=$since&timeout=25&types=encoder") || { sleep 2; continue; }
  echo "$out" | jq -c '.events[]'
  since=$(echo "$out" | jq .next)
done
```

## Display and audio

### `GET/POST brightness`, `GET/POST volume`

```sh
curl -s -H "$AUTH" $GM/brightness                                   # {"value": 60}
curl -s -H "$AUTH" -H 'Content-Type: application/json' -d '{"value": 30}' $GM/brightness
curl -s -H "$AUTH" -H 'Content-Type: application/json' -d '{"value": 80}' $GM/volume
```

Both take `{"value": 0-100}` and return the new value. Volume is the Bluetooth
speaker's level and also scales alert sounds. Both are saved to `config.py` once no
audio is streaming.

### `POST sound`, `DELETE sound`

Play a sound now, under the same rules as a notification's sound.

| Body | |
|---|---|
| one of `sound` / `sound_url` / `sound_data` | a built-in or uploaded sound name, a WAV URL (≤ 256 KB, fetched by the device), or a base64 WAV (≤ 32 KB). The WAV must be 16-bit PCM |
| `priority` | `normal` (default) or `high` |
| `force` | `true` plays even with alert sounds switched off or in quiet hours |

The response says what happened:

- `202 {"played": true, "result": "play"}`: the sound started. `result` is `"interrupt"`
  when it plays over a Bluetooth stream.
- `200 {"played": false, "result": R}`: nothing played. `R` is one of these values:
  - `off`: alert sounds are switched off.
  - `quiet`: it is quiet hours.
  - `streaming`: a phone is playing music.
  - `busy`: another sound is playing.

`force` never talks over a Bluetooth stream. Only a `high` sound with *High priority
sounds play over Bluetooth audio* turned on does that. If the URL fetch fails,
`GET sounds` shows the error as `last_result`. `DELETE sound` stops a sound started
this way: `{"stopped": true|false}`.

```sh
curl -s -H "$AUTH" -H 'Content-Type: application/json' -d '{"sound": "doorbell", "priority": "high"}' $GM/sound
curl -s -H "$AUTH" -H 'Content-Type: application/json' -d '{"sound_url": "http://nas.local/ding.wav", "force": true}' $GM/sound
curl -s -H "$AUTH" -X DELETE $GM/sound
```

## Plugins

### `GET plugins`, `GET plugins/{name}`

```json
{"plugins": [{"name": "contributions", "title": "Contributions", "enabled": true, "quarantined": false,
  "reason": null, "current": true, "loaded": true, "errors": 0, "last_error": null, "slow": false,
  "version": "1.0.0", "source": "built-in", "...": "..."}],
 "now_showing": {"name": "contributions", "title": "Contributions"}, "delay": 30}
```

The list is in rotation order. Built-in plugins appear even when disabled.

### `POST plugins/{name}/show`

There are two ways to use this:

- **No body.** Switch the rotation to the plugin. It stays until the knob, the rotation
  timer or another call moves on. Returns `{"ok": true, "now_showing": {...}}`, or 500
  if the plugin failed to load.
- **With `data` and/or `duration`.** Show it for a while, then go back to whatever was
  showing. This uses the notification path: it waits behind the menu, queues behind
  other notifications, and the plugin gets `on_notify(data)` and
  `gitmatrix.notification` (see the [plugin API](plugin-api.md#notifications)). The
  plugin may be disabled, but not quarantined (409). `duration` is in seconds, 60 at
  most. `priority`, `id`, `ttl`, `repeat` and `sound`/`sound_url`/`sound_data` work as
  in `notify`. Returns `202 {"ok": true, "id": "n7", "queued": 1, "temporary": true}`.

```sh
curl -s -H "$AUTH" -X POST $GM/plugins/weather/show
curl -s -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"duration": 10, "data": {"city": "Leeds"}}' $GM/plugins/weather/show
```

### `POST plugins/next`, `POST plugins/prev`

Step the rotation, like turning the knob. Returns `{"now_showing": {...}}`.

### `POST plugins/order`

`{"order": ["a", "b", ...]}` must list every installed plugin exactly once. Returns the
plugin list.

### `POST plugins/{name}/enable`, `disable`, `unquarantine`

Each returns the plugin's info.

- `enable` loads the plugin, or answers 409 if it is quarantined.
- `disable` unloads it and moves on if it was showing.
- `unquarantine` clears a plugin that crashed 3 times; it answers 409 if the plugin
  wasn't quarantined.

### `GET/POST plugins/{name}/settings`

`GET` returns `{name, title, schema, values, has_config_html, config_url}`. Secret
fields are never returned; they show `"set": true|false` instead. `POST` takes
`{key: value, ...}`, which is validated against the schema (400 on an unknown key or bad
value), and reloads the plugin. It answers 409 `audio_streaming` while Bluetooth audio
plays unless you send `"pause_audio": true`. Plugins with their own Art Matrix-style
page use `GET/POST /config?plugin={name}`, which is outside `/api/v1`.

## Notifications

### `POST notify`, `GET notify`, `DELETE notify`

```sh
curl -s -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"text": "Build failed on main", "sound": "error", "priority": "high", "color": "#ff4040"}' $GM/notify
# 202 {"id": "n4", "queued": 1}
curl -s -H "$AUTH" $GM/notify                 # active, queued, counters
curl -s -H "$AUTH" -X DELETE "$GM/notify?id=n4"   # dismiss one; no id: dismiss it and clear the queue
```

The fields are listed in the [README](../../gitmatrix/README.md#notifications-and-sounds):
`text`, `image`/`image_url`, `plugin`+`data`, `sound`/`sound_url`/`sound_data`,
`priority`, `duration`, `repeat`, `ttl`, `id`, `color`, `font`, `speed`. Unknown fields
get 400 and a full queue (8) gets 429. Each notification shows up on the event stream
as `notification` `start`/`end`.

## Sounds library

| Call | |
|---|---|
| `GET sounds` | `{sounds: [{name, builtin, size}], pending, used, max_size, max_total, playing, last_result}` |
| `GET sounds/{name}` | one entry, 404 if unknown |
| `POST sounds/{name}` | upload a WAV, either as a raw body (`Content-Type: audio/wav`, ≤ 256 KB) or `{"data": base64}`; 2 MB in total. Returns `202 {"queued": true}` while Bluetooth audio plays (it is saved when playback stops) |
| `DELETE sounds/{name}` | delete an uploaded sound (built-ins: 400) |
| `POST sounds/{name}/play` | preview it at the current volume, ignoring alert settings; `{"played": false, "reason"}` while streaming or busy |

```sh
curl -s -H "$AUTH" -H 'Content-Type: audio/wav' --data-binary @bell.wav $GM/sounds/bell
```

## Plugin store

| Call | |
|---|---|
| `GET store` | the catalogue: index entries plus installed/update state |
| `POST store/refresh` | refetch the index (202; 429 + `retry_ms` if too soon; 409 offline) |
| `POST store/install` | `{name, pause_audio?, queue?}` installs or updates. Answers `202 {"install": progress}`, or 409 `audio_streaming` |
| `GET store/install` | `{"install": progress}` for the install in progress |
| `POST plugins/{name}/remove` | `{keep_data: true, pause_audio?}` |

## MQTT and Home Assistant

### `GET/POST mqtt`

Optional MQTT client with Home Assistant discovery, off by default. `POST` takes any subset of
the fields below, validates all of them (400 and nothing changes on an error) and applies them
straight away: the client says goodbye and reconnects with the new settings. No reboot. While
Bluetooth audio plays, the change takes effect at once and is saved to flash when playback stops.

| Field | |
|---|---|
| `enabled` | boolean, default `false`. Turning it on needs a `host` |
| `host` | broker IP address (recommended) or host name, up to 128 characters. A name is looked up with a blocking DNS call, cached for 30 min; `.local` names may not resolve |
| `port` | 1-65535, or `null` for `default_port` (1883, or 8883 with TLS) |
| `user` | user name, or `null` |
| `password` | write-only; `""` or `null` clears it. `GET` shows `password_set` |
| `tls` | boolean. Encrypted, but the broker's certificate is **not** checked (`CERT_NONE`, like the plugin store) |
| `discovery` | boolean, default `true`: publish Home Assistant discovery config (retained) |
| `discovery_prefix` | default `homeassistant` |
| `base_topic` | `null` for `default_base_topic` (`gitmatrix/<hostname>`). `topic` is the one in use |

`GET` (and `POST`) also returns `status`: `{state, connected, reason, broker, base_topic,
node_id, last_error, retry_in_s, connected_for_s, connects, published, received, commands,
dropped}`. `state` is `disabled`, `waiting` (`reason`: `no WiFi` or `waiting for audio to
stop`), `connecting`, `connected` or `backoff` (retry in 2 s, doubling up to 5 min);
`unavailable` until WiFi has connected once after boot.

```sh
curl -X POST $GM/mqtt -H 'Content-Type: application/json' \
  -d '{"enabled": true, "host": "192.168.1.10", "user": "gitmatrix", "password": "..."}'
```

### Topics

`<base>` is `base_topic`. Everything is MQTT 3.1.1, QoS 0. State topics are retained and sent
on connect and whenever they change; numbers are 0-100.

| Topic | Payload |
|---|---|
| `<base>/availability` | `online` / `offline` (retained; `offline` is the last will) |
| `<base>/plugin` | the current plugin's name |
| `<base>/mode` | `plugin`, `menu` or `notification` |
| `<base>/brightness`, `<base>/volume` | `0`-`100` |
| `<base>/alert_sound` | `ON` / `OFF` |
| `<base>/bluetooth` | `{"connected": true, "streaming": false}` |
| `<base>/wifi` | `{"connected": true, "ip": "192.168.1.42"}` |
| `<base>/notification` | `{"showing": true, "id": "n4"}` |
| `<base>/encoder/position` | cumulative knob detents |
| `<base>/encoder` | not retained: `{"event_type": "turn_cw"\|"turn_ccw"\|"press"\|"long_press", "position": 3}` per knob action (at most 16 wait to be sent; extra ones count in `dropped`) |
| `<base>/encoder/action` | not retained: the same event type as plain text (for device triggers) |
| `<base>/result` | not retained: `{"command": "notify", "ok": true, ...}` after each command, or `ok: false` + `error` |

Commands (retained messages on these topics are ignored, so they never re-run on reconnect):

| Topic | Payload |
|---|---|
| `<base>/plugin/set` | a plugin name: show it |
| `<base>/plugin/next`, `<base>/plugin/prev` | anything |
| `<base>/brightness/set`, `<base>/volume/set` | `0`-`100` |
| `<base>/alert_sound/set` | `ON` / `OFF` |
| `<base>/notify` | plain text, or the same JSON object as `POST notify`. `result` has the `id` |
| `<base>/notify/dismiss` | a notification id, or empty / `PRESS` for all |
| `<base>/sound` | a sound name, or the same JSON object as `POST sound` |

```sh
mosquitto_pub -h 192.168.1.10 -t gitmatrix/gitmatrix/notify -m '{"text": "Build passed", "color": "#2da44e", "sound": "chime"}'
mosquitto_sub -h 192.168.1.10 -t 'gitmatrix/gitmatrix/#' -v
```

### Home Assistant discovery

With `discovery` on, the device appears as **GitMatrix** (identifier `gitmatrix_<chip id>`) with:
a **Plugin** select (the enabled plugins), **Next/Previous plugin** buttons, **Brightness** and
**Volume** numbers, an **Alert sounds** switch, a **Notify** entity, a **Dismiss notification**
button, a **Play sound** select (built-in and custom sounds), a **Knob** event entity, a **Knob
position** sensor, **Notification showing** / **Bluetooth connected** / **Bluetooth audio
playing** binary sensors, **IP address** and **Mode** sensors, and device triggers
`button_short_press`, `button_long_press`, `turn_cw`, `turn_ccw` (subtype `knob`). It listens
to `<discovery_prefix>/status` and republishes everything when Home Assistant restarts.
Turning discovery off, or changing the prefix, base topic or broker, removes the old entities.

## Settings, WiFi, Bluetooth, system

### `GET/POST settings`

| Field | |
|---|---|
| `brightness`, `volume` | 0-100 |
| `delay` | seconds per plugin in the rotation, 0-86400 (0 = no rotation) |
| `hostname` | the mDNS name; a change returns `reboot_required: true` |
| `time_zone` | a key from `time_zones` |
| `api_token` | write-only. Up to 64 characters, with no spaces, quotes, `;` or `,`. Send `""` to remove it. `GET` shows `token_set` |
| `alert_sound`, `quiet_hours_enabled`, `quiet_bypass_high`, `alert_interrupt_audio` | booleans |
| `quiet_start`, `quiet_end`, `quiet_days` | `"HH:MM"`, `"HH:MM"`, `[0..6]` (0 = Monday). `GET` also gives `quiet_now` |

### WiFi

| Call | |
|---|---|
| `GET wifi` | mode, ssid, ip, hostname, `audio_streaming`, etc. |
| `POST wifi` | `{ssid, password?, hostname?}`: saves and reboots in ~1.5 s. Leaving out `password` for the same SSID keeps the stored one |
| `POST wifi/forget` | forget the network and reboot into setup mode |
| `POST wifi/scan` | `202 {"scanning": true}`; 429 + `retry_ms` within 15 s of the last scan. The display pauses while the radio scans |
| `GET wifi/networks` | `{scanning, networks, age_ms, current}` |

### Bluetooth

| Call | |
|---|---|
| `GET bluetooth` | `{name, connected, streaming, pairable, pairable_supported}` |
| `POST bluetooth/forget` | forget the paired phone |
| `POST bluetooth/pairable` | `{"value": true|false}` |

### System

| Call | |
|---|---|
| `GET system` | version, MicroPython version, uptime, memory, flash, `audio_pauses`, safe mode, quarantined plugins, recent plugin errors, `reboot_pending` |
| `GET system/logs` | `{lines: [{age_ms, text}]}`, the in-RAM log |
| `POST system/reboot` | reboot in ~1.5 s. On this board a warm reset can occasionally hang until it is unplugged, so avoid scripting it |

The System page's **Developer** section can send any of these calls from the browser and
can watch the event stream live.
