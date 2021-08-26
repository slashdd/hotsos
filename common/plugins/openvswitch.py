from common import plugintools

OPENVSWITCH_SERVICES_EXPRS = [r"ovsdb[a-zA-Z-]*",
                              r"ovs-vswitch[a-zA-Z-]*",
                              r"ovn[a-zA-Z-]*"]
OVS_PKGS_CORE = [r"openvswitch-switch",
                 r"ovn",
                 ]
OVS_PKGS_DEPS = [r"libc-bin",
                 r"openvswitch-switch-dpdk",
                 ]


class OpenvSwitchChecksBase(plugintools.PluginPartBase):
    pass
