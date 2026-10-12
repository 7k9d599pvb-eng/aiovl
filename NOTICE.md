# Attribution and adaptation boundary

The MIT license and copyright notice of aiogram are preserved in `LICENSE`:
Copyright (c) 2017 - present Alex Root Junior.

The method-object and ordered-dispatch shapes in `aiovl/methods.py` and
`aiovl/dispatcher.py` are small VLook adaptations of ideas reviewed in aiogram
v3.31.0, pinned to commit
`97cfe79fa0ac9459d498bdb15cb7cb0530dbaac7`.
The existing copyright notices in those modules are retained.

Upstream source:
[aiogram v3.31.0](https://github.com/aiogram/aiogram/releases/tag/v3.31.0),
[pinned commit](https://github.com/aiogram/aiogram/commit/97cfe79fa0ac9459d498bdb15cb7cb0530dbaac7).
Machine-readable provenance is in `aiovl/UPSTREAM.json`.

The active HTTP client is the original bounded VLook transport. It sends bot
credentials only in the Authorization header and uses the VLook private-chat
contract. This repository contains no copied aiogram runtime or staged source
snapshot and has no aiogram dependency. It is not a drop-in aiogram fork, a
Telegram client, or an implementation of the complete Telegram Bot API.

The standalone tests use synthetic data and injected transports; no VLook server
implementation or private deployment materials are included.
