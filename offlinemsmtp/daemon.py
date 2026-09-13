"""Daemon that watches the offlinemsmtp outbox directory for queued emails.

Sends out the emails as soon as they arrive, if the system is online.
"""

import logging
import re
import socket
import threading
import time
from pathlib import Path
from queue import Queue
from subprocess import PIPE, TimeoutExpired, run

import gi
import inotify.adapters

gi.require_version("Notify", "0.7")
from gi.repository import Notify

from offlinemsmtp import util

# msmtp uses the exit codes from ``sysexits.h``. Two of them mean that the
# failure is permanent *for this particular message*, so that retrying it can
# never succeed:
#
# * ``EX_DATAERR`` (65): the server rejected the envelope or the DATA command
#   with a 5xx reply. Sending mail is the only thing msmtp uses this code for,
#   so it is an exact match for "permanent SMTP failure".
# * ``EX_USAGE`` (64): msmtp refused the arguments stored with the message
#   (``no recipients found``, for example). Those arguments were recorded when
#   the message was enqueued, so they will never change.
#
# Every other exit code is either transient or fixable without touching the
# message, so those messages stay in the queue. That notably includes
# ``EX_UNAVAILABLE`` (69), which is what a 4xx reply maps to, as well as
# ``EX_TEMPFAIL`` (75), ``EX_NOHOST`` (68), ``EX_IOERR`` (74), ``EX_NOPERM``
# (77, authentication failure) and ``EX_CONFIG`` (78).
EX_USAGE = 64
EX_DATAERR = 65
PERMANENT_FAILURE_EXIT_CODES = frozenset({EX_USAGE, EX_DATAERR})

# Messages that can never be sent are moved to this subdirectory of the outbox,
# rather than being retried forever or thrown away.
FAILED_DIR_NAME = "failed"

# How long msmtp gets to send one message before it is killed and the message
# is put back in the queue. Without a limit one unresponsive server stalls the
# whole flush, and with it the inotify thread, for as long as it stays
# unresponsive.
DEFAULT_SEND_TIMEOUT = 90  # seconds

# The pretend run only prints the configuration and does not talk to the
# network, so it has no business taking any time at all.
PRETEND_TIMEOUT = 10  # seconds


