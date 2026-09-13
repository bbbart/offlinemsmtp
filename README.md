![offlinemsmtp](./logo/logo.png)

Allows you to use `msmtp` offline by queuing email until you have an internet
connection.

[![Lint and Build](https://github.com/bbbart/offlinemsmtp/actions/workflows/build.yaml/badge.svg)](https://github.com/bbbart/offlinemsmtp/actions/workflows/build.yaml)
[![AUR Version](https://img.shields.io/aur/version/offlinemsmtp?logo=linux&logoColor=fff)](https://aur.archlinux.org/packages/offlinemsmtp/)
[![LiberaPay Donation Status](https://img.shields.io/liberapay/receives/sumner.svg?logo=liberapay)](https://liberapay.com/sumner/donate)

## Features

* Runs as a daemon and (at a configurable time interval) attempts to send the
  mail in the queue directory.
* Drop-in replacement for `msmtp` in your mutt config.
* Only attempts to send the queued email message if it can connect to the
  configured SMTP server.
* Tells temporary delivery failures apart from permanent ones: a message that
  the server rejects temporarily stays in the queue and is retried, while one
  it rejects permanently is set aside instead of being retried forever. See
  [Permanently rejected mail](#permanently-rejected-mail).
* When a new email message comes into the queue and you are already online,
  `offlinemsmtp` will send it immediately.
* Integrates with system notifications so that you are notified when mail is
  being sent. Notifications name the message by its subject, and the "Sending
  ..." notification is updated in place with the outcome instead of leaving a
  second one behind.
* Disable/enable sending of mail by the presence/absence of a file. This is
  useful if you want to have some sort of "offline mode".

## Installation

On Arch Linux, install the `offlinemsmtp` package from the
[AUR](https://aur.archlinux.org/packages/offlinemsmtp/). For example, if you use
`yay`:

    yay -S offlinemsmtp

Anywhere else, install straight from this repository:

    pip install --user git+https://github.com/bbbart/offlinemsmtp

**Not on PyPI.** The `offlinemsmtp` package on PyPI belongs to the original
project and its last release was 0.4.0 in November 2022, so `pip install
offlinemsmtp` gives you neither this fork nor anything recent. See
[Relationship to the original project](#relationship-to-the-original-project).

## Run the daemon using systemd

Create a file called ``~/.config/systemd/user/offlinemsmtp.service`` with the
following content (if you installed via the AUR package, a service file was
already created for you in ``/usr/lib/systemd/user`` so you only need to do this
step if you want to customize the parameters passed to the daemon):

    [Unit]
    Description=offlinemsmtp

    [Service]
    ExecStart=/usr/bin/offlinemsmtp --daemon

    [Install]
    WantedBy=default.target

Then, enable and start `offlinemsmtp` using systemd:

    systemctl --user daemon-reload
    systemctl --user enable --now offlinemsmtp

## Usage

`offlinemsmtp` has two components: a daemon for listening to the outbox folder
and sending the mail when the network is available and a enqueuer for adding
mail to the send queue.

To run the daemon in the current command line (this is useful for testing), run
this command::

    offlinemsmtp --daemon

To enqueue emails, use the `offlinemsmtp` executable without `--daemon`. All
parameters (with a few caveats described below in [Command Line
Arguments](#command-line-arguments)) are forwarded on to `msmtp`. Anything
passed in via standard in will be forwarded over standard in to `msmtp` when the
mail is sent.

### Configuration with Mutt

To use offlinemsmtp with mutt, just replace `msmtp` in your mutt configuration
file with `offlinemsmtp`. Here is an example:

    set sendmail = "offlinemsmtp -a personal"

### Permanently rejected mail

Once the SMTP server accepts a message, it is removed from the outbox. If the
server rejects it *temporarily* — an SMTP 4xx reply, such as greylisting or a
mailbox that is temporarily full — the message stays in the queue and is
retried at every interval, as does a message that could not be sent because
the server was unreachable.

If the server rejects the message *permanently* — an SMTP 5xx reply, such as an
address that does not exist or a message over the server's size limit — then
retrying can never succeed. Instead of retrying forever, `offlinemsmtp` moves
the message into the `failed` subdirectory of the outbox and shows a critical
notification. Alongside the message it writes a `.err` file containing
`msmtp`'s output, including the server's own reply:

    ~/.offlinemsmtp-outbox/failed/
    ├── 2026-09-08_14-22-57.998001-4703
    └── 2026-09-08_14-22-57.998001-4703.err

    $ cat ~/.offlinemsmtp-outbox/failed/2026-09-08_14-22-57.998001-4703.err
    exit code: 65
    msmtp: recipient address nosuch@example.com not accepted by the server
    msmtp: server message: 550 5.1.1 <nosuch@example.com>: no such user here
    msmtp: could not send mail (account personal from /home/you/.msmtprc)

Nothing in `failed` is ever deleted, so no mail is lost. It is up to you to
inspect these messages and clean them up. To retry one — after fixing the
recipient's address, for instance — move the message file (not its `.err` file)
back into the outbox directory and the daemon will pick it up again.

### Command Line Arguments

offlinemsmtp accepts a number of command line arguments:

- `-h`, `--help` - shows a help message and exits.
- `-o DIR`, `--outbox-directory DIR` - set the directory to use as the outbox.
  Defaults to `~/.offlinemsmtp-outbox`.
- `-d`, `--daemon` - run the offlinemsmtp daemon.
- `-s`, `--silent` - set to disable all logging and notifications.
- `-i INTERVAL`, `--interval INTERVAL` - set the interval (in seconds) at which
  to attempt to flush the send queue. Defaults to 60.
- `-C FILE`, `--file FILE` - the msmtp configuration file to use.
- `--send-mail-file FILE` - only send mail if this file exists (defaults to
  `None` meaning that no file is required for mail sending to be enabled)
- `--send-timeout SECONDS` - the number of seconds `msmtp` is given to send a
  single message before it is aborted and the message is put back in the queue.
  Defaults to 90. Without this, a server that accepts a connection and then
  stops responding stalls the queue indefinitely.
- All remaining arguments are passed to `msmtp`. The `-C` argument is
  automatically passed to `msmtp`.
- Anything after a special `--` argument will be passed to `msmtp`. This allows
  you to pass arguments that may conflict with `offlinemsmtp` arguments to
  `msmtp`.

## Contributing

See the [CONTRIBUTING.md](./CONTRIBUTING.md) document for details on how to
contribute to the project.

## Relationship to the original project

This is a fork of [sumnerevans/offlinemsmtp][upstream], which is where every
release up to 0.4.0 came from. As of its v1.0.0 the original has been
[rewritten in Go][upstream-rewrite] and relicensed under MIT, and it can no
longer be installed with `pip`. This fork continues the Python implementation
under GPL3, so the two have diverged: their 1.x and this 0.x are different
programs that happen to share a name and a purpose.

[upstream]: https://github.com/sumnerevans/offlinemsmtp
[upstream-rewrite]: https://github.com/sumnerevans/offlinemsmtp/releases/tag/v1.0.0

## Other projects

- https://github.com/marlam/msmtp-mirror/tree/master/scripts/msmtpqueue - this
  is included with `msmtp`, but doesn't have all of the features that I want.
- https://github.com/dcbaker/py-mailqueued - looks cool, I didn't see it when I
  was researching, but it's probably better than my implementation, even thought
  I had a lot of fun doing mine.
- https://github.com/venkytv/msmtp-offline - it's written in Ruby.
