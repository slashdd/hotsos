# Copyright 2021 Edward Hope-Morley
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from common import (
    issue_types,
    issues_utils,
)
from common.cli_helpers import CLIHelper
from common.host_helpers import HostNetworkingHelper
from common.plugins.kernel import KernelEventChecksBase

YAML_PRIORITY = 2


class KernelNetworkChecks(KernelEventChecksBase):

    def __init__(self):
        super().__init__(yaml_defs_group='network-checks')
        self.cli_helper = CLIHelper()
        self.hostnet_helper = HostNetworkingHelper()

    def check_mtu_dropped_packets(self, results):
        interfaces = {}
        for r in results:
            if r.get(1) in interfaces:
                interfaces[r.get(1)] += 1
            else:
                interfaces[r.get(1)] = 1

        if interfaces:
            # only report on interfaces that currently exist
            host_interfaces = [iface.name for iface in
                               self.hostnet_helper.host_interfaces_all]
            # filter out interfaces that are actually ovs bridge aliases
            ovs_bridges = self.cli_helper.ovs_vsctl_list_br()

            interfaces_extant = {}
            for iface in interfaces:
                if iface in host_interfaces:
                    if iface not in ovs_bridges:
                        interfaces_extant[iface] = interfaces[iface]

            if interfaces_extant:
                msg = ("kernel has reported over-mtu dropped packets for ({}) "
                       "interfaces".format(len(interfaces_extant)))
                issue = issue_types.NetworkWarning(msg)
                issues_utils.add_issue(issue)

                # sort by number of occurrences
                sorted_dict = {}
                for k, v in sorted(interfaces_extant.items(),
                                   key=lambda e: e[1], reverse=True):
                    sorted_dict[k] = v

                return {"over-mtu-dropped-packets": sorted_dict}

    def check_nf_conntrack_full(self, results):
        if results:
            # TODO: consider resticting this to last 24 hours
            msg = "kernel has reported nf_conntrack_full - please check"
            issue = issue_types.NetworkWarning(msg)
            issues_utils.add_issue(issue)

    def process_results(self, results):
        """ See defs/events.yaml for definitions. """
        info = {}
        for section in self.event_definitions.values():
            for event in section:
                _results = results.find_by_tag(event)
                if event == "over-mtu":
                    ret = self.check_mtu_dropped_packets(_results)
                    if ret:
                        info.update(ret)
                elif event == "nf-conntrack-full":
                    ret = self.check_nf_conntrack_full(_results)
                    if ret:
                        info.update(ret)

        return info

    def __call__(self):
        output = super().__call__()
        if output:
            self._output.update(output)
