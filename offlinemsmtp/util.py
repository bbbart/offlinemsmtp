import logging

import gi

gi.require_version("Notify", "0.7")
from gi.repository import Notify

SILENT = False
NOTIFICATIONS_INITIALIZED = False
_APP_NAME = "offlinemsmtp"


def notify(message, timeout=None, urgency=Notify.Urgency.LOW, replace=None):
    """Creates or updates, and shows, a ``gi.repository.Notify.Notification``.

    Pass a notification returned by an earlier call as ``replace`` to update
    that one in place. Sending a message otherwise leaves a trail of
    notifications: one saying it is being sent, and another saying how it went.

    This notifies and nothing else; it deliberately does not log. ``--silent``
    turns notifications off and must not be able to take the log with it, so
    callers log for themselves, at whatever level the message deserves.
    """
    global NOTIFICATIONS_INITIALIZED

    if SILENT:
        return None

    try:
        # Initialize the notifications if necessary.
        if not NOTIFICATIONS_INITIALIZED:
            Notify.init(_APP_NAME)
            NOTIFICATIONS_INITIALIZED = True

        if replace is not None:
            # Reuse the notification that is already on screen, so that the
            # desktop updates it rather than stacking a second one on top.
            notification = replace
            notification.update(_APP_NAME, message, None)
        else:
            notification = Notify.Notification.new(_APP_NAME, message)

        # Always set the timeout: a replaced notification would otherwise keep
        # the one it was given when it was the "Sending ..." message.
        notification.set_timeout(timeout if timeout else Notify.EXPIRES_DEFAULT)
        notification.set_urgency(urgency)
        notification.show()
        return notification
    except Exception as e:
        logging.warning(f"failed to show notification: {e}")
        return None