class Daemon:
    """Listens for changes to the outbox directory."""

    def __init__(self, args):
        """Initialize the daemon."""
        self.connected = False
        self.silent = args.silent
        self.config_file = Path(args.file).resolve()
        self.send_mail_file = Path(args.send_mail_file).resolve() if args.send_mail_file else None
        self.root_dir = Path(args.dir).resolve()
        self.failed_dir = self.root_dir.joinpath(FAILED_DIR_NAME)
        self.send_timeout = getattr(args, "send_timeout", DEFAULT_SEND_TIMEOUT)

        # Serializes flush_queue between the inotify watcher thread and the
        # periodic flush in the main loop.
        self.flush_lock = threading.Lock()

        # Initialize the queue
        self.queue = Queue()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        for file in self.root_dir.iterdir():
            if file.name.startswith("."):
                # Skip hidden files: in-progress (or abandoned) enqueue
                # temporary files.
                continue
            if not file.is_file():
                # Skip the failed/ directory, and anything else that is not a
                # message.
                continue
            self.queue.put(file)

    def send_enabled(self):
        """Is the file allowing us to send out emails present?

        Always returns True if such a file is not configured.
        """
        return self.send_mail_file is None or self.send_mail_file.exists()

    def on_created(self, filename: Path):
        """Handle file creation."""
        logging.info("New message detected: %s", filename)

        self.queue.put(filename)
        self.flush_queue()

    def flush_queue(self):
        """Send all emails in the queue."""
        with self.flush_lock:
            self._flush_queue()

    def _flush_queue(self):
        if not self.send_enabled():
            util.notify("Sending email disabled", timeout=5000)
            return

        failed = []
        while not self.queue.empty():
            message_path = self.queue.get()
            try:
                keep_queued = self.send_message(message_path)
            except Exception:
                # One message must never take down the flush, let alone the
                # daemon. _flush_queue also runs on the inotify thread, where
                # an uncaught exception kills the watcher and new messages go
                # unnoticed until the daemon is restarted; from the main loop
                # it ends the process. Reading a message or deleting it can
                # fail on a full or read-only filesystem, and msmtp may not be
                # installed at all.
                #
                # The message stays in the queue: whatever went wrong, it is
                # better to report it and retry than to set aside mail that is
                # probably still deliverable once the cause is fixed.
                logging.exception("Could not process %s", message_path)
                util.notify(
                    f"Could not process {message_path}. Keeping it in the "
                    f"queue; see the log for details.",
                    timeout=30000,  # 30 seconds
                    urgency=Notify.Urgency.CRITICAL,
                )
                keep_queued = True

            if keep_queued:
                failed.append(message_path)

        # Re-enqueue the failed messages.
        for file in failed:
            self.queue.put(file)

    def send_message(self, message_path):
        """Try to send a single queued message.

        Returns whether the message should stay in the queue.
        """
        if not message_path.exists():
            # It was removed, nothing we can do about that.
            return False

        # Open the message.
        with open(message_path, "rb") as message_file:
            msmtp_args = message_file.readline().decode()
            message_content = message_file.read()

        subject = self.get_subject(message_content)

        if not self.can_send_message(msmtp_args, message_content, subject):
            return True

        # A notification that lives "forever". Every outcome below updates it
        # in place, so that sending a message leaves one notification behind
        # rather than a "Sending ..." followed by a second one.
        sending = util.notify(f'Sending "{subject}"...', timeout=600000)

        # Send the message.
        try:
            logging.debug(self.get_msmtp_command(msmtp_args))
            send_cmd = run(
                self.get_msmtp_command(msmtp_args),
                input=message_content,
                stderr=PIPE,
                check=False,
                timeout=self.send_timeout,
            )
        except TimeoutExpired:
            # run() has already killed msmtp. A server that stopped responding
            # may well answer next time, so this counts as a temporary failure.
            util.notify(
                f'Sending "{subject}" took longer than {self.send_timeout} '
                f"seconds and was aborted. Putting it back into the queue to "
                f"try later.",
                timeout=30000,  # 30 seconds
                urgency=Notify.Urgency.NORMAL,
                replace=sending,
            )
            return True
        except Exception:
            # msmtp could not be run at all. Take the notification off the
            # screen rather than leave it there for its full ten minutes, and
            # let the caller report the failure.
            if sending:
                sending.close()
            raise

        # msmtp's diagnostics used to go to the daemon's own stderr. They are
        # captured now, so log them to keep them visible. msmtp already
        # prefixes every line it writes with "msmtp:".
        error_output = send_cmd.stderr.decode("utf-8", errors="replace").strip()
        if error_output:
            logging.warning("%s", error_output)

        # Determine whether or not the send was successful or not.
        if send_cmd.returncode == 0:
            try:
                message_path.unlink()
            except OSError as e:
                # The message did go out, so it must not be sent again: this
                # is the one failure where keeping it queued is wrong, because
                # every retry would deliver another copy.
                util.notify(
                    f'Sent "{subject}", but {message_path} could not be '
                    f"removed: {e}\n"
                    f"Delete it by hand, otherwise it is sent again the next "
                    f"time the daemon starts.",
                    timeout=30000,  # 30 seconds
                    urgency=Notify.Urgency.CRITICAL,
                    replace=sending,
                )
            else:
                util.notify(f'Sent "{subject}".', timeout=5000, replace=sending)
            return False

        if send_cmd.returncode in PERMANENT_FAILURE_EXIT_CODES:
            # Retrying is pointless, so take the message out of the queue
            # instead of letting it fail again every interval. If it cannot be
            # moved aside, keep it queued rather than lose track of it.
            return not self.fail_message(
                message_path, send_cmd.returncode, error_output, subject, replace=sending
            )

        util.notify(
            f'"{subject}" did not send. Putting it back into the queue to try '
            f"later.\n"
            f"{self.describe_failure(send_cmd.returncode, error_output)}",
            timeout=30000,  # 30 seconds
            urgency=Notify.Urgency.NORMAL,
            replace=sending,
        )
        return True

    host_re = re.compile("host = (.*)")
    port_re = re.compile("port = (.*)")
    subject_re = re.compile(r"^Subject:[ \t]*(.*)$", re.IGNORECASE)
    server_message_re = re.compile("^msmtp: server message: (.*)$", re.MULTILINE)

    @classmethod
    def get_subject(cls, message_content):
        """The message's ``Subject`` header, to name it in notifications."""
        for line in message_content.decode("utf-8", errors="replace").split("\n"):
            if not line.strip():
                # End of the headers. A "Subject:" line in the body is not one.
                break
            if subject_match := cls.subject_re.match(line):
                return subject_match.group(1).strip()
        return "<no subject>"

    @classmethod
    def describe_failure(cls, returncode, error_output):
        """Short description of why msmtp failed, to show in a notification.

        The SMTP server's own reply says a lot more than the exit code does, so
        it is preferred when msmtp reported one.
        """
        server_message = cls.server_message_re.search(error_output)
        if server_message:
            return server_message.group(1)
        return f"Return Code: {returncode}"

    def fail_message(self, message_path, returncode, error_output, subject, replace=None):
        """Move a message that can never be sent out of the queue.

        It goes to the ``failed`` subdirectory of the outbox, next to a
        ``.err`` file holding msmtp's diagnostics, so that the mail itself is
        not lost and the reason for the rejection can still be looked up.

        Returns whether the message was moved. A read-only or full outbox must
        not take the daemon down with it: this runs on the inotify thread as
        well as in the main loop, and an exception there would kill the watcher
        and leave new messages unnoticed. The caller keeps the message queued
        instead, which is what it did before permanent failures were
        recognized, so nothing is lost while the outbox is unwritable.
        """
        try:
            self.failed_dir.mkdir(parents=True, exist_ok=True)

            # Message names contain microseconds and the PID, so a name that is
            # already taken should be impossible. Pick another one rather than
            # overwrite mail if it happens anyway.
            failed_path = self.failed_dir.joinpath(message_path.name)
            attempt = 1
            while failed_path.exists():
                failed_path = self.failed_dir.joinpath(f"{message_path.name}.{attempt}")
                attempt += 1

            # Write the diagnostics first: a stray .err file is harmless (a
            # later attempt overwrites it), whereas a failed message without
            # one gives no clue as to what went wrong.
            failed_path.with_name(f"{failed_path.name}.err").write_text(
                f"exit code: {returncode}\n{error_output}\n"
            )
            message_path.rename(failed_path)
        except OSError as e:
            util.notify(
                f'"{subject}" was permanently rejected but could not be moved '
                f"to {self.failed_dir}: {e}\n"
                f"Keeping it in the queue.",
                timeout=30000,  # 30 seconds
                urgency=Notify.Urgency.CRITICAL,
                replace=replace,
            )
            return False

        util.notify(
            f'"{subject}" was permanently rejected; moved to {failed_path}.\n'
            f"{self.describe_failure(returncode, error_output)}",
            timeout=30000,  # 30 seconds
            urgency=Notify.Urgency.CRITICAL,
            replace=replace,
        )
        return True

    def get_msmtp_command(self, msmtp_args, pretend=False):
        """Full msmtp command to run to send emails."""
        args = ["/usr/bin/env", "msmtp"]
        if pretend or logging.getLogger().isEnabledFor(logging.DEBUG):
            args.append("--debug")
        if pretend:
            args.append("-P")
        args += ["-C", str(self.config_file), *msmtp_args.split()]
        return args

    def can_send_message(self, msmtp_args, message_content, subject):
        """Tests whether or not the computer can connect to the necessary server
        to send the given message.
        """
        try:
            test_run = run(
                self.get_msmtp_command(msmtp_args, pretend=True),
                input=message_content,
                stdout=PIPE,
                stderr=PIPE,
                check=False,
                timeout=PRETEND_TIMEOUT,
            )
        except TimeoutExpired:
            logging.warning(
                "msmtp did not print its configuration within %s seconds", PRETEND_TIMEOUT
            )
            return False

        host, port = None, None
        for line in test_run.stdout.decode("utf-8").split("\n"):
            if host_match := self.host_re.match(line):
                host = host_match.group(1)
            elif port_match := self.port_re.match(line):
                port = int(port_match.group(1))

            if host and port:
                break

        if not host or not port:
            return False

        # Try to connect to the socket.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)  # 2 second timeout
        try:
            socket_open = sock.connect_ex((host, port))
        except socket.gaierror:
            return False
        finally:
            sock.close()

        # Notify if it's not available.
        if socket_open != 0:
            util.notify(
                f"Cannot connect to {host}:{port} to send message with " f'subject: "{subject}".',
                timeout=5000,
            )
        return socket_open == 0

    @staticmethod
    def run(args):
        """Run the offlinemsmtp daemon."""
        util.notify("offlinemsmtp daemon started")
        # Listen on the outbox directory for new files.
        daemon = Daemon(args)
        observer = inotify.adapters.Inotify()
        observer.add_watch(Path(args.dir).resolve().as_posix())

        def watch_outbox(daemon, observer):
            """Watch the outbox directory and act upon files being added there."""
            for event in observer.event_gen(yield_nones=False):
                _, type_names, path, filename = event
                if filename.startswith("."):
                    # Enqueue temporary files are hidden; the message is
                    # renamed into place once it is completely written.
                    continue
                if "IN_CLOSE_WRITE" in type_names or "IN_MOVED_TO" in type_names:
                    daemon.on_created(Path(path) / filename)

        observer_thread = threading.Thread(
            target=watch_outbox, args=(daemon, observer), daemon=True
        )
        observer_thread.start()

        try:
            # Every interval, check whether there's anything to send and see if
            # there's an internet connection. If there is, try to flush the
            # send queue.
            while True:
                if not daemon.queue.empty():
                    daemon.flush_queue()

                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
