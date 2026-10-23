import unittest

from dbus_next import Variant

from timetracker.tray import StatusNotifierItem


class StatusNotifierItemTests(unittest.TestCase):
    def test_exposes_read_only_status_notifier_properties(self) -> None:
        item = StatusNotifierItem(bytes(64 * 64 * 4), lambda: None)
        interface = item.introspect()
        properties = {prop.name: prop for prop in interface.properties}

        self.assertEqual(interface.name, "org.kde.StatusNotifierItem")
        self.assertEqual(properties["IconPixmap"].signature, "a(iiay)")
        self.assertEqual(properties["ToolTip"].signature, "(sa(iiay)ss)")
        Variant("a(iiay)", item.IconPixmap)
        Variant("(sa(iiay)ss)", item.ToolTip)
        self.assertTrue(
            all(prop.access.value == "read" for prop in interface.properties)
        )

    def test_activation_calls_window_callback(self) -> None:
        activated = []
        item = StatusNotifierItem(bytes(64 * 64 * 4), lambda: activated.append(True))

        item.Activate(0, 0)

        self.assertEqual(activated, [True])


if __name__ == "__main__":
    unittest.main()
