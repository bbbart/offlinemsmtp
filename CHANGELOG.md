# Version 0.5.1

* **`msmtp` can no longer block the daemon indefinitely.** There was no timeout
  on the `msmtp` subprocess, so a server that accepted the connection and then
  stopped responding stalled the queue flush — and with it the inotify watcher,
  which meant new mail went unnoticed too — for as long as it stayed silent.
  `msmtp` now gets `--send-timeout` seconds (90 by default), after which it is
  killed and the message goes back into the queue. The pretend run that works
  out which server to connect to is bounded at ten seconds; it only prints the
  configuration and never touches the network.
* Notifications name the message by its `Subject` header instead of by its
  outbox filename, which was a timestamp and a process id and said nothing
  about which mail was meant. The header parsing was fixed along the way: it
  stops at the end of the headers (a `Subject:` line in the body used to win),
  it takes the first such header rather than the last, it accepts the
  whitespace that RFC 5322 makes optional after the colon (`Subject:like
  this`), it matches case-insensitively, and it no longer raises on a message
  that is not valid UTF-8.
* Sending a message leaves one notification behind rather than two: the
  "Sending ..." notification is now updated in place with the outcome.
* **`offlinemsmtp` is not published on PyPI.** The `offlinemsmtp` package there
  belongs to the original project and has not been updated since 0.4.0 in
  November 2022, so `pip install offlinemsmtp` installed neither this fork nor
  anything recent. The README now points at the AUR package and at `pip install
  git+https://github.com/bbbart/offlinemsmtp`, and explains how this fork
  relates to the original, which has since been rewritten in Go, relicensed
  under MIT and dropped `pip` installation altogether.
* The GitHub Actions workflow no longer carries a deploy job, which could never
  have worked without rights on that PyPI project.

# Version 0.5.0

* **Permanently rejected messages are no longer retried forever.** A message
  the SMTP server rejects with a 5xx reply can never be delivered, but it went
  straight back into the queue and was re-sent at every interval, raising a
  critical notification each time. Such a message is now moved into the
  `failed` subdirectory of the outbox, next to a `.err` file holding `msmtp`'s
  diagnostics and the server's own reply. Nothing is ever deleted: move the
  message back into the outbox to try it again. The distinction comes from
  `msmtp`'s exit code, which is `EX_DATAERR` (65) for a 5xx reply and
  `EX_UNAVAILABLE` (69) for a 4xx one.
* Temporary failures — a 4xx reply, or a server that cannot be reached — still
  stay in the queue and are retried, but no longer raise a `CRITICAL`
  notification every time.
* Notifications and the log now quote the SMTP server's own reply instead of
  only `msmtp`'s exit code. `msmtp`'s output is captured and logged as a
  warning, where it used to go to the daemon's own stderr.
* **Fixed the daemon dying on a filesystem error.** Reading a queued message,
  or deleting it after a successful send, can fail on a full or read-only
  filesystem. That ended the process when it happened during the periodic
  flush, and silently killed the inotify watcher when it happened there, after
  which the daemon stayed up but never noticed new mail again. A single
  message can no longer take down the flush. A message that was delivered but
  could not be deleted is reported and dropped from the queue, so that it is
  not delivered a second time.
* Note that `msmtp` reports a rejection *after* the message body with
  `EX_UNAVAILABLE` whatever the status code, so a 5xx at that point cannot be
  told apart from a 4xx and is still retried. The same applies to
  authentication failures. Both are limitations of `msmtp` itself.

# Version 0.4.4

* **Fixed silent mail loss when enqueueing multiple messages within the same
  second.** Outbox filenames had second resolution, so a second message
  truncated the first message's file while the daemon could already be
  sending it; the daemon's post-send unlink then destroyed the second
  message entirely, and its queued inotify event was skipped as
  "file no longer exists". Filenames now include microseconds and the PID.
* Messages are now written to a hidden temporary file and atomically renamed
  into the outbox, so the daemon can never pick up a partially written
  message. The daemon reacts to `IN_MOVED_TO` (in addition to
  `IN_CLOSE_WRITE`) and ignores dotfiles. **Upgrade the daemon and client
  together**: an old daemon will not notice messages enqueued by a new
  client until it is restarted.
* The queue flush is now serialized with a lock; previously the inotify
  watcher thread and the periodic retry loop could flush concurrently.
* Fixed a crash on daemon startup when `--outbox-directory` was passed on
  the command line (string was not converted to a `Path`).

# Version 0.4.1

* Only pass `--debug` to msmtp when log level is set to DEBUG, preventing
  full email contents from being logged to the journal.

* Infrastructure/DX Changes

  * Migrated build system from flit/pip-tools to uv + hatchling
  * Re-added `PyGObject` as a declared dependency (was dropped during flit
    migration)
  * Forked to https://github.com/bbbart/offlinemsmtp

# Version 0.4.0

* **Dependency change**: the `watchdog` dependency has been replaced by
  `inotify`.

* Infrastructure/DX Changes

  * Added pre-commit and isort
  * Migrated to GitHub
  * Added dependabot to auto-update GitHub Actions versions
  * Converted the CI to not use Nix for linting and building (it's now way
    faster)

# Version 0.3.10

* Require latest `PyGObject` and `watchdog` dependencies.

# Version 0.3.9

* Wait for one second after the path gets created on disk to allow the file to
  be fully written.
* Fixed `PyGObject` dependency

* INFRASTRUCTURE

  * Migrated to GitHub and GitHub Actions
  * Added a custom style check for TODOs and ensuring that all instances of the
    version are correct.
  * Add a `shell.nix` for a more consistent development environment, and use it
    with direnv.
  * Got rid of `setup.py` and replaced with `pyproject.toml`.

# Version 0.3.8

* Use `/usr/bin/env` to find `msmtp` executable for compatibility with NixOS.

# Version 0.3.7

* Fixed dependency issue where sphinx was required as an `install_dependency`
  rather than a dev dependency.

* INFRASTRUCTURE

  * Migrated to sr.ht because of usability regressions in GitLab.
  * Migrated from Pipenv to Poetry because Poetry is actually fast.
  * Added `CONTRIBUTING.md` document to help onboard contributors.
  * Added a `.editorconfig` file to help create consistent development
    environments for contributors.

# Version 0.3.6

* Added the ability to to delimit arguments that should always be sent to
  `msmtp` using `--`.
* Added better README documentation.
* The AUR package now automatically installs the `.service` file to
  `/usr/lib/systemd/user`.
* Convert to use the logging library instead of pure print.

# Version 0.3.5

* Use a real socket to try and connect to the SMTP server instead of ping.
* `offlinemsmtp` only tries to connect to the server that it is sending mail
  to for determining if it should attempt to send that element of the queue.

# Version 0.3.4

* Added `--send-mail-file` config option to allow the user to specify a file
  which must exist for mail sending to be enabled.

# Version 0.3.3

* Change failure timeout on notifications to 30s
