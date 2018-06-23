#!/usr/bin/env python3
'''
Classes to handle a DNS nameserver with zones, and a DHCP server

DNS and DHCP server functionality is implemented as drivers,
which does the actual work. Which driver to use is specified in
the configuration file

- Load all resource records
- Create forward A/AAAA records
- Create reverse IPv4 PTR records
- Create reverse IPv6 PTR records
- Create DNS include files with zone entries
  - If include files has any changes, increase SOA serial number and reload zones
- Create DHCP host include file
  - If include file has any changes, restart nameserver
'''

import os
import sys
import ipaddress
import logging
import yaml

from orderedattrdict import AttrDict

import dnsmgr_util as util


class Zones:

    def __init__(self):
        self.zones = []
        self.reverse4 = []
        self.reverse6 = []
        
        self.lpm4 = None
        self.lpm6 = None

    def __iter__(self):
        for zone in self.zones:
            yield zone
        for zone in self.reverse4:
            yield zone
        for zone in self.reverse6:
            yield zone

    def init_search(self):
        """
        Sort the IPv4 and IPv6 prefixes and create a data structure for
        fast longest prefix match
        """
        self.lpm4 = util.Mtrie4()
        self.lpm6 = util.Mtrie6()
        
        self.reverse4.sort(key=lambda x: x.prefix.prefixlen, reverse=True)
        for zone in self.reverse4:
            self.lpm4.add_prefix(zone.prefix, zone)
            
        self.reverse6.sort(key=lambda x: x.prefix.prefixlen, reverse=True)
        for zone in self.reverse6:
            self.lpm6.add_prefix(zone.prefix, zone)

    #
    # Zones
    #
     
    def add_zone(self, zone):
        """
        Forward zones
        Keep sorted on longest zonename
        """
        a = util.Zone(zone, typ="forward")
        tmp = self.zones + [a]
        tmp.sort(key=lambda x: len(x.zone))
        self.zones = tmp

    def add_zone_reverse4(self, zonename):
        """
        Add a IPv4 reverse zone
        input is the name of the zone, for example 1.168.192.in-addr.arpa
        The name is used to create the prefix, used for LPM
        """
        if not zonename.endswith(".in-addr.arpa"):
            raise ValueError("IPv4 reverse zone must end in .in-addr.arpa")
        
        # Extract the prefix, so we can do LPM
        prefix = zonename[:-13]
        tmp = prefix.split(".")
        if len(tmp) > 3:
            raise ValueError("Can't extract IP addresses from zonename. %s" % tmp)
        prefixlen = 8 * len(tmp)
        
        # Reverse the addresses
        prefix = []
        for t in tmp:
            prefix.insert(0, t)
        while len(prefix) != 4:
            prefix.append("0")

        prefixstr = ".".join(prefix)
        prefixstr += "/%s" % prefixlen
        prefix = ipaddress.IPv4Network(prefixstr, strict=True)
        
        zone = util.Zone(zone=zonename, prefix=prefix, typ="reverse4")
        self.reverse4.append(zone)

    def add_zone_reverse6(self, zonename):
        """
        Add a IPv6 reverse zone
        input is the name of the zone, for example 1.0.0.0.c.e.f.d.0.7.4.0.1.0.0.2.ip6.arpa
        The name is used to create the prefix, used for LPM
        """
        if not zonename.endswith(".ip6.arpa"):
            raise ValueError("IPv6 reverse zone must end in .ip6.arpa")

        # Extract the prefix, so we can do LPM
        prefix = zonename[:-9]
        tmp = prefix.split(".")
        if len(tmp) > 31:
            raise ValueError("Can't extract IP addresses from zonename. %s" % tmp)
        prefixlen = 4 * len(tmp)
        
        # Reverse the addresses
        prefix = []
        for t in tmp:
            prefix.insert(0, t)
        while len(prefix) != 32:
            prefix.append("0")
        
        prefixstr = ""
        for ix in range(0, len(prefix)):
            if ix and (ix % 4) == 0:
                prefixstr += ":"
            prefixstr += prefix[ix]
        prefixstr += "/%s" % prefixlen
        prefix = ipaddress.IPv6Network(prefixstr, strict=True)
        log.debug("prefix %s", prefix)
        
        zone = util.Zone(zone=zonename, prefix=prefix, typ="reverse6")
        self.reverse6.append(zone)


    #
    # Records
    #
    
    def add_rr(self, rr):
        # search for a matching zone
        for zone in self.zones:
            if zone.zone == rr.domain:
                zone.add_rr(rr)
                return
        log.info("Ignored, NOT handling forward DNS for %s", rr)

    def add_rr_reverse4(self, rr):
        zone = self.lpm4.lookup(str(rr.name))
        if zone is not None:
            zone.add_rr(rr)
        else:
            log.warning("Ignored, NOT handling reverse DNS for %s", rr)

    def add_rr_reverse6(self, rr):
        zone = self.lpm6.lookup(rr.name)
        if zone is not None:
            zone.add_rr(rr)
        else:
            log.warning("Ignored, NOT handling reverse DNS for %s", rr)


