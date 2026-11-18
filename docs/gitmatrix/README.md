# GitMatrix docs

GitMatrix is the firmware for Pimoroni's 53×7 GitHub Contribution Widget, modelled on
[Art Matrix](https://github.com/pimoroni/artmatrix). Building and deploying it is covered
in [`gitmatrix/README.md`](../../gitmatrix/README.md).

## Writing plugins

- [Plugin API](plugin-api.md): anatomy of a plugin, hooks, `package.json`, settings schema,
  `config.html`, Art Matrix compatibility tables, GitMatrix additions, notifications, fault
  containment.
- [Porting from Art Matrix](porting.md): every difference (display, input, audio, settings,
  state, unsupported APIs) and a worked before/after example.
- [Plugin template](../../gitmatrix/templates/plugin/README.md): a minimal plugin to copy,
  loaded by the host test harness (`gitmatrix/tests/test_template.py`).
- [gitmatrix-plugins](https://github.com/martinwoodward/gitmatrix-plugins): the public plugin
  repository the store installs from.

## Controlling the widget

- [REST API, events and MQTT](api.md): the `/api/v1/` reference, the
  [events long-poll](api.md#get-events), [notifications](api.md#notifications) and
  [MQTT / Home Assistant](api.md#mqtt-and-home-assistant).
- Notifications for plugin authors: [plugin-api.md](plugin-api.md#notifications); for
  users and scripts: [`gitmatrix/README.md`](../../gitmatrix/README.md#notifications-and-sounds).

## Design and hardware

- [Plan](plan.md): architecture, phases (§4) and the design of each phase as built (§6–§9).
- On-device checklists (not yet run; the host harness covers the logic):
  [phase 2](plan.md#51-phase-2-on-device-checklist-not-yet-run-host-harness-only),
  [phase 3](plan.md#52-phase-3-on-device-checklist-not-yet-run-host-harness-only),
  [phase 4](plan.md#53-phase-4-on-device-checklist-not-yet-run-host-harness-only),
  [phase 5](plan.md#54-phase-5-on-device-checklist-not-yet-run-host-harness-only),
  [phase 6](plan.md#55-phase-6-on-device-checklist-not-yet-run-host-harness-only).
- [Phase 0 spikes](phase0.md): measured hardware behaviour the design depends on, notably
  that warm resets can hang the board until it's replugged and that flash writes are
  deferred while Bluetooth audio streams.
