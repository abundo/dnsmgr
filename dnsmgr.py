#!/usr/bin/env python3
'''
Classes to handle DNS, with zones

Requires a driver, for example dnsmgr_bind, that does
all the implementation specific details

Load all resource records
Create forward A/AAAA records
Create reverse IPv4 PTR records
Create reverse IPv6 PTR records
Create include file with zone entries
If include file has any changes, increase SOA serial number and reload zone
'''

import os
import sys
import ipaddress
import logging
import yaml

from orderedattrdict import AttrDict

import dnsmgr_util as util


class Records:
    """
    Manage a list of records
    """    
    def __init__(self):
        self._records = {}    # key is name+typ
        self.domain = None
    
    def __len__(self):
        return len(self._records)

    def add(self, record):
        key = record.fqdn + chr(0) + record.typ
        if key in self._records:
            # Record exist, add additional value to it
            self._records[key].add_value(record.value)
        else:
            self._records[key] = record

    def __iter__(self):
        keys = list(self._records.keys())
        keys.sort()
        for key in keys:
            yield self._records[key]
            
    def items(self):
        return self._records.items()

    def get(self, name):
        return self._records[name]


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
     
    def __init__(self, config=None, driver=None):
        self.config = config
        self.driver = driver
        self.zones = None
        self.zonesinfo = None
        self.records = Records()

    def getZones(self):
        return self.driver.getZones()

    def restart(self):
        self.driver.restart()
    
    def load(self):
        if "records" not in self.config:
            # Backwards compatible, will be removed in the future
            if "recordsfile" not in self.config or self.config.recordsfile is None:
                util.die("Error: you need to specify a recordsfile")
            self.config.records = [AttrDict( type='file_loader.py', name=self.config.recordsfile,) ]

        for loader in self.config.records:
            log.debug("Loading records using %s from %s" % (loader.type, loader.name))
            # Import the loader to use
            loader_module = util.import_file(loader.type)
            
            obj = loader_module.Loader()
            obj.load(loader.name, self.records)

    def restart(self):
    def update_dns(self, records=None):
        """
        Convert all Record entries to resource records,
        update nameserver and dhcp server
        """
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

        # Go through all receords, and add them to the correct zone
        for record in records:
            if record.typ == "A":
                for value in record.value:
                    # forward
                    rr = util.RR(domain=record.domain, ttl=record.ttl, name=record.name, typ=record.typ, value=value)
                    self.zones.add_rr(rr)
                    
                    # reverse
                    rr = util.RR(domain=record.domain, ttl=record.ttl,name=value, typ="PTR", value=record.name)
                    self.zones.add_rr_reverse4(rr)
                    
            elif record.typ == "AAAA":
                for value in record.value:
                    # forward
                    rr = util.RR(domain=record.domain, ttl=record.ttl,name=record.name, typ=record.typ, value=value)
                    self.zones.add_rr(rr)
                    
                    # reverse
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
        try:
            if not self.config.dhcpd.enable:
                return
        except AttributeError:
            return

        # Load the driver
        dhcpd_module = util.import_file(self.config.dhcpd.driver)
        obj = dhcpd_module.DHCPd_manager(self.config.dhcpd)
        obj.update(self.records)


class BaseCLI(util.BaseCLI):
    
    def __init__(self):
        super().__init__()
        util.setLogLevel(self.args.loglevel)
        
        if os.path.isfile(self.args.configfile):
            try:
                self.config = util.yaml_load(self.args.configfile)
            except util.UtilException as err:
                util.die("Cannot load configuration file '%s', error: %s" % (self.args.configfile, err))
        else:
            log.warning("No configuration file found at %s" % self.args.configfile)
    
        if "bind" not in self.config:
            self.config.bind = AttrDict()
        
        # Load the driver
        if 'ns' in self.config:
            ns_driver_module = util.import_file(self.config.ns.driver)
            ns_driver = ns_driver_module.NS_Manager(**self.config.ns.config)
        else:
            # for compability, remove in a future version
            import dnsmgr_bind
            ns_driver = dnsmgr_bind.NS_Manager(**self.config.bind)
        self.mgr = DNS_Mgr(config=self.config, driver=ns_driver)
    

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
        if "configfile" not in self.config.bind or self.config.bind.configfile is None:
            util.die("Error: you need to specify a nsconfigfile")
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
                if record.mac_address:
                    print("        mac=%s" % (record.mac_address))


class CLI_restart(BaseCLI):
    
    def run(self):
        print("Restart DNS server")
        self.mgr.restart()


class CLI_status(BaseCLI):
    
    def run(self):
        print("Not implemented")


class CLI_update(BaseCLI):
    
    def run(self):
        print("Load resource records")
        self.mgr.load()

        print("Update zone data from recordsfile")
        self.mgr.update_dns()

        print("Update DHCP ")        
        self.mgr.update_dhcp()


def main():
    util.MyCLI(__name__)
        

    
if __name__ == "__main__":
    main()
