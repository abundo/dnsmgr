#!/usr/bin/env python3
'''
Classes to handle DNS, with zones

Requires a driver, for example dnsmgr_bind, that does
all the implementation specific details

Extract all network elements IP addresses
Create forward A/AAAA records
Create reverse IPv4 PTR records
Create reverse IPv6 PTR records
Create include file with zone entries
If include file has any changes, increase SOA serial number
If config ok, restart named
'''

import os
import sys
import ipaddress
import logging
import pprint
import yaml

from orderedattrdict import AttrDict

import dnsmgr_bind

pp = pprint.PrettyPrinter(indent=4)

allowed_chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-."

# Setup logger
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


def verify_dnsname(name):
    """Check if a name contains valid characters"""
    for n in name:
        if n not in allowed_chars:
            return False
    return True


class RR:
    """
    One Resource Record
    """
    def __init__(self, domain=None, name=None, typ=None, value=None, obj=None):
        self.domain = domain
        self.name = name
        self.fqdn = "%s.%s" % (self.name, self.domain)
        self.typ = typ.upper()
        self.value = value
        self.obj = obj

    def __str__(self):
        return "domain=%s, name=%s, typ=%s, value=%s obj=%s" % \
            (self.domain, self.name, self.typ, self.value, self.obj)


class Host:
    """
    Represents one host with type and values
    """
    def __init__(self, domain=None, name=None, typ=None, value=None):
        self.domain = domain
        self.name = name
        self.typ = typ
        if isinstance(value, list):
            self.value = value
        else:
            self.value = [value]
        self.fqdn = "%s.%s" % (name, domain)
    
    def __str__(self):
        return "Host(domain=%s, name=%s, typ=%s, value=%s)" % (self.domain, self.name, self.typ, self.value)
    
    def add_value(self, value):
        if isinstance(value, list):
            self.value += value
        else:
            self.value.append(value)
        
    def value_as_str(self):
        res = []
        for value in self.value:
            res.append(str(value))
        return ", ".join(res)


class Hosts:
    """
    Manage a list of hosts
    """    
    def __init__(self):
        self._hosts = {}    # key is name+typ
        self.domain = None
    
    def __len__(self):
        return len(self._hosts)

    def _add(self, host):
        key = host.fqdn + chr(0) + host.typ
        if key in self._hosts:
            # Host exist, add additional value to it
            self._hosts[key].add_value(host.value)
        else:
            self._hosts[key] = host

    def __iter__(self):
        keys = list(self._hosts.keys())
        keys.sort()
        for key in keys:
            yield self._hosts[key]
            
    def items(self):
        return self._hosts.items()

    def get(self, name):
        return self._hosts[name]

    def load(self, filename=None):
        """
        Read all host entries from the hosts file
        Empty lines and comments starting with # or ; is ignored

        recursive function, to handle $INCLUDE to other files
        """
        for line in open(filename, "r"):
            line = line.rstrip()
            if line == "" or line[0] == "#" or line[0] == ";":
                continue
            if line[0] == "$":
                tmp = line.split(None, 2)
                if len(tmp) < 2:
                    raise ValueError("Invalid $ syntax: %s" % line)
                elif tmp[0] == "$DOMAIN":
                    self.domain = tmp[1]
                elif tmp[0] == "$INCLUDE":
                    self.load(filename=tmp[1])
                else:
                    raise ValueError("Invalid command %s" % tmp[0])
                continue
    
            tmp = line.split(None, 3)
            if len(tmp) != 3:
                raise ValueError("Invalid syntax: %s" % line)
            
            name = tmp[0] 
            if name != "@" and not verify_dnsname(name):
                raise ValueError("Invalid name: %s in %s" % (name, line))

            typ = tmp[1].upper()
            value = tmp[2]
            if typ == "A":
                value = ipaddress.IPv4Address(value)
            elif typ == "AAAA":
                value = ipaddress.IPv6Address(value)
            elif typ in ["CNAME", "MX", "NS", "PTR", "SRV", "SSHFP", "TLSA", "TSIG", "TXT"]:
                pass
            
            else:
                raise ValueError("Invalid type: %s in %s" % (typ, line))
            
            host = Host(domain=self.domain, name=name, typ=typ, value=value)
            self._add(host)


