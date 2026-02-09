"""Vendor implementations for switch management.

Importing this package triggers vendor registration via @register_vendor.
"""

import networkmgmt.switchctrl.vendors.cisco  # noqa: F401
import networkmgmt.switchctrl.vendors.mikrotik  # noqa: F401
import networkmgmt.switchctrl.vendors.netgear  # noqa: F401
import networkmgmt.switchctrl.vendors.qnap  # noqa: F401
