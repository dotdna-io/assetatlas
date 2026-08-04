# Logging

The **Logging** tab controls how much the application logs at runtime. It lives under **Settings → System → Logging** and is admin-only. All four settings take effect without a restart.

## What you can set

- **Enable Request Logging** (default on) — log each HTTP request's start and completion, with timing and user context.
- **Enable Performance Tracking** (default on) — track request-latency percentiles.
- **Health Check Log Level** (default DEBUG) — how loudly health-check endpoints log; DEBUG is least noisy in normal logs, WARNING logs only errors.
- **Slow Request Threshold Ms** (default 1000) — requests slower than this many milliseconds are logged as warnings.

Edit any value and click **Save**; each row has a reset-to-default control.

The overall log level is set in the server's environment configuration, not here — this tab covers only the four options above.
