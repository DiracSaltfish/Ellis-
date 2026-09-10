# Native Webull module

`WebullEngine` is the sole owner of Webull business state inside the Hub Agent.
It implements the weekday 09:00–16:00 Asia/Shanghai collection schedule,
00:00/09:00 login checks, depth validation and price aggregation,
MQTT type-109/protobuf decoding, session/sequence/hash generation, bounded
latest-only fan-out, JSONL/latest persistence, and the external 18765 v2 API.

The browser boundary is the bundled, headless
`Contents/Helpers/machome-webull-browser-helper`. It is a native Qt/CDP process,
emits raw HTTP/MQTT/auth/browser
events over NDJSON, and imports no previous gateway package. It has no
LaunchAgent and is started/stopped only by `WebullEngine`.

Safety defaults:

- `live_browser_enabled=false` in checked-in configuration;
- checked-in configuration sets `record_only=true`; a later canary must explicitly
  opt in before the Engine may launch the bundled browser helper;
- data is restricted to `~/Library/Application Support/MachomeHub/data/webull`;
- API credentials are generated under that data root with mode 0600;
- no notification, upload, order, or other production mutation path exists.

Tests use sanitized HTTP and generated MQTT/protobuf fixtures. They do not need
Chrome, network access, a prior virtual environment, or a previous Webull
source/deployment directory.
