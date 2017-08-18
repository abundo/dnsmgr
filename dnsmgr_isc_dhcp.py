#!/usr/bin/env python3
'''
Class to handle ISC DHCP server

Functionality
- Restart server
- Write a partial configuration file with host,MAC address and IP address entries
'''

import os
import sys
import ipaddress
import logging
import yaml

from orderedattrdict import AttrDict

import dnsmgr_util as util


class DHCPd_manager:
     
    def __init__(self, config=None):
        self.config = config

    def restart_v4(self):
        log.info("Restart dhcpv4 server")
        cmd = self.config.ipv4.restart
        cmd = cmd.split(" ")
        return util.runCmd(None, cmd)

    def restart_v6(self):
        log.info("Restart dhcpv6 server")
        cmd = self.config.ipv6.restart
        cmd = cmd.split(" ")
        return util.runCmd(None, cmd)
    
    def status(self):
        raise NotImplementedError
    
    def update(self, records):
        """
        Write static DHCP bindings for ISC DHCP server
        """
        v4_enabled = self.config.ipv4.enable
        v6_enabled = self.config.ipv6.enable

        if v4_enabled:
            ipv4_file = util.MyFile(self.config.ipv4.include_file)
            ipv4_file.write("#\n")
            ipv4_file.write("# This file is automatically created by DnsMgr\n")
            ipv4_file.write("# Do not edit, changes will be lost\n")
            ipv4_file.write("#\n")

        if v6_enabled:
            ipv6_file = util.MyFile(self.config.ipv6.include_file)
            ipv6_file.write("#\n")
            ipv6_file.write("# This file is automatically created by DnsMgr\n")
            ipv6_file.write("# Do not edit, changes will be lost\n")
            ipv6_file.write("#\n")

        for record in records:
            if v4_enabled and record.typ == "A":
                if record.mac_address:
                    ipv4_file.write("\n")
                    name = record.fqdn.replace(".", "_")
                    ipv4_file.write("host %s {\n" % name)
                    ipv4_file.write("  hardware ethernet %s;\n" % record.mac_address)
                    ipv4_file.write("  fixed-address %s;\n" % record.value[0])
                    ipv4_file.write("}\n")
                    
            elif v6_enabled and record.typ == "AAAA":
                if record.mac_address:
                    print(record.value, record.mac_address)

        if v4_enabled:
            if ipv4_file.replace():
                self.restart_v4()
            ipv4_file.close()
        if v6_enabled:
            if ipv4_file.replace():
                self.restart_v6()
            ipv6_file.close()


class BaseCli(util.BaseCLI):
    
    def __init__(self):
        super().__init__()
        util.setLogLevel(self.args.loglevel)
        
        if os.path.isfile(self.args.configfile):
            try:
                self.config = util.yaml_load(self.args.configfile)
            except util.UtilException as err:
                util.die("Cannot load configuration file '%s', error: %s" % (self.args.configfile, err))
        else:
            log.warning("No configuration file found at %s", self.args.configfile)
    
        if "bind" not in self.config:
            self.config.bind = AttrDict()
        
        self.mgr = DHCPd_manager()
    

    def add_arguments2(self):
        self.parser.add_argument('--configfile',
                                 default='/etc/dnsmgr/dnsmgr.conf',
                                 )
        self.parser.add_argument('--loglevel',
                                 choices=['info', 'warning', 'error', 'debug'],
                                 help='Set loglevel, one of < info | warning | error | debug >', 
                                 default='debug',
                                 )


class CLI_restart(BaseCli):
    
    def run(self):
        print("Restart ISC DHCP server")
        self.mgr.restart()


class CLI_status(BaseCli):
    
    def run(self):
        print("Status")
        self.mgr.status()


class CLI_update(BaseCli):
    
    def add_arguments(self):
        self.parser.add_argument('-H', '--hostname',
                                 action='append',
                                 )
        self.parser.add_argument('-m', '--mac',
                                 action='append',
                                 )
    def run(self):
        print("Update ISC DHCP configuration")
        self.mgr.update()


def main():
    util.MyCLI(__name__)

    
if __name__ == "__main__":
    main()