class DNS_Mgr:
     
    def __init__(self, config_file=None):
        if config_file is None:
            config_file = '/etc/dnsmgr/dnsmgr.conf'
        self.config_file = config_file
        self.zones = None
        self.zonesinfo = None
        self.records = util.Records()

        # Load configuration file
        if not os.path.isfile(self.config_file):
            util.die("No configuration file found at %s" %self.config_file)
        try:
            self.config = util.yaml_load(self.config_file)
        except util.UtilException as err:
            util.die("Cannot load configuration file '%s', error: %s" % (self.args.configfile, err))
    
        # Load DNS server driver
        self.driver_module = util.import_file(self.config.dns_server.driver)
        self.driver = self.driver_module.NS_Manager(**self.config.dns_server.config)

    def getZones(self):
        return self.driver.getZones()

    def load(self):
        log.debug("Load resource records")
        self.records = util.Records()
        for loader in self.config.records:
            log.debug("Loading records using %s from %s", loader.type, loader.name)
            # Import the loader to use
            loader_module = util.import_file(loader.type)
            
            obj = loader_module.Loader()
            obj.load(loader.name, self.records)

    def restart(self):
        self.driver.restart()
    
    def status(self):
        raise NotImplementedError

    def update_dns(self, records=None):
        """
        Convert all Record entries to resource records,
        update nameserver and dhcp server
        """
        log.debug("Update DNS server")
        try:
            if not self.config.dns_server.enable:
                return
        except AttributeError:
            pass    # default enabled

        if records is None:
            records = self.records
        self.zonesinfo = self.driver.getZones()
        self.zones = Zones()
        
        for name, zoneinfo in self.zonesinfo.items():
            if zoneinfo.typ != "master":
                log.debug("Ignoring zone '%s' with type '%s'", name, zoneinfo.typ)
                continue
            log.debug("Adding zone %s", name)

            if name.endswith(".in-addr.arpa"):
                self.zones.add_zone_reverse4(name)
            elif name.endswith(".ip6.arpa"):
                self.zones.add_zone_reverse6(name)
            else:
                self.zones.add_zone(name)

        self.zones.init_search()

        # Go through all records, and add them to the correct zone
        for record in records:
            if record.typ == "A":
                for value in record.value:
                    # forward
                    rr = util.RR(domain=record.domain, ttl=record.ttl, name=record.name, typ=record.typ, value=value)
                    self.zones.add_rr(rr)
                    
                    # reverse
                    if record.reverse:
                        rr = util.RR(domain=record.domain, ttl=record.ttl,name=value, typ="PTR", value=record.name)
                        self.zones.add_rr_reverse4(rr)
                    
            elif record.typ == "AAAA":
                for value in record.value:
                    # forward
                    rr = util.RR(domain=record.domain, ttl=record.ttl,name=record.name, typ=record.typ, value=value)
                    self.zones.add_rr(rr)
                    
                    # reverse
                    if record.reverse:
                        rr = util.RR(domain=record.domain, ttl=record.ttl, name=value, typ="PTR", value=record.name)
                        self.zones.add_rr_reverse6(rr)
                    
            else:
                for value in record.value:
                    rr = util.RR(domain=record.domain, ttl=record.ttl, name=record.name, typ=record.typ, value=value)
                    self.zones.add_rr(rr)
                
        # Write the files to the backend
        for zone in self.zones:
            self.driver.saveZone(zone)

    def update_dhcp(self):
        """
        Write static DHCP bindings for ISC DHCP server
        """
        log.debug("Update DHCP server")
        try:
            if not self.config.dhcp_server.enable:
                return
        except AttributeError:
            return

        # Load the driver
        dhcpd_module = util.import_file(self.config.dhcp_server.driver)
        
        config_section = self.config.dhcp_server.driver
        if config_section.startswith("dnsmgr_"):
            config_section = config_section[7:]
        if config_section.endswith(".py"):
            config_section = config_section[:-3]
        dhcp_config = getattr(self.config, config_section)
        obj = dhcpd_module.DHCPd_manager(dhcp_config)
        obj.update(self.records)


class BaseCLI(util.BaseCLI):
    
    def __init__(self):
        super().__init__()
        log.setLevel(self.args.loglevel)
        
        self.mgr = DNS_Mgr(config_file=self.args.configfile)
    

    def add_arguments2(self):
        self.parser.add_argument('--configfile',
                                 default='/etc/dnsmgr/dnsmgr.conf',
                                 )
        self.parser.add_argument('--loglevel',
                                 choices=['info', 'warning', 'error', 'debug'],
                                 help='Set loglevel, one of < info | warning | error | debug >', 
                                 default='info'
                                 )


class CLI_getzones(BaseCLI):
    
    def run(self):
        print("Get zones")
        zonesinfo = self.mgr.getZones()
        for zoneinfo in zonesinfo.values():
            print("zone")
            print("    name", zoneinfo.name)
            print("    type", zoneinfo.typ)
            print("    file", zoneinfo.file)

class CLI_load(BaseCLI):
    
    def run(self):
        print("Load resource records")
        self.mgr.load()
            
        for record in self.mgr.records:
            for value in record.value:
                tmp = "%s.%s" % (record.name, record.domain)
                print("%-30s %5s %-8s %s" % (tmp, record.ttl, record.typ, value))
                print("        reverse=%s" % (record.reverse), end="")
                if record.mac_address:
                    print("  mac=%s" % (record.mac_address), end="")
                print()

class CLI_restart(BaseCLI):
    
    def run(self):
        print("Restart DNS server")
        self.mgr.restart()


class CLI_status(BaseCLI):
    
    def run(self):
        print("Check status")
        self.mgr.status()


class CLI_update(BaseCLI):
    
    def run(self):
        self.mgr.load()
        self.mgr.update_dns()
        self.mgr.update_dhcp()


def main():
    util.MyCLI(__name__)

    
if __name__ == "__main__":
    main()