class Mtrie4:
    """
    Implements Longest Prefix Match, for IPv4 addresses
    Uses a mtrie with 8-8-8-8 distribution
    
    Note, there are no functionality to remove prefixes
    Note, add prefixes in correct order, start with all /32 down to /1
    """
    
    class Node:
        def __init__(self):
            self.child = {}
            self.obj = {}
        
        def __repr__(self):
            return "Node(%s, %s)" % (self.child, self.obj)
   
    def __init__(self):
        self.root = self.Node()
    
    def add_prefix(self, prefix, obj):
        p = self.root
        ip = str(prefix[0]).split(".")
        l = prefix.prefixlen

        while l > 8:
            i = int(ip.pop(0))
            if i not in p.child:
                p.child[i] = self.Node()
            p = p.child[i]
            l -= 8
        
        i = int(ip.pop(0))
        b = i
        e = i + (128 >> l)
        for ix in range(b, e + 1):
            if ix not in p.obj:
                p.obj[ix] = obj

    def lookup(self, addr):
        """
        Search using LPM
        Returns obj if found
        If not found, returns None
        """
        p = self.root
        ip = addr.split(".")
        found = None
        while True:
            i = int(ip.pop(0))
            if i in p.obj:
                found = p.obj[i]
            if i not in p.child:
                return found
            p = p.child[i]


class Mtrie6:
    """
    Implements Longest Prefix Match, for IPv6 addresses
    
    Note, there are no functionality to remove prefixes
    """
    class Node:
        def __init__(self):
            self.child = {}
            self.obj = {}
        
        def __repr__(self):
            return "Node(%s, %s)" % (self.child, self.obj)

    def __init__(self):
        self.root = self.Node()

    
    def add_prefix(self, prefix, obj):
        p = self.root
        ip = prefix.exploded.replace(":", "")
        l = prefix.prefixlen
        if l % 4 != 0:
            raise ValueError("Cannot handle IPv6 prefixes on non-nibbles")

        while l > 4:
            i = int(ip[0], 16)
            ip = ip[1:]
            if i not in p.child:
                p.child[i] = self.Node()
            p = p.child[i]
            l -= 4
        
        i = int(ip[0])
        ip = ip[1:]
        b = i
        e = i + (128 >> l)
        for ix in range(b, e + 1):
            if ix not in p.obj:
                p.obj[ix] = obj


    def lookup(self, addr):
        """
        Search using LPM
        Returns obj if found
        If not found, returns None
        """
        addr = ipaddress.IPv6Address(addr)
        p = self.root
        ip = addr.exploded.replace(":", "")
        found = None
        while True:
            i = int(ip[0], 16)
            ip = ip[1:]
            if i in p.obj:
                found = p.obj[i]
            if i not in p.child:
                return found
            p = p.child[i]
        

class Zone:
    
    def __init__(self, zone, zonefile=None, typ=None, prefix=None):
        self.zone = zone
        self.typ = typ
        self.prefix = prefix
        if zonefile:
            self.zonefile = zonefile
        else:
            self.zonefile = zone
        
        self.records = {}
        self.l = len(self.zone)

    def __str__(self):
        return "Zone(name %s, typ %s, prefix %s, zonefile %s)" % \
            (self.zone, self.typ, self.prefix, self.zonefile)
 
    def __repr__(self):
        return "Zone %s" % self.zone
 
    def __len__(self):
        return len(self.records)

    def __iter__(self):
        keys = list(self.records.keys())
        keys.sort()
        for key in keys:
            yield self.records[key]

    def add_rr(self, rr):
        key = str(rr.name) + rr.domain
        if key in self.records:
            self.records[key].append(rr)
        else:
            self.records[key] = [rr]


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
        self.lpm4 = Mtrie4()
        self.lpm6 = Mtrie6()
        
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
        a = Zone(zone, typ="forward")
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
        
        zone = Zone(zone=zonename, prefix=prefix, typ="reverse4")
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
        
        zone = Zone(zone=zonename, prefix=prefix, typ="reverse6")
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
     
    def __init__(self, driver=None):
        self.driver = driver
        self.zones = None
        self.zonesinfo = None

    def getZones(self):
        return self.driver.getZones()

    def restart(self):
        self.driver.restart()

    def rebuild(self, hosts=None):
        """
        Convert all host entries to resource records, and
        update nameserver
        """
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

        # Go through all hosts, and add them to the correct zone
        for host in hosts:
            if host.typ == "A":
                for value in host.value:
                    # forward
                    rr = RR(domain=host.domain, name=host.name, typ=host.typ, value=value)
                    self.zones.add_rr(rr)
                    
                    # reverse
                    rr = RR(domain=host.domain, name=value, typ="PTR", value=host.name)
                    self.zones.add_rr_reverse4(rr)
                    
            elif host.typ == "AAAA":
                for value in host.value:
                    # forward
                    rr = RR(domain=host.domain, name=host.name, typ=host.typ, value=value)
                    self.zones.add_rr(rr)
                    
                    # reverse
                    rr = RR(domain=host.domain, name=value, typ="PTR", value=host.name)
                    self.zones.add_rr_reverse6(rr)
                    
            else:
                for value in host.value:
                    rr = RR(domain=host.domain, name=host.name, typ=host.typ, value=value)
                    self.zones.add_rr(rr)
                
        # Write the files to the backend
        for zone in self.zones:
            self.driver.saveZone(zone)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('cmd',
                        default=None,
                        choices=[
                            "status",
                            "loadrecords",
                            "getzones",
                            "restart",
                            "rebuild",
                            ],
                        help='Action to run',
                       )
    
    # dnsmgr arguments
    
    parser.add_argument('--configfile',
                        default='/etc/dnsmgr/dnsmgr.conf',
                       )
    parser.add_argument('--recordsfile',
                        default=None,
                       )

    # dnsmgr driver arguments
    
    parser.add_argument('--host',
                        default=None,
                       )
    parser.add_argument('--port',
                        type=str,
                        default=None,
                       )
    parser.add_argument('--includedir',
                        default=None,
                       )
    parser.add_argument('--tmpdir',
                        default=None,
                       )
    parser.add_argument('--nsconfigfile',
                        default=None,
                       )
    args = parser.parse_args()

    # Read config file, if any
    config = {}
    
    if os.path.isfile(args.configfile):
        with open(args.configfile, "r") as f:
            try:
                tmp = yaml.load(f)
                config = tmp
            except yaml.YAMLError as err:
                print("Cannot load configuration file '%s', error: %s" % (args.configfile, err))
                sys.exit(1)

    if "bind" not in config:
        config["bind"] = {}
       
    # Command line arguments overrides config file values
    
    # dnsmgr
    if args.recordsfile:   config["recordsfile"] = args.recordsfile
    
    # dnsmgr_bind
    if args.host:          config['bind']["host"]       = args.host
    if args.port:          config['bind']["port"]       = args.port
    if args.includedir:    config['bind']["includedir"] = args.includedir
    if args.tmpdir:        config['bind']["tmpdir"]     = args.tmpdir
    if args.nsconfigfile:  config['bind']["configfile"] = args.nsconfigfile    

    print("config", config)
    
    # We now have all arguments
    bindMgr = dnsmgr_bind.BindMgr(**config["bind"])
    mgr = DNS_Mgr(driver=bindMgr)
    
    if args.cmd == "status":
        print("Status not implemented")
        
    elif args.cmd == "loadrecords":
        print("Load recordsfile")
        if "recordsfile" not in config or config["recordsfile"] is None:
            print("Error: you need to specify a recordsfile")
            sys.exit(1)
        hosts = Hosts()
        hosts.load(filename=config["recordsfile"])
        for host in hosts:
            for value in host.value:
                tmp = "%s.%s" % (host.name, host.domain)
                print("%-30s %-8s %s" % (tmp, host.typ, value))
        
    elif args.cmd == "getzones":
        print("Get zones")
        if "configfile" not in config["bind"] or config["bind"]["configfile"] is None:
            print("Error: you need to specify a nsconfigfile")
            sys.exit(1)
        zonesinfo = mgr.getZones()
        for zoneinfo in zonesinfo.values():
            print("zone")
            print("    name", zoneinfo.name)
            print("    type", zoneinfo.typ)
            print("    file", zoneinfo.file)
        
    elif args.cmd == "restart":
        print("Restart DNS server")
        mgr.restart()
        
    elif args.cmd == "rebuild":
        print("Rebuild zone data from recordsfile")
        if "recordsfile" not in config or config["recordsfile"] is None:
            print("Error: you need to specify a recordsfile")
            sys.exit(1)
        hosts = Hosts()
        hosts.load(filename=config["recordsfile"])
        mgr.rebuild(hosts=hosts)
    
    else:
        print("Error: unknown command %s" % args.cmd)
    
if __name__ == "__main__":
    main()
